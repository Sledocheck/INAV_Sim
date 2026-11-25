"""Интерфейс физического ядра ПК-симулятора."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from ..dynamics import Aircraft, SimConfig


class FdmError(RuntimeError):
    """Ядро физики недоступно или не смогло загрузить модель."""


class FdmEngine(Protocol):
    """Минимальный контракт: состояние борта + шаг интегрирования."""

    name: str

    def reset(self, cfg: "SimConfig") -> None:
        ...

    def step(self, dt: float, ch_us, wind=None) -> None:
        ...

    def close(self) -> None:
        ...


__all__ = ["FdmEngine", "FdmError"]
