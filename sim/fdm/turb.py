"""Плавная турбулентность: одна интенсивность в м/с + порывы.

Не «белый шум»: сумма нескольких синусоид с разными частотами и фазами,
поэтому борт качает мягко, как в реальности. Интенсивность — в м/с, как
в Aerofly (`wind_turbulence`), а не проценты по каналам.
"""
from __future__ import annotations

import math
import random


class Turbulence:
    def __init__(self, seed: int = 20261119):
        self._rnd = random.Random(seed)
        self._ph = [self._rnd.uniform(0.0, 2.0 * math.pi)
                    for _ in range(9)]
        self.t = 0.0
        self.roll_deg = 0.0
        self.pitch_deg = 0.0
        self.roll_rate_deg = 0.0
        self.pitch_rate_deg = 0.0
        self.gx = 0.0
        self.gy = 0.0
        self.gz = 0.0

    def reset(self) -> None:
        self.t = 0.0
        self.roll_deg = self.pitch_deg = 0.0
        self.roll_rate_deg = self.pitch_rate_deg = 0.0
        self.gx = self.gy = self.gz = 0.0

    @staticmethod
    def _mix(t: float, f: float, ph: list[float], i: int) -> float:
        """Сумма трёх гармоник, нормированная примерно в [-1, 1]."""
        w = (0.62 * math.sin(2.0 * math.pi * f * t + ph[i])
             + 0.28 * math.sin(2.0 * math.pi * f * 2.31 * t + ph[i + 1])
             + 0.10 * math.sin(2.0 * math.pi * f * 4.17 * t + ph[i + 2]))
        return max(-1.0, min(1.0, w))

    def sample(self, dt: float, *, intensity_m_s: float, freq_hz: float,
               enabled: bool = True) -> None:
        dt = max(1e-3, min(0.1, float(dt)))
        self.t += dt
        i = max(0.0, float(intensity_m_s))
        if not enabled or i <= 0.0:
            self.roll_deg = self.pitch_deg = 0.0
            self.roll_rate_deg = self.pitch_rate_deg = 0.0
            self.gx = self.gy = self.gz = 0.0
            return
        # Плавные порывы: низкие частоты (периоды ~1–50 с) без тряски.
        # Интенсивность — в м/с, как wind_turbulence в Aerofly.
        f = max(0.02, min(2.0, float(freq_hz)))
        self.roll_deg = 1.5 * i * self._mix(self.t, f,
                                            self._ph, 0)
        self.pitch_deg = 0.45 * i * self._mix(self.t, f * 0.90,
                                              self._ph, 3)
        self.roll_rate_deg = 0.80 * i * self._mix(self.t, f * 0.70,
                                                  self._ph, 6)
        self.pitch_rate_deg = 0.30 * i * self._mix(self.t, f * 0.80,
                                                   self._ph, 1)
        self.gz = i * self._mix(self.t, f * 0.80, self._ph, 0)
        self.gx = 0.25 * i * self._mix(self.t, f * 0.55, self._ph, 3)
        self.gy = 0.45 * i * self._mix(self.t, f * 0.65, self._ph, 6)


__all__ = ["Turbulence"]
