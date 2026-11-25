"""Мост к INAV SITL: X-Plane-состояние, MSP-стики/выходы и OSD.

Схема:
    джойстик -> RC-каналы -> MSP_SET_RAW_RC -> INAV SITL
    физика   -> X-Plane RREF/DREF     -> INAV SITL
    INAV     -> MSP_MOTOR / MSP_SERVO -> рули и тяга JSBSim
    INAV     -> MSP_DISPLAYPORT       -> OSD на экране

SITL — отдельный процесс ``inav_SITL.exe`` (папка ``sitl/``). Каждый
UART прошивки в SITL — это TCP-порт: UART1 = 5760, UART2 = 5761 (MSP),
UART3 = 5762 (MSP DisplayPort / OSD). EEPROM в ``sitl/eeprom.bin`` уже
настроен: OSD включён, UART3 отдан под MSP DisplayPort.
"""
from __future__ import annotations

import math
import os
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

from .config import resolve

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FT = 0.3048

MSP_SET_RAW_RC = 200
MSP_MOTOR = 104
MSP_SERVO = 103
MSP_STATUS = 150
MSP_RC = 105
MSP_ATTITUDE = 108
MSP_ALTITUDE = 109
MSP_ANALOG = 110
MSP_RAW_GPS = 106
MSP_DISPLAYPORT = 182

# Биты arming_flags INAV
ARMING_FLAG_ARMED = 0x4
ARMING_FLAG_ARM_SWITCH = 0x4000
ARMING_FLAG_CALIBRATING = 0x200


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def msp_v1(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload), cmd & 0xFF]) + payload
    chk = 0
    for b in body:
        chk ^= b
    return b"$M<" + body + bytes([chk])


class MspReader:
    """Накопительный разбор MSP v1-ответов из TCP-потока."""

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, data: bytes):
        self.buf += data
        out = []
        while True:
            i = self.buf.find(b"$M>")
            if i < 0:
                if len(self.buf) > 8192:
                    self.buf = self.buf[-512:]
                break
            if len(self.buf) < i + 5:
                break
            ln = self.buf[i + 3]
            if len(self.buf) < i + 6 + ln:
                break
            frame = self.buf[i:i + 6 + ln]
            chk = 0
            for b in frame[3:-1]:
                chk ^= b
            self.buf = self.buf[i + 6 + ln:]
            if chk == frame[-1]:
                out.append((frame[4], frame[5:5 + ln]))
        return out


class SitlTelemetry:
    """Последние данные из INAV для статуса и настроек."""

    def __init__(self) -> None:
        self.armed = False
        self.arming_flags = 0
        self.motors = [0] * 8
        self.servos = [1500] * 18
        self.rc = [1500] * 16
        self.attitude = (0, 0, 0)
        self.altitude = 0.0
        self.vario = 0.0
        self.vbat = 0.0
        self.mah = 0.0
        self.current = 0.0
        self.rssi = 0
        self.gps_fix = 0
        self.sats = 0
        self.gps_speed = 0.0
        self.gps_course = 0.0


