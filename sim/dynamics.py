"""Состояние борта и конфиг для ПК-симулятора (без Qt).

Модуль намеренно минимальный: физика живёт в ``sim.fdm`` (JSBSim 6-DoF),
а здесь — только общие типы ``SimConfig`` и ``Aircraft``.

Оси мира: x — вперёд (север), y — вправо (восток), z — вниз.
Углы: roll > 0 — правое крыло вниз, pitch > 0 — нос вверх,
yaw > 0 — вправо.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


@dataclass
class SimConfig:
    # Пресет самолёта (AircraftPreset) — источник аэродинамики.
    # Физика одна: JSBSim (6-DoF), аддон ставится из окна настроек.
    preset: object | None = None
    # Схема борта: "plane" (элероны+РВ+РН) или "flying_wing"
    # (элевоны, дифференциальная тяга). Моторов 1..4, плечо — для разнотяга.
    airframe: str = "plane"
    motor_count: int = 1
    motor_arm_m: float = 0.0
    has_rudder: bool = True
    # Борт: масса и максимальные угловые скорости при полном стике.
    mass_kg: float = 4.0
    # Геометрия для JSBSim. 0 — вычислить из массы и v_min (CLmax).
    wing_area_m2: float = 0.0
    wing_span_m: float = 0.0
    cl_max: float = 1.30
    cd0: float = 0.022
    roll_rate_max: float = 180.0
    pitch_rate_max: float = 90.0
    yaw_rate_max: float = 40.0
    # Паспортный темп рыскания: 15 °/с при 40% газа.
    yaw_ref_rate_deg_s: float = 15.0
    yaw_ref_gas_pct: float = 40.0
    # Запаздывание серво + крыла.
    tau_roll: float = 0.12
    tau_pitch: float = 0.15
    tau_yaw: float = 0.20
    # Автопилот цели (и совместимость): "angle" | "rate" | "direct".
    # Борт игрока всегда управляется внешним полётником (FC).
    flight_mode: str = "angle"
    # Пределы AH: крен 75°, тангаж 45° (по паспорту борта).
    max_bank_deg: float = 75.0
    max_pitch_deg: float = 45.0
    angle_gain_roll: float = 4.0    # 1/с: скорость выхода на угол
    angle_gain_pitch: float = 4.0
    yaw_ah_rate_max: float = 15.0   # °/с: руль в AH, не «юла»
    # Зона нейтрали стика в AH: внутри неё целевой угол = 0 (горизонт).
    ah_stick_deadband: float = 0.04
    # Скорости, м/с; v_min = 70 км/ч (скорость сваливания).
    # Газ 0 -> цель скорости 0, газ 100% -> v_max.
    v_min: float = 19.44
    v_max: float = 69.4          # 250 км/ч на полном газу
    v_start: float = 20.0
    # Ограничения набора/спуска, м/с (в нормальном полёте).
    climb_max_m_s: float = 10.0
    descend_max_m_s: float = 10.0
    stall_authority_min: float = 0.15
    alt_start: float = 120.0
    # FOV камеры FPV.
    fov_h_deg: float = 120.0
    wind_x: float = 0.0
    wind_y: float = 0.0
    # Ветер/турбулентность. Одна ручка интенсивности, м/с.
    turb_enable: bool = True
    turb_intensity_m_s: float = 0.8
    # Устаревшие поля (для старых профилей); в UI больше не показываются.
    turb_roll_pct: float = 45.0
    turb_pitch_pct: float = 10.0
    turb_gust_m_s: float = 0.8
    turb_freq_hz: float = 0.12
    # Аккумулятор (пока не выводится, но учитывается в модели).
    batt_cells: int = 4
    batt_capacity_mah: float = 3300.0
    batt_idle_a: float = 2.0
    batt_full_a: float = 35.0
    # Диапазон ШИМ каналов управления.
    rc_min_us: float = 1000.0
    rc_max_us: float = 2000.0


@dataclass
class Aircraft:
    x: float = 0.0
    y: float = 0.0
    alt: float = 120.0
    v: float = 20.0
    roll: float = 0.0       # градусы
    pitch: float = 0.0      # градусы
    yaw: float = 0.0        # градусы
    p: float = 0.0          # рад/с
    q: float = 0.0
    r: float = 0.0
    wind_x: float = 0.0
    wind_y: float = 0.0
    wind_z: float = 0.0
    vertical_speed: float = 0.0
    gs: float = 0.0
    stalled: bool = False
    stall_ratio: float = 0.0
    # Тяга/батарея (батарея пока не выводится, но учитывается моделью).
    thr: float = 0.0
    turb_t: float = 0.0
    batt_v: float = 16.8
    batt_a: float = 0.0
    batt_mah: float = 0.0
    batt_pct: float = 100.0
    crash: bool = False

    def reset(self, cfg: SimConfig) -> None:
        self.x = 0.0
        self.y = 0.0
        self.alt = float(cfg.alt_start)
        self.v = float(cfg.v_start)
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.p = self.q = self.r = 0.0
        self.wind_x = self.wind_y = self.wind_z = 0.0
        self.vertical_speed = 0.0
        self.gs = 0.0
        self.stalled = False
        self.stall_ratio = 0.0
        self.thr = 0.0
        self.turb_t = 0.0
        self.batt_v = 4.2 * max(1, min(12, int(cfg.batt_cells)))
        self.batt_a = 0.0
        self.batt_mah = 0.0
        self.batt_pct = 100.0
        self.crash = False

    @property
    def gs_m_s(self) -> float:
        """Путевая скорость с учётом ветра, м/с."""
        if self.gs > 0.0:
            return self.gs
        cp = math.cos(math.radians(self.pitch))
        vx = self.v * cp * math.cos(math.radians(self.yaw)) + self.wind_x
        vy = self.v * cp * math.sin(math.radians(self.yaw)) + self.wind_y
        return math.hypot(vx, vy)

    @property
    def vs_m_s(self) -> float:
        """Вертикальная скорость, м/с (+ вверх)."""
        return self.vertical_speed

    @property
    def pos(self) -> tuple[float, float, float]:
        """Мировые координаты: x, y, z (z вниз)."""
        return (self.x, self.y, -self.alt)
