"""Лёгкий 3D-вид FPV без внешних ассетов и карт.

Мир: x — север, y — восток, z — вверх; углы в градусах (yaw 0 = север,
по часовой; pitch > 0 — нос вверх; roll > 0 — правое крыло вниз).
Рисуем небо, солнце, землю с сеткой, полосу, несколько построек и деревьев,
а в виде «снаружи» — простую модель борта. Никаких тайлов и интернета:
симулятор должен работать из любой папки.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient,
)

NEAR = 0.8
MAX_COORD = 60000.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


@dataclass
class Pose:
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    roll: float


class Camera:
    def __init__(self, pose: Pose, w: int, h: int, fov_deg: float):
        self.pose = pose
        self.w = max(1, int(w))
        self.h = max(1, int(h))
        fov = math.radians(_clamp(float(fov_deg), 30.0, 150.0))
        self.focal = (self.w * 0.5) / max(0.05, math.tan(fov * 0.5))
        self.cx = self.w * 0.5
        self.cy = self.h * 0.5
        y = math.radians(pose.yaw)
        p = math.radians(pose.pitch)
        r = math.radians(pose.roll)
        self.cy_, self.sy_ = math.cos(y), math.sin(y)
        self.cp, self.sp = math.cos(p), math.sin(p)
        self.cr, self.sr = math.cos(r), math.sin(r)
        self.ox, self.oy, self.oz = pose.x, pose.y, pose.z

    def to_cam(self, x: float, y: float, z: float):
        dx = x - self.ox
        dy = y - self.oy
        dz = z - self.oz
        X = dx * self.cy_ + dy * self.sy_
        Y = -dx * self.sy_ + dy * self.cy_
        X2 = X * self.cp + dz * self.sp
        Z2 = -X * self.sp + dz * self.cp
        Y2 = Y * self.cr + Z2 * self.sr
        Z3 = -Y * self.sr + Z2 * self.cr
        return X2, Y2, Z3

    def project_cam(self, X: float, Y: float, Z: float):
        if X <= 1e-6:
            return None
        k = self.focal / X
        sx = self.cx + Y * k
        sy = self.cy - Z * k
        if not math.isfinite(sx) or not math.isfinite(sy):
            return None
        # Точки у самой near-плоскости дают огромные координаты.
        # Не выбрасываем весь полигон, а прижимаем к разумному пределу.
        return (_clamp(sx, -MAX_COORD, MAX_COORD),
                _clamp(sy, -MAX_COORD, MAX_COORD))

    def project(self, x: float, y: float, z: float):
        return self.project_cam(*self.to_cam(x, y, z))

    def _clip(self, pts):
        """Sutherland-Hodgman по X >= NEAR в координатах камеры."""
        if not pts:
            return []
        out = []
        n = len(pts)
        for i in range(n):
            a = pts[i]
            b = pts[(i + 1) % n]
            ina = a[0] >= NEAR
            inb = b[0] >= NEAR
            if ina:
                out.append(a)
            if ina != inb:
                t = (NEAR - a[0]) / (b[0] - a[0])
                out.append((NEAR,
                            a[1] + (b[1] - a[1]) * t,
                            a[2] + (b[2] - a[2]) * t))
        return out

    def poly(self, pts3):
        cam = [self.to_cam(*p) for p in pts3]
        clipped = self._clip(cam)
        if len(clipped) < 3:
            return None
        out = []
        for X, Y, Z in clipped:
            p = self.project_cam(X, Y, Z)
            if p is None:
                return None
            out.append(QPointF(p[0], p[1]))
        return QPolygonF(out)

    def segment(self, a3, b3):
        a = self.to_cam(*a3)
        b = self.to_cam(*b3)
        if a[0] < NEAR and b[0] < NEAR:
            return None
        if a[0] < NEAR:
            t = (NEAR - a[0]) / (b[0] - a[0])
            a = (NEAR, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
        if b[0] < NEAR:
            t = (NEAR - b[0]) / (a[0] - b[0])
            b = (NEAR, b[1] + (a[1] - b[1]) * t, b[2] + (a[2] - b[2]) * t)
        pa = self.project_cam(*a)
        pb = self.project_cam(*b)
        if pa is None or pb is None:
            return None
        return QPointF(pa[0], pa[1]), QPointF(pb[0], pb[1])


def _rot_body(dx, dy, dz, yaw, pitch, roll):
    """Вектор из связанных осей (вперёд/вправо/вверх) в мир."""
    y = math.radians(yaw)
    p = math.radians(pitch)
    r = math.radians(roll)
    # yaw
    x1 = dx * math.cos(y) - dy * math.sin(y)
    y1 = dx * math.sin(y) + dy * math.cos(y)
    z1 = dz
    # pitch
    x2 = x1 * math.cos(p) - z1 * math.sin(p)
    z2 = x1 * math.sin(p) + z1 * math.cos(p)
    # roll
    y3 = y1 * math.cos(r) + z2 * math.sin(r)
    z3 = -y1 * math.sin(r) + z2 * math.cos(r)
    return x2, y3, z3


def _shade(color: QColor, k: float) -> QColor:
    return QColor(int(_clamp(color.red() * k, 0, 255)),
                  int(_clamp(color.green() * k, 0, 255)),
                  int(_clamp(color.blue() * k, 0, 255)),
                  color.alpha())


class WorldRenderer:
    def __init__(self) -> None:
        rng = random.Random(20261125)
        # Постройки: x, y, ширина, глубина, высота
        self.buildings = [
            (180, 120, 40, 28, 16), (260, -150, 30, 22, 11),
            (430, 90, 55, 35, 24), (520, -260, 26, 26, 9),
            (-220, 180, 34, 24, 13), (-330, -120, 48, 30, 20),
            (90, -330, 22, 40, 8), (700, 320, 60, 40, 28),
        ]
        self.trees = []
        for _ in range(60):
            ang = rng.uniform(0, math.tau)
            dist = rng.uniform(80, 900)
            x = math.cos(ang) * dist
            y = math.sin(ang) * dist
            if abs(y) < 40 and abs(x) < 700:   # не на полосе
                continue
            self.trees.append((x, y, rng.uniform(6, 14)))
        self.clouds = [
            (-1200, 900, 900, 260, 90), (400, -1600, 1100, 340, 110),
            (1800, 700, 1000, 300, 100), (-900, -900, 800, 220, 80),
        ]

    # ── фон ─────────────────────────────────────────────────
    def render(self, p: QPainter, w: int, h: int, state, cfg: dict) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        cam, chase = self._camera(state, w, h, cfg)
        self._sky(p, w, h, cam)
        self._ground(p, cam)
        self._runway(p, cam)
        self._grid(p, cam)
        self._objects(p, cam)
        if chase:
            self._aircraft(p, cam, state, cfg)

    def _camera(self, state, w, h, cfg):
        fov = float(cfg.get("camera_fov_deg", 95.0))
        chase = bool(cfg.get("chase_view", False))
        if not chase:
            pose = Pose(float(state.x), float(state.y), float(state.alt),
                        float(state.yaw), float(state.pitch), float(state.roll))
            return Camera(pose, w, h, fov), False
        yaw, pitch, roll = float(state.yaw), float(state.pitch), float(state.roll)
        back = 9.0
        up = 2.6
        dx, dy, dz = -back, 0.0, up
        ox, oy, oz = _rot_body(dx, dy, dz, yaw, pitch, 0.0)
        pose = Pose(float(state.x) + ox, float(state.y) + oy,
                    float(state.alt) + oz, yaw, pitch, roll * 0.35)
        return Camera(pose, w, h, fov), True

    def _sky(self, p: QPainter, w: int, h: int, cam: Camera) -> None:
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(28, 66, 120))
        grad.setColorAt(0.45, QColor(78, 132, 190))
        grad.setColorAt(0.75, QColor(156, 196, 228))
        grad.setColorAt(1.0, QColor(196, 216, 226))
        p.fillRect(0, 0, w, h, grad)
        # Солнце
        az = math.radians(135.0)
        el = math.radians(28.0)
        sx = cam.ox + math.cos(el) * math.cos(az) * 6000.0
        sy = cam.oy + math.cos(el) * math.sin(az) * 6000.0
        sz = cam.oz + math.sin(el) * 6000.0
        sp = cam.project(sx, sy, sz)
        if sp is not None:
            rad = max(18.0, h * 0.035)
            glow = QRadialGradient(QPointF(sp[0], sp[1]), rad * 2.4)
            glow.setColorAt(0.0, QColor(255, 250, 220, 220))
            glow.setColorAt(0.35, QColor(255, 245, 200, 90))
            glow.setColorAt(1.0, QColor(255, 245, 200, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(sp[0], sp[1]), rad * 2.4, rad * 2.4)
            p.setBrush(QColor(255, 252, 230))
            p.drawEllipse(QPointF(sp[0], sp[1]), rad, rad)
        # Облака
        p.setPen(Qt.NoPen)
        for cx, cy, cz, cw, ch in self.clouds:
            base = cam.project(cam.ox + cx, cam.oy + cy, max(300.0, cz))
            if base is None:
                continue
            k = cam.focal / max(NEAR, cam.to_cam(cam.ox + cx, cam.oy + cy, cz)[0])
            rw = cw * k
            rh = ch * k
            if rw < 4 or rw > w * 4:
                continue
            p.setBrush(QColor(246, 250, 255, 150))
            for i in range(4):
                ox = (i - 1.5) * rw * 0.36
                p.drawEllipse(QPointF(base[0] + ox, base[1] + (i % 2) * rh * 0.18),
                              rw * 0.42, rh * 0.55)

    def _ground(self, p: QPainter, cam: Camera) -> None:
        cx, cy = cam.ox, cam.oy
        r = 30000.0
        quad = [(cx - r, cy - r, 0.0), (cx + r, cy - r, 0.0),
                (cx + r, cy + r, 0.0), (cx - r, cy + r, 0.0)]
        poly = cam.poly(quad)
        if poly is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(66, 96, 58))
            p.drawPolygon(poly)
        # Поля — крупные пятна, чтобы был виден бег земли.
        fields = [
            (-500, 200, 700, 500, QColor(88, 116, 62)),
            (400, 300, 900, 600, QColor(74, 104, 56)),
            (-300, -600, 800, 500, QColor(96, 122, 70)),
            (900, -500, 700, 700, QColor(70, 98, 52)),
            (-1100, 100, 600, 800, QColor(82, 108, 60)),
        ]
        for fx, fy, fw, fh, col in fields:
            pts = [(fx, fy, 0.02), (fx + fw, fy, 0.02),
                   (fx + fw, fy + fh, 0.02), (fx, fy + fh, 0.02)]
            poly = cam.poly(pts)
            if poly is not None:
                p.setBrush(col)
                p.drawPolygon(poly)

    def _grid(self, p: QPainter, cam: Camera) -> None:
        step = 100.0
        rng = 2500.0
        base_x = round(cam.ox / step) * step
        base_y = round(cam.oy / step) * step
        for i in range(-14, 15):
            gx = base_x + i * step
            gy = base_y + i * step
            for a, b, is_x in (
                ((gx, cam.oy - rng, 0.05), (gx, cam.oy + rng, 0.05), True),
                ((cam.ox - rng, gy, 0.05), (cam.ox + rng, gy, 0.05), False),
            ):
                seg = cam.segment(a, b)
                if seg is None:
                    continue
                d = abs(i) * step
                alpha = int(_clamp(150.0 - d * 0.05, 18.0, 120.0))
                p.setPen(QPen(QColor(190, 210, 180, alpha), 1.0))
                p.drawLine(seg[0], seg[1])

    def _runway(self, p: QPainter, cam: Camera) -> None:
        x0, x1 = -650.0, 650.0
        y0, y1 = -18.0, 18.0
        asphalt = [(x0, y0, 0.1), (x1, y0, 0.1), (x1, y1, 0.1), (x0, y1, 0.1)]
        poly = cam.poly(asphalt)
        if poly is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(52, 54, 58))
            p.drawPolygon(poly)
        # Осевая разметка
        p.setPen(QPen(QColor(232, 232, 220, 220), 2.0))
        for x in range(-600, 601, 60):
            seg = cam.segment((x, 0, 0.2), (x + 32, 0, 0.2))
            if seg is not None:
                p.drawLine(seg[0], seg[1])
        # Порог
        p.setPen(QPen(QColor(240, 240, 230, 220), 3.0))
        for y in (-14, -8, 0, 8, 14):
            seg = cam.segment((x0 + 6, y, 0.2), (x0 + 36, y, 0.2))
            if seg is not None:
                p.drawLine(seg[0], seg[1])

    # ── объекты ─────────────────────────────────────────────
    def _objects(self, p: QPainter, cam: Camera) -> None:
        items = []
        for b in self.buildings:
            cx = b[0] + b[2] * 0.5
            cy = b[1] + b[3] * 0.5
            d = math.hypot(cx - cam.ox, cy - cam.oy)
            items.append((d, "box", b))
        for t in self.trees:
            d = math.hypot(t[0] - cam.ox, t[1] - cam.oy)
            items.append((d, "tree", t))
        items.sort(key=lambda it: it[0], reverse=True)
        for d, kind, obj in items:
            if d > 4000.0:
                continue
            if kind == "box":
                self._box(p, cam, obj)
            else:
                self._tree(p, cam, obj)

    def _box(self, p: QPainter, cam: Camera, b) -> None:
        x, y, w, d, h = b
        pts = [(x, y, 0.0), (x + w, y, 0.0), (x + w, y + d, 0.0),
               (x, y + d, 0.0), (x, y, h), (x + w, y, h),
               (x + w, y + d, h), (x, y + d, h)]
        faces = [
            ((0, 1, 5, 4), (0, -1, 0), QColor(156, 148, 136)),
            ((1, 2, 6, 5), (1, 0, 0), QColor(172, 164, 150)),
            ((2, 3, 7, 6), (0, 1, 0), QColor(140, 132, 120)),
            ((3, 0, 4, 7), (-1, 0, 0), QColor(150, 142, 128)),
            ((4, 5, 6, 7), (0, 0, 1), QColor(184, 176, 162)),
        ]
        cx, cy, cz = x + w * 0.5, y + d * 0.5, h * 0.5
        drawn = []
        for idx, n, col in faces:
            vx, vy, vz = cx - cam.ox, cy - cam.oy, cz - cam.oz
            # нормаль в мире (оси не повёрнуты)
            if n[0] * vx + n[1] * vy + n[2] * vz <= 0.0:
                continue
            pts3 = [pts[i] for i in idx]
            poly = cam.poly(pts3)
            if poly is None:
                continue
            depth = sum(cam.to_cam(*q)[0] for q in pts3) / len(pts3)
            drawn.append((depth, poly, col))
        drawn.sort(key=lambda it: it[0], reverse=True)
        for _depth, poly, col in drawn:
            k = 0.75 + 0.25 * (_depth % 7) / 7.0
            p.setPen(Qt.NoPen)
            p.setBrush(_shade(col, k))
            p.drawPolygon(poly)

    def _tree(self, p: QPainter, cam: Camera, t) -> None:
        x, y, size = t
        base = cam.project(x, y, 0.0)
        top = cam.project(x, y, size * 1.6)
        if base is None or top is None:
            return
        h = base[1] - top[1]
        if h <= 1:
            return
        w = h * 0.42
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(54, 84, 44))
        p.drawPolygon(QPolygonF([
            QPointF(top[0], top[1]),
            QPointF(base[0] - w, base[1]),
            QPointF(base[0] + w, base[1]),
        ]))
        p.setBrush(QColor(76, 60, 40))
        p.drawRect(QRectF(base[0] - max(1.0, w * 0.12), base[1] - h * 0.18,
                          max(2.0, w * 0.24), h * 0.18))

    def _aircraft(self, p: QPainter, cam: Camera, state, cfg) -> None:
        x, y, z = float(state.x), float(state.y), float(state.alt)
        yaw, pitch, roll = float(state.yaw), float(state.pitch), float(state.roll)
        # Связанные координаты: вперёд (+x), вправо (+y), вверх (+z).
        def tr(dx, dy, dz):
            wx, wy, wz = _rot_body(dx, dy, dz, yaw, pitch, roll)
            return (x + wx, y + wy, z + wz)
        verts = {
            "nose": tr(1.1, 0, 0), "tail": tr(-1.1, 0, 0),
            "lw": tr(-0.1, -1.15, 0), "rw": tr(-0.1, 1.15, 0),
            "lwt": tr(-0.55, -1.05, 0.05), "rwt": tr(-0.55, 1.05, 0.05),
            "fin": tr(-1.05, 0, 0.35),
            "body_l": tr(-0.9, -0.13, 0), "body_r": tr(-0.9, 0.13, 0),
            "body_t": tr(-0.9, 0, 0.16),
        }
        faces = [
            (("nose", "lw", "lwt"), QColor(210, 214, 220), 0.95),
            (("nose", "lwt", "tail"), QColor(196, 202, 210), 0.85),
            (("nose", "tail", "rwt"), QColor(196, 202, 210), 1.0),
            (("nose", "rwt", "rw"), QColor(210, 214, 220), 1.05),
            (("tail", "fin", "body_t"), QColor(232, 86, 72), 0.9),
        ]
        drawn = []
        for names, col, k in faces:
            pts3 = [verts[n] for n in names]
            poly = cam.poly(pts3)
            if poly is None:
                continue
            depth = sum(cam.to_cam(*q)[0] for q in pts3) / len(pts3)
            drawn.append((depth, poly, col, k))
        drawn.sort(key=lambda it: it[0], reverse=True)
        for _d, poly, col, k in drawn:
            p.setPen(QPen(QColor(30, 34, 40, 180), 1.0))
            p.setBrush(_shade(col, k))
            p.drawPolygon(poly)


__all__ = ["WorldRenderer", "Camera", "Pose"]
