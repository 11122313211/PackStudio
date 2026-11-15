"""Deterministic parametric geometry for the 9 implemented box types.

Each builder returns ``(cut, crease, glue, panels, bounds)`` where the three
builders are ``SegmentBuilder`` instances and ``panels`` is a tuple of panel
guide rectangles (metadata only).

Coordinate system: millimetres, y up, body occupies y in [0, height].
"""

from __future__ import annotations

from packstudio.engines.packaging_engine.engine.base import (
    SegmentBuilder,
    dust_down,
    dust_up,
    glue_angle,
    net_xs,
    panel_rects,
    tab_down,
    tab_up,
)
from packstudio.engines.packaging_engine.models.ir import LAYER_CREASE, LAYER_CUT, LAYER_GLUE


def _body(length, width, height, shrink, glue, tuck):
    x0, x1, x2, x3, x4 = net_xs(length, width, shrink)
    arch_h = width + shrink
    depth = (width + tuck + shrink) / 2
    return x0, x1, x2, x3, x4, arch_h, depth


def _vertical_folds(crease, x0, x1, x2, x3, height):
    """Panel-to-panel vertical folds at x1/x2/x3.

    x0（粘口折线）不在列：该线由 render 统一以 GLUE 层输出（GLUE_FOLD），
    避免两层重合线段。
    """
    del x0
    for x, label in ((x1, "CREASE_FRONT_SIDEA"), (x2, "CREASE_SIDEA_BACK"), (x3, "CREASE_BACK_SIDEB")):
        crease.line(x, 0, x, height, LAYER_CREASE, "dashed", 0.25, label)


def build_straight(spec):
    """Reverse Tuck End (RTE): top tuck on front, bottom tuck on back."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4, arch_h, depth = _body(L, W, H, shrink, glue, tuck)
    a = glue_angle(glue)

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # --- outer contour (clockwise) ---
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, arch_h)
    dust_up(b, x1, x2, H, depth)
    b.line(x2, H, x3, H)
    dust_up(b, x3, x4, H, depth)
    b.line(x4, H, x4, 0)
    dust_down(b, x4, x3, 0, depth)
    tab_down(b, x3, x2, 0, arch_h)
    dust_down(b, x2, x1, 0, depth)
    b.line(x1, 0, x0, 0)
    b.line(x0, 0, -glue, a)

    # --- creases ---
    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_TUCK")
    crease.line(x2, 0, x3, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_TUCK")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_TOP_DUST")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BOTTOM_DUST")
    crease.line(x3, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_TOP_DUST")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_BOTTOM_DUST")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -depth - arch_h, x4, H + max(depth, arch_h))


def build_lock_bottom(spec):
    """Lock bottom: top tuck on front; bottom has a locking tuck on back."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4, arch_h, depth = _body(L, W, H, shrink, glue, tuck)
    a = glue_angle(glue)
    lock_h = arch_h + tuck  # locking tab is deeper

    b = SegmentBuilder()
    crease = SegmentBuilder()

    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, arch_h)
    dust_up(b, x1, x2, H, depth)
    b.line(x2, H, x3, H)
    dust_up(b, x3, x4, H, depth)
    b.line(x4, H, x4, 0)
    dust_down(b, x4, x3, 0, depth)
    tab_down(b, x3, x2, 0, lock_h)
    dust_down(b, x2, x1, 0, depth)
    b.line(x1, 0, x0, 0)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_TUCK")
    crease.line(x2, 0, x3, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_LOCK")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_TOP_DUST")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BOTTOM_DUST")
    crease.line(x3, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_TOP_DUST")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_BOTTOM_DUST")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -depth - lock_h, x4, H + max(depth, arch_h))


