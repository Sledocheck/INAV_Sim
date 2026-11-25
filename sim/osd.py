"""OSD, который рисует сам INAV, и его отрисовка поверх FPV.

INAV отправляет на UART3 (TCP 5762) кадры MSP_DISPLAYPORT: очистка экрана,
строки символов, «нарисовать кадр». Мы повторяем его сетку символов и
рисуем пиксельным шрифтом INAV (assets/font_inav.png). Никаких своих
значений OSD не подставляем — всё приходит из прошивки.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

# Размеры экранов INAV (MSP_DP_OPTIONS)
MODES = {
    0: (30, 16),   # SD_3016 / PAL
    1: (50, 18),   # HD_5018 / HDZero
    2: (30, 16),   # HD_3016
    3: (60, 22),   # HD_6022 / DJI WTF
    4: (53, 20),   # HD_5320 / Avatar
}

MSP_DP_RELEASE = 1
MSP_DP_CLEAR_SCREEN = 2
MSP_DP_WRITE_STRING = 3
MSP_DP_DRAW_SCREEN = 4
MSP_DP_OPTIONS = 5


class OsdScreen:
    """Сетка символов, которую присылает INAV."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cols = 30
        self.rows = 16
        self.grid = [[" "] * self.cols for _ in range(self.rows)]
        self.attr = [[0] * self.cols for _ in range(self.rows)]
        self.mode = 0
        self.frames = 0
        self.last_rx = 0.0
        self.connected = False
        self.version = ""

    def touch(self) -> None:
        with self._lock:
            self.last_rx = time.monotonic()
            self.connected = True

    def _clear(self) -> None:
        self.grid = [[" "] * self.cols for _ in range(self.rows)]
        self.attr = [[0] * self.cols for _ in range(self.rows)]

    def _resize(self, cols: int, rows: int) -> None:
        if cols == self.cols and rows == self.rows:
            return
        old, old_attr = self.grid, self.attr
        self.cols, self.rows = cols, rows
        self.grid = [[" "] * cols for _ in range(rows)]
        self.attr = [[0] * cols for _ in range(rows)]
        for r in range(min(rows, len(old))):
            for c in range(min(cols, len(old[r]))):
                self.grid[r][c] = old[r][c]
                self.attr[r][c] = old_attr[r][c]

    def apply(self, payload: bytes) -> None:
        if not payload:
            return
        sub = payload[0]
        with self._lock:
            self.last_rx = time.monotonic()
            self.connected = True
            if sub == MSP_DP_CLEAR_SCREEN:
                self._clear()
            elif sub == MSP_DP_WRITE_STRING and len(payload) >= 4:
                row = payload[1]
                col = payload[2]
                attr = payload[3]
                text = payload[4:].decode("latin-1", "replace")
                if row >= self.rows:
                    self._resize(self.cols, row + 1)
                if col + len(text) > self.cols:
                    self._resize(max(self.cols, col + len(text)), self.rows)
                for i, ch in enumerate(text):
                    if 0 <= col + i < self.cols:
                        self.grid[row][col + i] = ch
                        self.attr[row][col + i] = attr
            elif sub == MSP_DP_OPTIONS and len(payload) >= 3:
                mode = payload[2]
                self.mode = mode
                cols, rows = MODES.get(mode, (30, 16))
                self._resize(cols, rows)
            elif sub == MSP_DP_DRAW_SCREEN:
                self.frames += 1

    def snapshot(self):
        with self._lock:
            return (self.cols, self.rows,
                    [row[:] for row in self.grid],
                    [row[:] for row in self.attr],
                    float(self.last_rx), int(self.frames))


class OsdRenderer:
    """Пиксельный шрифт INAV и отрисовка сетки."""

    def __init__(self, font_path: Path) -> None:
        self._img = QImage(str(font_path))
        self._ok = not self._img.isNull()
        self._gw = max(1, self._img.width() // 2) if self._ok else 0
        self._gh = max(1, self._img.height() // 256) if self._ok else 0
        self._cache: dict[tuple[int, int, int], QPixmap] = {}
        self._shadow: dict[tuple[int, int, int], QPixmap] = {}

    def _glyph(self, code: int, page: int, cell_h: int) -> QPixmap | None:
        if not self._ok:
            return None
        key = (code & 0xFF, page & 1, cell_h)
        pm = self._cache.get(key)
        if pm is not None:
            return pm
        src = self._img.copy(page * self._gw, (code & 0xFF) * self._gh,
                             self._gw, self._gh)
        cw = max(1, int(round(cell_h * self._gw / self._gh)))
        pm = QPixmap.fromImage(src.scaled(cw, cell_h, Qt.IgnoreAspectRatio,
                                          Qt.FastTransformation))
        self._cache[key] = pm
        return pm

    def _shadow_pm(self, code: int, page: int,
                   cell_h: int) -> QPixmap | None:
        key = (code & 0xFF, page & 1, cell_h)
        pm = self._shadow.get(key)
        if pm is not None:
            return pm
        src = self._glyph(code, page, cell_h)
        if src is None:
            return None
        img = src.toImage().convertToFormat(QImage.Format_ARGB32)
        black = QImage(img.width(), img.height(), QImage.Format_ARGB32)
        black.fill(Qt.transparent)
        for y in range(img.height()):
            for x in range(img.width()):
                a = (img.pixel(x, y) >> 24) & 0xFF
                if a > 0:
                    black.setPixel(x, y, (a << 24) | 0x000000)
        pm = QPixmap.fromImage(black)
        self._shadow[key] = pm
        return pm

    def draw(self, p: QPainter, w: int, h: int, snap, scale: float = 1.0,
             stale: bool = False) -> None:
        cols, rows, grid, attr, _last, _frames = snap
        if not self._ok or cols <= 0 or rows <= 0:
            return
        cell_h = max(8.0, (h / float(rows)) * max(0.5, float(scale)))
        cell_w = cell_h * self._gw / max(1, self._gh)
        total_w = cell_w * cols
        total_h = cell_h * rows
        x0 = (w - total_w) * 0.5
        y0 = (h - total_h) * 0.5
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        blink_phase = int(time.monotonic() * 2.0) % 2 == 0
        for r in range(min(rows, len(grid))):
            row = grid[r]
            arow = attr[r] if r < len(attr) else [0] * cols
            for c in range(min(cols, len(row))):
                ch = row[c]
                if not ch or ch == " ":
                    continue
                a = arow[c] if c < len(arow) else 0
                if (a & 0x40) and not blink_phase:
                    continue
                code = ord(ch) & 0xFF
                page = a & 1
                gx = int(x0 + c * cell_w)
                gy = int(y0 + r * cell_h)
                sh = self._shadow_pm(code, page, int(cell_h))
                if sh is not None:
                    p.drawPixmap(gx + 1, gy + 1, sh)
                glyph = self._glyph(code, page, int(cell_h))
                if glyph is not None:
                    p.drawPixmap(gx, gy, glyph)
        if stale:
            p.setPen(QColor(255, 90, 90, 220))
            font = p.font()
            font.setPointSizeF(max(9.0, h / 55.0))
            p.setFont(font)
            p.drawText(QRect(0, int(y0 + total_h), w, int(cell_h)),
                       Qt.AlignHCenter | Qt.AlignVCenter,
                       "OSD: нет данных от INAV")


__all__ = ["OsdScreen", "OsdRenderer", "MODES"]
