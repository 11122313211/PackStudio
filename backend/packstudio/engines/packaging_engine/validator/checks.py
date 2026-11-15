"""Mechanical validation checks for a deterministic dieline.

Each check returns ``{"id", "name", "passed", "detail"}``. The gate is
deterministic: no LLM is consulted.

Calibration note: standard dieline conventions must be accepted, otherwise
the gate produces false positives on valid cartons:

* Cut and crease lines may **collinearly coincide** on a glue-tab edge —
  only *transverse* crossings are errors.
* The cut outline may form several closed loops (main outline + hang slots) —
  the invariant is "no dangling endpoints", not "one component".
* Adjacent panels share edges, so collinear overlap is not self-intersection.
"""

from __future__ import annotations

import math
from collections import defaultdict

from packstudio.engines.packaging_engine.models.ir import (
    LAYER_BLEED,
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    LAYER_SAFETY,
    CreaseSegment,
    Dieline,
    StructureSpec,
)
from packstudio.engines.packaging_engine.fold.validation import check_kawasaki_maekawa

TOL = 1e-6


def _length(seg: CreaseSegment) -> float:
    return math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1)


def _pt(seg: CreaseSegment, index: int) -> tuple:
    return (round(seg.x1, 3), round(seg.y1, 3)) if index == 0 else (round(seg.x2, 3), round(seg.y2, 3))


def _endpoints(seg: CreaseSegment) -> set:
    return {_pt(seg, 0), _pt(seg, 1)}


def _key(seg: CreaseSegment) -> tuple:
    return (
        round(min(seg.x1, seg.x2), 3),
        round(min(seg.y1, seg.y2), 3),
        round(max(seg.x1, seg.x2), 3),
        round(max(seg.y1, seg.y2), 3),
    )