def build_mailer(spec):
    """Crash-lock mailer: interlocking tabs top and bottom on front/back."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    # crash-lock 耳深与盒深 W 相关（与 crash_lock 同族公式），错误地挂 H
    # 会在高盒上折出过深的耳朵。
    tab_h = (W + spec.tuck_height + shrink) / 2

    b = SegmentBuilder()
    crease = SegmentBuilder()

    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, tab_h)
    b.line(x1, H, x2, H)
    tab_up(b, x2, x3, H, tab_h)
    b.line(x3, H, x4, H)
    b.line(x4, H, x4, 0)
    b.line(x4, 0, x3, 0)
    tab_down(b, x3, x2, 0, tab_h)
    b.line(x2, 0, x1, 0)
    tab_down(b, x1, x0, 0, tab_h)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_FRONT_TOP_LOCK")
    crease.line(x2, H, x3, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BACK_TOP_LOCK")
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_FRONT_BOTTOM_LOCK")
    crease.line(x2, 0, x3, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BACK_BOTTOM_LOCK")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -tab_h, x4, H + tab_h)


def build_top_cover(spec):
    """Top-cover: large lid tab on front (covers the top), bottom tuck on back."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4, arch_h, depth = _body(L, W, H, shrink, glue, tuck)
    a = glue_angle(glue)
    lid_h = W + shrink + tuck

    b = SegmentBuilder()
    crease = SegmentBuilder()

    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, lid_h)
    dust_up(b, x1, x2, H, depth)
    b.line(x2, H, x3, H)
    dust_up(b, x3, x4, H, depth)
    b.line(x4, H, x4, 0)
    dust_down(b, x4, x3, 0, depth)
    tab_down(b, x3, x2, 0, arch_h)
    dust_down(b, x2, x1, 0, depth)
    b.line(x1, 0, x0, 0)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_LID_FOLD")
    crease.line(x2, 0, x3, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_TUCK")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_TOP_DUST")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BOTTOM_DUST")
    crease.line(x3, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_TOP_DUST")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_BOTTOM_DUST")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -depth - arch_h, x4, H + max(depth, lid_h))


def build_same_direction_tuck(spec):
    """Same-direction tuck: both top and bottom tuck tabs on the front panel."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4, arch_h, depth = _body(L, W, H, shrink, glue, tuck)
    a = glue_angle(glue)

    b = SegmentBuilder()
    crease = SegmentBuilder()

    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, arch_h)
    dust_up(b, x1, x2, H, depth)
    b.line(x2, H, x3, H)
    dust_up(b, x3, x4, H, depth)
    b.line(x4, H, x4, 0)
    dust_down(b, x4, x3, 0, depth)
    b.line(x3, 0, x2, 0)
    dust_down(b, x2, x1, 0, depth)
    tab_down(b, x1, x0, 0, arch_h)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_TUCK")
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_TUCK")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_TOP_DUST")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BOTTOM_DUST")
    crease.line(x3, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_TOP_DUST")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_BOTTOM_DUST")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -depth - arch_h, x4, H + max(depth, arch_h))


def build_glue_bottom(spec):
    """Glue bottom: top tuck on front; bottom closed by a glue tab (no tuck)."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4, arch_h, depth = _body(L, W, H, shrink, glue, tuck)
    a = glue_angle(glue)

    b = SegmentBuilder()
    crease = SegmentBuilder()

    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, arch_h)
    dust_up(b, x1, x2, H, depth)
    b.line(x2, H, x3, H)
    dust_up(b, x3, x4, H, depth)
    b.line(x4, H, x4, 0)
    dust_down(b, x4, x3, 0, depth)
    b.line(x3, 0, x2, 0)
    dust_down(b, x2, x1, 0, depth)
    b.line(x1, 0, x0, 0)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_TUCK")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_TOP_DUST")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BOTTOM_DUST")
    crease.line(x3, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_TOP_DUST")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_BOTTOM_DUST")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -depth, x4, H + max(depth, arch_h))


def build_hang_tab(spec):
    """RTE + hanging aperture on the front panel."""
    cut, crease, glue_layer, panels, bounds = build_straight(spec)
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    slot_w, slot_h = 56.0, 16.0
    slot_x = (L - slot_w) / 2.0
    slot_y = H - W + 15.0
    cut.rect(slot_x, slot_y, slot_w, slot_h, LAYER_CUT, "solid", 0.35, rx=slot_h / 2)
    return cut, crease, glue_layer, panels, bounds


def build_carry_handle(spec):
    """RTE + carry-handle apertures (front, back, two sides)."""
    cut, crease, glue_layer, panels, bounds = build_straight(spec)
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    half = W / 2.0
    slot_w, slot_h = 30.0, 12.0
    # front + back horizontal slots
    cut.rect((L - slot_w) / 2, H - W + 15, slot_w, slot_h, LAYER_CUT, "solid", 0.35, rx=slot_h / 2)
    cut.rect(x2 + (L - slot_w) / 2, H - W + 15, slot_w, slot_h, LAYER_CUT, "solid", 0.35, rx=slot_h / 2)
    # side vertical slots
    cut.rect(x1 + half - slot_h / 2, H - 20, slot_h, 20, LAYER_CUT, "solid", 0.35, rx=slot_h / 2)
    cut.rect(x3 + half - slot_h / 2, H - 20, slot_h, 20, LAYER_CUT, "solid", 0.35, rx=slot_h / 2)
    return cut, crease, glue_layer, panels, bounds


