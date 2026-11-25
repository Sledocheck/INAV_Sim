"""Прямое чтение HID-пульта на Windows (без внешних зависимостей).

winmm отдаёт максимум 6 осей и не всегда показывает все кнопки. Сам пульт
(например, EdgeTX/Jumper) публикует HID-gamepad с 8 осями и 24 кнопками.
Здесь перечисляем такие устройства через SetupAPI и читаем input-отчёты
через hid.dll + фоновый поток.

На не-Windows модуль ничего не находит и не мешает winmm-фолбэку.
"""
from __future__ import annotations

import ctypes
import sys
import threading
from dataclasses import dataclass

# usages HID Generic Desktop -> наши индексы осей.
# X, Y, Z, Rz, Slider, Rx, Ry, Dial
_AXIS_BY_USAGE = {
    0x30: 0,   # X
    0x31: 1,   # Y
    0x32: 2,   # Z
    0x35: 3,   # Rz -> R
    0x36: 4,   # Slider -> U
    0x33: 5,   # Rx -> V
    0x34: 6,   # Ry -> S1
    0x37: 7,   # Dial -> S2
}
AXIS_COUNT = 8
BUTTON_COUNT = 24

if sys.platform == "win32":
    from ctypes import wintypes

    _hid = ctypes.WinDLL("hid")
    _setupapi = ctypes.WinDLL("setupapi")
    _kernel32 = ctypes.WinDLL("kernel32")

    DIGCF_PRESENT = 0x02
    DIGCF_DEVICEINTERFACE = 0x10
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    FILE_SHARE_READ = 0x01
    FILE_SHARE_WRITE = 0x02
    OPEN_EXISTING = 3
    GENERIC_READ = 0x80000000
    ERROR_IO_PENDING = 997
    HIDP_STATUS_SUCCESS = 0x00110000

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("InterfaceClassGuid", _GUID),
                    ("Flags", wintypes.DWORD),
                    ("Reserved", ctypes.c_size_t)]

    class _HIDD_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Size", wintypes.ULONG),
                    ("VendorID", wintypes.USHORT),
                    ("ProductID", wintypes.USHORT),
                    ("VersionNumber", wintypes.USHORT)]

    class _HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
            ("InputReportByteLength", wintypes.USHORT),
            ("OutputReportByteLength", wintypes.USHORT),
            ("FeatureReportByteLength", wintypes.USHORT),
            ("Reserved", wintypes.USHORT * 17),
            ("NumberLinkCollectionNodes", wintypes.USHORT),
            ("NumberInputButtonCaps", wintypes.USHORT),
            ("NumberInputValueCaps", wintypes.USHORT),
            ("NumberInputDataIndices", wintypes.USHORT),
            ("NumberOutputButtonCaps", wintypes.USHORT),
            ("NumberOutputValueCaps", wintypes.USHORT),
            ("NumberOutputDataIndices", wintypes.USHORT),
            ("NumberFeatureButtonCaps", wintypes.USHORT),
            ("NumberFeatureValueCaps", wintypes.USHORT),
            ("NumberFeatureDataIndices", wintypes.USHORT)]

    class _HIDP_DATA(ctypes.Structure):
        _fields_ = [("DataIndex", wintypes.USHORT),
                    ("Reserved", wintypes.USHORT),
                    ("RawValue", wintypes.ULONG)]

    class _Range(ctypes.Structure):
        _fields_ = [("UsageMin", wintypes.USHORT),
                    ("UsageMax", wintypes.USHORT),
                    ("StringMin", wintypes.USHORT),
                    ("StringMax", wintypes.USHORT),
                    ("DesignatorMin", wintypes.USHORT),
                    ("DesignatorMax", wintypes.USHORT),
                    ("DataIndexMin", wintypes.USHORT),
                    ("DataIndexMax", wintypes.USHORT)]

    class _NotRange(ctypes.Structure):
        _fields_ = [("Usage", wintypes.USHORT),
                    ("Reserved1", wintypes.USHORT),
                    ("StringIndex", wintypes.USHORT),
                    ("Reserved2", wintypes.USHORT),
                    ("DesignatorIndex", wintypes.USHORT),
                    ("Reserved3", wintypes.USHORT),
                    ("DataIndex", wintypes.USHORT),
                    ("Reserved4", wintypes.USHORT)]

    class _UsageUnion(ctypes.Union):
        _fields_ = [("Range", _Range), ("NotRange", _NotRange)]

    class _HIDP_BUTTON_CAPS(ctypes.Structure):
        _fields_ = [
            ("UsagePage", wintypes.USHORT),
            ("ReportID", ctypes.c_ubyte),
            ("IsAlias", ctypes.c_ubyte),
            ("BitField", wintypes.USHORT),
            ("LinkCollection", wintypes.USHORT),
            ("LinkUsagePage", wintypes.USHORT),
            ("LinkUsage", wintypes.USHORT),
            ("IsRange", ctypes.c_ubyte),
            ("IsStringRange", ctypes.c_ubyte),
            ("IsDesignatorRange", ctypes.c_ubyte),
            ("IsAbsolute", ctypes.c_ubyte),
            ("Reserved", wintypes.ULONG * 10),
            ("U", _UsageUnion)]

    class _HIDP_VALUE_CAPS(ctypes.Structure):
        _fields_ = [
            ("UsagePage", wintypes.USHORT),
            ("ReportID", ctypes.c_ubyte),
            ("IsAlias", ctypes.c_ubyte),
            ("BitField", wintypes.USHORT),
            ("LinkCollection", wintypes.USHORT),
            ("LinkUsagePage", wintypes.USHORT),
            ("LinkUsage", wintypes.USHORT),
            ("IsRange", ctypes.c_ubyte),
            ("IsStringRange", ctypes.c_ubyte),
            ("IsDesignatorRange", ctypes.c_ubyte),
            ("IsAbsolute", ctypes.c_ubyte),
            ("HasNull", ctypes.c_ubyte),
            ("Reserved", ctypes.c_ubyte),
            ("BitSize", wintypes.USHORT),
            ("ReportCount", wintypes.USHORT),
            ("Reserved2", wintypes.USHORT * 5),
            ("UnitsExp", wintypes.ULONG),
            ("Units", wintypes.ULONG),
            ("LogicalMin", wintypes.LONG),
            ("LogicalMax", wintypes.LONG),
            ("PhysicalMin", wintypes.LONG),
            ("PhysicalMax", wintypes.LONG),
            ("U", _UsageUnion)]

    _hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
    _hid.HidD_GetPreparsedData.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _hid.HidD_GetPreparsedData.restype = wintypes.BOOL
    _hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _hid.HidP_GetCaps.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
    _hid.HidP_GetCaps.restype = wintypes.ULONG
    _hid.HidP_GetButtonCaps.argtypes = [
        wintypes.DWORD, ctypes.POINTER(_HIDP_BUTTON_CAPS),
        ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p]
    _hid.HidP_GetButtonCaps.restype = wintypes.ULONG
    _hid.HidP_GetValueCaps.argtypes = [
        wintypes.DWORD, ctypes.POINTER(_HIDP_VALUE_CAPS),
        ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p]
    _hid.HidP_GetValueCaps.restype = wintypes.ULONG
    _hid.HidP_GetData.argtypes = [
        wintypes.DWORD, ctypes.POINTER(_HIDP_DATA),
        ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.ULONG]
    _hid.HidP_GetData.restype = wintypes.ULONG
    _hid.HidD_GetAttributes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
    _hid.HidD_GetAttributes.restype = wintypes.BOOL
    _hid.HidD_GetProductString.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
    _hid.HidD_GetProductString.restype = wintypes.BOOL

    _setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND,
        wintypes.DWORD]
    _setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    _setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(_GUID),
        wintypes.DWORD, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
    _setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    _setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p]
    _setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    _setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _kernel32.CancelIoEx.restype = wintypes.BOOL

