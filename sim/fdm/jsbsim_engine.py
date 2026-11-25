"""Адаптер JSBSim: настоящее 6-DoF ядро вместо упрощённой кинематики.

JSBSim считает аэродинамику, моменты и движение. Мы оставляем привычную
логику ПК-симулятора: стики RC → режим AH (углы/высота) или ACRO (угловые
скорости), газ → тяга. Внутренние контуры (rate/angle) замыкаются здесь, на
200 Гц, поверх поверхностей JSBSim.
"""
from __future__ import annotations

import atexit
import ctypes
import math
import os
import shutil
import tempfile
from pathlib import Path

from . import jsbsim_info
from .aircraft import NAME, aircraft_spec, write_aircraft
from .base import FdmError
from .turb import Turbulence

FT = 0.3048
LB = 4.4482216
G = 9.81
RHO0 = 1.225
SUBSTEP = 1.0 / 400.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _short_path(path: str) -> str:
    """Windows: короткий путь без кириллицы/пробелов для C++ JSBSim."""
    if os.name != "nt":
        return path
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 32768)
        if n:
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return path


class JSBSimEngine:
    name = "jsbsim"

    def __init__(self, cfg, state):
        self.cfg = cfg
        self.state = state
        self._fdm = None
        self._tmp: Path | None = None
        self._spec = aircraft_spec(cfg)
        self._int_roll = 0.0
        self._int_pitch = 0.0
        self._int_yaw = 0.0
        self._int_vs = 0.0
        self._surf = [0.0, 0.0, 0.0]
        self._gas = 0.0
        self._pitch_trim = 0.0
        self._power = 0.0
        self._acc = 0.0
        self._t_static = 0.0
        self._eff = 0.75
        self._v_stall = 0.0
        self._power_motor = 0.0
        self._t_static_motor = 0.0
        self._motor_count = 1
        self._motor_arm = 0.0
        self._airframe = "plane"
        self._has_rudder = True
        self._fc_mode = False
        self._fc = (0.0, 0.0, 0.0, 0.0, 0.0)
        self._origin_x = 0.0
        self._origin_y = 0.0
        self._turb = Turbulence()
        self._load()

    # ── загрузка ────────────────────────────────────────────
    def _load(self) -> None:
        info = jsbsim_info()
        if not info.get("installed"):
            raise FdmError("JSBSim не установлен")
        os.environ.setdefault("JSBSIM_DEBUG", "0")
        import jsbsim  # noqa: PLC0415  (ленивый импорт: аддон необязателен)

        self._jsbsim = jsbsim
        self._tmp = Path(tempfile.mkdtemp(prefix="inav-sim-jsbsim-"))
        atexit.register(shutil.rmtree, str(self._tmp), True)
        write_aircraft(self.cfg, self._tmp)

        root = _short_path(str(info["root"]))
        self._fdm = jsbsim.FGFDMExec(root)
        self._fdm.set_debug_level(0)
        self._fdm.set_dt(SUBSTEP)
        self._fdm.set_aircraft_path(_short_path(str(self._tmp / "aircraft")))
        if not self._fdm.load_model(NAME):
            raise FdmError("JSBSim не загрузил модель %s" % NAME)
        self._spec = aircraft_spec(self.cfg)
        self._update_power()
        self.reset(self.cfg)

    def _update_power(self) -> None:
        s = self._spec
        self._power = float(s["power_w"])
        self._t_static = float(s["thrust_static_n"])
        self._eff = float(s.get("efficiency", 0.75))
        # Паспортная v_max — ориентир для полного газа в горизонте. Если
        # явной мощности не хватает, чуть поднимаем её/статическую тягу:
        # иначе JSBSim упирается в 230 км/ч при паспортных 250.
        vmax = max(5.0, float(s["v_max"]))
        q_s = 0.5 * RHO0 * float(s["area"]) * vmax * vmax
        t_req = (q_s * float(s["cd0"])
                 + float(s["cdi_k"]) * (float(s["mass"]) * G) ** 2
                 / max(1.0, q_s))
        t_avail = min(self._t_static,
                      self._power * self._eff / max(6.0, vmax))
        if t_avail > 1.0 and t_req > t_avail:
            self._power *= t_req / t_avail
            self._t_static = max(self._t_static, t_req * 1.02)
        # Физическая скорость сваливания по геометрии/CLmax. При
        # автоматической площади она совпадает с паспортной.
        self._v_stall = max(4.0, math.sqrt(
            2.0 * float(s["mass"]) * G
            / (RHO0 * float(s["area"]) * float(s["cl_max"]))))
        self._motor_count = max(1, int(s.get("motor_count", 1)))
        self._motor_arm = float(s.get("motor_arm", 0.0))
        self._airframe = str(s.get("airframe", "plane"))
        self._has_rudder = bool(s.get("has_rudder", True))
        self._power_motor = self._power / self._motor_count
        self._t_static_motor = self._t_static / self._motor_count

    # ── сброс ───────────────────────────────────────────────
    def reset(self, cfg) -> None:
        self.cfg = cfg
        self.state.reset(cfg)
        self._update_power()
        self._origin_x = 0.0
        self._origin_y = 0.0
        self._set_ic(float(cfg.alt_start), float(cfg.v_start),
                     float(self.state.yaw), float(self.state.roll),
                     float(self.state.pitch))
        self._int_roll = 0.0
        self._int_pitch = 0.0
        self._int_yaw = 0.0
        self._int_vs = 0.0
        self._surf = [0.0, 0.0, 0.0]
        self._gas = 0.0
        self._acc = 0.0
        self._pitch_trim = -self._trim_norm(float(cfg.v_start) * 1.15)
        if hasattr(self, "_turb"):
            self._turb.reset()
        self._sync(0.02, (0.0, 0.0, 0.0), 0.0)

    def _set_ic(self, alt: float, v: float, yaw: float,
                roll: float = 0.0, pitch: float = 0.0) -> None:
        fdm = self._fdm
        fdm["ic/lat-gc-deg"] = 55.75
        fdm["ic/long-gc-deg"] = 37.62
        fdm["ic/h-sl-ft"] = max(1.0, float(alt)) / FT
        fdm["ic/vc-kts"] = max(1.0, float(v)) * 1.943844
        fdm["ic/phi-deg"] = float(roll)
        fdm["ic/theta-deg"] = float(pitch)
        fdm["ic/psi-true-deg"] = float(yaw) % 360.0
        fdm["ic/gamma-deg"] = 0.0
        fdm["atmosphere/wind-north-fps"] = 0.0
        fdm["atmosphere/wind-east-fps"] = 0.0
        fdm["atmosphere/wind-down-fps"] = 0.0
        for key in ("fcs/aileron-cmd-norm", "fcs/elevator-cmd-norm",
                    "fcs/rudder-cmd-norm", "propulsion/thrust-lbs",
                    "propulsion/thrust-left-lbs",
                    "propulsion/thrust-right-lbs"):
            try:
                fdm[key] = 0.0
            except Exception:  # noqa: BLE001
                pass
        fdm.run_ic()

    def place(self, x: float, y: float, alt: float, yaw: float, v: float,
              roll: float = 0.0, pitch: float = 0.0) -> None:
        """Поставить борт в точку мира (для цели, которая стартует не в 0)."""
        self._origin_x = float(x)
        self._origin_y = float(y)
        self._set_ic(alt, v, yaw, roll, pitch)
        self._int_roll = 0.0
        self._int_pitch = 0.0
        self._int_yaw = 0.0
        self._int_vs = 0.0
        self._surf = [0.0, 0.0, 0.0]
        self._gas = 0.0
        self._acc = 0.0
        self._pitch_trim = -self._trim_norm(max(5.0, float(v)) * 1.15)
        if hasattr(self, "_turb"):
            self._turb.reset()
        self._sync(0.02, (0.0, 0.0, 0.0), 0.0)
        st = self.state
        st.x, st.y, st.alt = float(x), float(y), float(alt)
        st.yaw, st.v = float(yaw), float(v)
        st.roll, st.pitch = float(roll), float(pitch)

    # ── шаг ─────────────────────────────────────────────────
    def step(self, dt: float, ch_us, wind=None) -> None:
        if self._fdm is None or self.state.crash:
            return
        dt = _clamp(float(dt), 1e-3, 0.10)
        self._ch_us = list(ch_us)
        cfg = self.cfg
        self._turb.sample(
            dt,
            intensity_m_s=float(getattr(cfg, "turb_intensity_m_s", 0.0)),
            freq_hz=float(getattr(cfg, "turb_freq_hz", 0.12)),
            enabled=bool(getattr(cfg, "turb_enable", False)))
        if wind is None:
            wind = (self.cfg.wind_x, self.cfg.wind_y, 0.0)
        wx = (float(wind[0]) if len(wind) > 0 else 0.0) + self._turb.gx
        wy = (float(wind[1]) if len(wind) > 1 else 0.0) + self._turb.gy
        wz = (float(wind[2]) if len(wind) > 2 else 0.0) + self._turb.gz
        self._fdm["atmosphere/wind-north-fps"] = wx / FT
        self._fdm["atmosphere/wind-east-fps"] = wy / FT
        self._fdm["atmosphere/wind-down-fps"] = wz / FT
        n = 0
        # JSBSim интегрирует ровно SUBSTEP (5 мс). Раньше шаг округляли до
        # целого (round), и физика отставала от часов симулятора на 10–25 %
        # при 60–144 к/с. Теперь остаток копится между кадрами.
        self._acc += dt
        while self._acc >= SUBSTEP and n < 40:
            self._substep(SUBSTEP)
            self._acc -= SUBSTEP
            n += 1
        self._sync(dt, (wx, wy, wz), self._gas)

    def _inputs(self, ch_us):
        cfg = self.cfg
        rc_min = float(getattr(cfg, "rc_min_us", 900.0))
        rc_max = float(getattr(cfg, "rc_max_us", 2100.0))
        if rc_max <= rc_min:
            rc_min, rc_max = 900.0, 2100.0
        center = 1500.0
        half = max(1.0, center - rc_min, rc_max - center)
        u_roll = _clamp((float(ch_us[0]) - center) / half, -1.0, 1.0)
        u_pitch = _clamp((float(ch_us[1]) - center) / half, -1.0, 1.0)
        u_gas = _clamp((float(ch_us[2]) - rc_min)
                       / max(1.0, rc_max - rc_min), 0.0, 1.0)
        u_yaw = _clamp((float(ch_us[3]) - center) / half, -1.0, 1.0)
        return u_roll, u_pitch, u_gas, u_yaw

    def _substep(self, dt: float) -> None:
        fdm = self._fdm
        cfg = self.cfg
        u_roll, u_pitch, u_gas, u_yaw = self._inputs(self._ch_us)
        v = max(1.0, float(fdm["velocities/vtrue-fps"]) * FT)
        phi = math.radians(float(fdm["attitude/phi-deg"]))
        theta = math.radians(float(fdm["attitude/theta-deg"]))
        p = float(fdm["velocities/p-rad_sec"])
        q = float(fdm["velocities/q-rad_sec"])
        r = float(fdm["velocities/r-rad_sec"])
        alpha = float(fdm["aero/alpha-rad"])
        beta = math.radians(float(fdm["aero/beta-deg"]))

        # Внешний полётник (INAV SITL): рули и тяга приходят готовыми.
        # Внутренние контуры AH/ACRO при этом не работают.
        if self._fc_mode:
            a, e, r, tl, tr = self._fc
            self._apply_surfaces([_clamp(a, -1.0, 1.0),
                                  _clamp(e, -1.0, 1.0),
                                  _clamp(r, -1.0, 1.0)], dt)
            self._apply_fc_thrust(_clamp(tl, 0.0, 1.0),
                                  _clamp(tr, 0.0, 1.0), v)
            return

        v_stall = max(1.0, self._v_stall)
        authority = _clamp(v / v_stall,
                           max(0.0, float(cfg.stall_authority_min)), 1.0)
        ref_gas = _clamp(float(cfg.yaw_ref_gas_pct) / 100.0, 0.01, 1.0)
        yaw_limit = _clamp(
            float(cfg.yaw_ref_rate_deg_s) * (u_gas / ref_gas),
            0.0, float(cfg.yaw_rate_max))
        turn = G * math.sin(phi) / max(8.0, v)

        mode = str(getattr(cfg, "flight_mode", "angle")).lower()
        if mode.startswith("direct"):
            # Чистое ядро: стик -> поверхность, без контуров.
            self._int_roll = self._int_pitch = self._int_yaw = 0.0
            self._apply_surfaces([u_roll, u_pitch,
                                  (0.0 if not self._has_rudder else -u_yaw)],
                                 dt)
            self._fdm["fcs/pitch-trim-cmd-norm"] = self._pitch_trim
            self._apply_thrust(u_gas, v,
                               u_yaw if self._airframe == "flying_wing"
                               else 0.0)
            return
        self._fdm["fcs/pitch-trim-cmd-norm"] = 0.0
        if mode.startswith("angle"):
            target_roll = self._stick_angle(u_roll, cfg.max_bank_deg)
            gamma = self._stick_angle(-u_pitch, cfg.max_pitch_deg)
            if v > 1.0:
                up = math.degrees(math.asin(_clamp(
                    float(cfg.climb_max_m_s) / v, 0.0, 1.0)))
                down = -math.degrees(math.asin(_clamp(
                    float(cfg.descend_max_m_s) / v, 0.0, 1.0)))
                gamma = _clamp(gamma, down, up)
            # AH держит горизонт и в вираже: при нейтральном стике
            # тангажа цель — нулевая вертикальная скорость. Текущий
            # траекторный угол вычитаем напрямую (это и есть набор/спуск),
            # а интегратор убирает остаток от неточности модели и ветра.
            # Иначе в крене вертикальная составляющая подъёмной силы
            # падает и борт проседает (а на глубоком крене — уходит
            # в спираль).
            vs = -float(fdm["velocities/v-down-fps"]) * FT
            gamma_act = math.degrees(math.asin(_clamp(
                vs / max(8.0, v), -0.6, 0.6)))
            pitch_hold = 0.0
            if abs(u_pitch) <= 0.08:
                self._int_vs = _clamp(
                    self._int_vs - vs * dt * 0.35, -8.0, 8.0)
                pitch_hold = -gamma_act + self._int_vs
            else:
                self._int_vs *= max(0.0, 1.0 - 2.0 * dt)
            alpha_ff = self._alpha_for_level(v, math.degrees(phi))
            target_pitch = gamma + math.degrees(alpha_ff) + pitch_hold
            target_pitch = _clamp(target_pitch,
                                  -float(cfg.max_pitch_deg),
                                  float(cfg.max_pitch_deg))
            # Защита от сваливания: если угол атаки выше допустимого,
            # не держим нос, а опускаем его до alpha_limit. Иначе на
            # малой скорости борт «клюёт» и уходит в глубокий срыв.
            alpha_lim = self._spec["alpha_stall"] - 0.02
            if alpha > alpha_lim:
                gamma_act = theta - alpha
                target_pitch = math.degrees(gamma_act + alpha_lim)
            target_pitch = _clamp(target_pitch,
                                  -float(cfg.max_pitch_deg),
                                  float(cfg.max_pitch_deg))
            # Плавная турбулентность: медленно заваливает/поднимает нос.
            target_roll = _clamp(target_roll + self._turb.roll_deg,
                                 -float(cfg.max_bank_deg),
                                 float(cfg.max_bank_deg))
            target_pitch = _clamp(target_pitch + self._turb.pitch_deg,
                                  -float(cfg.max_pitch_deg),
                                  float(cfg.max_pitch_deg))
            p_des = math.radians(_clamp(
                float(cfg.angle_gain_roll) * (target_roll - math.degrees(phi)),
                -float(cfg.roll_rate_max), float(cfg.roll_rate_max)))
            # В крене тангаж в мировых осях зависит не только от q:
            # theta' = q·cos(φ) − r·sin(φ). Компенсируем кинематику,
            # иначе на глубоком крене руль высоты почти не поднимает нос.
            cos_phi = max(0.25, math.cos(phi))
            q_cmd = (float(cfg.angle_gain_pitch)
                     * (target_pitch - math.degrees(theta))
                     + math.degrees(r) * math.sin(phi)) / cos_phi
            q_des = math.radians(_clamp(q_cmd,
                                        -float(cfg.pitch_rate_max),
                                        float(cfg.pitch_rate_max)))
            # Координированный разворот: в вираже борт должен скользить
            # нулевым боком. Корректируем рулём направления по углу
            # скольжения (β → 0), а не «закручиваем» его на темп виража.
            r_cmd = (math.radians(u_yaw * min(float(cfg.yaw_ah_rate_max),
                                             yaw_limit))
                     + 2.0 * beta)
        else:
            p_des = math.radians(u_roll * float(cfg.roll_rate_max))
            q_des = math.radians(-u_pitch * float(cfg.pitch_rate_max))
            # Плавные порывы в ACRO: медленные угловые скорости.
            p_des += math.radians(self._turb.roll_rate_deg)
            q_des += math.radians(self._turb.pitch_rate_deg)
            r_cmd = math.radians(u_yaw * yaw_limit)
            alpha_ff = 0.0

        p_des *= authority
        q_des *= authority
        r_des = r_cmd * authority
        if mode.startswith("angle"):
            r_des += turn

        # Эффективность рулей растёт как V^2: ослабляем петли на скорости,
        # усиливаем на малой, чтобы отклик был одинаковым.
        gain = _clamp((25.0 / max(8.0, v)) ** 2, 0.35, 2.5)
        self._int_roll, ail = self._pid(p_des - p, self._int_roll,
                                        0.55 * gain, 0.08 * gain, dt)
        self._int_pitch, elev = self._pid(q_des - q, self._int_pitch,
                                          0.75 * gain, 0.10 * gain, dt)
        self._int_yaw, rud = self._pid(r_des - r, self._int_yaw,
                                       1.40 * gain, 0.15 * gain, dt)
        # Знаки поверхности: в JSBSim положительный руль высоты — нос вниз,
        # положительный руль направления — влево. Ползунок трима сдвигает
        # балансировочное положение РВ только в rate-режиме.
        # В rate/ACRO триммируем по скорости: иначе на полном газу борт
        # задирает нос, лезет вверх и не разгоняется до паспортной v_max.
        trim = (-self._trim_norm(max(6.0, v))
                if mode.startswith("rate") else 0.0)
        if not self._has_rudder:
            rud = 0.0
        self._apply_surfaces([ail, -elev + trim, -rud], dt)
        self._apply_thrust(u_gas, v,
                           u_yaw if self._airframe == "flying_wing" else 0.0)

    def _apply_fc_thrust(self, tl: float, tr: float, v: float) -> None:
        """Тяга от внешнего полётника: два готовых значения 0..1."""
        fdm = self._fdm
        rho_ratio = float(fdm["atmosphere/rho-slugs_ft3"]) / 0.0023769
        avail = min(self._t_static_motor,
                    self._power_motor * self._eff / max(6.0, v))
        thrust_n = avail * max(0.3, rho_ratio)
        if self._motor_count >= 2:
            fdm["propulsion/thrust-left-lbs"] = tl * thrust_n / LB
            fdm["propulsion/thrust-right-lbs"] = tr * thrust_n / LB
        else:
            fdm["propulsion/thrust-lbs"] = 0.5 * (tl + tr) * thrust_n / LB
        self._gas = 0.5 * (tl + tr)
        fdm.run()

    # ── внешний полётник (INAV SITL) ─────────────────────────
    def enable_fc(self, enabled: bool) -> None:
        """Переключить на готовые команды рулей/тяги от SITL."""
        self._fc_mode = bool(enabled)
        self._int_roll = self._int_pitch = self._int_yaw = 0.0
        self._int_vs = 0.0
        if not enabled:
            self._surf = [0.0, 0.0, 0.0]
            self._fc = (0.0, 0.0, 0.0, 0.0, 0.0)

    def set_fc_controls(self, aileron: float, elevator: float,
                        rudder: float, thrust_left: float,
                        thrust_right: float) -> None:
        """Вызывается из потока моста; кортеж присваивается атомарно."""
        self._fc = (float(aileron), float(elevator), float(rudder),
                    float(thrust_left), float(thrust_right))

    def _apply_surfaces(self, cmd, dt: float) -> None:
        cfg = self.cfg
        tau = (max(0.02, float(cfg.tau_roll)),
               max(0.02, float(cfg.tau_pitch)),
               max(0.02, float(cfg.tau_yaw)))
        for i in range(3):
            a = 1.0 - math.exp(-dt / tau[i])
            self._surf[i] += (float(cmd[i]) - self._surf[i]) * a
        self._fdm["fcs/aileron-cmd-norm"] = self._surf[0]
        self._fdm["fcs/elevator-cmd-norm"] = self._surf[1]
        self._fdm["fcs/rudder-cmd-norm"] = self._surf[2]

    def _apply_thrust(self, u_gas: float, v: float, yaw: float = 0.0) -> None:
        fdm = self._fdm
        rho_ratio = float(fdm["atmosphere/rho-slugs_ft3"]) / 0.0023769
        avail = min(self._t_static_motor,
                    self._power_motor * self._eff / max(6.0, v))
        thrust_n = avail * max(0.3, rho_ratio)
        if self._motor_count >= 2:
            # Дифференциальная тяга: yaw > 0 — правый мотор слабее,
            # левый сильнее; так сила на левом плече даёт момент вправо.
            left = _clamp(u_gas + 0.5 * float(yaw), 0.0, 1.0)
            right = _clamp(u_gas - 0.5 * float(yaw), 0.0, 1.0)
            fdm["propulsion/thrust-left-lbs"] = left * thrust_n / LB
            fdm["propulsion/thrust-right-lbs"] = right * thrust_n / LB
        else:
            fdm["propulsion/thrust-lbs"] = u_gas * thrust_n / LB
        self._gas = u_gas
        fdm.run()

    def _stick_angle(self, u: float, max_deg: float) -> float:
        dz = _clamp(float(getattr(self.cfg, "ah_stick_deadband", 0.04)),
                    0.0, 0.5)
        u = _clamp(u, -1.0, 1.0)
        if abs(u) <= dz:
            return 0.0
        sign = 1.0 if u > 0.0 else -1.0
        frac = (abs(u) - dz) / max(1e-6, 1.0 - dz)
        return sign * frac * float(max_deg)

    def _alpha_for_level(self, v: float, bank_deg: float) -> float:
        s = self._spec
        nz = 1.0 / max(0.2, math.cos(math.radians(bank_deg)))
        cl = (2.0 * s["mass"] * G * nz
              / max(1.0, RHO0 * v * v * s["area"]))
        a = (cl - s["cl0"]) / max(0.5, s["cl_alpha"])
        return _clamp(a, -0.10, s["alpha_stall"])

    def _trim_norm(self, v: float) -> float:
        """Нормированный трим РВ, при котором Cm(alpha) + Cmde*de = 0."""
        s = self._spec
        a = self._alpha_for_level(max(5.0, v), 0.0)
        cm = s["cm0"] + s["cm_alpha"] * a
        de = -cm / s["cm_de"] if abs(s["cm_de"]) > 1e-9 else 0.0
        return _clamp(-de / max(1e-6, s["elevator_max"]), -0.95, 0.95)

    @staticmethod
    def _pid(err: float, integ: float, kp: float, ki: float, dt: float):
        # Условное интегрирование: пока выход насыщен и ошибка тянет его
        # дальше в насыщение, интегратор не копим. Иначе при вводе в крен
        # он «залипает», и борт уходит далеко за целевой угол, а потом
        # несколько секунд не может вернуться.
        out = kp * err + integ
        if (abs(out) < 1.0
                or (out >= 1.0 and err < 0.0)
                or (out <= -1.0 and err > 0.0)):
            integ = _clamp(integ + err * dt * ki, -0.6, 0.6)
        out = _clamp(kp * err + integ, -1.0, 1.0)
        return integ, out

    def _sync(self, dt: float, wind, gas: float) -> None:
        st = self.state
        fdm = self._fdm
        st.x = self._origin_x + float(fdm["position/from-start-neu-n-ft"]) * FT
        st.y = self._origin_y + float(fdm["position/from-start-neu-e-ft"]) * FT
        st.alt = float(fdm["position/h-sl-meters"])
        st.v = max(0.0, float(fdm["velocities/vtrue-fps"]) * FT)
        st.roll = _wrap_deg(float(fdm["attitude/phi-deg"]))
        st.pitch = _wrap_deg(float(fdm["attitude/theta-deg"]))
        st.yaw = _wrap_deg(float(fdm["attitude/psi-deg"]))
        st.p = float(fdm["velocities/p-rad_sec"])
        st.q = float(fdm["velocities/q-rad_sec"])
        st.r = float(fdm["velocities/r-rad_sec"])
        st.vertical_speed = -float(fdm["velocities/v-down-fps"]) * FT
        st.gs = max(0.0, float(fdm["velocities/vg-fps"]) * FT)
        st.wind_x, st.wind_y, st.wind_z = (float(wind[0]), float(wind[1]),
                                           float(wind[2]))
        v_stall = max(1.0, self._v_stall)
        st.stall_ratio = _clamp((v_stall - st.v) / v_stall, 0.0, 1.0)
        st.stalled = st.v < v_stall
        st.thr = float(gas)
        if dt > 0.0:
            self._battery(st, gas, dt)
        if st.alt <= 3.0:
            st.alt = 3.0
            st.crash = True

    def _battery(self, st, u_gas: float, dt: float) -> None:
        cfg = self.cfg
        cells = max(1, min(12, int(cfg.batt_cells)))
        cap_mah = max(1.0, float(cfg.batt_capacity_mah))
        idle_a = max(0.0, float(cfg.batt_idle_a))
        full_a = max(idle_a, float(cfg.batt_full_a))
        current_a = idle_a + (full_a - idle_a) * u_gas
        st.batt_a += (current_a - st.batt_a) * (1.0 - math.exp(-dt / 0.8))
        st.batt_mah += st.batt_a * dt / 3.6
        frac = _clamp(st.batt_mah / cap_mah, 0.0, 1.0)
        st.batt_pct = 100.0 * (1.0 - frac)
        full_v = 4.2 * cells
        empty_v = 3.3 * cells
        sag = 0.55 * (cells / 4.0) * u_gas
        st.batt_v = full_v - (full_v - empty_v) * frac - sag

    # ── служебное ───────────────────────────────────────────
    @property
    def pitch_trim(self) -> float:
        return float(self._pitch_trim)

    def set_pitch_trim(self, value: float) -> None:
        self._pitch_trim = _clamp(float(value), -1.0, 1.0)

    @property
    def info(self) -> dict:
        s = self._spec
        return {
            "mass_kg": round(s["mass"], 2),
            "area_m2": round(s["area"], 4),
            "span_m": round(s["span"], 3),
            "aspect_ratio": round(s["ar"], 2),
            "power_w": round(self._power, 0),
            "thrust_static_n": round(self._t_static, 1),
        }

    def close(self) -> None:
        self._fdm = None
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None


__all__ = ["JSBSimEngine", "SUBSTEP"]
