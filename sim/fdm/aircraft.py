"""Генерация XML-модели самолёта для JSBSim по пресету.

Пресет (``presets.AircraftPreset``) задаёт массу, геометрию, производные
устойчивости/управляемости, тягу и ограничения. Из них собирается
полноценная FDM-модель: коэффициенты CL/CD/Cm/Cl/Cn, демпфирование,
эффективность рулей и таблицы сваливания.
"""
from __future__ import annotations

import math
from pathlib import Path

NAME = "skylock"
RHO0 = 1.225
G = 9.81

# Значения по умолчанию для старых вызовов без пресета.
CL_MAX = 1.30
CL_ALPHA = 5.0
CL_AT_ZERO = 0.22
STALL_ALPHA = 0.24
ASPECT_RATIO = 6.0
OSWALD = 0.85
CD0 = 0.022


def _f(v: float) -> str:
    return ("%.6f" % float(v)).rstrip("0").rstrip(".") or "0"


def _preset(cfg):
    p = getattr(cfg, "preset", None)
    if p is not None:
        return p
    from .presets import AircraftPreset
    return AircraftPreset.from_cfg(cfg)


def aircraft_spec(cfg) -> dict:
    """Геометрия, массы, аэродинамика и тяга из пресета (SI)."""
    p = _preset(cfg)
    mass = max(0.1, float(p.mass_kg))
    v_min = max(2.0, float(p.v_stall_m_s))
    v_max = max(v_min + 2.0, float(p.v_max_m_s))
    cl_max = max(0.3, float(p.cl_max))
    cd0 = max(0.002, float(p.cd0))
    area = float(p.wing_area_m2 or 0.0)
    span = float(p.wing_span_m or 0.0)
    if area <= 0.0:
        area = 2.0 * mass * G / (RHO0 * v_min * v_min * cl_max)
    if span <= 0.0:
        span = math.sqrt(ASPECT_RATIO * area)
    chord = area / span
    ar = span * span / area
    cdi_k = 1.0 / (math.pi * ar * max(0.3, float(p.oswald_e)))

    # Инерция: либо из пресета, либо тонкое крыло/фюзеляж.
    ixx = float(p.ixx_kg_m2) or max(0.02, mass * span * span / 14.0)
    iyy = float(p.iyy_kg_m2) or max(0.03, mass * (0.9 * span) ** 2 / 14.0)
    izz = float(p.izz_kg_m2) or max(0.04, mass * (0.9 * span) ** 2 / 10.0)
    ixz = float(p.ixz_kg_m2) or 0.08 * min(ixx, izz)

    # Тяга: явная из пресета или расчёт «чтобы хватало на Vmax».
    if float(p.power_w) > 0.0:
        power = float(p.power_w)
    else:
        power = (1.07 * 0.5 * RHO0 * area * cd0 * v_max ** 3
                 / max(0.3, float(p.efficiency)))
    static_thrust = (float(p.static_thrust_n)
                     if float(p.static_thrust_n) > 0.0 else mass * G)

    return {
        "mass": mass, "v_min": v_min, "v_max": v_max,
        "cl_max": cl_max, "cd0": cd0, "area": area, "span": span,
        "chord": chord, "ar": ar, "cdi_k": cdi_k,
        "oswald": float(p.oswald_e),
        "airframe": str(getattr(p, "airframe", "plane")),
        "motor_count": max(1, min(4, int(getattr(p, "motor_count", 1)))),
        "motor_arm": (float(getattr(p, "motor_arm_m", 0.0))
                      or 0.28 * span),
        "has_rudder": bool(getattr(p, "has_rudder", True)),
        "ixx": ixx, "iyy": iyy, "izz": izz, "ixz": ixz,
        "power_w": power, "thrust_static_n": static_thrust,
        "efficiency": float(p.efficiency),
        "htail_area": 0.13 * area, "vtail_area": 0.09 * area,
        "tail_arm": 0.65 * span,
        # аэродинамические коэффициенты
        "cl0": float(p.cl0),
        "cl_alpha": max(0.5, float(p.cl_alpha)),
        "alpha_stall": math.radians(float(p.alpha_stall_deg)),
        "cl_de": float(p.cl_de), "cl_q": float(p.cl_q),
        "cl_adot": float(p.cl_adot),
        "cd_beta": float(p.cd_beta), "cd_de": float(p.cd_de),
        "cm0": float(p.cm0), "cm_alpha": float(p.cm_alpha),
        "cm_q": float(p.cm_q), "cm_adot": float(p.cm_adot),
        "cm_de": float(p.cm_de),
        "cl_b": float(p.cl_b), "cl_p": float(p.cl_p),
        "cl_r": float(p.cl_r), "cl_da": float(p.cl_da),
        "cl_dr": float(p.cl_dr),
        "cn_b": float(p.cn_b), "cn_r": float(p.cn_r),
        "cn_dr": float(p.cn_dr), "cn_da": float(p.cn_da),
        "cy_b": float(p.cy_b),
        "aileron_max": math.radians(float(p.aileron_max_deg)),
        "elevator_max": math.radians(float(p.elevator_max_deg)),
        "rudder_max": math.radians(float(p.rudder_max_deg)),
    }


