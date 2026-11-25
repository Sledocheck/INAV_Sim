"""Пресеты самолётов: параметры, хранение и применение к физике.

Пресет описывает не только «паспорт» (масса, площадь, скорости), но и
коэффициенты, которые JSBSim реально использует для 6-DoF:
производные подъёмной силы/моментов, демпфирование, эффективность рулей,
тягу и инерцию. Встроенные пресеты неизменяемы; пользовательские лежат
JSON-файлами в ``userdata/aircraft``.
"""
from __future__ import annotations

import json
import math
import os
import sys
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

VERSION = 1
BUILTIN_PREFIX = "builtin:"


def preset_dir() -> Path:
    env = os.environ.get("INAV_SIM_PRESETS")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "userdata" / "aircraft"
    return here.parents[1] / "userdata" / "aircraft"


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class AircraftPreset:
    # Идентификация
    uid: str = ""
    name: str = "Новый самолёт"
    note: str = ""
    # Геометрия/схема. airframe: "plane" — элероны+РВ+РН,
    # "flying_wing" — элевоны и (обычно) два мотора с разным тягами.
    airframe: str = "plane"
    motor_count: int = 1
    motor_arm_m: float = 0.0       # 0 — из размаха
    has_rudder: bool = True
    # Паспорт/геометрия
    mass_kg: float = 4.0
    wing_area_m2: float = 0.0       # 0 = из массы, Vstall и CLmax
    wing_span_m: float = 0.0        # 0 = из площади и удлинения
    v_stall_m_s: float = 19.44      # 70 км/ч
    v_max_m_s: float = 69.4         # 250 км/ч
    cl_max: float = 1.30
    cd0: float = 0.022
    oswald_e: float = 0.85
    # Инерция (0 = оценить по массе/размаху)
    ixx_kg_m2: float = 0.0
    iyy_kg_m2: float = 0.0
    izz_kg_m2: float = 0.0
    ixz_kg_m2: float = 0.0
    # Аэродинамика: подъёмная сила и сопротивление
    cl0: float = 0.22               # CL при alpha=0
    cl_alpha: float = 5.0           # 1/рад
    alpha_stall_deg: float = 13.8
    cl_de: float = 0.10
    cl_q: float = 2.5
    cl_adot: float = 1.0
    cd_beta: float = 0.06
    cd_de: float = 0.020
    # Момент тангажа
    cm0: float = 0.0
    cm_alpha: float = -0.60         # 1/рад, отрицательный = статически устойчив
    cm_q: float = -10.0
    cm_adot: float = -4.0
    cm_de: float = -0.40
    # Момент крена
    cl_b: float = -0.06
    cl_p: float = -0.90
    cl_r: float = 0.08
    cl_da: float = 0.14
    cl_dr: float = 0.01
    # Момент рыскания / боковая сила
    cn_b: float = 0.09
    cn_r: float = -0.12
    cn_dr: float = -0.09
    cn_da: float = -0.015
    cy_b: float = -0.55
    # Тяга (0 = рассчитать из Vmax/CD0/площади)
    power_w: float = 0.0
    static_thrust_n: float = 0.0
    efficiency: float = 0.75
    cruise_throttle_pct: float = 40.0
    cruise_speed_m_s: float = 0.0   # 0 = из Vstall
    # Рули и контуры
    aileron_max_deg: float = 25.2
    elevator_max_deg: float = 25.2
    rudder_max_deg: float = 25.2
    roll_rate_max: float = 180.0
    pitch_rate_max: float = 90.0
    yaw_rate_max: float = 40.0
    tau_roll: float = 0.12
    tau_pitch: float = 0.15
    tau_yaw: float = 0.20
    # Ограничения контура (не физика, а «паспорт» поведения)
    max_bank_deg: float = 75.0
    max_pitch_deg: float = 45.0
    angle_gain_roll: float = 4.0
    angle_gain_pitch: float = 4.0
    yaw_ah_rate_max: float = 15.0
    ah_stick_deadband: float = 0.04
    stall_authority_min: float = 0.15
    climb_max_m_s: float = 10.0
    descend_max_m_s: float = 10.0

    # ── свойства ────────────────────────────────────────────
    @property
    def builtin(self) -> bool:
        return self.uid.startswith(BUILTIN_PREFIX)

    @property
    def alpha_stall_rad(self) -> float:
        return math.radians(float(self.alpha_stall_deg))

    def validate(self) -> list[str]:
        """Проверить только то, без чего ядро не соберётся.

        Числовые пределы (углы, скорости, коэффициенты) больше не
        ограничиваются: их можно задавать любыми и подбирать по реакции
        модели. Оставлены структурные проверки и защита от деления на ноль.
        """
        errs: list[str] = []
        if not self.name.strip():
            errs.append("Имя пресета пустое")
        if self.airframe not in ("plane", "flying_wing"):
            errs.append("Схема борта должна быть plane или flying_wing")
        if not 1 <= int(self.motor_count) <= 4:
            errs.append("Количество моторов должно быть от 1 до 4")
        if float(self.cm_de) == 0.0:
            errs.append("Эффективность руля высоты не может быть нулевой")
        if float(self.elevator_max_deg) == 0.0:
            errs.append("Максимальный угол руля высоты не может быть нулевым")
        for key in ("aileron_max_deg", "rudder_max_deg"):
            if float(getattr(self, key)) < 0.0:
                errs.append("Максимальный угол поверхностей не может быть "
                            "отрицательным")
                break
        return errs

    # ── сериализация ────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("builtin", None)
        d["version"] = VERSION
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AircraftPreset":
        allowed = set(cls.__dataclass_fields__.keys())
        kwargs = {k: v for k, v in data.items()
                  if k in allowed and k != "builtin"}
        p = cls(**kwargs)
        p.uid = str(data.get("uid") or p.uid or "")
        p.name = str(p.name or "Без имени")
        return p

    # ── применение ─────────────────────────────────────────
    def apply_to_cfg(self, cfg) -> None:
        """Перенести пресет в SimConfig (базовые поля + объект для aero)."""
        cfg.airframe = str(self.airframe)
        cfg.motor_count = int(self.motor_count)
        cfg.motor_arm_m = float(self.motor_arm_m)
        cfg.has_rudder = bool(self.has_rudder)
        cfg.mass_kg = float(self.mass_kg)
        cfg.wing_area_m2 = float(self.wing_area_m2)
        cfg.wing_span_m = float(self.wing_span_m)
        cfg.cl_max = float(self.cl_max)
        cfg.cd0 = float(self.cd0)
        cfg.v_min = float(self.v_stall_m_s)
        cfg.v_max = float(self.v_max_m_s)
        cfg.roll_rate_max = float(self.roll_rate_max)
        cfg.pitch_rate_max = float(self.pitch_rate_max)
        cfg.yaw_rate_max = float(self.yaw_rate_max)
        cfg.tau_roll = float(self.tau_roll)
        cfg.tau_pitch = float(self.tau_pitch)
        cfg.tau_yaw = float(self.tau_yaw)
        cfg.max_bank_deg = float(self.max_bank_deg)
        cfg.max_pitch_deg = float(self.max_pitch_deg)
        cfg.angle_gain_roll = float(self.angle_gain_roll)
        cfg.angle_gain_pitch = float(self.angle_gain_pitch)
        cfg.yaw_ah_rate_max = float(self.yaw_ah_rate_max)
        cfg.ah_stick_deadband = float(self.ah_stick_deadband)
        cfg.stall_authority_min = float(self.stall_authority_min)
        cfg.climb_max_m_s = float(self.climb_max_m_s)
        cfg.descend_max_m_s = float(self.descend_max_m_s)
        cfg.preset = self

    @classmethod
    def from_cfg(cls, cfg, name: str = "Из текущей модели") -> "AircraftPreset":
        p = cls(name=name)
        p.mass_kg = float(getattr(cfg, "mass_kg", p.mass_kg))
        p.wing_area_m2 = float(getattr(cfg, "wing_area_m2", 0.0))
        p.wing_span_m = float(getattr(cfg, "wing_span_m", 0.0))
        p.cl_max = float(getattr(cfg, "cl_max", p.cl_max))
        p.cd0 = float(getattr(cfg, "cd0", p.cd0))
        p.v_stall_m_s = float(getattr(cfg, "v_min", p.v_stall_m_s))
        p.v_max_m_s = float(getattr(cfg, "v_max", p.v_max_m_s))
        p.roll_rate_max = float(getattr(cfg, "roll_rate_max", p.roll_rate_max))
        p.pitch_rate_max = float(getattr(cfg, "pitch_rate_max",
                                        p.pitch_rate_max))
        p.yaw_rate_max = float(getattr(cfg, "yaw_rate_max", p.yaw_rate_max))
        p.tau_roll = float(getattr(cfg, "tau_roll", p.tau_roll))
        p.tau_pitch = float(getattr(cfg, "tau_pitch", p.tau_pitch))
        p.tau_yaw = float(getattr(cfg, "tau_yaw", p.tau_yaw))
        p.max_bank_deg = float(getattr(cfg, "max_bank_deg", p.max_bank_deg))
        p.max_pitch_deg = float(getattr(cfg, "max_pitch_deg", p.max_pitch_deg))
        p.climb_max_m_s = float(getattr(cfg, "climb_max_m_s", p.climb_max_m_s))
        p.descend_max_m_s = float(getattr(cfg, "descend_max_m_s",
                                          p.descend_max_m_s))
        src = getattr(cfg, "preset", None)
        if src is not None:
            for f in cls.__dataclass_fields__:
                if f in ("uid", "name", "note"):
                    continue
                try:
                    setattr(p, f, getattr(src, f))
                except AttributeError:
                    pass
        return p