class INAVSitl:
    """Процесс SITL + обмен по X-Plane UDP и MSP TCP."""

    def __init__(self, cfg: dict, engine=None) -> None:
        self.cfg = cfg
        self.engine = engine
        self.exe = resolve(str(cfg.get("sitl_exe", "sitl/inav_SITL.exe")))
        self.eeprom = resolve(str(cfg.get("eeprom", "sitl/eeprom.bin")))
        self.xp_port = int(cfg.get("xp_port", 49000))
        self.msp_port = int(cfg.get("msp_port", 5761))
        self.osd_port = int(cfg.get("osd_port", 5762))
        self.chanmap = str(cfg.get("chanmap", "M01-01,S01-02,S02-03"))
        self.auto_arm = bool(cfg.get("auto_arm", True))
        self.mode = str(cfg.get("start_mode", "AH")).upper()
        self.manual_armed = False
        self.telemetry = SitlTelemetry()
        self.osd = None            # OsdScreen, назначается извне
        self.error = ""
        self.connected = False
        self.ready = False           # MSP-связь есть, можно отпускать физику
        self.xplane_seen = False
        self.armed = False
        self.msp_hz = 0.0
        self.out_hz = 0.0
        self.osd_hz = 0.0
        self.udp_hz = 0.0

        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._msp: socket.socket | None = None
        self._address = None
        self._regs: dict[int, str] = {}
        self._state = {}
        self._rc = [1500] * 16
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._log = None
        self._last_rref = 0.0
        self._msp_win_t = time.monotonic()
        self._msp_win_n = 0
        self._out_win_n = 0
        self._osd_win_n = 0
        self._osd_win_t = time.monotonic()
        self._udp_win_n = 0
        self._udp_win_t = time.monotonic()
        self.udp_hz = 0.0
        self._arm_off_until = 0.0
        self._have_status = False
        self._calib_until = 0.0
        self._start_time = 0.0

    # ── запуск/остановка ────────────────────────────────────
    def start(self) -> bool:
        self.stop()
        self.error = ""
        self.connected = False
        self.ready = False
        self.xplane_seen = False
        self.armed = False
        self._have_status = False
        self._stop.clear()
        self._start_time = time.monotonic()
        if not self.exe.is_file():
            self.error = "Не найден inav_SITL.exe: %s" % self.exe
            return False
        if not self.eeprom.is_file():
            self.error = "Не найден eeprom: %s" % self.eeprom
            return False
        for port, name in ((self.msp_port, "MSP"), (self.osd_port, "OSD")):
            if self._port_in_use(port):
                # Порт мог остаться от только что остановленного SITL.
                for _ in range(8):
                    time.sleep(0.25)
                    if not self._port_in_use(port):
                        break
                else:
                    self.error = (
                        "TCP-порт %d (%s) уже занят. Закройте другой INAV SITL "
                        "(например, из SkyLock) или измените порты в настройках."
                        % (port, name))
                    return False
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("127.0.0.1", self.xp_port))
            self._sock.settimeout(0.05)
        except OSError as e:
            self.error = "UDP %d: %s" % (self.xp_port, e)
            self._close_sock()
            return False
        log_path = self.eeprom.parent / "sitl.log"
        try:
            self._log = open(log_path, "w", encoding="utf-8", errors="replace")
        except OSError:
            self._log = subprocess.DEVNULL
        cmd = [str(self.exe), "--sim=xp", "--simip=127.0.0.1",
               "--simport=%d" % self.xp_port,
               "--path=%s" % self.eeprom,
               "--chanmap=%s" % self.chanmap]
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=str(self.exe.parent),
                stdout=self._log, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW)
        except OSError as e:
            self.error = "Запуск SITL: %s" % e
            self._close_sock()
            return False
        if self.engine is not None:
            try:
                self.engine.enable_fc(True)
            except Exception:  # noqa: BLE001
                pass
        # INAV калибрует гироскоп на старте. Держим состояние
        # неподвижным до статуса «датчики готовы», но не дольше 20 с.
        self._calib_until = time.monotonic() + 20.0
        for fn in (self._udp_loop, self._msp_loop, self._osd_loop):
            th = threading.Thread(target=fn, name="inav-sitl", daemon=True)
            th.start()
            self._threads.append(th)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self.engine is not None:
            try:
                self.engine.set_fc_controls(0.0, 0.0, 0.0, 0.0, 0.0)
            except Exception:  # noqa: BLE001
                pass
        self._close_sock()
        self._close_msp()
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3.0)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        # Cygwin-процесс иногда оставляет дочерние процессы. Убиваем только
        # своё дерево, чтобы не тронуть чужой SITL (основной проект SkyLock).
        if os.name == "nt" and proc is not None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=5)
            except Exception:  # noqa: BLE001
                pass
        for th in self._threads:
            if th.is_alive():
                th.join(timeout=2.0)
        self._threads.clear()
        if self._log not in (None, subprocess.DEVNULL):
            try:
                self._log.close()
            except Exception:  # noqa: BLE001
                pass
        self._log = None
        self.connected = False
        self.ready = False
        self.armed = False

    def rebind(self, engine) -> None:
        self.engine = engine
        if engine is not None:
            try:
                engine.enable_fc(True)
                engine.set_fc_controls(0.0, 0.0, 0.0, 0.0, 0.0)
            except Exception:  # noqa: BLE001
                pass

    # ── данные из UI ────────────────────────────────────────
    def set_rc(self, ch_us) -> None:
        ch = [int(round(float(c))) for c in (ch_us or [])][:16]
        if len(ch) < 16:
            ch.extend([1500] * (16 - len(ch)))
        self._rc = ch

    def set_mode(self, mode: str) -> None:
        self.mode = "ACRO" if str(mode).upper().startswith("ACRO") else "AH"

    def set_auto_arm(self, on: bool) -> None:
        self.auto_arm = bool(on)
        if self.auto_arm:
            self.manual_armed = False

    def toggle_arm(self) -> None:
        if not self.ready:
            return
        self.auto_arm = False
        self.manual_armed = not self.manual_armed

    def update_state(self, st) -> None:
        """Снимок борта из UI-потока."""
        try:
            snapshot = {
                "roll": float(st.roll), "pitch": float(st.pitch),
                "yaw": float(st.yaw), "p": float(st.p), "q": float(st.q),
                "r": float(st.r), "v": float(st.v), "gs": float(st.gs),
                "alt": float(st.alt), "x": float(st.x), "y": float(st.y),
                "vz": float(st.vertical_speed),
            }
        except (AttributeError, TypeError, ValueError):
            return
        with self._lock:
            self._state = snapshot

    @property
    def status(self) -> str:
        if self.error:
            return self.error
        if self._proc is None:
            return "выключен"
        if not self.connected:
            return "запуск / нет связи"
        if not self.ready:
            return "калибровка"
        return "ARM" if self.armed else "связь"

    # ── X-Plane UDP ─────────────────────────────────────────
    def _udp_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
                self._address = addr
                self.xplane_seen = True
            except (socket.timeout, ConnectionResetError, OSError):
                data = None
            if data:
                try:
                    self._handle_xp(data)
                except Exception:  # noqa: BLE001
                    pass
            now = time.monotonic()
            if self._address is not None and now - self._last_rref >= 0.01:
                self._last_rref = now
                try:
                    sock.sendto(self._rref_packet(), self._address)
                    self._udp_win_n += 1
                except OSError:
                    pass
            if now - self._udp_win_t >= 1.0:
                dt = max(1e-6, now - self._udp_win_t)
                self.udp_hz = self._udp_win_n / dt
                self._udp_win_t = now
                self._udp_win_n = 0
        self._close_sock()

    def _handle_xp(self, data: bytes) -> None:
        if data[:4] == b"RREF":
            if len(data) < 13:
                return
            freq = struct.unpack_from("<I", data, 5)[0]
            rid = struct.unpack_from("<I", data, 9)[0]
            name = data[13:].split(b"\0")[0].decode("ascii", "replace")
            if freq > 0 and name:
                self._regs[rid] = name
            return
        if data[:4] == b"DREF":
            self.connected = True

    def _rref_packet(self) -> bytes:
        with self._lock:
            s = dict(self._state)
        alt_m = float(s.get("alt", 0.0) or 0.0)
        v = float(s.get("v", 0.0) or 0.0)
        gs = float(s.get("gs", v) or v)
        yaw = float(s.get("yaw", 0.0) or 0.0)
        alt_ft = alt_m / FT
        baro = 29.92 * max(0.05, 1.0 - 6.8756e-6 * alt_ft) ** 5.2559
        lat0 = float(self.cfg.get("origin_lat", 55.75))
        lon0 = float(self.cfg.get("origin_lon", 37.62))
        lat = lat0 + float(s.get("y", 0.0) or 0.0) / 111320.0
        lon = lon0 + float(s.get("x", 0.0) or 0.0) / (
            111320.0 * max(0.1, math.cos(math.radians(lat0))))
        rad = math.radians(yaw)
        vn = gs * math.cos(rad)
        ve = gs * math.sin(rad)
        hpath = math.degrees(math.atan2(ve, vn)) % 360.0 if gs > 0.5 else yaw % 360.0
        values = {
            "sim/flightmodel/position/latitude": lat,
            "sim/flightmodel/position/longitude": lon,
            "sim/flightmodel/position/elevation": alt_m,
            "sim/flightmodel/position/y_agl": max(0.0, alt_m),
            "sim/flightmodel/position/local_vx": vn,
            "sim/flightmodel/position/local_vy": ve,
            "sim/flightmodel/position/local_vz": -float(s.get("vz", 0.0) or 0.0),
            "sim/flightmodel/position/groundspeed": gs,
            "sim/flightmodel/position/true_airspeed": v,
            "sim/flightmodel/position/phi": float(s.get("roll", 0.0) or 0.0),
            # X-Plane-мост INAV сам инвертирует theta и Q/R (см.
            # target/SITL/sim/xplane.c: pitch_inav = -pitch,
            # gyro_y = -gyro_y). Поэтому в DREF отдаём минус, чтобы
            # внутри INAV углы и скорости совпали с физикой.
            "sim/flightmodel/position/theta": -float(s.get("pitch", 0.0) or 0.0),
            "sim/flightmodel/position/psi": yaw % 360.0,
            "sim/flightmodel/position/hpath": hpath,
            "sim/flightmodel/forces/g_axil": 0.0,
            "sim/flightmodel/forces/g_side": 0.0,
            "sim/flightmodel/forces/g_nrml": 1.0,
            "sim/flightmodel/position/P": math.degrees(float(s.get("p", 0.0) or 0.0)),
            "sim/flightmodel/position/Q": -math.degrees(float(s.get("q", 0.0) or 0.0)),
            "sim/flightmodel/position/R": -math.degrees(float(s.get("r", 0.0) or 0.0)),
            "sim/weather/barometer_current_inhg": baro,
            "sim/joystick/has_joystick": 0.0,
        }
        buf = bytearray(b"RREF\0")
        for rid, name in self._regs.items():
            buf += struct.pack("<if", rid, float(values.get(name, 0.0)))
        return bytes(buf)

    # ── MSP ─────────────────────────────────────────────────
    def _msp_loop(self) -> None:
        reader = MspReader()
        self._msp_win_t = time.monotonic()
        self._msp_win_n = 0
        self._out_win_n = 0
        n = 0
        while not self._stop.is_set():
            if self._msp is None:
                try:
                    self._msp = socket.create_connection(
                        ("127.0.0.1", self.msp_port), timeout=1.0)
                    self._msp.settimeout(0.02)
                    reader = MspReader()
                except OSError:
                    self._msp = None
                    time.sleep(0.5)
                    continue
            msp = self._msp
            if msp is None:
                continue
            n += 1
            self._msp_win_n += 1
            now = time.monotonic()
            if now - self._msp_win_t >= 1.0:
                dt = max(1e-6, now - self._msp_win_t)
                self.msp_hz = self._msp_win_n / dt
                self.out_hz = self._out_win_n / dt
                self._msp_win_t = now
                self._msp_win_n = 0
                self._out_win_n = 0
            ch = self._build_channels()
            try:
                msp.sendall(msp_v1(MSP_SET_RAW_RC,
                                   struct.pack("<" + "H" * 16, *ch)))
                msp.sendall(msp_v1(MSP_MOTOR))
                msp.sendall(msp_v1(MSP_SERVO))
                msp.sendall(msp_v1(MSP_STATUS))
                if n % 4 == 0:
                    msp.sendall(msp_v1(MSP_RC))
                    msp.sendall(msp_v1(MSP_ATTITUDE))
                    msp.sendall(msp_v1(MSP_ALTITUDE))
                    msp.sendall(msp_v1(MSP_ANALOG))
                    msp.sendall(msp_v1(MSP_RAW_GPS))
                data = msp.recv(8192)
                for cmd, payload in reader.feed(data):
                    self._on_msp(cmd, payload)
            except (socket.timeout, OSError):
                pass
            time.sleep(0.02)

    def _build_channels(self) -> list[int]:
        ch = list(self._rc)
        if self.auto_arm:
            armed = bool(self.telemetry.arming_flags & ARMING_FLAG_ARMED) or self.armed
            ready = (self._have_status
                     and not (self.telemetry.arming_flags & ARMING_FLAG_CALIBRATING))
            now = time.monotonic()
            if not armed:
                ch[2] = 1000
                if self.telemetry.arming_flags & ARMING_FLAG_ARM_SWITCH and now >= self._arm_off_until:
                    self._arm_off_until = now + 1.5
                if ready and now >= self._arm_off_until:
                    ch[4] = 1000
                else:
                    ch[4] = 2000
            else:
                ch[4] = 1000
        else:
            ch[4] = 1000 if (self.manual_armed and self.ready) else 2000
        ch[5] = 1500 if self.mode == "AH" else 1000
        return [int(clamp(round(float(c)), 800, 2200)) for c in ch]

    def _on_msp(self, cmd: int, payload: bytes) -> None:
        t = self.telemetry
        if cmd == MSP_MOTOR and len(payload) >= 16:
            self._out_win_n += 1
            t.motors = list(struct.unpack_from("<" + "H" * 8, payload, 0))
            self.connected = True
            self.armed = any(m > 1050 for m in t.motors[:2])
            self._apply_outputs()
        elif cmd == MSP_SERVO and len(payload) >= 4:
            n = min(18, len(payload) // 2)
            t.servos = list(struct.unpack_from("<" + "H" * n, payload, 0))
            self.connected = True
            self._apply_outputs()
        elif cmd == MSP_STATUS and len(payload) >= 15:
            t.arming_flags = struct.unpack_from("<H", payload, 13)[0]
            self._have_status = True
            self.connected = True
            if not (t.arming_flags & ARMING_FLAG_CALIBRATING):
                self.ready = True
        elif cmd == MSP_RC and len(payload) >= 8:
            n = min(16, len(payload) // 2)
            t.rc = list(struct.unpack_from("<" + "H" * n, payload, 0))
        elif cmd == MSP_ATTITUDE and len(payload) >= 6:
            r, p, y = struct.unpack_from("<hhh", payload, 0)
            # INAV: roll/pitch — decidegrees, yaw уже в градусах.
            t.attitude = (r / 10.0, p / 10.0, float(y))
        elif cmd == MSP_ALTITUDE and len(payload) >= 6:
            alt, vario = struct.unpack_from("<iH", payload, 0)
            t.altitude = alt / 100.0
            t.vario = struct.unpack("<h", struct.pack("<H", vario))[0] / 100.0
        elif cmd == MSP_ANALOG and len(payload) >= 7:
            t.vbat = payload[0] / 10.0
            t.mah = struct.unpack_from("<H", payload, 1)[0]
            t.rssi = struct.unpack_from("<H", payload, 3)[0]
            t.current = struct.unpack_from("<h", payload, 5)[0] / 100.0
        elif cmd == MSP_RAW_GPS and len(payload) >= 14:
            t.gps_fix = payload[0]
            t.sats = payload[1]
            # lat/lon u32 (2..9), alt u16 (10), speed u16 (12), course u16 (14)
            if len(payload) >= 16:
                t.gps_speed = struct.unpack_from("<H", payload, 12)[0] / 100.0
                t.gps_course = struct.unpack_from("<H", payload, 14)[0] / 10.0

    def _apply_outputs(self) -> None:
        if self.engine is None:
            return
        t = self.telemetry
        servo = t.servos
        motor = t.motors

        def nz(pwm: float) -> float:
            return clamp((1500.0 - float(pwm)) / 500.0, -1.0, 1.0)

        s1 = nz(servo[1]) if len(servo) > 1 else 0.0
        s2 = nz(servo[2]) if len(servo) > 2 else 0.0
        airframe = str(getattr(self.engine, "_airframe", "flying_wing"))
        if airframe == "plane":
            aileron = s1
            elevator = s2
            rudder = nz(servo[3]) if len(servo) > 3 else 0.0
        else:
            # Летающее крыло: S1 = -R + P, S2 = -R - P (с реверсом INAV).
            aileron = clamp(-(s1 + s2) * 0.5, -1.0, 1.0)
            elevator = clamp((s1 - s2) * 0.5, -1.0, 1.0)
            rudder = 0.0
        if self.cfg.get("invert_aileron"):
            aileron = -aileron
        if self.cfg.get("invert_elevator"):
            elevator = -elevator
        if self.cfg.get("invert_rudder"):
            rudder = -rudder
        # INAV: положительный тангаж — нос вверх; JSBSim: положительный
        # elevator — нос вниз. Поэтому знак инвертируем всегда (это не
        # «настройка пульта», а соглашение осей).
        elevator = -elevator

        m0 = clamp((motor[0] - 1000.0) / 1000.0, 0.0, 1.0)
        m1 = clamp((motor[1] - 1000.0) / 1000.0, 0.0, 1.0)
        if m1 <= 0.01 and m0 > 0.01:
            yaw = clamp((self._rc[3] - 1500.0) / 500.0, -1.0, 1.0)
            d = 0.5 * yaw
            tl = clamp(m0 + d, 0.0, 1.0)
            tr = clamp(m0 - d, 0.0, 1.0)
        else:
            tl, tr = m1, m0
        try:
            self.engine.set_fc_controls(aileron, elevator, rudder, tl, tr)
        except Exception:  # noqa: BLE001
            pass

    # ── OSD (MSP DisplayPort) ───────────────────────────────
    def _osd_loop(self) -> None:
        reader = MspReader()
        self._osd_win_t = time.monotonic()
        self._osd_win_n = 0
        while not self._stop.is_set():
            try:
                sock = socket.create_connection(("127.0.0.1", self.osd_port),
                                                timeout=1.0)
                sock.settimeout(0.05)
                reader = MspReader()
            except OSError:
                time.sleep(0.5)
                continue
            last_hb = 0.0
            try:
                while not self._stop.is_set():
                    now = time.monotonic()
                    if now - last_hb >= 0.2:
                        last_hb = now
                        try:
                            sock.sendall(msp_v1(1))  # MSP_API_VERSION
                        except OSError:
                            break
                    try:
                        data = sock.recv(8192)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    for cmd, payload in reader.feed(data):
                        if cmd != MSP_DISPLAYPORT or not payload:
                            continue
                        self._osd_win_n += 1
                        if self.osd is not None:
                            self.osd.apply(payload)
                    if self.osd is not None:
                        self.osd.touch()
                    now2 = time.monotonic()
                    if now2 - self._osd_win_t >= 1.0:
                        dt = max(1e-6, now2 - self._osd_win_t)
                        self.osd_hz = self._osd_win_n / dt
                        self._osd_win_t = now2
                        self._osd_win_n = 0
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            time.sleep(0.3)

    # ── служебное ───────────────────────────────────────────
    @staticmethod
    def _port_in_use(port: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.15)
        try:
            s.connect(("127.0.0.1", int(port)))
            return True
        except OSError:
            return False
        finally:
            try:
                s.close()
            except OSError:
                pass

    def _close_sock(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _close_msp(self) -> None:
        msp = self._msp
        self._msp = None
        if msp is not None:
            try:
                msp.close()
            except OSError:
                pass


__all__ = ["INAVSitl", "SitlTelemetry", "MspReader", "msp_v1",
           "MSP_DISPLAYPORT", "ARMING_FLAG_ARMED"]
