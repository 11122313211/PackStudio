"""PackStudio 合并升级的 bug 回归测试（先写失败用例，修复后转为守护）。

覆盖从 aiskill-packaging 调研中实锤的缺陷：

* A1  ``engine.base.tab_down`` RTL 分支底耳圆角弧扫过 270°（应为 90°）
* A2  ``exporters.pdf`` / ``exporters.pdf_spot`` 把 y-up 内容翻转（PDF 原生 y-up）
* SVG ``exporters.svg`` viewBox 直接用 y-up 坐标导致显示上下镜像
* ``validator.checks`` 出血/安全区两项恒 True 空壳
* ``validator.check_net_dimension_consistency`` 期望值漏减 shrink
* ``bom.models.blank_area_mm2`` 展开面积公式与引擎几何脱节
* 枕式/食品盒/屋顶盒重合零面积线段
* ``fold.model`` 山谷方向推断（翻板应向后折=山折）与水平面板折线角度
"""

from __future__ import annotations

import re
import unittest

import pytest

from packstudio.engines.packaging_engine.engine.base import SegmentBuilder, tab_down
from packstudio.engines.packaging_engine.engine.render import render
from packstudio.engines.packaging_engine.exporters import (
    dieline_to_pdf,
    dieline_to_spot_pdf,
    dieline_to_svg,
)
from packstudio.engines.packaging_engine.models.ir import (
    LAYER_BLEED,
    LAYER_CUT,
    LAYER_CREASE,
    LAYER_GLUE,
    CreaseSegment,
    Dieline,
    StructureSpec,
)
from packstudio.engines.packaging_engine.structure.registry import BOX_TYPES

L, W, H = 120.0, 60.0, 90.0
IMPLEMENTED_CODES = tuple(b.code for b in BOX_TYPES if b.implemented)
NON_TUBE_CODES = ("carton.box_v2.tray", "carton.box_v2.sleeve_tray", "carton.box_v2.rectangular_blank")


def make_spec(code: str = "carton.box_v2.straight", **overrides) -> StructureSpec:
    dims = {"length": L, "width": W, "height": H, "unit": "mm"}
    return StructureSpec.from_dict({"model_code": code, "dimensions": dims, **overrides})


def make_simple_dieline() -> Dieline:
    """cut 在 y=0、glue 竖线在 y=100..200 的可手算刀版（y-up）。"""
    segments = [
        CreaseSegment(0.0, 0.0, 100.0, 0.0, LAYER_CUT),
        CreaseSegment(50.0, 0.0, 50.0, 100.0, LAYER_CREASE, "dashed", 0.25),
        CreaseSegment(0.0, 100.0, 0.0, 200.0, LAYER_GLUE, "glue", 0.25),
    ]
    return Dieline("test.simple", tuple(segments), (0.0, 0.0, 100.0, 200.0), {})


# ---------------------------------------------------------------------------
# A1: tab_down RTL 底耳弧线
# ---------------------------------------------------------------------------


def _seg_endpoints(builder: SegmentBuilder) -> set[tuple[float, float]]:
    return {
        (round(v, 6), round(w, 6))
        for s in builder.segments
        for v, w in ((s.x1, s.y1), (s.x2, s.y2))
    }


def test_tab_down_rtl_mirrors_ltr():
    """RTL 遍历的圆角底耳必须是 LTR 版本的镜像（当前 270° 弧扫出多余回环）。"""
    ltr = SegmentBuilder()
    tab_down(ltr, 0.0, 40.0, 100.0, 12.0)
    rtl = SegmentBuilder()
    tab_down(rtl, 40.0, 0.0, 100.0, 12.0)
    mirrored = {(round(40.0 - x, 6), round(y, 6)) for x, y in _seg_endpoints(ltr)}
    assert _seg_endpoints(rtl) == mirrored, "RTL 底耳与 LTR 镜像不一致：弧线扫角错误"


@pytest.mark.parametrize("xl,xr,yb,h", [(0.0, 40.0, 100.0, 12.0), (40.0, 0.0, 100.0, 12.0)])
def test_tab_down_stays_within_bounding_box(xl, xr, yb, h):
    """圆角底耳的所有点必须落在 [y_bot, yb] 包围盒内。

    270° 弧会向上侵入翻板本体（最高点 = y_bot + 2r > yb）。
    """
    b = SegmentBuilder()
    tab_down(b, xl, xr, yb, h)
    assert b.segments, "底耳应产生几何"
    y_bot = yb - h
    ys = [v for s in b.segments for v in (s.y1, s.y2)]
    assert min(ys) >= y_bot - 1e-6, f"底耳低点越界：{min(ys):.3f} < {y_bot}"
    assert max(ys) <= yb + 1e-6, f"底耳高点越界（疑似 270° 弧）：{max(ys):.3f} > {yb}"


# ---------------------------------------------------------------------------
# A2: PDF / Spot PDF 方向与出血
# ---------------------------------------------------------------------------