else:  # pragma: no cover - Windows-only
    wintypes = None
    _hid = _setupapi = _kernel32 = None


@dataclass(frozen=True)
class HidInfo:
    """Найденный HID-gamepad."""

    path: str
    name: str
    vendor: int
    product: int
    num_axes: int
    num_buttons: int


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _open(path: str, access: int = 0):
    if _kernel32 is None:
        return None
    h = _kernel32.CreateFileW(
        path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
        OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE:
        return None
    return h


def _hid_paths():
    """Все HID-интерфейсы Windows."""
    if _hid is None or _setupapi is None:
        return
    guid = _GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))
    hdev = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE_VALUE:
        return
    try:
        i = 0
        while True:
            did = _SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not _setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(guid), i, ctypes.byref(did)):
                break
            i += 1
            need = wintypes.DWORD(0)
            _setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(need), None)
            if need.value <= 0:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), ctypes.byref(buf), need.value,
                    ctypes.byref(need), None):
                continue
            yield ctypes.wstring_at(ctypes.addressof(buf) + 4)
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(hdev)


def _caps(handle):
    pp = ctypes.c_void_p()
    if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(pp)):
        return None, None
    caps = _HIDP_CAPS()
    if _hid.HidP_GetCaps(pp, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
        return caps, pp
    _hid.HidD_FreePreparsedData(pp)
    return None, None


def _button_count(pp, caps) -> int:
    n = int(caps.NumberInputButtonCaps)
    if n <= 0:
        return 0
    arr = (_HIDP_BUTTON_CAPS * n)()
    ln = wintypes.USHORT(n)
    _hid.HidP_GetButtonCaps(0, arr, ctypes.byref(ln), pp)
    count = 0
    for i in range(ln.value):
        cap = arr[i]
        if cap.IsRange:
            r = cap.U.Range
            if r.UsageMax >= r.UsageMin:
                count += int(r.UsageMax - r.UsageMin + 1)
        else:
            count += 1
    return min(BUTTON_COUNT, count)


def _value_map(pp, caps) -> dict[int, tuple[int, int, int]]:
    """DataIndex -> (usage, logical_min, logical_max)."""
    n = int(caps.NumberInputValueCaps)
    if n <= 0:
        return {}
    arr = (_HIDP_VALUE_CAPS * n)()
    ln = wintypes.USHORT(n)
    _hid.HidP_GetValueCaps(0, arr, ctypes.byref(ln), pp)
    out = {}
    for i in range(ln.value):
        cap = arr[i]
        if cap.IsRange:
            r = cap.U.Range
            di = int(r.DataIndexMin)
            for usage in range(int(r.UsageMin), int(r.UsageMax) + 1):
                out[di] = (usage, int(cap.LogicalMin), int(cap.LogicalMax))
                di += 1
        else:
            r = cap.U.NotRange
            out[int(r.DataIndex)] = (
                int(r.Usage), int(cap.LogicalMin), int(cap.LogicalMax))
    return out


def _product_name(handle, vid: int, pid: int) -> str:
    buf = (wintypes.WCHAR * 256)()
    try:
        if _hid.HidD_GetProductString(
                handle, ctypes.byref(buf), ctypes.sizeof(buf)):
            name = str(buf.value).strip()
            if name:
                return name
    except Exception:  # noqa: BLE001
        pass
    return "HID-пульт %04X:%04X" % (vid, pid)


def _looks_like_gamepad(caps) -> bool:
    if caps is None:
        return False
    return (caps.UsagePage == 0x01 and caps.Usage in (0x04, 0x05)
            and caps.InputReportByteLength > 0
            and (caps.NumberInputValueCaps > 0
                 or caps.NumberInputButtonCaps > 0))


def enumerate_hid_gamepads() -> list[HidInfo]:
    """Найти HID-джойстики/геймпады, включая 8-осевые пульты EdgeTX."""
    if _hid is None:
        return []
    out: list[HidInfo] = []
    seen = set()
    for path in _hid_paths():
        if path in seen:
            continue
        seen.add(path)
        h = _open(path, 0)
        if h is None:
            continue
        try:
            attrs = _HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(attrs)
            if not _hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
                continue
            caps, pp = _caps(h)
            if not _looks_like_gamepad(caps):
                if pp:
                    _hid.HidD_FreePreparsedData(pp)
                continue
            buttons = _button_count(pp, caps)
            values = _value_map(pp, caps)
            _hid.HidD_FreePreparsedData(pp)
            name = _product_name(h, attrs.VendorID, attrs.ProductID)
            out.append(HidInfo(
                path=path, name=name, vendor=attrs.VendorID,
                product=attrs.ProductID,
                num_axes=min(AXIS_COUNT, len(values)) or AXIS_COUNT,
                num_buttons=buttons))
        except Exception:  # noqa: BLE001
            continue
        finally:
            _kernel32.CloseHandle(h)
    return out


class HidGamepad:
    """Фоновое чтение выбранного HID-геймпада."""

    def __init__(self, info: HidInfo):
        self.info = info
        self._handle = None
        self._pp = None
        self._caps = None
        self._buttons = 0
        self._values: dict[int, tuple[int, int, int]] = {}
        self._axes = [0.5] * AXIS_COUNT
        self._pressed = 0
        self._latest = False
        self._alive = False
        self._stop = False
        self._thread = None
        self._lock = threading.Lock()
        self._buf = None

    def open(self) -> bool:
        if _hid is None:
            return False
        self.close()
        self._handle = _open(self.info.path, GENERIC_READ)
        if self._handle is None:
            return False
        caps, pp = _caps(self._handle)
        if caps is None or not _looks_like_gamepad(caps):
            self.close()
            return False
        self._caps = caps
        self._pp = pp
        self._values = _value_map(pp, caps)
        self._buttons = _button_count(pp, caps)
        self._buf = (ctypes.c_ubyte * caps.InputReportByteLength)()
        self._axes = [0.5] * AXIS_COUNT
        self._pressed = 0
        self._latest = False
        self._stop = False
        self._alive = True
        self._thread = threading.Thread(
            target=self._reader, name="hid-joystick", daemon=True)
        self._thread.start()
        return True

    def _reader(self) -> None:
        h = self._handle
        buf = self._buf
        pp = self._pp
        caps = self._caps
        try:
            while not self._stop and h is not None:
                n = wintypes.DWORD(0)
                if not _kernel32.ReadFile(
                        h, ctypes.byref(buf), len(buf),
                        ctypes.byref(n), None) or n.value <= 0:
                    break
                axes = list(self._axes)
                buttons = 0
                data = (_HIDP_DATA * 64)()
                dn = wintypes.USHORT(64)
                if _hid.HidP_GetData(
                        0, data, ctypes.byref(dn), pp,
                        ctypes.byref(buf), len(buf)) == HIDP_STATUS_SUCCESS:
                    for j in range(dn.value):
                        item = data[j]
                        idx = int(item.DataIndex)
                        val = int(item.RawValue)
                        cap = self._values.get(idx)
                        if cap is not None:
                            usage, lo, hi = cap
                            axis = _AXIS_BY_USAGE.get(usage)
                            if axis is not None and hi > lo:
                                axes[axis] = _clamp01(
                                    (val - lo) / float(hi - lo))
                        elif idx < self._buttons:
                            if val:
                                buttons |= 1 << idx
                with self._lock:
                    self._axes = axes
                    self._pressed = buttons
                    self._latest = True
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._alive = False

    @property
    def alive(self) -> bool:
        """Жив ли фоновый поток чтения (устройство не отвалилось)."""
        return bool(self._alive)

    def read(self):
        """(axes[8], buttons_mask, pov) или None, если поток уже умер."""
        with self._lock:
            if not self._alive:
                return None
            return tuple(self._axes), self._pressed, 0xFFFF

    def close(self) -> None:
        self._stop = True
        self._alive = False
        h, self._handle = self._handle, None
        if h is not None and _kernel32 is not None:
            try:
                _kernel32.CancelIoEx(h, None)
            except Exception:  # noqa: BLE001
                pass
            try:
                _kernel32.CloseHandle(h)
            except Exception:  # noqa: BLE001
                pass
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        self._thread = None
        # preparsed-данные освобождаем только если поток уже завершился;
        # иначе оставляем их (поток всё ещё может обращаться к ним).
        if (self._pp is not None and _hid is not None
                and (t is None or not t.is_alive())):
            try:
                _hid.HidD_FreePreparsedData(self._pp)
            except Exception:  # noqa: BLE001
                pass
        self._pp = None


__all__ = ["AXIS_COUNT", "BUTTON_COUNT", "HidGamepad", "HidInfo",
           "enumerate_hid_gamepads"]
