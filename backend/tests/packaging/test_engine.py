"""Unit tests for the deterministic dieline engine.

Covers ``engine.base`` (SegmentBuilder / tab / panel helpers),
``engine.cartons`` (the fifteen parametric box builders) and
``engine.render`` (``render`` / ``render_from_dict``).

The engine is deterministic: the same spec always yields the same geometry.
These tests lock that behaviour in place and catch regressions.
"""

from __future__ import annotations

import math
import unittest
from collections import defaultdict

from packstudio.engines.packaging_engine.engine.base import net_xs, panel_rects
from packstudio.engines.packaging_engine.engine.cartons import BUILDERS
from packstudio.engines.packaging_engine.engine.render import render, render_from_dict
from packstudio.engines.packaging_engine.exceptions import (
    RequestValidationError,
    StructureNotImplementedError,
)
from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    CreaseSegment,
    Dimensions,
    Dieline,
    StructureSpec,
)
from packstudio.engines.packaging_engine.structure.registry import BOX_TYPES, model_choices

L = 120.0
W = 60.0
H = 90.0
EPS = 1e-6

IMPLEMENTED_CODES = (
    "carton.box_v2.straight",
    "carton.box_v2.lock_bottom",
    "carton.box_v2.mailer",
    "carton.box_v2.top_cover",
    "carton.box_v2.same_direction_tuck",
    "carton.box_v2.glue_bottom",
    "carton.box_v2.hang_tab",
    "carton.box_v2.carry_handle",
    "carton.box_v2.shipping_carton",
    "carton.box_v2.crash_lock",
    "carton.box_v2.gable_top",
    "carton.box_v2.pillow",
    "carton.box_v2.tray",
    "carton.box_v2.sleeve_tray",
    "carton.box_v2.food_box",
    "carton.box_v2.fefco_0427",
    "carton.box_v2.diamond",
    "carton.box_v2.rectangular_blank",
)


def make_spec(
    code: str = "carton.box_v2.straight",
    length: float = L,
    width: float = W,
    height: float = H,
    unit: str = "mm",
    **kwargs,
) -> StructureSpec:
    """Build a StructureSpec, forwarding extra keys as top-level params."""
    data = {
        "model_code": code,
        "dimensions": {"length": length, "width": width, "height": height, "unit": unit},
    }
    data.update(kwargs)
    return StructureSpec.from_dict(data)


def _cut(dl: Dieline) -> list[CreaseSegment]:
    return [s for s in dl.segments if s.layer == LAYER_CUT]


def _crease(dl: Dieline) -> list[CreaseSegment]:
    return [s for s in dl.segments if s.layer == LAYER_CREASE]


def _pt(seg: CreaseSegment, index: int) -> tuple:
    if index == 0:
        return (round(seg.x1, 3), round(seg.y1, 3))
    return (round(seg.x2, 3), round(seg.y2, 3))


def _geom_key(seg: CreaseSegment) -> tuple:
    return (round(seg.x1, 3), round(seg.y1, 3), round(seg.x2, 3), round(seg.y2, 3))


def _components(segments: list[CreaseSegment]) -> list[list[tuple]]:
    """Connected components of an undirected segment graph (rounded points)."""
    adj: dict = defaultdict(set)
    for seg in segments:
        p, q = _pt(seg, 0), _pt(seg, 1)
        adj[p].add(q)
        adj[q].add(p)
    seen: set = set()
    components: list[list[tuple]] = []
    for start in list(adj):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp: list[tuple] = []
        while stack:
            u = stack.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
        components.append(comp)
    return components