# ── встроенные пресеты ─────────────────────────────────────
def _builtin(uid: str, name: str, **kw) -> AircraftPreset:
    return AircraftPreset(uid=BUILTIN_PREFIX + uid, name=name, **kw)


def builtin_presets() -> list[AircraftPreset]:
    return [
        _builtin(
            "flywing", "FLYWING 4 кг (элевоны, 2 мотора)",
            note="Пресет для INAV SITL: летающее крыло, элевоны S1/S2, "
                 "два мотора с разнотягом. 4 кг, сваливание 70 км/ч, "
                 "максимум 250 км/ч.",
            airframe="flying_wing", motor_count=2, motor_arm_m=0.35,
            has_rudder=False,
            mass_kg=4.0, v_stall_m_s=19.44, v_max_m_s=69.4,
            cl_max=1.30, cd0=0.022,
            roll_rate_max=180, pitch_rate_max=90, yaw_rate_max=40,
        ),
        _builtin(
            "skylock", "SkyLock 4 кг (базовый)",
            note="Паспорт текущего борта: 4 кг, сваливание 70 км/ч, "
                 "максимум 250 км/ч.",
            mass_kg=4.0, v_stall_m_s=19.44, v_max_m_s=69.4,
            cl_max=1.30, cd0=0.022,
            roll_rate_max=180, pitch_rate_max=90, yaw_rate_max=40,
        ),
        _builtin(
            "skylock-log", "SkyLock по логу 30.09",
            note="Оценка по логу от 30 сентября. Использованы только участки, "
                 "где борт управлялся пилотом. Трекинг Малины не использован: "
                 "там был ограничен крен и рыскание в процентах. Масса и "
                 "скорости взяты из паспорта: в логе не было GPS и датчика "
                 "воздушной скорости.",
            mass_kg=4.0, v_stall_m_s=19.44, v_max_m_s=69.4,
            cl_max=1.30, cd0=0.022,
            roll_rate_max=120, pitch_rate_max=70, yaw_rate_max=40,
            max_bank_deg=45, max_pitch_deg=30,
            angle_gain_roll=2.3, angle_gain_pitch=5.0,
            yaw_ah_rate_max=15, ah_stick_deadband=0.04,
            climb_max_m_s=10, descend_max_m_s=10,
            tau_roll=0.10, tau_pitch=0.12, tau_yaw=0.15,
            cruise_throttle_pct=38,
        ),
        _builtin(
            "trainer", "Тренер 2.5 кг",
            note="Медленный высокоплан для отработки: меньше скорости и "
                 "угловых скоростей.",
            mass_kg=2.5, v_stall_m_s=12.0, v_max_m_s=35.0,
            cl_max=1.40, cd0=0.030, oswald_e=0.82,
            roll_rate_max=120, pitch_rate_max=60, yaw_rate_max=30,
            aileron_max_deg=22, elevator_max_deg=25, rudder_max_deg=25,
            max_bank_deg=65, max_pitch_deg=40,
            climb_max_m_s=6, descend_max_m_s=6,
            power_w=230, static_thrust_n=14,
        ),
        _builtin(
            "glider", "Планер 1.5 кг",
            note="Лёгкий планер: малая тяга, большое качество, плавные рули.",
            mass_kg=1.5, v_stall_m_s=8.0, v_max_m_s=28.0,
            cl_max=1.45, cd0=0.011, oswald_e=0.92,
            roll_rate_max=90, pitch_rate_max=45, yaw_rate_max=20,
            aileron_max_deg=20, elevator_max_deg=22, rudder_max_deg=22,
            max_bank_deg=60, max_pitch_deg=35,
            climb_max_m_s=4, descend_max_m_s=8,
            power_w=55, static_thrust_n=4, efficiency=0.78,
            tau_roll=0.18, tau_pitch=0.20,
        ),
        _builtin(
            "fast", "Скоростной 5 кг",
            note="Тяжёлый скоростной борт: высокая Vmax, быстрые рули, "
                 "большая нагрузка на крыло.",
            mass_kg=5.0, v_stall_m_s=22.0, v_max_m_s=90.0,
            cl_max=1.20, cd0=0.018, oswald_e=0.80,
            roll_rate_max=240, pitch_rate_max=120, yaw_rate_max=55,
            aileron_max_deg=28, elevator_max_deg=22, rudder_max_deg=25,
            max_bank_deg=80, max_pitch_deg=50,
            climb_max_m_s=14, descend_max_m_s=14,
            power_w=1600, static_thrust_n=45,
            tau_roll=0.10, tau_pitch=0.12,
        ),
    ]