def _pdf_line_ops(pdf: bytes) -> list[tuple[float, float, float, float]]:
    text = pdf.decode("ascii", "replace")
    return [
        tuple(float(v) for v in m)
        for m in re.findall(r"(-?[\d.]+) (-?[\d.]+) m (-?[\d.]+) (-?[\d.]+) l", text)
    ]


def test_pdf_preserves_y_up_direction():
    """glue 竖线（y-up 上部）在 PDF 页面上必须高于 cut 横线（y-up 底部）。"""
    pdf = dieline_to_pdf(make_simple_dieline())
    ops = _pdf_line_ops(pdf)
    assert ops, "PDF 内容流中没有线段"
    pt = 72.0 / 25.4
    page_h = (100.0 + 20.0) * pt
    glue_x = 0.0 * pt + 10.0 * pt  # glue 线 x=0 → 距页左 10mm
    glue_lines = [op for op in ops if abs(op[0] - op[2]) < 0.01 and abs(op[0] - glue_x) < 0.5]
    assert glue_lines, "找不到 glue 竖线（x≈10mm 处）"
    glue_top = max(max(op[1], op[3]) for op in glue_lines)
    cut_bottom = min(min(op[1], op[3]) for op in ops)
    assert glue_top > page_h / 2, f"glue 线顶 y={glue_top:.1f}pt 未过页中线，方向被翻转"
    assert cut_bottom < page_h / 2, "cut 线不在页面下半部"


def test_spot_pdf_bleed_is_ring_not_solid():
    """出血必须是切线外环形区（even-odd 双矩形），不能整面实心覆盖。"""
    spec = make_spec("carton.box_v2.lock_bottom")
    dieline = render(spec)
    pdf = dieline_to_spot_pdf(dieline, spec)
    assert b"f*" in pdf, "出血未使用 even-odd 环形填充"
    assert not re.search(rb"re\n[\d.\s-]+f\n", pdf), "出血仍是整面实心填充"
    # 环形区 = 外矩形（含 bleed）与内矩形（刀版边界）两个 re
    tail = pdf.split(b"/CS3 CS 1 SCN", 1)[1]
    rects = re.findall(rb"(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+) re", tail)
    assert len(rects) == 2, f"出血环形区需要内外两个矩形，实际 {len(rects)} 个"


# ---------------------------------------------------------------------------
# SVG y 方向
# ---------------------------------------------------------------------------


def test_svg_y_down_viewbox_and_coords():
    """SVG 是 y-down 媒介：导出必须翻转 y，使 glue（y-up 上部）显示在上方。"""
    svg = dieline_to_svg(make_simple_dieline())
    m = re.search(r'viewBox="([^"]+)"', svg)
    assert m and m.group(1) == "-10 -210 120 220"
    cut = re.search(r'data-layer="CUT"[^>]*y1="0"[^>]*y2="0"', svg)
    assert cut, "cut 线应保持 y'=0"
    assert re.search(r'data-layer="GLUE"[^>]*y1="-100"', svg), "glue 线 y 应翻转为 -100"
    assert re.search(r'data-layer="GLUE"[^>]*y2="-200"', svg), "glue 线 y 应翻转为 -200"
    # SVG 屏幕坐标 y 越小越靠上：glue 必须"高于" cut
    assert re.search(r'data-layer="GLUE"[^>]*y2="-200"', svg)


# ---------------------------------------------------------------------------
# 展开尺寸一致性（校验器公式 vs 引擎真值）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [c for c in IMPLEMENTED_CODES if c not in NON_TUBE_CODES])
def test_net_width_validator_uses_engine_truth(code):
    """校验器期望宽度必须含 -shrink，并用紧公差（1mm）而不是 8% 吸收。"""
    from packstudio.engines.packaging_engine.validator.checks import check_net_dimension_consistency

    spec = make_spec(code)
    dieline = render(spec)
    result = check_net_dimension_consistency(dieline, spec)
    assert result["passed"], f"{code}: {result['detail']}"
    net_w = dieline.bounds[2] - dieline.bounds[0]
    expected = 2 * L + 2 * W + spec.glue_width - spec.shrink
    assert abs(net_w - expected) <= 1.0, f"{code}: net_w={net_w:.2f} vs {expected:.2f}"


# ---------------------------------------------------------------------------
# 退化 / 重合线段
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", IMPLEMENTED_CODES)
def test_no_degenerate_duplicate_segments(code):
    """几何退化防线：

    * 同层同向重复线段 = bug（往返重合是合法的轮廓遍历方式，允许）；
    * CUT 与 CREASE 端点重合 = 既切又折，物理矛盾，禁止。
    """
    dieline = render(make_spec(code))
    directed: set[tuple] = set()
    undirected: dict[tuple, set[str]] = {}
    for s in dieline.segments:
        fwd = ((round(s.x1, 3), round(s.y1, 3)), (round(s.x2, 3), round(s.y2, 3)))
        dkey = (s.layer, fwd)
        assert dkey not in directed, f"{code} 同向重复线段 {fwd}（{s.layer}/{s.label}）"
        directed.add(dkey)
        undirected.setdefault(tuple(sorted(fwd)), set()).add(s.layer)
    clash = {k: v for k, v in undirected.items() if {"CUT", "CREASE"} <= v}
    assert not clash, f"{code} 切线与压线重合（既切又折）：{clash}"