def build_shipping_carton(spec):
    """Regular Slotted Container (RSC, FEFCO 0201)."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    flap = W / 2.0

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # outer contour (clockwise)
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    b.line(x0, H, x0, H + flap)
    b.line(x0, H + flap, x1, H + flap)
    b.line(x1, H + flap, x1, H)
    b.line(x1, H, x2, H)
    b.line(x2, H, x2, H + flap)
    b.line(x2, H + flap, x3, H + flap)
    b.line(x3, H + flap, x3, H)
    b.line(x3, H, x4, H)
    b.line(x4, H, x4, 0)
    b.line(x4, 0, x3, 0)
    b.line(x3, 0, x3, -flap)
    b.line(x3, -flap, x2, -flap)
    b.line(x2, -flap, x2, 0)
    b.line(x2, 0, x1, 0)
    b.line(x1, 0, x1, -flap)
    b.line(x1, -flap, x0, -flap)
    b.line(x0, -flap, x0, 0)
    b.line(x0, 0, -glue, a)

    # creases
    crease.line(x1, 0, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_FRONT_SIDEA")
    crease.line(x2, 0, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BACK")
    crease.line(x3, 0, x3, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BACK_SIDEB")
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_FRONT_TOP_FLAP")
    crease.line(x2, H, x3, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BACK_TOP_FLAP")
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_FRONT_BOTTOM_FLAP")
    crease.line(x2, 0, x3, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BACK_BOTTOM_FLAP")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -flap, x4, H + flap)


# ── Track B: extended box types (iteration 2) ────────────────────────────


def build_crash_lock(spec):
    """Crash-lock (飞机盒): top tuck on front; bottom has interlocking lock tabs."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    arch_h = W + shrink
    depth = (W + tuck + shrink) / 2
    lock_h = depth + tuck  # locking tabs are deeper

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise)
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, arch_h)
    dust_up(b, x1, x2, H, depth)
    b.line(x2, H, x3, H)
    dust_up(b, x3, x4, H, depth)
    b.line(x4, H, x4, 0)
    # Bottom: crash-lock tabs on front and back interlocking
    tab_down(b, x4, x3, 0, lock_h)
    # 背面板支撑板：原先直接沿 y=0 画轮廓直线，导致 CREASE_BOTTOM_LOCK_BACK
    # 与轮廓切口线重合（既切又折，物理矛盾）。补上标准深度的支撑板。
    tab_down(b, x3, x2, 0, depth)
    dust_down(b, x2, x1, 0, depth)
    tab_down(b, x1, x0, 0, lock_h)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_TUCK")
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_LOCK_FRONT")
    crease.line(x2, 0, x3, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_LOCK_BACK")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_TOP_DUST")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BOTTOM_DUST")
    crease.line(x3, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_TOP_DUST")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_BOTTOM_DUST")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -lock_h, x4, H + max(depth, arch_h))