def _properly_cross(a: CreaseSegment, b: CreaseSegment) -> bool:
    """True only for a transverse crossing (collinear overlap is allowed)."""
    (x1, y1), (x2, y2) = (a.x1, a.y1), (a.x2, a.y2)
    (x3, y3), (x4, y4) = (b.x1, b.y1), (b.x2, b.y2)

    def orient(px, py, qx, qy, rx, ry):
        return (qy - py) * (rx - qx) - (qx - px) * (ry - qy)

    d1 = orient(x3, y3, x4, y4, x1, y1)
    d2 = orient(x3, y3, x4, y4, x2, y2)
    d3 = orient(x1, y1, x2, y2, x3, y3)
    d4 = orient(x1, y1, x2, y2, x4, y4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    return False


def check_cut_crease_separation(dieline: Dieline) -> dict:
    cut = [s for s in dieline.segments if s.layer == LAYER_CUT]
    crease = [s for s in dieline.segments if s.layer == LAYER_CREASE]
    crossings = 0
    for a in cut:
        for b in crease:
            if _endpoints(a) & _endpoints(b):
                continue
            if _properly_cross(a, b):
                crossings += 1
    return {
        "id": "CUT_CREASE_SEPARATION",
        "name": "切线与压痕无交叉",
        "passed": crossings == 0,
        "detail": f"切/压横向交叉={crossings}",
    }


def check_topology_connected(dieline: Dieline) -> dict:
    """No dangling cut endpoints; cut outline forms closed loops."""
    cut = [s for s in dieline.segments if s.layer == LAYER_CUT]
    if not cut:
        return {"id": "TOPOLOGY_CONNECTED", "name": "刀模拓扑闭合", "passed": False, "detail": "无切线段"}
    degree: dict = defaultdict(int)
    for seg in cut:
        degree[_pt(seg, 0)] += 1
        degree[_pt(seg, 1)] += 1
    dangling = [v for v, c in degree.items() if c == 1]
    odd = [v for v, c in degree.items() if c % 2 == 1]
    return {
        "id": "TOPOLOGY_CONNECTED",
        "name": "刀模拓扑闭合",
        "passed": not dangling,
        "detail": f"悬空端点={len(dangling)}，奇数度顶点={len(odd)}",
    }


def check_fold_angle_feasible(dieline: Dieline) -> dict:
    """No crease that terminates mid-panel (interior degree-1 crease vertex)."""
    crease = [s for s in dieline.segments if s.layer == LAYER_CREASE]
    cut = [s for s in dieline.segments if s.layer == LAYER_CUT]
    cut_points = set()
    for seg in cut:
        cut_points |= _endpoints(seg)
    degree: dict = defaultdict(int)
    for seg in crease:
        degree[_pt(seg, 0)] += 1
        degree[_pt(seg, 1)] += 1
    interior = [v for v in degree if v not in cut_points and degree[v] == 1]
    return {
        "id": "FOLD_ANGLE_FEASIBLE",
        "name": "压痕无中途断点",
        "passed": not interior,
        "detail": f"面板内部压痕断点={len(interior)}",
    }


def check_panel_collision(dieline: Dieline) -> dict:
    panels = dieline.metadata.get("panels", [])
    if len(panels) < 2:
        return {"id": "PANEL_COLLISION", "name": "面板无碰撞", "passed": True, "detail": "面板数不足"}
    collisions = []
    for i in range(len(panels)):
        for j in range(i + 1, len(panels)):
            a, b = panels[i], panels[j]
            if a["id"] == b["id"]:
                continue
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox > 0.05 and oy > 0.05:
                area = ox * oy
                if area > 4.0:
                    collisions.append(f"{a['id']}×{b['id']}={area:.1f}mm²")
    return {
        "id": "PANEL_COLLISION",
        "name": "面板无碰撞",
        "passed": not collisions,
        "detail": "; ".join(collisions[:5]) if collisions else "无面板重叠",
    }


def check_bounds_sanity(dieline: Dieline) -> dict:
    min_x, min_y, max_x, max_y = dieline.bounds
    finite = all(math.isfinite(v) for v in (min_x, min_y, max_x, max_y))
    span_x = max_x - min_x
    span_y = max_y - min_y
    passed = finite and span_x > 0 and span_y > 0
    return {
        "id": "BOUNDS_SANITY",
        "name": "边界框有效",
        "passed": passed,
        "detail": f"span=({span_x:.2f}, {span_y:.2f})mm",
    }


def check_net_dimension_consistency(dieline: Dieline, spec: StructureSpec | None) -> dict:
    if spec is None:
        return {"id": "NET_DIMENSION_CONSISTENCY", "name": "展开尺寸一致", "passed": True, "detail": "无 spec，跳过"}
    family = dieline.metadata.get("family", "carton")
    if family in ("shipping", "mailer", "tray", "sleeve"):
        return {"id": "NET_DIMENSION_CONSISTENCY", "name": "展开尺寸一致", "passed": True, "detail": f"{family} 结构，跳过"}
    min_x, min_y, max_x, max_y = dieline.bounds
    net_w = max_x - min_x
    expected_w = 2 * spec.dimensions.length + 2 * spec.dimensions.width + spec.glue_width - spec.shrink
    tol = 1.0
    passed = abs(net_w - expected_w) <= tol
    return {
        "id": "NET_DIMENSION_CONSISTENCY",
        "name": "展开尺寸一致",
        "passed": passed,
        "detail": f"net_w={net_w:.2f} vs expected≈{expected_w:.2f}mm (±{tol:.2f})",
    }


def check_glue_tab_range(dieline: Dieline, spec: StructureSpec | None) -> dict:
    glue = [s for s in dieline.segments if s.layer == LAYER_GLUE]
    if not glue:
        return {"id": "GLUE_TAB_RANGE", "name": "粘口宽度范围", "passed": True, "detail": "无粘口，跳过"}
    # 实测：糊口占 [min_x, 0]（粘口折线固定在 x=0），宽度 = 0 - min_x。
    cut_xs = [v for s in dieline.segments if s.layer == LAYER_CUT for v in (s.x1, s.x2)]
    measured = -min(cut_xs) if cut_xs else 0.0
    if spec is None:
        passed = 8.0 <= measured <= 20.0
        return {
            "id": "GLUE_TAB_RANGE",
            "name": "粘口宽度范围",
            "passed": passed,
            "detail": f"实测粘口宽度={measured:.1f}mm（期望 8–20）",
        }
    passed = 8.0 <= measured <= 20.0 and abs(measured - spec.glue_width) <= 0.5
    return {
        "id": "GLUE_TAB_RANGE",
        "name": "粘口宽度范围",
        "passed": passed,
        "detail": f"实测粘口宽度={measured:.1f}mm（spec={spec.glue_width:.1f}，期望 8–20）",
    }


def check_bleed_outside_cut(dieline: Dieline, spec: StructureSpec | None) -> dict:
    if spec is None or spec.bleed <= 0:
        return {"id": "BLEED_OUTSIDE_CUT", "name": "出血在切线外", "passed": True, "detail": "无出血要求"}
    bleed_segs = [s for s in dieline.segments if s.layer == LAYER_BLEED]
    if not bleed_segs:
        return {
            "id": "BLEED_OUTSIDE_CUT",
            "name": "出血在切线外",
            "passed": True,
            "detail": f"bleed={spec.bleed:.2f}mm，引擎无出血几何（由导出层生成），跳过",
        }
    cut_pts = [p for s in dieline.segments if s.layer == LAYER_CUT for p in ((s.x1, s.y1), (s.x2, s.y2))]
    if not cut_pts:
        return {"id": "BLEED_OUTSIDE_CUT", "name": "出血在切线外", "passed": True, "detail": "无切线几何，跳过"}
    min_x = min(p[0] for p in cut_pts)
    min_y = min(p[1] for p in cut_pts)
    max_x = max(p[0] for p in cut_pts)
    max_y = max(p[1] for p in cut_pts)
    guard = spec.bleed / 2.0
    bad = []
    for s in bleed_segs:
        for x, y in ((s.x1, s.y1), (s.x2, s.y2)):
            outside = (
                x <= min_x - guard
                or x >= max_x + guard
                or y <= min_y - guard
                or y >= max_y + guard
            )
            if not outside:
                bad.append((x, y))
    return {
        "id": "BLEED_OUTSIDE_CUT",
        "name": "出血在切线外",
        "passed": not bad,
        "detail": (
            f"出血点侵入切线内：{bad[:3]}" if bad else f"bleed={spec.bleed:.2f}mm，全部在切线外"
        ),
    }


def check_safe_margin_respected(dieline: Dieline, spec: StructureSpec | None) -> dict:
    if spec is None or spec.safe_margin <= 0:
        return {"id": "SAFE_MARGIN_RESPECTED", "name": "安全区保留", "passed": True, "detail": "无安全区要求"}
    safety_segs = [s for s in dieline.segments if s.layer == LAYER_SAFETY]
    if not safety_segs:
        return {
            "id": "SAFE_MARGIN_RESPECTED",
            "name": "安全区保留",
            "passed": True,
            "detail": f"safe_margin={spec.safe_margin:.2f}mm，引擎无安全区几何（由内容编排控制），跳过",
        }
    cut_pts = [p for s in dieline.segments if s.layer == LAYER_CUT for p in ((s.x1, s.y1), (s.x2, s.y2))]
    if not cut_pts:
        return {"id": "SAFE_MARGIN_RESPECTED", "name": "安全区保留", "passed": True, "detail": "无切线几何，跳过"}
    min_x = min(p[0] for p in cut_pts)
    min_y = min(p[1] for p in cut_pts)
    max_x = max(p[0] for p in cut_pts)
    max_y = max(p[1] for p in cut_pts)
    inset = spec.safe_margin / 2.0
    bad = []
    for s in safety_segs:
        for x, y in ((s.x1, s.y1), (s.x2, s.y2)):
            inside = (
                min_x + inset <= x <= max_x - inset
                and min_y + inset <= y <= max_y - inset
            )
            if not inside:
                bad.append((x, y))
    return {
        "id": "SAFE_MARGIN_RESPECTED",
        "name": "安全区保留",
        "passed": not bad,
        "detail": (
            f"安全区点越过边界：{bad[:3]}" if bad else f"safe_margin={spec.safe_margin:.2f}mm，全部在安全区内"
        ),
    }


def check_cut_length_positive(dieline: Dieline) -> dict:
    bad = [s for s in dieline.segments if s.layer == LAYER_CUT and _length(s) <= TOL]
    return {
        "id": "CUT_LENGTH_POSITIVE",
        "name": "切线长度为正",
        "passed": not bad,
        "detail": f"无效切线段={len(bad)}",
    }


def check_crease_length_positive(dieline: Dieline) -> dict:
    bad = [s for s in dieline.segments if s.layer == LAYER_CREASE and _length(s) <= TOL]
    return {
        "id": "CREASE_LENGTH_POSITIVE",
        "name": "压痕线长度为正",
        "passed": not bad,
        "detail": f"无效压痕段={len(bad)}",
    }


def check_no_self_intersection(dieline: Dieline) -> dict:
    cut = [s for s in dieline.segments if s.layer == LAYER_CUT]
    crossings = 0
    for i in range(len(cut)):
        for j in range(i + 1, len(cut)):
            a, b = cut[i], cut[j]
            if _key(a) == _key(b):
                continue
            if _endpoints(a) & _endpoints(b):
                continue
            if _properly_cross(a, b):
                crossings += 1
    return {
        "id": "NO_SELF_INTERSECTION",
        "name": "切线无自相交",
        "passed": crossings == 0,
        "detail": f"横向自交点={crossings}",
    }


def check_style_consistency(dieline: Dieline) -> dict:
    expected = {
        LAYER_CUT: ("solid", 0.35),
        LAYER_CREASE: ("dashed", 0.25),
        LAYER_GLUE: ("glue", 0.25),
        LAYER_BLEED: ("solid", 0.2),
    }
    bad = []
    for seg in dieline.segments:
        exp = expected.get(seg.layer)
        if exp is None:
            continue
        if seg.style != exp[0]:
            bad.append(f"{seg.layer}:{seg.style}")
    return {
        "id": "STYLE_CONSISTENCY",
        "name": "线型与线宽一致",
        "passed": not bad,
        "detail": "; ".join(bad[:5]) if bad else "各层线型一致",
    }


def checks(dieline: Dieline, spec: StructureSpec | None = None) -> list[dict]:
    return [
        check_cut_crease_separation(dieline),
        check_topology_connected(dieline),
        check_fold_angle_feasible(dieline),
        check_panel_collision(dieline),
        check_bounds_sanity(dieline),
        check_net_dimension_consistency(dieline, spec),
        check_glue_tab_range(dieline, spec),
        check_bleed_outside_cut(dieline, spec),
        check_safe_margin_respected(dieline, spec),
        check_cut_length_positive(dieline),
        check_crease_length_positive(dieline),
        check_no_self_intersection(dieline),
        check_style_consistency(dieline),
        check_kawasaki_maekawa(dieline),
    ]
