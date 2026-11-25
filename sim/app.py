"""Логика симулятора: физика, пульт, INAV SITL и обновление кадра."""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from .config import app_dir
from .dynamics import Aircraft, SimConfig
from .fdm import FdmError, jsbsim_info, make_engine
from .joystick import Joystick, list_devices
from .osd import OsdRenderer, OsdScreen
from .presets import find_preset, default_preset
from .render import WorldRenderer
from .sitl import INAVSitl


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


class SimController(QObject):
    status_changed = Signal(str)
    log = Signal(str)
    stopped = Signal(str)

    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = cfg
        self.preset = find_preset(str(cfg.get("preset_uid", ""))) or default_preset()
        self.sim_cfg = SimConfig()
        self.preset.apply_to_cfg(self.sim_cfg)
        self.sim_cfg.alt_start = float(cfg.get("start_alt_m", 120.0))
        self.sim_cfg.v_start = float(cfg.get("start_speed_m_s", 24.0))
        self.aircraft = Aircraft()
        self.engine = None
        self.sitl: INAVSitl | None = None
        self.osd = OsdScreen()
        self.osd_renderer = OsdRenderer(app_dir() / "assets" / "font_inav.png")
        self.world = WorldRenderer()
        self.joy = Joystick()
        self.joy_state = None
        self.paused = False
        self.started = False
        self.error = ""
        self._last = time.monotonic()
        self._fps = 0.0
        self._fps_n = 0
        self._fps_t = time.monotonic()
        self._keys: set[int] = set()
        self._kb_thr = 0.0
        self._kb_roll = 0.0
        self._kb_pitch = 0.0
        self._kb_yaw = 0.0
        self._arm_btn_prev = False
        self._mode_btn_prev = False
        self._last_channels = [1500] * 16
        self._last_status = ""

    # ── запуск ──────────────────────────────────────────────
    def start(self) -> bool:
        self.error = ""
        self.stopped.emit("")
        if self.engine is None:
            info = jsbsim_info()
            if not info.get("installed"):
                self.error = info.get("error") or "JSBSim не установлен"
                return False
            try:
                self.engine = make_engine(self.sim_cfg, self.aircraft)
                self.engine.enable_fc(True)
            except FdmError as e:
                self.error = str(e)
                return False
        self._open_joystick()
        if self.sitl is None:
            self.sitl = INAVSitl(self.cfg, self.engine)
            self.sitl.osd = self.osd
        elif self.sitl.engine is not self.engine:
            self.sitl.rebind(self.engine)
        if not self.sitl.start():
            self.error = self.sitl.error
            return False
        self.started = True
        self.paused = False
        self._last = time.monotonic()
        self.log.emit("INAV SITL: %s" % self.sitl.status)
        return True

    def stop(self) -> None:
        self.started = False
        if self.sitl is not None:
            self.sitl.stop()
        self.joy.close()
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:  # noqa: BLE001
                pass
            self.engine = None

    def reset(self) -> None:
        if self.engine is not None:
            try:
                self.engine.reset(self.sim_cfg)
            except Exception as e:  # noqa: BLE001
                self.log.emit("Сброс физики: %s" % e)
        self.aircraft.reset(self.sim_cfg)
        self.paused = False
        self.log.emit("Сброс")

    def restart_sitl(self) -> None:
        if self.sitl is None:
            return
        ok = self.sitl.start()
        if not ok:
            self.error = self.sitl.error
        self.log.emit("INAV: %s" % (self.sitl.status if ok else self.sitl.error))

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)
        self._last = time.monotonic()

    def apply_settings(self, cfg: dict) -> None:
        """Применить настройки. Если сменился борт — пересобрать физику."""
        old_uid = str(self.cfg.get("preset_uid", ""))
        self.cfg.clear()
        self.cfg.update(cfg)
        new_uid = str(self.cfg.get("preset_uid", ""))
        self.sim_cfg.alt_start = float(self.cfg.get("start_alt_m", 120.0))
        self.sim_cfg.v_start = float(self.cfg.get("start_speed_m_s", 24.0))
        if new_uid != old_uid:
            self.rebuild()
        else:
            self._open_joystick()
        if self.sitl is not None:
            self.sitl.auto_arm = bool(self.cfg.get("auto_arm", True))
            self.sitl.set_mode(str(self.cfg.get("start_mode", "AH")))

    def rebuild(self) -> None:
        preset = find_preset(str(self.cfg.get("preset_uid", ""))) or default_preset()
        self.preset = preset
        self.sim_cfg = SimConfig()
        preset.apply_to_cfg(self.sim_cfg)
        self.sim_cfg.alt_start = float(self.cfg.get("start_alt_m", 120.0))
        self.sim_cfg.v_start = float(self.cfg.get("start_speed_m_s", 24.0))
        old_engine = self.engine
        self.engine = None
        if old_engine is not None:
            try:
                old_engine.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.engine = make_engine(self.sim_cfg, self.aircraft)
            self.engine.enable_fc(True)
        except FdmError as e:
            self.error = str(e)
            self.log.emit("Физика: %s" % e)
            return
        self.aircraft.reset(self.sim_cfg)
        if self.sitl is not None:
            self.sitl.rebind(self.engine)
        self.log.emit("Борт: %s" % preset.name)


    # ── пульт ───────────────────────────────────────────────
    def _open_joystick(self) -> None:
        devices = list_devices()
        if not devices:
            self.joy.close()
            self.log.emit("Пульт не найден — клавиатура")
            return
        want = str(self.cfg.get("joystick_name", ""))
        dev = None
        for d in devices:
            if want and d.name == want:
                dev = d
                break
        if dev is None:
            dev = devices[0]
        if self.joy.device is not None and self.joy.device.path == dev.path:
            return
        ok = self.joy.open(dev)
        self.log.emit("Пульт: %s" % (dev.name if ok else "не открылся"))

    def _axis(self, axes, idx: int, fallback: float) -> float:
        try:
            if idx is None or int(idx) < 0 or int(idx) >= len(axes):
                return fallback
            return _clamp(float(axes[int(idx)]), 0.0, 1.0)
        except (TypeError, ValueError, IndexError):
            return fallback

    def _keyboard_axes(self, dt: float) -> tuple[float, float, float, float]:
        speed = 1.6
        self._kb_roll = _clamp(self._kb_roll + (
            (1.0 if Qt.Key_Right in self._keys else 0.0)
            - (1.0 if Qt.Key_Left in self._keys else 0.0)) * dt * speed, -1, 1)
        self._kb_pitch = _clamp(self._kb_pitch + (
            (1.0 if Qt.Key_Up in self._keys else 0.0)
            - (1.0 if Qt.Key_Down in self._keys else 0.0)) * dt * speed, -1, 1)
        self._kb_yaw = _clamp(self._kb_yaw + (
            (1.0 if Qt.Key_D in self._keys else 0.0)
            - (1.0 if Qt.Key_A in self._keys else 0.0)) * dt * speed, -1, 1)
        self._kb_thr = _clamp(self._kb_thr + (
            (1.0 if Qt.Key_W in self._keys else 0.0)
            - (1.0 if Qt.Key_S in self._keys else 0.0)) * dt * 0.7, 0, 1)
        # без нажатий стики плавно возвращаются в центр
        for name in ("_kb_roll", "_kb_pitch", "_kb_yaw"):
            v = getattr(self, name)
            if (Qt.Key_Left not in self._keys and Qt.Key_Right not in self._keys
                    and Qt.Key_Up not in self._keys and Qt.Key_Down not in self._keys
                    and Qt.Key_A not in self._keys and Qt.Key_D not in self._keys):
                setattr(self, name, v * max(0.0, 1.0 - dt * 4.0))
        return self._kb_roll, self._kb_pitch, self._kb_yaw, self._kb_thr

    def _read_channels(self, dt: float) -> list[int]:
        state = self.joy.read() if self.joy.alive else None
        self.joy_state = state
        axes = list(state.axes) if state is not None else []
        kb_roll, kb_pitch, kb_yaw, kb_thr = self._keyboard_axes(dt)
        cfg = self.cfg

        def axis_norm(idx, fallback):
            return self._axis(axes, idx, (fallback + 1.0) * 0.5) * 2.0 - 1.0

        roll = axis_norm(cfg.get("axis_roll", 0), kb_roll)
        pitch = axis_norm(cfg.get("axis_pitch", 1), kb_pitch)
        yaw = axis_norm(cfg.get("axis_yaw", 3), kb_yaw)
        if state is None:
            roll, pitch, yaw = kb_roll, kb_pitch, kb_yaw
        if cfg.get("invert_roll"):
            roll = -roll
        if cfg.get("invert_pitch"):
            pitch = -pitch
        if cfg.get("invert_yaw"):
            yaw = -yaw
        dead = _clamp(float(cfg.get("deadband", 0.03)), 0.0, 0.3)
        for name, v in (("roll", roll), ("pitch", pitch), ("yaw", yaw)):
            if abs(v) < dead:
                if name == "roll":
                    roll = 0.0
                elif name == "pitch":
                    pitch = 0.0
                else:
                    yaw = 0.0

        if state is not None:
            if str(cfg.get("throttle_mode", "stick")) == "center":
                thr = _clamp((self._axis(axes, cfg.get("axis_throttle", 2), 0.5) - 0.5) * 2.0, 0.0, 1.0)
            else:
                thr = self._axis(axes, cfg.get("axis_throttle", 2), 0.0)
            if cfg.get("invert_throttle"):
                thr = 1.0 - thr
        else:
            thr = kb_thr
        thr = _clamp(thr, 0.0, 1.0)

        ch = [1500] * 16
        ch[0] = int(round(1500 + roll * 500))
        ch[1] = int(round(1500 + pitch * 500))
        ch[2] = int(round(1000 + thr * 1000))
        ch[3] = int(round(1500 + yaw * 500))

        # Кнопки пульта
        if state is not None:
            btns = int(state.buttons)
            ab = int(cfg.get("arm_button", -1))
            mb = int(cfg.get("mode_button", -1))
            if ab >= 0:
                cur = bool(btns & (1 << ab))
                if cur and not self._arm_btn_prev:
                    self.toggle_arm()
                self._arm_btn_prev = cur
            if mb >= 0:
                cur = bool(btns & (1 << mb))
                if cur and not self._mode_btn_prev:
                    self.toggle_mode()
                self._mode_btn_prev = cur

        self._last_channels = ch
        return ch

    def toggle_arm(self) -> None:
        if self.sitl is None:
            return
        self.sitl.toggle_arm()
        self.cfg["auto_arm"] = False
        self.log.emit("ARM: %s" % ("взведён" if self.sitl.manual_armed else "снят"))

    def toggle_mode(self) -> None:
        if self.sitl is None:
            return
        mode = "ACRO" if self.sitl.mode == "AH" else "AH"
        self.sitl.set_mode(mode)
        self.cfg["start_mode"] = mode
        self.log.emit("Режим INAV: %s" % mode)

    def toggle_chase(self) -> None:
        self.cfg["chase_view"] = not bool(self.cfg.get("chase_view", False))

    # ── шаг ─────────────────────────────────────────────────
    def tick(self) -> None:
        now = time.monotonic()
        dt = _clamp(now - self._last, 0.0, 0.1)
        self._last = now
        if not self.started:
            return
        ch = self._read_channels(dt)
        if self.sitl is not None:
            self.sitl.set_rc(ch)
        # Пока INAV калибруется, физику не двигаем: прошивка должна
        # увидеть неподвижный борт и спокойно выставить оси.
        ready = self.sitl is not None and self.sitl.ready
        if self.engine is not None and not self.paused:
            if ready or (self.sitl is None):
                self.engine.step(dt, ch)
            else:
                # кормим INAV начальным состоянием
                pass
        if self.sitl is not None:
            self.sitl.update_state(self.aircraft)
            status = self.sitl.status
            if status != self._last_status:
                self._last_status = status
                self.status_changed.emit(status)
        self._fps_n += 1
        if now - self._fps_t >= 1.0:
            self._fps = self._fps_n / max(1e-6, now - self._fps_t)
            self._fps_n = 0
            self._fps_t = now

    @property
    def fps(self) -> float:
        return self._fps

    def osd_snapshot(self):
        snap = self.osd.snapshot()
        stale = (time.monotonic() - snap[4]) > 1.5 if snap[4] > 0 else True
        return snap, stale