class AllBoxTypesTest(unittest.TestCase):
    """Structural guarantees shared by every implemented box type."""

    def test_registry_declares_exactly_the_implemented_builders(self) -> None:
        self.assertEqual(tuple(BUILDERS.keys()), IMPLEMENTED_CODES)
        implemented = sorted(b.code for b in BOX_TYPES if b.implemented)
        self.assertEqual(implemented, sorted(IMPLEMENTED_CODES))
        # The 9 implemented types must all be "available"; the custom entry is not_implemented.
        available = [m for m in model_choices() if m["status"] == "available"]
        self.assertEqual(len(available), len(IMPLEMENTED_CODES))

    def test_every_box_type_renders_successfully(self) -> None:
        for code in IMPLEMENTED_CODES:
            with self.subTest(code=code):
                dl = render(make_spec(code))
                self.assertIsInstance(dl, Dieline)
                self.assertEqual(dl.model_code, code)
                self.assertGreater(len(dl.segments), 0)

    def test_every_box_type_has_cut_and_crease_segments(self) -> None:
        for code in IMPLEMENTED_CODES:
            with self.subTest(code=code):
                dl = render(make_spec(code))
                self.assertGreater(len(_cut(dl)), 0, "expected CUT segments")
                self.assertGreater(len(_crease(dl)), 0, "expected CREASE segments")

    def test_bounds_are_finite_with_positive_spans(self) -> None:
        for code in IMPLEMENTED_CODES:
            with self.subTest(code=code):
                min_x, min_y, max_x, max_y = render(make_spec(code)).bounds
                self.assertEqual(len((min_x, min_y, max_x, max_y)), 4)
                for value in (min_x, min_y, max_x, max_y):
                    self.assertTrue(math.isfinite(value), f"non-finite bound {value}")
                self.assertGreater(max_x - min_x, 0)
                self.assertGreater(max_y - min_y, 0)

    def test_net_width_matches_2l_2w_plus_glue(self) -> None:
        """Carton nets span 2*L + 2*W + glue (minus the shrink allowance)."""
        for code in IMPLEMENTED_CODES:
            if code in ("carton.box_v2.tray", "carton.box_v2.sleeve_tray", "carton.box_v2.rectangular_blank"):
                continue  # cross/sleeve contours have different net width
            with self.subTest(code=code):
                spec = make_spec(code)
                dl = render(spec)
                net_w = dl.bounds[2] - dl.bounds[0]
                expected = 2 * spec.dimensions.length + 2 * spec.dimensions.width + spec.glue_width
                self.assertAlmostEqual(net_w, expected - spec.shrink, delta=EPS)

    def test_cut_and_crease_layers_are_disjoint(self) -> None:
        for code in IMPLEMENTED_CODES:
            with self.subTest(code=code):
                dl = render(make_spec(code))
                allowed = {LAYER_CUT, LAYER_CREASE, LAYER_GLUE}
                self.assertTrue(all(s.layer in allowed for s in dl.segments))
                cut_geom = {_geom_key(s) for s in _cut(dl)}
                crease_geom = {_geom_key(s) for s in _crease(dl)}
                self.assertEqual(cut_geom & crease_geom, set())

    def test_panel_rects_do_not_overlap(self) -> None:
        for code in IMPLEMENTED_CODES:
            with self.subTest(code=code):
                panels = render(make_spec(code)).metadata["panels"]
                ids = [p["id"] for p in panels]
                self.assertEqual(len(ids), len(set(ids)))
                for p in panels:
                    self.assertGreater(p["w"], 0)
                    self.assertGreater(p["h"], 0)
                for i in range(len(panels)):
                    for j in range(i + 1, len(panels)):
                        a, b = panels[i], panels[j]
                        ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                        oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                        self.assertLessEqual(max(ox, 0.0) * max(oy, 0.0), EPS)

    def test_glue_tab_sits_on_the_left_edge(self) -> None:
        for code in IMPLEMENTED_CODES:
            with self.subTest(code=code):
                spec = make_spec(code)
                dl = render(spec)
                cut_min_x = min(min(s.x1, s.x2) for s in _cut(dl))
                self.assertLess(cut_min_x, 0.0)
                self.assertAlmostEqual(cut_min_x, -spec.glue_width, delta=0.5)
                glue_panel = [p for p in dl.metadata["panels"] if p["id"] == "panel-glue"]
                self.assertEqual(len(glue_panel), 1)
                self.assertAlmostEqual(glue_panel[0]["x"], -spec.glue_width, delta=1e-6)
                self.assertAlmostEqual(glue_panel[0]["w"], spec.glue_width, delta=1e-6)

    def test_render_from_dict_matches_render(self) -> None:
        spec = make_spec("carton.box_v2.lock_bottom")
        dl_from_render = render(spec)
        dl_from_dict = render_from_dict(
            {
                "model_code": "carton.box_v2.lock_bottom",
                "dimensions": {"length": L, "width": W, "height": H},
            }
        )
        self.assertEqual(dl_from_render.model_code, dl_from_dict.model_code)
        self.assertEqual(len(dl_from_render.segments), len(dl_from_dict.segments))
        self.assertEqual(dl_from_render.bounds, dl_from_dict.bounds)


