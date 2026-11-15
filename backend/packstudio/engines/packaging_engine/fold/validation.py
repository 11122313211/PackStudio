"""Kawasaki / Maekawa flat-foldability validation for a dieline.

At every *interior* vertex (a point that is not on a CUT contour) the crease
pattern must satisfy two classical origami conditions to fold flat:

* **Maekawa** – the number of crease rays meeting at the vertex is even.
* **Kawasaki** – the alternating sum of the sector angles between consecutive
  crease rays equals 180° (within tolerance).

Boundary vertices (lying on a CUT contour) are exempt from Kawasaki because the
paper edge itself closes the sector; carton corners routinely have an odd
number of interior crease rays and are perfectly foldable.  A boundary vertex
is only reported when it carries a genuine interior crossing (degree >= 4 with
rays on both sides of the boundary), which never happens for the standard
cartons shipped with this skill.
"""

from __future__ import annotations

import math
from collections import defaultdict

from packstudio.engines.packaging_engine.models.ir import LAYER_CREASE, LAYER_CUT, Dieline

TOL = 1e-6
ANGLE_TOL_DEG = 0.5
MIN_INTERIOR_DEGREE = 4


def _key(x: float, y: float) -> tuple:
    return (round(x, 3), round(y, 3))


def _on_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float, tol: float = TOL) -> bool:
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length <= tol:
        return False
    cross = dx * (py - y1) - dy * (px - x1)
    if abs(cross) > tol * length:
        return False
    dot = (px - x1) * dx + (py - y1) * dy
    return -tol <= dot <= length * length + tol


def _angle_deg(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360.0


def _sector_angles(angles: list) -> list:
    ordered = sorted(a % 360.0 for a in angles)
    n = len(ordered)
    sectors = []
    for i in range(n):
        diff = ordered[(i + 1) % n] - ordered[i]
        if diff < 0:
            diff += 360.0
        sectors.append(diff)
    return sectors


def _collect_vertices(segments) -> dict:
    """Return ``vertex_key -> {"rays": [deg,...], "on_cut": bool}``."""
    points = set()
    for s in segments:
        points.add(_key(s.x1, s.y1))
        points.add(_key(s.x2, s.y2))

    # Add intersections between every pair of segments (transverse or T).
    n = len(segments)
    for i in range(n):
        a = segments[i]
        for j in range(i + 1, n):
            b = segments[j]
            inter = _intersection(a, b)
            if inter is not None:
                points.add(_key(inter[0], inter[1]))

    info = {}
    for kx, ky in points:
        rays = []
        on_cut = False
        for s in segments:
            x1, y1, x2, y2 = s.x1, s.y1, s.x2, s.y2
            if s.layer == LAYER_CUT and _on_segment(kx, ky, x1, y1, x2, y2):
                on_cut = True
            if s.layer != LAYER_CREASE:
                continue
            end0 = _key(x1, y1)
            end1 = _key(x2, y2)
            vk = (kx, ky)
            if vk == end0:
                rays.append(_angle_deg(x1, y1, x2, y2))
            elif vk == end1:
                rays.append(_angle_deg(x2, y2, x1, y1))
            elif _on_segment(kx, ky, x1, y1, x2, y2):
                rays.append(_angle_deg(kx, ky, x1, y1))
                rays.append(_angle_deg(kx, ky, x2, y2))
        if rays:
            info[(kx, ky)] = {"rays": rays, "on_cut": on_cut}
    return info


def _intersection(a, b):
    """Return the intersection point of segments a and b, or None."""
    x1, y1, x2, y2 = a.x1, a.y1, a.x2, a.y2
    x3, y3, x4, y4 = b.x1, b.y1, b.x2, b.y2
    rx, ry = x2 - x1, y2 - y1
    sx, sy = x4 - x3, y4 - y3
    denom = rx * sy - ry * sx
    if abs(denom) <= 1e-12:
        return None
    t = ((x3 - x1) * sy - (y3 - y1) * sx) / denom
    u = ((x3 - x1) * ry - (y3 - y1) * rx) / denom
    if -TOL <= t <= 1 + TOL and -TOL <= u <= 1 + TOL:
        return (x1 + t * rx, y1 + t * ry)
    return None


def check_kawasaki_maekawa(dieline: Dieline) -> dict:
    segments = [s for s in dieline.segments if s.layer in (LAYER_CREASE, LAYER_CUT)]
    if not segments:
        return {
            "id": "KAWASAKI_MAEKAWA",
            "name": "川崎/前泽折叠校验",
            "passed": True,
            "detail": "无压痕线，跳过",
        }

    vertices = _collect_vertices(segments)

    violations = []
    interior_checked = 0
    boundary_checked = 0

    for (kx, ky), data in sorted(vertices.items()):
        rays = data["rays"]
        degree = len(rays)
        if data["on_cut"]:
            # Boundary vertex: exempt from Kawasaki.  Only a genuine interior
            # crossing (rays on both sides of the paper edge) is reported.
            if degree >= MIN_INTERIOR_DEGREE:
                boundary_checked += 1
                sectors = _sector_angles(rays)
                odd = sum(sectors[i] for i in range(0, len(sectors), 2))
                even = sum(sectors[i] for i in range(1, len(sectors), 2))
                if (
                    degree % 2 != 0
                    or abs(odd - 180.0) > ANGLE_TOL_DEG
                    or abs(even - 180.0) > ANGLE_TOL_DEG
                ):
                    violations.append(
                        f"边界顶点({kx},{ky}) 度={degree} 川崎交替和={odd:.2f}°/{even:.2f}°"
                    )
            continue

        if degree == 0:
            continue

        # Maekawa: an interior vertex with an odd number of crease rays can
        # never fold flat.  Degree 2 is a straight crease line and is fine.
        if degree % 2 != 0:
            interior_checked += 1
            violations.append(f"内部顶点({kx},{ky}) 奇数度={degree}（Maekawa 失败）")
            continue

        if degree < MIN_INTERIOR_DEGREE:
            continue

        interior_checked += 1
        sectors = _sector_angles(rays)
        odd = sum(sectors[i] for i in range(0, len(sectors), 2))
        even = sum(sectors[i] for i in range(1, len(sectors), 2))
        if abs(odd - 180.0) > ANGLE_TOL_DEG or abs(even - 180.0) > ANGLE_TOL_DEG:
            violations.append(
                f"内部顶点({kx},{ky}) 川崎交替和={odd:.2f}°/{even:.2f}°（应为180°）"
            )

    passed = not violations
    if passed:
        detail = f"内部顶点检查={interior_checked}，边界顶点检查={boundary_checked}，无违规"
    else:
        detail = "; ".join(violations[:5])
        if len(violations) > 5:
            detail += f"（共 {len(violations)} 处）"

    return {
        "id": "KAWASAKI_MAEKAWA",
        "name": "川崎/前泽折叠校验",
        "passed": passed,
        "detail": detail,
    }