def _table(rows: list[tuple[float, float]]) -> str:
    """Строки таблицы JSBSim: без дублей по X, по возрастанию."""
    by_x: dict[float, float] = {}
    for x, y in rows:
        by_x[round(float(x), 6)] = float(y)
    out = []
    for x in sorted(by_x):
        out.append("              %9s  %9s" % (_f(x), _f(by_x[x])))
    return "\n".join(out)


def _cl_rows(spec: dict) -> list[tuple[float, float]]:
    cl0 = spec["cl0"]
    ca = spec["cl_alpha"]
    ast = spec["alpha_stall"]
    clmax = spec["cl_max"]
    rows = []
    for a in (-0.60, -0.30, -0.10, 0.0, 0.5 * ast, 0.9 * ast, ast):
        cl = cl0 + ca * a
        rows.append((a, min(clmax, cl)))
    rows.append((ast, clmax))
    rows.append((ast + 0.03, 0.92 * clmax))
    rows.append((ast + 0.09, 0.70 * clmax))
    rows.append((0.60, 0.42 * clmax))
    return rows


def _cd_rows(spec: dict) -> list[tuple[float, float]]:
    c = spec["cd0"]
    ast = spec["alpha_stall"]
    return [
        (-0.60, c * 4.0), (-0.30, c * 1.6), (-0.10, c * 1.08),
        (0.0, c), (0.10, c * 1.08), (0.8 * ast, c * 1.6),
        (ast, c * 2.2), (ast + 0.06, c * 5.0), (0.60, c * 9.0),
    ]


def _cm_rows(spec: dict) -> list[tuple[float, float]]:
    cm0 = spec["cm0"]
    ca = spec["cm_alpha"]
    ast = spec["alpha_stall"]
    rows = []
    for a in (-0.60, -0.30, -0.10, 0.0, 0.10, 0.20, ast):
        rows.append((a, cm0 + ca * a))
    rows.append((ast + 0.03, cm0 + ca * (ast + 0.03) + 0.03))
    rows.append((0.40, cm0 + ca * 0.40 + 0.02))
    rows.append((0.60, cm0 + ca * 0.60))
    return rows