class SpecializedGeometryTest(unittest.TestCase):
    """Box-type-specific geometry that is easy to regress."""

    def test_hang_tab_adds_one_aperture_loop(self) -> None:
        dl = render(make_spec("carton.box_v2.hang_tab"))
        loops = _components(_cut(dl))
        self.assertEqual(len(loops), 2, "main contour + one hang aperture")

    def test_carry_handle_adds_multiple_aperture_loops(self) -> None:
        dl = render(make_spec("carton.box_v2.carry_handle"))
        loops = _components(_cut(dl))
        self.assertGreaterEqual(len(loops), 3)
        self.assertEqual(len(loops), 5, "main contour + four handle apertures")

    def test_shipping_carton_produces_rsc_flaps(self) -> None:
        spec = make_spec("carton.box_v2.shipping_carton")
        dl = render(spec)
        x0, x1, x2, x3, x4 = net_xs(spec.dimensions.length, spec.dimensions.width, spec.shrink)
        flap = spec.dimensions.width / 2.0

        def has_horizontal(y: float, x_lo: float, x_hi: float) -> bool:
            for s in _cut(dl):
                if abs(s.y1 - s.y2) > 1e-9 or abs(s.y1 - y) > 1e-9:
                    continue
                a, b = sorted((s.x1, s.x2))
                if b - a <= 1e-6:
                    continue
                if a >= x_lo - 1e-6 and b <= x_hi + 1e-6:
                    return True
            return False

        # Top flaps rise above the body; bottom flaps drop below it.
        self.assertTrue(has_horizontal(H + flap, x0, x1))
        self.assertTrue(has_horizontal(H + flap, x2, x3))
        self.assertTrue(has_horizontal(-flap, x0, x1))
        self.assertTrue(has_horizontal(-flap, x2, x3))
        self.assertGreater(dl.bounds[3], H)
        self.assertLess(dl.bounds[1], 0.0)


class ValidationErrorTest(unittest.TestCase):
    """Input validation and the NOT_IMPLEMENTED path."""

    def test_unknown_model_code_raises_structure_not_implemented(self) -> None:
        for bad in ("carton.box_v2.does_not_exist", "no.such.box"):
            with self.subTest(model_code=bad):
                with self.assertRaises(StructureNotImplementedError) as ctx:
                    render(make_spec(bad))
                self.assertEqual(ctx.exception.code, "STRUCTURE_NOT_IMPLEMENTED")

    def test_empty_model_code_raises_validation_error(self) -> None:
        with self.assertRaises(RequestValidationError):
            render(make_spec(""))

    def test_declared_but_unimplemented_model_code_raises(self) -> None:
        with self.assertRaises(StructureNotImplementedError) as ctx:
            render(make_spec("carton.box_v2.custom"))
        self.assertEqual(ctx.exception.code, "STRUCTURE_NOT_IMPLEMENTED")

    def test_render_from_dict_propagates_not_implemented(self) -> None:
        with self.assertRaises(StructureNotImplementedError):
            render_from_dict(
                {
                    "model_code": "carton.box_v2.nope",
                    "dimensions": {"length": 100, "width": 50, "height": 50},
                }
            )

    def test_zero_and_negative_dimensions_raise(self) -> None:
        cases = [
            {"length": 0.0, "width": 50.0, "height": 50.0},
            {"length": -10.0, "width": 50.0, "height": 50.0},
            {"length": 100.0, "width": 0.0, "height": 50.0},
            {"length": 100.0, "width": 50.0, "height": -1.0},
        ]
        for dims in cases:
            with self.subTest(dimensions=dims):
                with self.assertRaises(RequestValidationError):
                    StructureSpec.from_dict(
                        {"model_code": "carton.box_v2.straight", "dimensions": dims}
                    )

    def test_non_numeric_and_unsupported_unit_raise(self) -> None:
        with self.assertRaises(RequestValidationError):
            StructureSpec.from_dict(
                {"model_code": "carton.box_v2.straight", "dimensions": {"length": "abc", "width": 50, "height": 50}}
            )
        with self.assertRaises(RequestValidationError):
            StructureSpec.from_dict(
                {
                    "model_code": "carton.box_v2.straight",
                    "dimensions": {"length": 100, "width": 50, "height": 50, "unit": "furlong"},
                }
            )

    def test_missing_dimensions_raise(self) -> None:
        with self.assertRaises(RequestValidationError):
            StructureSpec.from_dict({"model_code": "carton.box_v2.straight"})