def _read_json(path: Path) -> AircraftPreset | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        p = AircraftPreset.from_dict(data)
        if not p.uid:
            p.uid = path.stem
        return p
    except Exception:  # noqa: BLE001
        return None


def list_presets(include_builtin: bool = True) -> list[AircraftPreset]:
    out: list[AircraftPreset] = builtin_presets() if include_builtin else []
    folder = preset_dir()
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            p = _read_json(path)
            if p is not None:
                p.uid = path.stem
                out.append(p)
    return out


def find_preset(uid: str) -> AircraftPreset | None:
    for p in list_presets():
        if p.uid == uid:
            return p
    return None


def default_preset() -> AircraftPreset:
    return builtin_presets()[0]


def save_user(p: AircraftPreset) -> AircraftPreset:
    """Сохранить пользовательский пресет. Встроенные не перезаписываются."""
    if p.builtin:
        p = replace(p, uid=_uid(), name=p.name + " (копия)")
    if not p.uid or p.builtin:
        p.uid = _uid()
    errs = p.validate()
    if errs:
        raise ValueError("\n".join(errs))
    folder = preset_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (p.uid + ".json")
    path.write_text(json.dumps(p.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return p


def duplicate(p: AircraftPreset, name: str | None = None) -> AircraftPreset:
    q = replace(p, uid=_uid(), name=name or (p.name + " (копия)"))
    return save_user(q)


def delete_user(uid: str) -> bool:
    if not uid or uid.startswith(BUILTIN_PREFIX):
        return False
    folder = preset_dir()
    path = folder / (uid + ".json")
    if path.is_file():
        path.unlink()
        return True
    for p in folder.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if str(data.get("uid")) == uid:
            p.unlink()
            return True
    return False


__all__ = [
    "AircraftPreset", "builtin_presets", "list_presets", "find_preset",
    "default_preset", "save_user", "duplicate", "delete_user", "preset_dir",
]