def _xml(cfg, s: dict) -> str:
    a = s["area"]
    b = s["span"]
    c = s["chord"]
    arm = s["tail_arm"]
    ail = s["aileron_max"]
    ele = s["elevator_max"]
    rud = s["rudder_max"]
    if int(s.get("motor_count", 1)) >= 2:
        m_arm = float(s.get("motor_arm", 0.0))
        motor_xml = f"""    <property>propulsion/thrust-left-lbs</property>
    <force name="motor-left" frame="BODY">
      <function>
        <property>propulsion/thrust-left-lbs</property>
      </function>
      <location unit="M"><x>0.18</x><y>{_f(-m_arm)}</y><z>0</z></location>
      <direction><x>1</x><y>0</y><z>0</z></direction>
    </force>
    <property>propulsion/thrust-right-lbs</property>
    <force name="motor-right" frame="BODY">
      <function>
        <property>propulsion/thrust-right-lbs</property>
      </function>
      <location unit="M"><x>0.18</x><y>{_f(m_arm)}</y><z>0</z></location>
      <direction><x>1</x><y>0</y><z>0</z></direction>
    </force>"""
    else:
        motor_xml = """    <property>propulsion/thrust-lbs</property>
    <force name="motor" frame="BODY">
      <function>
        <property>propulsion/thrust-lbs</property>
      </function>
      <location unit="M"><x>0.18</x><y>0</y><z>0</z></location>
      <direction><x>1</x><y>0</y><z>0</z></direction>
    </force>"""
    return f"""<?xml version="1.0"?>
<fdm_config name="SkyLock { _f(s['mass']) } kg" version="2.0" release="BETA">
  <fileheader>
    <author>SkyLock PC simulator</author>
    <filecreationdate>2026-11-19</filecreationdate>
    <description>SkyLock airframe generated from preset</description>
    <note>Generated model. Change the preset, not this file.</note>
  </fileheader>

  <metrics>
    <wingarea unit="M2">{_f(a)}</wingarea>
    <wingspan unit="M">{_f(b)}</wingspan>
    <chord unit="M">{_f(c)}</chord>
    <htailarea unit="M2">{_f(s['htail_area'])}</htailarea>
    <htailarm unit="M">{_f(arm)}</htailarm>
    <vtailarea unit="M2">{_f(s['vtail_area'])}</vtailarea>
    <vtailarm unit="M">{_f(arm)}</vtailarm>
    <location name="AERORP" unit="M">
      <x>0</x><y>0</y><z>0</z>
    </location>
    <location name="EYEPOINT" unit="M">
      <x>0.15</x><y>0</y><z>0.05</z>
    </location>
    <location name="VRP" unit="M">
      <x>0</x><y>0</y><z>0</z>
    </location>
  </metrics>

  <mass_balance>
    <ixx unit="KG*M2">{_f(s['ixx'])}</ixx>
    <iyy unit="KG*M2">{_f(s['iyy'])}</iyy>
    <izz unit="KG*M2">{_f(s['izz'])}</izz>
    <ixz unit="KG*M2">{_f(s['ixz'])}</ixz>
    <emptywt unit="KG">{_f(s['mass'])}</emptywt>
    <location name="CG" unit="M">
      <x>0</x><y>0</y><z>0</z>
    </location>
  </mass_balance>

  <ground_reactions>
    <contact type="BOGEY" name="NOSE">
      <location unit="M"><x>0.30</x><y>0</y><z>-0.12</z></location>
      <static_friction>0.6</static_friction>
      <dynamic_friction>0.4</dynamic_friction>
      <rolling_friction>0.02</rolling_friction>
      <spring_coeff unit="LBS/FT">600</spring_coeff>
      <damping_coeff unit="LBS/FT/SEC">120</damping_coeff>
      <max_steer unit="DEG">0</max_steer>
      <brake_group>NONE</brake_group>
      <retractable>0</retractable>
    </contact>
    <contact type="BOGEY" name="LEFT">
      <location unit="M"><x>-0.10</x><y>-0.18</y><z>-0.12</z></location>
      <static_friction>0.6</static_friction>
      <dynamic_friction>0.4</dynamic_friction>
      <rolling_friction>0.02</rolling_friction>
      <spring_coeff unit="LBS/FT">900</spring_coeff>
      <damping_coeff unit="LBS/FT/SEC">180</damping_coeff>
      <max_steer unit="DEG">0</max_steer>
      <brake_group>LEFT</brake_group>
      <retractable>0</retractable>
    </contact>
    <contact type="BOGEY" name="RIGHT">
      <location unit="M"><x>-0.10</x><y>0.18</y><z>-0.12</z></location>
      <static_friction>0.6</static_friction>
      <dynamic_friction>0.4</dynamic_friction>
      <rolling_friction>0.02</rolling_friction>
      <spring_coeff unit="LBS/FT">900</spring_coeff>
      <damping_coeff unit="LBS/FT/SEC">180</damping_coeff>
      <max_steer unit="DEG">0</max_steer>
      <brake_group>RIGHT</brake_group>
      <retractable>0</retractable>
    </contact>
  </ground_reactions>

  <external_reactions>
{motor_xml}
  </external_reactions>

  <propulsion/>

  <flight_control name="SkyLock FCS">
    <channel name="Pitch">
      <summer name="fcs/pitch-cmd-sum">
        <input>fcs/elevator-cmd-norm</input>
        <input>fcs/pitch-trim-cmd-norm</input>
        <clipto><min>-1</min><max>1</max></clipto>
      </summer>
      <aerosurface_scale name="Elevator">
        <input>fcs/pitch-cmd-sum</input>
        <range><min>{_f(-ele)}</min><max>{_f(ele)}</max></range>
        <output>fcs/elevator-pos-rad</output>
      </aerosurface_scale>
    </channel>
    <channel name="Roll">
      <aerosurface_scale name="Left Aileron">
        <input>fcs/aileron-cmd-norm</input>
        <range><min>{_f(-ail)}</min><max>{_f(ail)}</max></range>
        <output>fcs/left-aileron-pos-rad</output>
      </aerosurface_scale>
      <aerosurface_scale name="Right Aileron">
        <input>-fcs/aileron-cmd-norm</input>
        <range><min>{_f(-ail)}</min><max>{_f(ail)}</max></range>
        <output>fcs/right-aileron-pos-rad</output>
      </aerosurface_scale>
    </channel>
    <channel name="Yaw">
      <aerosurface_scale name="Rudder">
        <input>fcs/rudder-cmd-norm</input>
        <range><min>{_f(-rud)}</min><max>{_f(rud)}</max></range>
        <output>fcs/rudder-pos-rad</output>
      </aerosurface_scale>
    </channel>
  </flight_control>

  <aerodynamics>
    <alphalimits unit="RAD">
      <min>-0.60</min>
      <max>0.60</max>
    </alphalimits>

    <axis name="LIFT">
      <function name="aero/coefficient/CLalpha">
        <description>Lift vs alpha, with stall break</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <table>
            <independentVar lookup="row">aero/alpha-rad</independentVar>
            <tableData>
{_table(_cl_rows(s))}
            </tableData>
          </table>
        </product>
      </function>
      <function name="aero/coefficient/CLde">
        <description>Lift due to elevator</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>fcs/elevator-pos-rad</property>
          <value>{_f(s['cl_de'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/CLq">
        <description>Lift due to pitch rate</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>aero/ci2vel</property>
          <property>velocities/q-aero-rad_sec</property>
          <value>{_f(s['cl_q'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/CLadot">
        <description>Lift due to alpha rate</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>aero/ci2vel</property>
          <property>aero/alphadot-rad_sec</property>
          <value>{_f(s['cl_adot'])}</value>
        </product>
      </function>
    </axis>

    <axis name="DRAG">
      <function name="aero/coefficient/CD0">
        <description>Parasitic drag vs alpha</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <table>
            <independentVar lookup="row">aero/alpha-rad</independentVar>
            <tableData>
{_table(_cd_rows(s))}
            </tableData>
          </table>
        </product>
      </function>
      <function name="aero/coefficient/CDi">
        <description>Induced drag</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>aero/cl-squared</property>
          <value>{_f(s['cdi_k'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/CDbeta">
        <description>Drag due to sideslip</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <table>
            <independentVar lookup="row">aero/beta-rad</independentVar>
            <tableData>
              -1.570  1.000
              -0.300  {_f(s['cd_beta'])}
               0.000  0.000
               0.300  {_f(s['cd_beta'])}
               1.570  1.000
            </tableData>
          </table>
        </product>
      </function>
      <function name="aero/coefficient/CDde">
        <description>Drag due to elevator</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>fcs/mag-elevator-pos-rad</property>
          <value>{_f(s['cd_de'])}</value>
        </product>
      </function>
    </axis>

    <axis name="SIDE">
      <function name="aero/coefficient/CYb">
        <description>Side force due to beta</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>aero/beta-rad</property>
          <value>{_f(s['cy_b'])}</value>
        </product>
      </function>
    </axis>

    <axis name="ROLL">
      <function name="aero/coefficient/Clb">
        <description>Roll due to beta (dihedral)</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>aero/beta-rad</property>
          <value>{_f(s['cl_b'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Clp">
        <description>Roll damping</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>aero/bi2vel</property>
          <property>velocities/p-aero-rad_sec</property>
          <value>{_f(s['cl_p'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Clr">
        <description>Roll due to yaw rate</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>aero/bi2vel</property>
          <property>velocities/r-aero-rad_sec</property>
          <value>{_f(s['cl_r'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Clda">
        <description>Roll due to aileron</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>fcs/left-aileron-pos-rad</property>
          <value>{_f(s['cl_da'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Cldr">
        <description>Roll due to rudder</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>fcs/rudder-pos-rad</property>
          <value>{_f(s['cl_dr'])}</value>
        </product>
      </function>
    </axis>

    <axis name="PITCH">
      <function name="aero/coefficient/Cmalpha">
        <description>Pitch moment vs alpha (static margin)</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/cbarw-ft</property>
          <table>
            <independentVar lookup="row">aero/alpha-rad</independentVar>
            <tableData>
{_table(_cm_rows(s))}
            </tableData>
          </table>
        </product>
      </function>
      <function name="aero/coefficient/Cmde">
        <description>Pitch moment due to elevator</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/cbarw-ft</property>
          <property>fcs/elevator-pos-rad</property>
          <value>{_f(s['cm_de'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Cmq">
        <description>Pitch damping</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/cbarw-ft</property>
          <property>aero/ci2vel</property>
          <property>velocities/q-aero-rad_sec</property>
          <value>{_f(s['cm_q'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Cmadot">
        <description>Pitch moment due to alpha rate</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/cbarw-ft</property>
          <property>aero/ci2vel</property>
          <property>aero/alphadot-rad_sec</property>
          <value>{_f(s['cm_adot'])}</value>
        </product>
      </function>
    </axis>

    <axis name="YAW">
      <function name="aero/coefficient/Cnb">
        <description>Weathercock stability</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>aero/beta-rad</property>
          <value>{_f(s['cn_b'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Cnr">
        <description>Yaw damping</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>aero/bi2vel</property>
          <property>velocities/r-aero-rad_sec</property>
          <value>{_f(s['cn_r'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Cndr">
        <description>Yaw due to rudder</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>fcs/rudder-pos-rad</property>
          <value>{_f(s['cn_dr'])}</value>
        </product>
      </function>
      <function name="aero/coefficient/Cnda">
        <description>Adverse yaw</description>
        <product>
          <property>aero/qbar-psf</property>
          <property>metrics/Sw-sqft</property>
          <property>metrics/bw-ft</property>
          <property>fcs/left-aileron-pos-rad</property>
          <value>{_f(s['cn_da'])}</value>
        </product>
      </function>
    </axis>
  </aerodynamics>
</fdm_config>
"""


def write_aircraft(cfg, parent: Path) -> tuple[Path, dict]:
    """Записать ``<parent>/aircraft/skylock/skylock.xml``. Вернуть путь и spec."""
    spec = aircraft_spec(cfg)
    folder = Path(parent) / "aircraft" / NAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (NAME + ".xml")
    path.write_text(_xml(cfg, spec), encoding="utf-8")
    return path, spec


__all__ = ["NAME", "aircraft_spec", "write_aircraft"]