class UnitConversionTest(unittest.TestCase):
    def test_dimensions_cm_are_normalized_to_mm(self) -> None:
        cm = Dimensions.from_dict({"length": 12.0, "width": 5.0, "height": 3.0, "unit": "cm"})
        self.assertEqual(cm.unit, "mm")
        self.assertAlmostEqual(cm.length, 120.0, places=6)
        self.assertAlmostEqual(cm.width, 50.0, places=6)
        self.assertAlmostEqual(cm.height, 30.0, places=6)

    def test_dimensions_in_are_normalized_to_mm(self) -> None:
        inch = Dimensions.from_dict({"length": 4.0, "width": 2.0, "height": 1.0, "unit": "in"})
        self.assertEqual(inch.unit, "mm")
        self.assertAlmostEqual(inch.length, 4 * 25.4, places=6)
        self.assertAlmostEqual(inch.width, 2 * 25.4, places=6)
        self.assertAlmostEqual(inch.height, 25.4, places=6)

    def test_rendered_metadata_reports_mm(self) -> None:
        dl = render(make_spec("carton.box_v2.straight", unit="cm"))
        dims = dl.metadata["dimensions_mm"]
        self.assertAlmostEqual(dims["length"], 1200.0, places=3)
        self.assertAlmostEqual(dims["width"], 600.0, places=3)
        self.assertAlmostEqual(dims["height"], 900.0, places=3)

    def test_unit_conversion_scales_geometry(self) -> None:
        # Body dimensions scale exactly; tuck/glue/shrink are absolute mm offsets
        # so the net width scales approximately (within the fixed-offset tolerance).
        base = render(make_spec("carton.box_v2.straight"))
        scaled = render(make_spec("carton.box_v2.straight", unit="cm"))
        base_net_w = base.bounds[2] - base.bounds[0]
        scaled_net_w = scaled.bounds[2] - scaled.bounds[0]
        # The fixed mm offsets (glue + shrink ≈ 11.5mm) don't scale with the body,
        # so the net width scales approximately. Tolerance = 9 × (glue + shrink).
        offset = 9.0 * 11.5
        self.assertAlmostEqual(scaled_net_w, 10 * base_net_w, delta=offset + 10.0)
        # The body height (the dominant dimension) scales exactly.
        self.assertAlmostEqual(scaled.metadata["dimensions_mm"]["height"], 10 * base.metadata["dimensions_mm"]["height"], delta=1e-3)


class ParameterApplicationTest(unittest.TestCase):
    def test_process_parameters_land_in_metadata(self) -> None:
        dl = render(
            make_spec(
                "carton.box_v2.straight",
                board_thickness=0.5,
                bleed=6.0,
                safe_margin=4.0,
                glue_width=15.0,
            )
        )
        params = dl.metadata["parameters_mm"]
        self.assertAlmostEqual(params["board_thickness"], 0.5, places=6)
        self.assertAlmostEqual(params["bleed"], 6.0, places=6)
        self.assertAlmostEqual(params["safe_margin"], 4.0, places=6)
        self.assertAlmostEqual(params["glue_width"], 15.0, places=6)

    def test_glue_width_moves_the_glue_tab(self) -> None:
        dl = render(make_spec("carton.box_v2.straight", glue_width=20.0))
        cut_min_x = min(min(s.x1, s.x2) for s in _cut(dl))
        self.assertAlmostEqual(cut_min_x, -20.0, delta=EPS)
        glue_panel = [p for p in dl.metadata["panels"] if p["id"] == "panel-glue"][0]
        self.assertAlmostEqual(glue_panel["x"], -20.0, delta=EPS)
        self.assertAlmostEqual(glue_panel["w"], 20.0, delta=EPS)

    def test_default_glue_width_is_applied(self) -> None:
        dl = render(make_spec("carton.box_v2.straight"))
        cut_min_x = min(min(s.x1, s.x2) for s in _cut(dl))
        self.assertAlmostEqual(cut_min_x, -11.0, delta=EPS)

    def test_panel_rects_helper_matches_metadata(self) -> None:
        spec = make_spec("carton.box_v2.straight")
        dl = render(spec)
        self.assertEqual(dl.metadata["panels"], panel_rects(L, W, H, spec.shrink, spec.glue_width))


class SegmentBuilderTest(unittest.TestCase):
    """Direct coverage of the deterministic geometry helpers."""

    def test_segment_builder_drops_zero_length_lines(self) -> None:
        from packstudio.engines.packaging_engine.engine.base import SegmentBuilder

        b = SegmentBuilder()
        b.line(5.0, 5.0, 5.0, 5.0, layer=LAYER_CUT)
        b.line(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT)
        self.assertEqual(len(b.segments), 1)

    def test_poly_closes_and_refuses_short_input(self) -> None:
        from packstudio.engines.packaging_engine.engine.base import SegmentBuilder

        b = SegmentBuilder()
        b.poly([(0, 0), (10, 0), (10, 10)], close=True)
        self.assertEqual(len(b.segments), 3)
        b2 = SegmentBuilder()
        b2.poly([(0, 0)], close=True)
        self.assertEqual(len(b2.segments), 0)

    def test_net_xs_formula(self) -> None:
        x0, x1, x2, x3, x4 = net_xs(100.0, 40.0, 2.0)
        self.assertEqual((x0, x1, x2, x3), (0.0, 100.0, 140.0, 240.0))
        self.assertAlmostEqual(x4, 278.0, delta=EPS)


if __name__ == "__main__":
    unittest.main()
