"""Главное окно и диалог настроек."""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSlider, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import config
from .app import SimController, SimView
from .joystick import AXIS_NAMES, list_devices
from .presets import list_presets
from .updater import current_version, maybe_check_updates


def _axis_combo(value: int) -> QComboBox:
    box = QComboBox()
    box.addItem("не назначено", -1)
    for i, name in enumerate(AXIS_NAMES):
        box.addItem(name, i)
    idx = max(0, box.findData(int(value)))
    box.setCurrentIndex(idx)
    return box


class AxisPreview(QWidget):
    """Живые полоски осей и кнопок выбранного пульта."""

    def __init__(self, controller: SimController, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.setMinimumHeight(150)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(50)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(16, 22, 30))
        state = self.c.joy.read() if self.c.joy.alive else None
        axes = list(state.axes) if state is not None else []
        font = QFont()
        font.setPointSizeF(8.5)
        p.setFont(font)
        rows = len(AXIS_NAMES)
        bar_h = max(6, int((h - 24) / rows) - 3)
        for i, name in enumerate(AXIS_NAMES):
            y = 6 + i * (bar_h + 4)
            p.setPen(QColor(190, 205, 220))
            p.drawText(2, y + bar_h - 1, name)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(44, 56, 70))
            p.drawRect(30, y, w - 40, bar_h)
            v = float(axes[i]) if i < len(axes) else 0.5
            p.setBrush(QColor(90, 200, 255) if i < len(axes) else QColor(70, 80, 90))
            p.drawRect(30, y, int((w - 40) * v), bar_h)
        if state is not None:
            p.setPen(QColor(150, 210, 230))
            p.drawText(2, h - 4, "buttons: 0x%06X" % int(state.buttons))