def build_gable_top(spec):
    """Gable-top handle box (屋顶提手盒): triangular gable roof + carry handle."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    gable_h = W + shrink  # triangular peak height
    flap = W / 2.0  # bottom flap depth
    # 屋峰水平偏移不超过面板宽（W > 2L 时峰值会跑出面板外）。
    peak_dx = min(W / 2.0, x1 - x0)

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise)
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    # Gable roof: front panel rises to a peak
    b.line(x0, H, x0 + peak_dx, H + gable_h)  # up to peak
    b.line(x0 + peak_dx, H + gable_h, x2 - peak_dx, H + gable_h)  # peak top (front to back)
    b.line(x2 - peak_dx, H + gable_h, x2, H)  # down from peak to back top
    b.line(x2, H, x3, H)
    # Side panels flush at top (no dust flap on gable box)
    b.line(x3, H, x4, H)
    b.line(x4, H, x4, 0)
    b.line(x4, 0, x3, 0)
    tab_down(b, x3, x2, 0, flap)
    b.line(x2, 0, x1, 0)
    tab_down(b, x1, x0, 0, flap)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    # Roof fold line split at x1 to avoid degree-1 interior vertices
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_ROOF_FRONT")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_ROOF_BACK")
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_FLAP_FRONT")
    crease.line(x2, 0, x3, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_FLAP_BACK")

    # V-cut slit at (x1, H): separates the roof fold line from the side
    # panel boundary. Makes the junction a boundary vertex (exempt from
    # Kawasaki/Maekawa) — standard gable-carton construction.
    # 正反两条重合线段是刻意的“往返缝”（死端割缝），拓扑校验依赖成对边。
    b.line(x1, H, x1, H + 2, LAYER_CUT, "solid", 0.35, "SLIT_ROOF_FRONT")
    b.line(x1, H + 2, x1, H, LAYER_CUT, "solid", 0.35, "SLIT_ROOF_BACK")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -flap, x4, H + gable_h)


def build_pillow(spec):
    """Pillow box (枕式盒): tube with rounded closures on all 4 edges."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    arch_h = W + shrink

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise) with rounded closures on all edges
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, arch_h)
    tab_up(b, x1, x2, H, arch_h)
    b.line(x2, H, x3, H)
    tab_up(b, x3, x4, H, arch_h)
    b.line(x4, H, x4, 0)
    tab_down(b, x4, x3, 0, arch_h)
    b.line(x3, 0, x2, 0)
    tab_down(b, x2, x1, 0, arch_h)
    tab_down(b, x1, x0, 0, arch_h)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_FRONT")
    crease.line(x1, H, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_SIDEA")
    crease.line(x3, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_SIDEB")
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_FRONT")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_SIDEA")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_SIDEB")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -arch_h, x4, H + arch_h)


def build_tray(spec):
    """Open-top tray (托盘盒): cross-shaped contour with 4 wall flaps."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    wall = H  # wall height = the "height" dimension

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise): cross shape (base + 4 wall flaps)
    b.line(-glue, a, -glue, H - a)  # glue left edge (up)
    b.line(-glue, H - a, x0, H)  # glue top to base top-left
    b.line(x0, H, x0, H + wall)  # back wall left edge up
    b.line(x0, H + wall, x4, H + wall)  # back wall top across
    b.line(x4, H + wall, x4, H)  # back wall right edge down to base top-right
    b.line(x4, H, x4 + wall, H)  # right wall top edge
    b.line(x4 + wall, H, x4 + wall, 0)  # right wall right edge down
    b.line(x4 + wall, 0, x4, 0)  # right wall bottom to base bottom-right
    b.line(x4, 0, x4, -wall)  # front wall right edge down
    b.line(x4, -wall, x0, -wall)  # front wall bottom across
    b.line(x0, -wall, x0, 0)  # front wall left edge up to base bottom-left
    b.line(x0, 0, -glue, a)  # glue bottom edge

    # Creases: base perimeter (internal fold lines)
    crease.line(x0, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BASE_FRONT")
    crease.line(x0, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BASE_BACK")
    crease.line(x4, 0, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BASE_RIGHT")

    # Custom panel rects for the tray (base + 4 walls + glue)
    panels = (
        {"id": "panel-base", "name": "底面", "x": x0, "y": 0.0, "w": x4 - x0, "h": H},
        {"id": "panel-back", "name": "后壁", "x": x0, "y": H, "w": x4 - x0, "h": wall},
        {"id": "panel-front", "name": "前壁", "x": x0, "y": -wall, "w": x4 - x0, "h": wall},
        {"id": "panel-right", "name": "右壁", "x": x4, "y": 0.0, "w": wall, "h": H},
        {"id": "panel-glue", "name": "糊口", "x": -glue, "y": a, "w": glue, "h": H - 2 * a},
    )

    return b, crease, LAYER_GLUE, panels, (-glue, -wall, x4 + wall, H + wall)


def build_sleeve_tray(spec):
    """Sleeve + tray (套筒+托盘): wrapping sleeve tube for a four-wall tray."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise): simple sleeve tube rectangle
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    b.line(x0, H, x1, H)
    b.line(x1, H, x2, H)
    b.line(x2, H, x3, H)
    b.line(x3, H, x4, H)
    b.line(x4, H, x4, 0)
    b.line(x4, 0, x3, 0)
    b.line(x3, 0, x2, 0)
    b.line(x2, 0, x1, 0)
    b.line(x1, 0, x0, 0)
    b.line(x0, 0, -glue, a)

    # Creases: vertical panel folds
    crease.line(x1, 0, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_FRONT_SIDEA")
    crease.line(x2, 0, x2, H, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BACK")
    crease.line(x3, 0, x3, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BACK_SIDEB")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, 0, x4, H)


def build_food_box(spec):
    """Food box with hinged flip-top lid (翻盖食品盒)."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    tuck = spec.tuck_height
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    lid_h = W + shrink + tuck  # lid extends above the body

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise)
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    # Lid: front panel extends up and folds over
    tab_up(b, x0, x1, H, lid_h)
    b.line(x1, H, x2, H)
    b.line(x2, H, x3, H)
    b.line(x3, H, x4, H)
    b.line(x4, H, x4, 0)
    tab_down(b, x4, x3, 0, W + shrink)
    b.line(x3, 0, x2, 0)
    dust_down(b, x2, x1, 0, (W + tuck + shrink) / 2)
    tab_down(b, x1, x0, 0, W + shrink)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_LID_FOLD")
    # 背面板底边（x2..x3）是轮廓切口线，无翻板，不画压线。
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_TUCK")
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_BOTTOM_DUST")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_BOTTOM_LOCK")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -(W + shrink), x4, H + lid_h)


# ── Track C: third iteration box types ───────────────────────────────────


def build_fefco_0427(spec):
    """FEFCO 0427 roll-end tray with locking cover (卷边托盘锁扣盒)."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    arch_h = W + shrink
    dust_h = W / 2.0  # dust flap depth
    tab_h = W / 4.0  # locking tab depth on sides

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise): tube body with dust flaps and locking tabs
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    # Front panel top: dust flap up
    dust_up(b, x0, x1, H, dust_h)
    # Side A: locking tab on right side
    b.line(x1, H, x1, H + tab_h)  # locking tab up
    b.line(x1, H + tab_h, x2, H + tab_h)  # tab top
    b.line(x2, H + tab_h, x2, H)  # back to body top
    # Back panel top: cover flap up
    tab_up(b, x2, x3, H, arch_h)
    # Side B: locking tab on left side
    b.line(x3, H, x3, H + tab_h)
    b.line(x3, H + tab_h, x4, H + tab_h)
    b.line(x4, H + tab_h, x4, H)
    b.line(x4, H, x4, 0)
    # Bottom: dust flaps down
    dust_down(b, x4, x3, 0, dust_h)
    b.line(x3, 0, x2, 0)
    dust_down(b, x2, x1, 0, dust_h)
    b.line(x1, 0, x0, 0)
    dust_down(b, x0, -glue, 0, dust_h)
    b.line(-glue, 0, -glue, a)  # close glue tab

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_FRONT_DUST")
    crease.line(x2, H, x3, H, LAYER_CREASE, "dashed", 0.25, "CREASE_COVER_FOLD")
    # 底部压线放在实际翻板折线上（sideA/sideB 的防尘耳下方），
    # 原先画在 front/back 的切口直边上，既切又折。
    crease.line(x1, 0, x2, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEA_DUST_BOTTOM")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_SIDEB_DUST_BOTTOM")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -dust_h, x4, H + max(arch_h, tab_h))


def build_diamond(spec):
    """Diamond-faceted luxury box (菱形折面盒)."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    arch_h = W + shrink

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise): standard tube with top/bottom tuck flaps
    b.line(-glue, a, -glue, H - a)
    b.line(-glue, H - a, x0, H)
    tab_up(b, x0, x1, H, arch_h)
    b.line(x1, H, x2, H)
    tab_up(b, x2, x3, H, arch_h)
    b.line(x3, H, x4, H)
    b.line(x4, H, x4, 0)
    tab_down(b, x4, x3, 0, arch_h)
    b.line(x3, 0, x2, 0)
    tab_down(b, x2, x1, 0, arch_h)
    tab_down(b, x1, x0, 0, arch_h)
    b.line(x0, 0, -glue, a)

    _vertical_folds(crease, x0, x1, x2, x3, H)
    crease.line(x0, H, x1, H, LAYER_CREASE, "dashed", 0.25, "CREASE_TOP_FRONT")
    crease.line(x0, 0, x1, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_FRONT")
    crease.line(x3, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BOTTOM_SIDEB")
    # Diamond facet diagonals: one per side panel, endpoints on contour
    # with degree 3 (boundary exempt). No interior crossing.
    crease.line(x0, H, x1, 0, LAYER_CREASE, "dashed", 0.25, "DIAMOND_FACET_SIDEA")
    crease.line(x3, H, x4, 0, LAYER_CREASE, "dashed", 0.25, "DIAMOND_FACET_SIDEB")

    return b, crease, LAYER_GLUE, panel_rects(L, W, H, shrink, glue), (-glue, -arch_h, x4, H + arch_h)


def build_rectangular_blank(spec):
    """Four-wall tray with corner glue tabs (四壁托盘带角扣)."""
    L = spec.dimensions.length
    W = spec.dimensions.width
    H = spec.dimensions.height
    shrink = spec.shrink
    glue = spec.glue_width
    x0, x1, x2, x3, x4 = net_xs(L, W, shrink)
    a = glue_angle(glue)
    wall = H  # wall height
    tab_w = W / 4.0  # corner tab width

    b = SegmentBuilder()
    crease = SegmentBuilder()

    # Outer contour (clockwise): cross shape with corner tabs
    b.line(-glue, a, -glue, H - a)  # glue left edge
    b.line(-glue, H - a, x0, H)  # glue top to base top-left
    # Back wall left edge up, with corner tab
    b.line(x0, H, x0, H + wall)  # back wall left up
    b.line(x0, H + wall, x0 + tab_w, H + wall)  # corner tab top
    b.line(x0 + tab_w, H + wall, x0 + tab_w, H + wall + tab_w)  # tab corner up
    b.line(x0 + tab_w, H + wall + tab_w, x1 - tab_w, H + wall + tab_w)  # tab top across
    b.line(x1 - tab_w, H + wall + tab_w, x1 - tab_w, H + wall)  # tab corner down
    b.line(x1 - tab_w, H + wall, x4, H + wall)  # back wall top across
    b.line(x4, H + wall, x4, H)  # back wall right down
    b.line(x4, H, x4 + wall, H)  # right wall top
    b.line(x4 + wall, H, x4 + wall, 0)  # right wall right down
    b.line(x4 + wall, 0, x4, 0)  # right wall bottom
    b.line(x4, 0, x4, -wall)  # front wall right down
    b.line(x4, -wall, x0, -wall)  # front wall bottom across
    b.line(x0, -wall, x0, 0)  # front wall left up
    b.line(x0, 0, -glue, a)  # glue bottom

    # Creases: base perimeter + wall folds
    crease.line(x0, 0, x4, 0, LAYER_CREASE, "dashed", 0.25, "CREASE_BASE_FRONT")
    crease.line(x0, H, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BASE_BACK")
    crease.line(x4, 0, x4, H, LAYER_CREASE, "dashed", 0.25, "CREASE_BASE_RIGHT")

    # Custom panel rects
    panels = (
        {"id": "panel-base", "name": "底面", "x": x0, "y": 0.0, "w": x4 - x0, "h": H},
        {"id": "panel-back", "name": "后壁", "x": x0, "y": H, "w": x4 - x0, "h": wall},
        {"id": "panel-front", "name": "前壁", "x": x0, "y": -wall, "w": x4 - x0, "h": wall},
        {"id": "panel-right", "name": "右壁", "x": x4, "y": 0.0, "w": wall, "h": H},
        {"id": "panel-glue", "name": "糊口", "x": -glue, "y": a, "w": glue, "h": H - 2 * a},
    )

    return b, crease, LAYER_GLUE, panels, (-glue, -wall, x4 + wall, H + wall + tab_w)


BUILDERS = {
    "carton.box_v2.straight": build_straight,
    "carton.box_v2.lock_bottom": build_lock_bottom,
    "carton.box_v2.mailer": build_mailer,
    "carton.box_v2.top_cover": build_top_cover,
    "carton.box_v2.same_direction_tuck": build_same_direction_tuck,
    "carton.box_v2.glue_bottom": build_glue_bottom,
    "carton.box_v2.hang_tab": build_hang_tab,
    "carton.box_v2.carry_handle": build_carry_handle,
    "carton.box_v2.shipping_carton": build_shipping_carton,
    "carton.box_v2.crash_lock": build_crash_lock,
    "carton.box_v2.gable_top": build_gable_top,
    "carton.box_v2.pillow": build_pillow,
    "carton.box_v2.tray": build_tray,
    "carton.box_v2.sleeve_tray": build_sleeve_tray,
    "carton.box_v2.food_box": build_food_box,
    "carton.box_v2.fefco_0427": build_fefco_0427,
    "carton.box_v2.diamond": build_diamond,
    "carton.box_v2.rectangular_blank": build_rectangular_blank,
}