# ---------------------------------------------------------------------------
# 校验器：出血/安全区不再是恒 True 空壳
# ---------------------------------------------------------------------------


def test_bleed_check_rejects_bleed_geometry_inside_cut():
    """出血几何若出现在切线边界内，校验必须失败。"""
    from packstudio.engines.packaging_engine.validator.checks import check_bleed_outside_cut

    spec = make_spec(bleed=3.0)
    segments = [
        CreaseSegment(0.0, 0.0, 100.0, 0.0, LAYER_CUT),
        CreaseSegment(-1.0, -1.0, 101.0, -1.0, LAYER_BLEED),  # 距切线仅 1mm < bleed 3mm
    ]
    dieline = Dieline("test.bleed", tuple(segments), (-1.0, -1.0, 101.0, 0.0), {})
    result = check_bleed_outside_cut(dieline, spec)
    assert result["passed"] is False, "出血几何在切线内却通过了校验"


def test_bleed_check_honest_skip_without_geometry():
    from packstudio.engines.packaging_engine.validator.checks import check_bleed_outside_cut

    spec = make_spec(bleed=3.0)
    dieline = render(spec)
    result = check_bleed_outside_cut(dieline, spec)
    assert "跳过" in result["detail"] or "导出层" in result["detail"], "无出血几何时应明示跳过原因"


def test_glue_tab_range_measures_real_geometry():
    """粘口宽度必须从几何量出（刀版 min_x 到粘口折线 x=0），并与 spec 一致。"""
    from packstudio.engines.packaging_engine.validator.checks import check_glue_tab_range

    spec = make_spec()
    dieline = render(spec)
    result = check_glue_tab_range(dieline, spec)
    assert result["passed"], result["detail"]
    assert f"{spec.glue_width:.1f}" in result["detail"], f"应报告实测粘口宽度：{result['detail']}"


# ---------------------------------------------------------------------------
# 折叠方向 / 角度推断
# ---------------------------------------------------------------------------


def _crease(label: str, x1=0.0, y1=0.0, x2=100.0, y2=0.0) -> CreaseSegment:
    return CreaseSegment(x1, y1, x2, y2, LAYER_CREASE, "dashed", 0.25, label)


def test_fold_direction_flaps_fold_behind():
    """插耳/防尘耳/锁舌/粘口向后折入盒内 = 山折；面板间竖折 = 谷折。"""
    from packstudio.engines.packaging_engine.fold.model import _fold_direction

    axis = ((0.0, 0.0), (100.0, 0.0))
    for label in ("CREASE_TUCK_TOP", "CREASE_TUCK_BOTTOM", "DUST_FLAP", "CREASE_BOTTOM_LOCK", "GLUE_FOLD"):
        assert _fold_direction(_crease(label), axis) == "mountain", label
    assert _fold_direction(_crease("CREASE_VERTICAL_PANEL"), ((0.0, 0.0), (0.0, 100.0))) == "valley"


def test_fold_angle_roof_ridge_is_panel_fold():
    """屋顶盒屋脊是水平面板折线（90°），不是翻板平折（180°）。"""
    from packstudio.engines.packaging_engine.fold.model import _fold_angle

    horizontal = ((0.0, 0.0), (100.0, 0.0))
    vertical = ((0.0, 0.0), (0.0, 100.0))
    assert _fold_angle(_crease("CREASE_ROOF_FRONT"), horizontal) == 90.0
    assert _fold_angle(_crease("CREASE_ROOF_BACK"), horizontal) == 90.0
    # 翻板类平折保持 180°
    assert _fold_angle(_crease("CREASE_TUCK_TOP"), horizontal) == 180.0
    assert _fold_angle(_crease("CREASE_FRONT_TOP_FLAP"), horizontal) == 180.0
    # 普通竖直面板折 90°
    assert _fold_angle(_crease("CREASE_LEFT_PANEL"), vertical) == 90.0


# ---------------------------------------------------------------------------
# BOM 展开面积与引擎几何一致
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", IMPLEMENTED_CODES)
def test_blank_area_matches_rendered_bounds(code):
    """BOM 展开面积必须等于引擎渲染刀版的实际外接矩形面积。"""
    from packstudio.engines.packaging_engine.bom.models import blank_area_mm2

    spec = make_spec(code)
    dieline = render(spec)
    min_x, min_y, max_x, max_y = dieline.bounds
    expected = (max_x - min_x) * (max_y - min_y)
    actual = blank_area_mm2(spec)
    assert abs(actual - expected) <= 1.0, f"{code}: blank_area={actual:.1f} vs bounds={expected:.1f}"


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