class SettingsDialog(QDialog):
    def __init__(self, controller: SimController, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        self.cfg = copy.deepcopy(controller.cfg)
        self.setWindowTitle("Настройки INAV Sim")
        self.setMinimumWidth(680)
        self._orig_device = controller.joy.device
        self._devices = list_devices()

        tabs = QTabWidget()
        tabs.addTab(self._tab_joystick(), "Пульт")
        tabs.addTab(self._tab_inav(), "INAV")
        tabs.addTab(self._tab_view(), "Вид")
        tabs.addTab(self._tab_aircraft(), "Борт")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        root = QVBoxLayout(self)
        root.addWidget(tabs)
        root.addWidget(buttons)

    # ── вкладки ─────────────────────────────────────────────
    def _tab_joystick(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.device = QComboBox()
        self.device.addItem("— клавиатура —", "")
        for d in self._devices:
            self.device.addItem("%s (%d осей, %d кнопок)"
                                % (d.name, d.num_axes, d.num_buttons),
                                d.name)
        cur_name = str(self.cfg.get("joystick_name", ""))
        if not cur_name and self.c.joy.device is not None:
            cur_name = self.c.joy.device.name
        idx = self.device.findData(cur_name)
        self.device.setCurrentIndex(max(0, idx))
        self.device.currentIndexChanged.connect(self._device_changed)
        form.addRow("Устройство", self.device)

        self.ax_roll = _axis_combo(self.cfg.get("axis_roll", 0))
        self.ax_pitch = _axis_combo(self.cfg.get("axis_pitch", 1))
        self.ax_thr = _axis_combo(self.cfg.get("axis_throttle", 2))
        self.ax_yaw = _axis_combo(self.cfg.get("axis_yaw", 3))
        for label, box in (("Roll", self.ax_roll), ("Pitch", self.ax_pitch),
                           ("Throttle", self.ax_thr), ("Yaw", self.ax_yaw)):
            form.addRow(label, box)
        self.inv_roll = QCheckBox("инвертировать")
        self.inv_pitch = QCheckBox("инвертировать")
        self.inv_thr = QCheckBox("инвертировать")
        self.inv_yaw = QCheckBox("инвертировать")
        self.inv_roll.setChecked(bool(self.cfg.get("invert_roll")))
        self.inv_pitch.setChecked(bool(self.cfg.get("invert_pitch")))
        self.inv_thr.setChecked(bool(self.cfg.get("invert_throttle")))
        self.inv_yaw.setChecked(bool(self.cfg.get("invert_yaw")))
        form.addRow("", self.inv_roll)
        form.addRow("", self.inv_pitch)
        form.addRow("", self.inv_thr)
        form.addRow("", self.inv_yaw)

        self.thr_mode = QComboBox()
        self.thr_mode.addItem("стик 0..100%", "stick")
        self.thr_mode.addItem("центр = 0", "center")
        self.thr_mode.setCurrentIndex(max(
            0, self.thr_mode.findData(str(self.cfg.get("throttle_mode", "stick")))))
        form.addRow("Газ", self.thr_mode)
        self.deadband = QDoubleSpinBox()
        self.deadband.setRange(0.0, 0.25)
        self.deadband.setSingleStep(0.01)
        self.deadband.setValue(float(self.cfg.get("deadband", 0.03)))
        form.addRow("Мёртвая зона", self.deadband)
        self.arm_btn = QSpinBox()
        self.arm_btn.setRange(-1, 23)
        self.arm_btn.setSpecialValueText("клавиша Space")
        self.arm_btn.setValue(int(self.cfg.get("arm_button", -1)))
        form.addRow("Кнопка ARM", self.arm_btn)
        self.mode_btn = QSpinBox()
        self.mode_btn.setRange(-1, 23)
        self.mode_btn.setSpecialValueText("клавиша M")
        self.mode_btn.setValue(int(self.cfg.get("mode_button", -1)))
        form.addRow("Кнопка AH/ACRO", self.mode_btn)
        lay.addLayout(form)
        lay.addWidget(QLabel("Живые оси (назначьте нужные в списках выше):"))
        lay.addWidget(AxisPreview(self.c), 1)
        reset = QPushButton("Пульт по умолчанию")
        reset.clicked.connect(self._reset_joystick)
        lay.addWidget(reset)
        return w

    def _tab_inav(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.sitl_exe = QLineEdit(str(self.cfg.get("sitl_exe", "")))
        form.addRow("inav_SITL.exe", self._path_row(self.sitl_exe, False))
        self.eeprom = QLineEdit(str(self.cfg.get("eeprom", "")))
        form.addRow("EEPROM", self._path_row(self.eeprom, False))
        self.xp_port = QSpinBox()
        self.xp_port.setRange(1024, 65535)
        self.xp_port.setValue(int(self.cfg.get("xp_port", 49000)))
        form.addRow("X-Plane UDP порт", self.xp_port)
        self.msp_port = QSpinBox()
        self.msp_port.setRange(1024, 65535)
        self.msp_port.setValue(int(self.cfg.get("msp_port", 5761)))
        form.addRow("MSP TCP порт", self.msp_port)
        self.osd_port = QSpinBox()
        self.osd_port.setRange(1024, 65535)
        self.osd_port.setValue(int(self.cfg.get("osd_port", 5762)))
        form.addRow("OSD TCP порт", self.osd_port)
        self.auto_arm = QCheckBox("взводить автоматически после калибровки")
        self.auto_arm.setChecked(bool(self.cfg.get("auto_arm", True)))
        form.addRow("", self.auto_arm)
        self.start_mode = QComboBox()
        self.start_mode.addItem("AH (углы)", "AH")
        self.start_mode.addItem("ACRO (угловые скорости)", "ACRO")
        self.start_mode.setCurrentIndex(max(
            0, self.start_mode.findData(str(self.cfg.get("start_mode", "AH")))))
        form.addRow("Режим INAV", self.start_mode)
        self.in_air = QCheckBox("старт в воздухе (без разбега)")
        self.in_air.setChecked(bool(self.cfg.get("start_in_air", True)))
        form.addRow("", self.in_air)
        self.alt = QDoubleSpinBox()
        self.alt.setRange(5.0, 3000.0)
        self.alt.setSuffix(" м")
        self.alt.setValue(float(self.cfg.get("start_alt_m", 120.0)))
        form.addRow("Стартовая высота", self.alt)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(8.0, 90.0)
        self.speed.setSuffix(" м/с")
        self.speed.setValue(float(self.cfg.get("start_speed_m_s", 24.0)))
        form.addRow("Стартовая скорость", self.speed)
        hint = QLabel("SITL и EEPROM лежат в папке sitl/. EEPROM уже настроен:\n"
                      "OSD включён, UART3 = MSP DisplayPort.")
        hint.setWordWrap(True)
        form.addRow(hint)
        return w

    def _tab_view(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        row = QHBoxLayout()
        self.fov = QSlider(Qt.Horizontal)
        self.fov.setRange(50, 140)
        self.fov.setValue(int(self.cfg.get("camera_fov_deg", 95)))
        self.fov_label = QLabel("%d°" % self.fov.value())
        self.fov.valueChanged.connect(
            lambda v: self.fov_label.setText("%d°" % v))
        row.addWidget(self.fov, 1)
        row.addWidget(self.fov_label)
        form.addRow("Угол обзора", row)
        self.osd_on = QCheckBox("показывать OSD из INAV")
        self.osd_on.setChecked(bool(self.cfg.get("osd_enabled", True)))
        form.addRow("", self.osd_on)
        self.osd_scale = QDoubleSpinBox()
        self.osd_scale.setRange(0.5, 2.0)
        self.osd_scale.setSingleStep(0.05)
        self.osd_scale.setValue(float(self.cfg.get("osd_scale", 1.0)))
        form.addRow("Масштаб OSD", self.osd_scale)
        self.chase = QCheckBox("вид снаружи (F2)")
        self.chase.setChecked(bool(self.cfg.get("chase_view", False)))
        form.addRow("", self.chase)
        self.update_check = QCheckBox("проверять обновления при запуске")
        self.update_check.setChecked(bool(self.cfg.get("update_check", True)))
        form.addRow("", self.update_check)
        return w

    def _tab_aircraft(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.preset = QComboBox()
        presets = list_presets()
        current = str(self.cfg.get("preset_uid", ""))
        for p in presets:
            self.preset.addItem(p.name, p.uid)
        idx = self.preset.findData(current)
        self.preset.setCurrentIndex(max(0, idx))
        self.preset.currentIndexChanged.connect(self._preset_changed)
        form.addRow("Пресет борта", self.preset)
        self.preset_info = QLabel()
        self.preset_info.setWordWrap(True)
        form.addRow(self.preset_info)
        lay.addLayout(form)
        lay.addWidget(QLabel(
            "Физика — JSBSim 6-DoF. Параметры пресета: масса, площадь крыла,\n"
            "CLmax/CD0, эффективность рулей, тяга. Свои JSON-пресеты можно\n"
            "положить в userdata/aircraft/."))
        lay.addStretch(1)
        self._preset_changed()
        return w

    # ── вспомогательное ─────────────────────────────────────
    def _path_row(self, edit: QLineEdit, folder: bool):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(edit, 1)
        btn = QPushButton("…")
        btn.setMaximumWidth(32)

        def browse():
            if folder:
                path = QFileDialog.getExistingDirectory(self, "Папка",
                                                        edit.text())
            else:
                path, _ = QFileDialog.getOpenFileName(self, "Файл", edit.text())
            if path:
                edit.setText(path)

        btn.clicked.connect(browse)
        lay.addWidget(btn)
        return box

    def _device_changed(self) -> None:
        name = str(self.device.currentData() or "")
        dev = None
        for d in self._devices:
            if d.name == name:
                dev = d
                break
        if dev is None:
            self.c.joy.close()
        else:
            self.c.joy.open(dev)

    def _reset_joystick(self) -> None:
        self.ax_roll.setCurrentIndex(max(0, self.ax_roll.findData(0)))
        self.ax_pitch.setCurrentIndex(max(0, self.ax_pitch.findData(1)))
        self.ax_thr.setCurrentIndex(max(0, self.ax_thr.findData(2)))
        self.ax_yaw.setCurrentIndex(max(0, self.ax_yaw.findData(3)))
        self.inv_roll.setChecked(False)
        self.inv_pitch.setChecked(True)
        self.inv_thr.setChecked(False)
        self.inv_yaw.setChecked(False)
        self.thr_mode.setCurrentIndex(max(0, self.thr_mode.findData("stick")))
        self.arm_btn.setValue(-1)
        self.mode_btn.setValue(-1)

    def _preset_changed(self) -> None:
        uid = str(self.preset.currentData() or "")
        from .presets import find_preset
        p = find_preset(uid)
        if p is None:
            self.preset_info.setText("")
            return
        self.preset_info.setText(
            "%s\nМасса %.1f кг · сваливание %.0f км/ч · макс %.0f км/ч\n"
            "Схема: %s, моторов %d"
            % (p.note or "", p.mass_kg, p.v_stall_m_s * 3.6,
               p.v_max_m_s * 3.6,
               "летающее крыло" if p.airframe == "flying_wing" else "самолёт",
               p.motor_count))

    # ── принятие ────────────────────────────────────────────
    def _on_ok(self) -> None:
        cfg = self.cfg
        cfg["joystick_name"] = str(self.device.currentData() or "")
        cfg["axis_roll"] = int(self.ax_roll.currentData())
        cfg["axis_pitch"] = int(self.ax_pitch.currentData())
        cfg["axis_throttle"] = int(self.ax_thr.currentData())
        cfg["axis_yaw"] = int(self.ax_yaw.currentData())
        cfg["invert_roll"] = self.inv_roll.isChecked()
        cfg["invert_pitch"] = self.inv_pitch.isChecked()
        cfg["invert_throttle"] = self.inv_thr.isChecked()
        cfg["invert_yaw"] = self.inv_yaw.isChecked()
        cfg["throttle_mode"] = str(self.thr_mode.currentData())
        cfg["deadband"] = float(self.deadband.value())
        cfg["arm_button"] = int(self.arm_btn.value())
        cfg["mode_button"] = int(self.mode_btn.value())
        cfg["sitl_exe"] = self.sitl_exe.text().strip()
        cfg["eeprom"] = self.eeprom.text().strip()
        cfg["xp_port"] = int(self.xp_port.value())
        cfg["msp_port"] = int(self.msp_port.value())
        cfg["osd_port"] = int(self.osd_port.value())
        cfg["auto_arm"] = self.auto_arm.isChecked()
        cfg["start_mode"] = str(self.start_mode.currentData())
        cfg["start_in_air"] = self.in_air.isChecked()
        cfg["start_alt_m"] = float(self.alt.value())
        cfg["start_speed_m_s"] = float(self.speed.value())
        cfg["camera_fov_deg"] = float(self.fov.value())
        cfg["osd_enabled"] = self.osd_on.isChecked()
        cfg["osd_scale"] = float(self.osd_scale.value())
        cfg["chase_view"] = self.chase.isChecked()
        cfg["update_check"] = self.update_check.isChecked()
        cfg["preset_uid"] = str(self.preset.currentData() or "")
        self.accept()

    def reject(self) -> None:
        if self._orig_device is None:
            self.c.joy.close()
        elif (self.c.joy.device is None
              or self.c.joy.device.path != self._orig_device.path):
            self.c.joy.open(self._orig_device)
        super().reject()


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = cfg
        self.controller = SimController(cfg)
        self.view = SimView(self.controller, self)
        self.setCentralWidget(self.view)
        self.setWindowTitle("INAV Sim — FPV + INAV SITL")
        self.resize(1280, 800)
        self._build_menu()
        self.status_sitl = QLabel("INAV: выключен")
        self.status_stats = QLabel("")
        self.statusBar().addWidget(self.status_sitl, 1)
        self.statusBar().addPermanentWidget(self.status_stats)
        self.controller.status_changed.connect(self._on_status)
        self.controller.log.connect(self._log)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _build_menu(self) -> None:
        bar = self.menuBar()

        m_file = bar.addMenu("Файл")
        act_settings = QAction("Настройки…", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self._settings)
        m_file.addAction(act_settings)
        m_file.addSeparator()
        act_quit = QAction("Выход", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_fly = bar.addMenu("Полёт")
        self.act_start = QAction("Старт", self)
        self.act_start.setShortcut(QKeySequence("F5"))
        self.act_start.triggered.connect(self._start)
        m_fly.addAction(self.act_start)
        self.act_pause = QAction("Пауза", self)
        self.act_pause.setShortcut(QKeySequence("P"))
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(self._pause)
        m_fly.addAction(self.act_pause)
        act_reset = QAction("Сброс", self)
        act_reset.setShortcut(QKeySequence("R"))
        act_reset.triggered.connect(self.controller.reset)
        m_fly.addAction(act_reset)
        act_restart = QAction("Перезапустить INAV", self)
        act_restart.setShortcut(QKeySequence("F9"))
        act_restart.triggered.connect(self.controller.restart_sitl)
        m_fly.addAction(act_restart)
        m_fly.addSeparator()
        act_arm = QAction("ARM / снять", self)
        act_arm.setShortcut(QKeySequence("Space"))
        act_arm.triggered.connect(self.controller.toggle_arm)
        m_fly.addAction(act_arm)
        act_mode = QAction("AH / ACRO", self)
        act_mode.setShortcut(QKeySequence("M"))
        act_mode.triggered.connect(self.controller.toggle_mode)
        m_fly.addAction(act_mode)
        act_chase = QAction("Вид снаружи", self)
        act_chase.setShortcut(QKeySequence("F2"))
        act_chase.triggered.connect(self.controller.toggle_chase)
        m_fly.addAction(act_chase)

        m_upd = bar.addMenu("Обновление")
        act_check = QAction("Проверить обновления", self)
        act_check.triggered.connect(self._check_updates)
        m_upd.addAction(act_check)

        m_help = bar.addMenu("Справка")
        act_help = QAction("Управление", self)
        act_help.setShortcut(QKeySequence("F1"))
        act_help.triggered.connect(self._help)
        m_help.addAction(act_help)
        act_about = QAction("О программе", self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    # ── действия ────────────────────────────────────────────
    def _start(self) -> None:
        if self.controller.started:
            return
        self.statusBar().showMessage("Запуск физики и INAV SITL…")
        if not self.controller.start():
            QMessageBox.critical(
                self, "Не удалось запустить",
                (self.controller.error or "Неизвестная ошибка")
                + "\n\nЕсли не установлен JSBSim — выполните setup.bat,\n"
                  "затем запустите run.bat.")
            return
        self.statusBar().showMessage("INAV SITL запущен", 4000)
        self.view.setFocus()

    def _pause(self) -> None:
        self.controller.set_paused(self.act_pause.isChecked())

    def _settings(self) -> None:
        dlg = SettingsDialog(self.controller, self)
        if dlg.exec() == QDialog.Accepted:
            self.controller.apply_settings(dlg.cfg)
            self.cfg.clear()
            self.cfg.update(self.controller.cfg)
            config.save(self.cfg)

    def _check_updates(self) -> None:
        maybe_check_updates(self, self.cfg, interactive=True)
        config.save(self.cfg)

    def _help(self) -> None:
        QMessageBox.information(self, "Управление", (
            "Пульт: оси назначаются в «Файл → Настройки → Пульт».\n"
            "Клавиатура (если пульта нет):\n"
            "  ← →  крен,  ↑ ↓  тангаж,  A D  рыскание,\n"
            "  W S  газ,  Space  ARM,  M  AH/ACRO.\n\n"
            "Полезное:\n"
            "  F5 — старт, P — пауза, R — сброс,\n"
            "  F9 — перезапустить INAV, F2 — вид снаружи.\n\n"
            "OSD и все параметры приходят из INAV по MSP.\n"
            "Физика — JSBSim, борт — пресет из настроек."))

    def _about(self) -> None:
        QMessageBox.about(self, "О программе", (
            "<b>INAV Sim</b> %s<br><br>"
            "Отдельный FPV-симулятор: JSBSim 6-DoF + INAV SITL.<br>"
            "OSD рисует сама прошивка INAV (MSP DisplayPort).<br><br>"
            "Репозиторий: %s" % (current_version(),
                                 self.cfg.get("github_repo", ""))))

    def _on_status(self, text: str) -> None:
        self.status_sitl.setText("INAV: " + text)

    def _log(self, text: str) -> None:
        if text:
            self.statusBar().showMessage(text, 4000)

    def _tick(self) -> None:
        self.controller.tick()
        self.view.update()
        c = self.controller
        s = c.aircraft
        self.status_stats.setText(
            "FPS %2.0f · %.0f м/с · %.0f м · курс %03.0f° · MSP %2.0f Гц · OSD %2.0f Гц"
            % (c.fps, s.v, s.alt, s.yaw % 360.0,
               c.sitl.msp_hz if c.sitl else 0.0,
               c.sitl.osd_hz if c.sitl else 0.0))

    def closeEvent(self, ev) -> None:
        try:
            self.controller.stop()
        finally:
            super().closeEvent(ev)


__all__ = ["MainWindow", "SettingsDialog"]
