"""Чтение USB-пульта/джойстика на Windows без внешних зависимостей.

Основной путь — прямой HID-gamepad (``hidjoy.py``): у EdgeTX-пультов там
8 осей (X/Y/Z/R/U/V + S1/S2) и 24 кнопки, включая переключатели SA..SF.
Если HID-устройство не найдено или не открылось, остаётся winmm-фолбэк
(6 осей + кнопки). Значения нормируются в 0..1.
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

JOY_RETURNALL = 0x000000FF
MAX_AXES = 8
AXIS_NAMES = ("X", "Y", "Z", "R", "U", "V", "S1", "S2")
# HID-пульт EdgeTX отдаёт 8 осей и 24 кнопки. Это 32 «канала» для
# калибровки/назначения: X..S2, затем CH1..CH24.
BUTTON_COUNT = 24
CHANNEL_COUNT = MAX_AXES + BUTTON_COUNT
CHANNEL_LABELS = (
    "X", "Y", "Z", "R", "U", "V", "S1", "S2",
    "CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "CH8",
    "CH9", "CH10", "CH11", "CH12", "CH13", "CH14", "CH15", "CH16",
    "CH17", "CH18", "CH19", "CH20", "CH21", "CH22", "CH23", "CH24",
)

try:
    from .hidjoy import HidGamepad, HidInfo, enumerate_hid_gamepads
except Exception:  # noqa: BLE001
    HidGamepad = None
    HidInfo = None
    enumerate_hid_gamepads = None


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


if sys.platform == "win32":
    from ctypes import wintypes

    class JOYCAPSW(ctypes.Structure):
        _fields_ = [
            ("wMid", wintypes.WORD),
            ("wPid", wintypes.WORD),
            ("szPname", wintypes.WCHAR * 32),
            ("wXmin", wintypes.UINT), ("wXmax", wintypes.UINT),
            ("wYmin", wintypes.UINT), ("wYmax", wintypes.UINT),
            ("wZmin", wintypes.UINT), ("wZmax", wintypes.UINT),
            ("wNumButtons", wintypes.UINT),
            ("wPeriodMin", wintypes.UINT), ("wPeriodMax", wintypes.UINT),
            ("wRmin", wintypes.UINT), ("wRmax", wintypes.UINT),
            ("wUmin", wintypes.UINT), ("wUmax", wintypes.UINT),
            ("wVmin", wintypes.UINT), ("wVmax", wintypes.UINT),
            ("wCaps", wintypes.UINT),
            ("wMaxAxes", wintypes.UINT), ("wNumAxes", wintypes.UINT),
            ("wMaxButtons", wintypes.UINT),
            ("szRegKey", wintypes.WCHAR * 32),
            ("szOEMVxD", wintypes.WCHAR * 260),
        ]

    class JOYINFOEX(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("dwXpos", wintypes.DWORD), ("dwYpos", wintypes.DWORD),
            ("dwZpos", wintypes.DWORD), ("dwRpos", wintypes.DWORD),
            ("dwUpos", wintypes.DWORD), ("dwVpos", wintypes.DWORD),
            ("dwButtons", wintypes.DWORD),
            ("dwButtonNumber", wintypes.DWORD),
            ("dwPOV", wintypes.DWORD),
            ("dwReserved1", wintypes.DWORD),
            ("dwReserved2", wintypes.DWORD),
        ]

    try:
        _winmm = ctypes.WinDLL("winmm")
        _winmm.joyGetNumDevs.restype = wintypes.UINT
        _winmm.joyGetDevCapsW.argtypes = [
            ctypes.c_size_t, ctypes.POINTER(JOYCAPSW), wintypes.UINT]
        _winmm.joyGetDevCapsW.restype = wintypes.UINT
        _winmm.joyGetPosEx.argtypes = [
            wintypes.UINT, ctypes.POINTER(JOYINFOEX)]
        _winmm.joyGetPosEx.restype = wintypes.UINT
    except Exception:  # noqa: BLE001
        _winmm = None
else:
    _winmm = None
    JOYCAPSW = None
    JOYINFOEX = None


@dataclass(frozen=True)
class JoyDevice:
    id: int
    name: str
    num_axes: int
    mins: tuple[float, ...] = ()
    maxs: tuple[float, ...] = ()
    num_buttons: int = 0
    kind: str = "winmm"     # "hid" | "winmm"
    path: str = ""


@dataclass
class JoyState:
    axes: tuple[float, ...]
    buttons: int = 0
    pov: int = 0xFFFF


def list_devices() -> list[JoyDevice]:
    """HID-пульты (8 осей/24 кнопки), иначе winmm-джойстики."""
    if enumerate_hid_gamepads is not None:
        try:
            hids = enumerate_hid_gamepads()
        except Exception:  # noqa: BLE001
            hids = []
        if hids:
            return [
                JoyDevice(
                    id=i, name=info.name, num_axes=info.num_axes,
                    num_buttons=info.num_buttons, kind="hid",
                    path=info.path)
                for i, info in enumerate(hids)
            ]

    if _winmm is None:
        return []
    out: list[JoyDevice] = []
    try:
        n = min(16, int(_winmm.joyGetNumDevs()))
    except Exception:  # noqa: BLE001
        return []
    for i in range(n):
        caps = JOYCAPSW()
        try:
            rc = _winmm.joyGetDevCapsW(
                ctypes.c_size_t(i), ctypes.byref(caps), ctypes.sizeof(caps))
        except Exception:  # noqa: BLE001
            continue
        if rc != 0:
            continue
        name = str(caps.szPname).split("\x00", 1)[0].strip()
        mins = (caps.wXmin, caps.wYmin, caps.wZmin,
                caps.wRmin, caps.wUmin, caps.wVmin)
        maxs = (caps.wXmax, caps.wYmax, caps.wZmax,
                caps.wRmax, caps.wUmax, caps.wVmax)
        out.append(JoyDevice(
            id=i, name=name or ("Джойстик #%d" % i),
            num_axes=int(caps.wNumAxes),
            mins=tuple(float(x) for x in mins),
            maxs=tuple(float(x) for x in maxs),
            num_buttons=int(caps.wNumButtons)))
    return out


class Joystick:
    """Выбранное устройство + чтение состояния."""

    def __init__(self):
        self.device: JoyDevice | None = None
        self._hid = None

    def open(self, device: JoyDevice | None) -> bool:
        self.close()
        self.device = device
        if device is None:
            return False
        if device.kind == "hid":
            if HidGamepad is None:
                return False
            try:
                gp = HidGamepad(HidInfo(
                    path=device.path, name=device.name, vendor=0, product=0,
                    num_axes=device.num_axes,
                    num_buttons=device.num_buttons))
                if gp.open():
                    self._hid = gp
                    return True
                gp.close()
            except Exception:  # noqa: BLE001
                self._hid = None
            return False
        return _winmm is not None

    @property
    def alive(self) -> bool:
        """Открыт ли пульт и жив ли его поток чтения."""
        if self.device is None:
            return False
        if self.device.kind == "hid":
            return self._hid is not None and self._hid.alive
        return True

    def close(self) -> None:
        hid, self._hid = self._hid, None
        if hid is not None:
            try:
                hid.close()
            except Exception:  # noqa: BLE001
                pass
        self.device = None

    def read(self) -> JoyState | None:
        if self.device is None:
            return None
        if self.device.kind == "hid":
            # HID-устройство, которое не открылось, нельзя читать через
            # winmm по индексу: у него другая нумерация. Честно «нет связи».
            if self._hid is None:
                return None
            state = self._hid.read()
            if state is None:
                return None
            axes, buttons, pov = state
            return JoyState(tuple(axes), int(buttons), int(pov))
        if _winmm is None:
            return None
        info = JOYINFOEX()
        info.dwSize = ctypes.sizeof(JOYINFOEX)
        info.dwFlags = JOY_RETURNALL
        try:
            rc = _winmm.joyGetPosEx(
                ctypes.c_uint(self.device.id), ctypes.byref(info))
        except Exception:  # noqa: BLE001
            return None
        if rc != 0:
            return None
        raw = (info.dwXpos, info.dwYpos, info.dwZpos,
               info.dwRpos, info.dwUpos, info.dwVpos)
        axes = []
        mins = self.device.mins or tuple([0.0] * 6)
        maxs = self.device.maxs or tuple([65535.0] * 6)
        for i in range(min(6, MAX_AXES)):
            lo = mins[i] if i < len(mins) else 0.0
            hi = maxs[i] if i < len(maxs) else 65535.0
            if hi <= lo:
                lo, hi = 0.0, 65535.0
            axes.append(_clamp((raw[i] - lo) / max(1.0, hi - lo), 0.0, 1.0))
        while len(axes) < MAX_AXES:
            axes.append(0.5)
        return JoyState(tuple(axes), int(info.dwButtons), int(info.dwPOV))


def channel_values(state: JoyState | None) -> tuple[float, ...]:
    """8 осей + 24 кнопки = 32 канала для калибровки/назначения."""
    if state is None:
        return tuple([0.5] * MAX_AXES + [0.0] * BUTTON_COUNT)
    axes = list(state.axes[:MAX_AXES])
    axes.extend([0.5] * (MAX_AXES - len(axes)))
    btns = [1.0 if (state.buttons & (1 << i)) else 0.0
            for i in range(BUTTON_COUNT)]
    return tuple(axes + btns)


__all__ = [
    "AXIS_NAMES", "BUTTON_COUNT", "CHANNEL_COUNT", "CHANNEL_LABELS",
    "JoyDevice", "Joystick", "JoyState", "channel_values", "list_devices",
]
