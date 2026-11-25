"""Настройки симулятора: пути, порты, раскладка пульта, камера.

Файл ``config.json`` лежит рядом с программой и не перезаписывается
обновлением. Все ключи необязательны: недостающие берутся из DEFAULTS.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


CONFIG_PATH = app_dir() / "config.json"

DEFAULTS: dict = {
    # INAV SITL
    "sitl_exe": "sitl/inav_SITL.exe",
    "eeprom": "sitl/eeprom.bin",
    "xp_port": 49100,
    "msp_port": 5761,
    "osd_port": 5762,
    "chanmap": "M01-01,S01-02,S02-03",
    "auto_arm": True,
    "start_mode": "AH",            # AH | ACRO
    "start_in_air": True,
    "start_alt_m": 120.0,
    "start_speed_m_s": 24.0,
    # Пульт: индексы осей 0..7, -1 = не назначено
    "axis_roll": 0,
    "axis_pitch": 1,
    "axis_throttle": 2,
    "axis_yaw": 3,
    "invert_roll": False,
    "invert_pitch": True,
    "invert_throttle": False,
    "invert_yaw": False,
    "deadband": 0.03,
    "throttle_mode": "stick",      # stick (0..1) | center (центр = 0)
    "arm_button": -1,              # кнопка HID (0..23), -1 = авто/клавиша
    "mode_button": -1,
    "joystick_name": "",
    # Камера/вид
    "camera_fov_deg": 95.0,
    "chase_view": False,
    "osd_scale": 0.85,
    "osd_enabled": True,
    # Борт
    "preset_uid": "builtin:flywing",
    # Мир
    "origin_lat": 55.75,
    "origin_lon": 37.62,
    # Обновления
    "update_check": True,
    "skipped_version": "",
    "github_repo": "Sledocheck/INAV_Sim",
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = _merge(cfg, data)
        except (OSError, ValueError):
            pass
    return cfg


def save(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else app_dir() / p


__all__ = ["app_dir", "CONFIG_PATH", "DEFAULTS", "load", "save", "resolve"]