class SimView(QWidget):
    def __init__(self, controller: SimController, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(640, 400)
        self.setMouseTracking(True)
        self.setStyleSheet("background:#0b1016;")

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        if self.c.engine is None or not self.c.started:
            p.fillRect(0, 0, w, h, QColor(10, 16, 22))
            p.setPen(QColor(220, 228, 236))
            font = QFont()
            font.setPointSizeF(max(11.0, h / 40.0))
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Нажмите «Старт»\n(INAV SITL + джойстик)")
            return
        try:
            self.c.world.render(p, w, h, self.c.aircraft, self.c.cfg)
        except Exception:  # noqa: BLE001
            p.fillRect(0, 0, w, h, QColor(10, 16, 22))
        if self.c.cfg.get("osd_enabled", True):
            snap, stale = self.c.osd_snapshot()
            sitl_ready = self.c.sitl is None or self.c.sitl.ready
            self.c.osd_renderer.draw(
                p, w, h, snap, float(self.c.cfg.get("osd_scale", 1.0)),
                stale=stale and sitl_ready)
        sitl = self.c.sitl
        hint = ""
        if sitl is not None and not sitl.ready:
            hint = ("Инициализация INAV SITL…\n"
                    "Калибровка датчиков, ~10 секунд")
        elif self.c.paused:
            hint = "Пауза (P)"
        if hint:
            p.save()
            p.setOpacity(0.72)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(8, 12, 18))
            box_w, box_h = min(w - 40, 460), 86
            p.drawRoundedRect(QRectF((w - box_w) / 2, (h - box_h) / 2,
                                     box_w, box_h), 8, 8)
            p.setOpacity(1.0)
            p.setPen(QColor(235, 240, 245))
            font = QFont()
            font.setPointSizeF(max(10.0, h / 55.0))
            p.setFont(font)
            p.drawText(QRectF((w - box_w) / 2, (h - box_h) / 2, box_w, box_h),
                       Qt.AlignCenter, hint)
            p.restore()

    def keyPressEvent(self, ev) -> None:
        key = ev.key()
        if key == Qt.Key_Space:
            self.c.toggle_arm()
            return
        if key in (Qt.Key_M,):
            self.c.toggle_mode()
            return
        self.c._keys.add(key)
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev) -> None:
        self.c._keys.discard(ev.key())
        super().keyReleaseEvent(ev)

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        super().mousePressEvent(ev)


__all__ = ["SimController", "SimView"]
