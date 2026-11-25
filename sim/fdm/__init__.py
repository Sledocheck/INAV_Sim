"""Физическое ядро симулятора: JSBSim 6-DoF.

В отличие от основного проекта SkyLock, здесь JSBSim ставится обычным
``pip install jsbsim`` в виртуальное окружение симулятора. Если пакета нет,
``make_engine`` вернёт понятную ошибку, а окно настроек предложит установку.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from .base import FdmEngine, FdmError

_LAST_ERROR = ""


def jsbsim_info(refresh: bool = False) -> dict:
    info = {"installed": False, "version": "", "root": "", "path": "",
            "error": ""}
    try:
        spec = importlib.util.find_spec("jsbsim")
    except (ImportError, ValueError):
        spec = None
    if spec is None or not spec.origin:
        info["error"] = ("JSBSim не установлен. Выполните setup.bat или "
                         "pip install jsbsim")
        return info
    pkg = Path(spec.origin).parent
    version = ""
    for meta in pkg.parent.glob("jsbsim-*.dist-info/METADATA"):
        try:
            for line in meta.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
                if line.lower().startswith("version:"):
                    version = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    info.update(installed=True, version=version, root=str(pkg),
                path=str(pkg))
    return info


def jsbsim_installed() -> bool:
    return bool(jsbsim_info().get("installed"))


def last_error() -> str:
    return _LAST_ERROR


def make_engine(cfg, state):
    global _LAST_ERROR
    _LAST_ERROR = ""
    if not jsbsim_installed():
        _LAST_ERROR = jsbsim_info().get("error") or "JSBSim не установлен"
        raise FdmError(_LAST_ERROR)
    try:
        from .jsbsim_engine import JSBSimEngine
        return JSBSimEngine(cfg, state)
    except FdmError:
        raise
    except Exception as e:  # noqa: BLE001
        _LAST_ERROR = "%s: %s" % (type(e).__name__, e)
        raise FdmError(_LAST_ERROR) from e


__all__ = ["FdmEngine", "FdmError", "jsbsim_info", "jsbsim_installed",
           "last_error", "make_engine"]
