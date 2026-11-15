"""Deterministic geometry primitives shared by every box builder.

All coordinates are absolute millimetres, y up. The LLM never touches this
module.

Each carton is built as ONE continuous closed outer contour (CUT), with fold
lines (CREASE) inside it and a glue tab (GLUE) on one edge. Apertures
(hang-tab / carry-handle) are separate closed CUT loops by design.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    CreaseSegment,
)

TOL = 1e-6


@dataclass
class SegmentBuilder:
    """Accumulates deterministic line segments per layer."""

    segments: list[CreaseSegment] = field(default_factory=list)

    def line(self, x1, y1, x2, y2, layer=LAYER_CUT, style="solid", width=0.35, label="") -> None:
        if abs(x1 - x2) < TOL and abs(y1 - y2) < TOL:
            return
        self.segments.append(CreaseSegment(x1, y1, x2, y2, layer=layer, style=style, width=width, label=label))

    def poly(self, points, layer=LAYER_CUT, style="solid", width=0.35, close=True, label="") -> None:
        pts = list(points)
        if len(pts) < 2:
            return
        for i in range(len(pts) - 1):
            self.line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], layer, style, width, label)
        if close and len(pts) > 2:
            self.line(pts[-1][0], pts[-1][1], pts[0][0], pts[0][1], layer, style, width, label)

    def arc(self, cx, cy, r, a0, a1, layer=LAYER_CUT, style="solid", width=0.35, steps=16, label="") -> None:
        r = max(r, 0.0)
        if r <= 0:
            return
        prev = None
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pt = (cx + r * math.cos(a), cy + r * math.sin(a))
            if prev is not None:
                self.line(prev[0], prev[1], pt[0], pt[1], layer, style, width, label)
            prev = pt

    def rect(self, x, y, w, h, layer=LAYER_CUT, style="solid", width=0.35, rx=0.0, label="") -> None:
        if rx > 0:
            r = min(rx, w / 2, h / 2)
            self.line(x + r, y, x + w - r, y, layer, style, width, label)
            self.line(x + w, y + r, x + w, y + h - r, layer, style, width, label)
            self.line(x + w - r, y + h, x + r, y + h, layer, style, width, label)
            self.line(x, y + h - r, x, y + r, layer, style, width, label)
            self.arc(x + r, y + r, r, 180, 270, layer, style, width, label=label)
            self.arc(x + w - r, y + r, r, 270, 360, layer, style, width, label=label)
            self.arc(x + w - r, y + h - r, r, 0, 90, layer, style, width, label=label)
            self.arc(x + r, y + h - r, r, 90, 180, layer, style, width, label=label)
        else:
            self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], layer, style, width, label=label)


def glue_angle(glue: float, deg: float = 15.0) -> float:
    return glue * math.tan(math.radians(deg))


def net_xs(length, width, shrink):
    x0 = 0.0
    x1 = length
    x2 = length + width
    x3 = 2 * length + width
    x4 = 2 * length + 2 * width - shrink
    return x0, x1, x2, x3, x4


def panel_rects(length, width, height, shrink, glue):
    """Canonical panel guide rectangles (metadata only, not drawn). y-up."""
    a = glue_angle(glue)
    x0, x1, x2, x3, x4 = net_xs(length, width, shrink)
    return (
        {"id": "panel-front", "name": "正面", "x": x0, "y": 0.0, "w": x1 - x0, "h": height},
        {"id": "panel-left", "name": "侧面A", "x": x1, "y": 0.0, "w": x2 - x1, "h": height},
        {"id": "panel-back", "name": "背面", "x": x2, "y": 0.0, "w": x3 - x2, "h": height},
        {"id": "panel-right", "name": "侧面B", "x": x3, "y": 0.0, "w": x4 - x3, "h": height},
        {"id": "panel-glue", "name": "糊口", "x": -glue, "y": a, "w": glue, "h": height - 2 * a},
    )


def tab_up(b, xl, xr, yb, height, layer=LAYER_CUT, radius=None):
    """Emit left side, rounded top, right side of a tab rising from yb.

    Does NOT emit the base (the fold line supplies it). Returns (start, end).
    """
    w = xr - xl
    if w <= 0 or height <= 0:
        return (xl, yb), (xr, yb)
    r = radius if radius is not None else min(height / 3.0, w / 4.0, 4.0)
    r = max(0.0, min(r, height / 2.0, w / 2.0))
    y_top = yb + height
    if r <= 0:
        b.line(xl, yb, xl, y_top, layer)
        b.line(xl, y_top, xr, y_top, layer)
        b.line(xr, y_top, xr, yb, layer)
        return (xl, yb), (xr, yb)
    b.line(xl, yb, xl, y_top - r, layer)
    b.arc(xl + r, y_top - r, r, 180, 90, layer)
    b.line(xl + r, y_top, xr - r, y_top, layer)
    b.arc(xr - r, y_top - r, r, 90, 0, layer)
    b.line(xr, y_top - r, xr, yb, layer)
    return (xl, yb), (xr, yb)


def tab_down(b, xl, xr, yb, height, layer=LAYER_CUT, radius=None):
    """Emit a tab descending from yb. Handles both LTR and RTL traversal.

    Does NOT emit the top (the fold line supplies it). Returns (start, end).
    """
    w = abs(xr - xl)
    if w <= 0 or height <= 0:
        return (xl, yb), (xr, yb)
    r = radius if radius is not None else min(height / 3.0, w / 4.0, 4.0)
    r = max(0.0, min(r, height / 2.0, w / 2.0))
    y_bot = yb - height
    if xl < xr:
        # left-to-right traversal
        if r <= 0:
            b.line(xl, yb, xl, y_bot, layer)
            b.line(xl, y_bot, xr, y_bot, layer)
            b.line(xr, y_bot, xr, yb, layer)
            return (xl, yb), (xr, yb)
        b.line(xl, yb, xl, y_bot + r, layer)
        b.arc(xl + r, y_bot + r, r, 180, 270, layer)
        b.line(xl + r, y_bot, xr - r, y_bot, layer)
        b.arc(xr - r, y_bot + r, r, 270, 360, layer)
        b.line(xr, y_bot + r, xr, yb, layer)
        return (xl, yb), (xr, yb)
    # right-to-left traversal (bottom contour goes right-to-left)
    if r <= 0:
        b.line(xl, yb, xl, y_bot, layer)
        b.line(xl, y_bot, xr, y_bot, layer)
        b.line(xr, y_bot, xr, yb, layer)
        return (xl, yb), (xr, yb)
    b.line(xl, yb, xl, y_bot + r, layer)
    b.arc(xl - r, y_bot + r, r, 0, -90, layer)
    b.line(xl - r, y_bot, xr + r, y_bot, layer)
    b.arc(xr + r, y_bot + r, r, 270, 180, layer)
    b.line(xr, y_bot + r, xr, yb, layer)
    return (xl, yb), (xr, yb)


def dust_up(b, xl, xr, yb, depth, layer=LAYER_CUT):
    """Rectangular dust flap rising from yb."""
    if depth <= 0:
        return
    b.line(xl, yb, xl, yb + depth, layer)
    b.line(xl, yb + depth, xr, yb + depth, layer)
    b.line(xr, yb + depth, xr, yb, layer)


def dust_down(b, xl, xr, yb, depth, layer=LAYER_CUT):
    """Rectangular dust flap descending from yb."""
    if depth <= 0:
        return
    b.line(xl, yb, xl, yb - depth, layer)
    b.line(xl, yb - depth, xr, yb - depth, layer)
    b.line(xr, yb - depth, xr, yb, layer)
