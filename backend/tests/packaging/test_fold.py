"""Unit tests for the fold model, Kawasaki/Maekawa validation and 3D preview.

Covers ``packstudio.engines.packaging_engine.fold.model`` (planar arrangement, material
faces, hinge tree), ``packstudio.engines.packaging_engine.fold.validation`` (Kawasaki /
Maekawa) and ``packstudio.engines.packaging_engine.fold.viewer`` (self-contained HTML).
"""

from __future__ import annotations

import html.parser
import json
import unittest

from packstudio.engines.packaging_engine.engine.render import render
from packstudio.engines.packaging_engine.fold.model import FoldModel, build_fold_model
from packstudio.engines.packaging_engine.fold.validation import check_kawasaki_maekawa
from packstudio.engines.packaging_engine.fold.viewer import dieline_to_preview_html
from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    CreaseSegment,
    Dieline,
    StructureSpec,
)

# The nine box types that shipped with the MVP.  The registry keeps growing,
# but Track A is contracted against these nine cartons.
BOX_TYPES = [
    "carton.box_v2.straight",
    "carton.box_v2.lock_bottom",
    "carton.box_v2.mailer",
    "carton.box_v2.top_cover",
    "carton.box_v2.same_direction_tuck",
    "carton.box_v2.glue_bottom",
    "carton.box_v2.hang_tab",
    "carton.box_v2.carry_handle",
    "carton.box_v2.shipping_carton",
]

L = 120.0
W = 60.0
H = 90.0


def make_spec(code: str = "carton.box_v2.straight", **kwargs) -> StructureSpec:
    data = {
        "model_code": code,
        "dimensions": {"length": L, "width": W, "height": H},
    }
    data.update(kwargs)
    return StructureSpec.from_dict(data)


def make_dieline(code: str = "carton.box_v2.straight") -> Dieline:
    return render(make_spec(code))


def _check(report, check_id: str) -> dict:
    for result in report.checks:
        if result["id"] == check_id:
            return result
    raise AssertionError(f"check {check_id} missing from report")


class FoldModelAllBoxTypesTest(unittest.TestCase):
    def test_fold_model_available_for_all_box_types(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                self.assertTrue(model.available, f"{code}: {model.status}")
                self.assertIsInstance(model.status, str)
                self.assertGreaterEqual(len(model.faces), 5)
                self.assertGreaterEqual(len(model.hinges), 4)

    def test_fold_model_status_empty_for_simple_carton(self) -> None:
        model = build_fold_model(make_dieline("carton.box_v2.straight"))
        self.assertTrue(model.available)
        self.assertEqual(model.status, "")

    def test_fold_model_root_is_largest_face(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                largest = max(model.faces, key=lambda f: f["area_mm2"])["id"]
                self.assertEqual(model.root, largest)

    def test_fold_model_hinges_reference_known_faces(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                ids = {f["id"] for f in model.faces}
                self.assertIn(model.root, ids)
                for hinge in model.hinges:
                    self.assertIn(hinge["parent"], ids)
                    self.assertIn(hinge["child"], ids)
                    self.assertNotEqual(hinge["parent"], hinge["child"])

    def test_fold_model_hinges_are_tree_edges(self) -> None:
        # No face can appear as a child twice in the spanning tree.
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                children = [h["child"] for h in model.hinges]
                self.assertEqual(len(children), len(set(children)))

    def test_fold_model_hinge_axes_are_valid(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                for hinge in model.hinges:
                    (x1, y1), (x2, y2) = hinge["axis"]
                    self.assertGreater(abs(x2 - x1) + abs(y2 - y1), 1e-6)

    def test_fold_model_faces_have_positive_area(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                for face in model.faces:
                    self.assertGreater(face["area_mm2"], 0.0)
                    self.assertGreaterEqual(len(face["vertices"]), 3)


class FoldDirectionTest(unittest.TestCase):
    def test_fold_direction_is_set_on_hinges(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                directions = {h["fold_direction"] for h in model.hinges}
                self.assertTrue(directions <= {"mountain", "valley"})
                self.assertNotEqual(directions, set())
                self.assertNotIn("auto", directions)

    def test_explicit_fold_direction_is_honoured(self) -> None:
        # A crease explicitly labelled mountain keeps that direction.
        segments = [
            CreaseSegment(0.0, 0.0, 100.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(100.0, 0.0, 100.0, 100.0, layer=LAYER_CUT),
            CreaseSegment(100.0, 100.0, 0.0, 100.0, layer=LAYER_CUT),
            CreaseSegment(0.0, 100.0, 0.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(50.0, 0.0, 50.0, 100.0, layer=LAYER_CREASE, fold_direction="mountain"),
        ]
        dieline = Dieline("explicit", segments, (0.0, 0.0, 100.0, 100.0), {"panels": []})
        model = build_fold_model(dieline)
        self.assertTrue(model.available, model.status)
        self.assertEqual(len(model.hinges), 1)
        self.assertEqual(model.hinges[0]["fold_direction"], "mountain")

    def test_crease_segment_exposes_fold_direction_field(self) -> None:
        seg = CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CREASE)
        self.assertEqual(seg.fold_direction, "auto")
        self.assertIn("fold_direction", seg.to_dict())

    def test_glue_fold_is_mountain(self) -> None:
        model = build_fold_model(make_dieline("carton.box_v2.straight"))
        glue_hinges = [h for h in model.hinges if "GLUE" in h["label"]]
        self.assertTrue(glue_hinges)
        for hinge in glue_hinges:
            self.assertEqual(hinge["fold_direction"], "mountain")


class FoldAngleTest(unittest.TestCase):
    def test_fold_angles_are_90_or_180(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                model = build_fold_model(make_dieline(code))
                for hinge in model.hinges:
                    self.assertIn(hinge["angle_deg"], (90.0, 180.0))

    def test_vertical_panel_folds_are_90(self) -> None:
        model = build_fold_model(make_dieline("carton.box_v2.straight"))
        flap_tokens = ("TUCK", "DUST", "LOCK", "LID", "FLAP", "GLUE")
        for hinge in model.hinges:
            (x1, y1), (x2, y2) = hinge["axis"]
            if abs(x2 - x1) < 1e-6:  # vertical crease
                label = (hinge.get("label") or "").upper()
                if any(tok in label for tok in flap_tokens):
                    # 翻板/粘口类竖直折线向后平折（180°），物理语义优先于朝向
                    self.assertEqual(hinge["angle_deg"], 180.0, hinge)
                else:
                    self.assertEqual(hinge["angle_deg"], 90.0, hinge)


class KawasakiMaekawaTest(unittest.TestCase):
    def test_kawasaki_passes_for_all_box_types(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                result = check_kawasaki_maekawa(make_dieline(code))
                self.assertTrue(result["passed"], f"{code}: {result['detail']}")

    def test_kawasaki_check_shape(self) -> None:
        result = check_kawasaki_maekawa(make_dieline())
        self.assertEqual(result["id"], "KAWASAKI_MAEKAWA")
        self.assertEqual(result["name"], "川崎/前泽折叠校验")
        self.assertIn("passed", result)
        self.assertIn("detail", result)

    def test_kawasaki_is_fourteenth_check(self) -> None:
        from packstudio.engines.packaging_engine.validator.checks import checks

        results = checks(make_dieline(), make_spec())
        self.assertEqual(len(results), 14)
        self.assertEqual(results[-1]["id"], "KAWASAKI_MAEKAWA")

    def test_kawasaki_fails_odd_degree_interior_vertex(self) -> None:
        # Square cut contour, three creases meeting at an interior point ->
        # degree 3 (odd) violates Maekawa.
        segments = [
            CreaseSegment(0.0, 0.0, 100.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(100.0, 0.0, 100.0, 100.0, layer=LAYER_CUT),
            CreaseSegment(100.0, 100.0, 0.0, 100.0, layer=LAYER_CUT),
            CreaseSegment(0.0, 100.0, 0.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(50.0, 50.0, 50.0, 0.0, layer=LAYER_CREASE),
            CreaseSegment(50.0, 50.0, 0.0, 50.0, layer=LAYER_CREASE),
            CreaseSegment(50.0, 50.0, 50.0, 100.0, layer=LAYER_CREASE),
        ]
        dieline = Dieline("odd", segments, (0.0, 0.0, 100.0, 100.0), {"panels": []})
        result = check_kawasaki_maekawa(dieline)
        self.assertFalse(result["passed"])
        self.assertIn("奇数度", result["detail"])

    def test_kawasaki_fails_bad_sector_sum(self) -> None:
        # Degree 4 (even) but the alternating sector sums are 120/240, not 180.
        cx, cy = 100.0, 100.0
        r = 90.0
        import math

        angles = [0.0, 60.0, 180.0, 240.0]
        segments = [
            CreaseSegment(0.0, 0.0, 200.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(200.0, 0.0, 200.0, 200.0, layer=LAYER_CUT),
            CreaseSegment(200.0, 200.0, 0.0, 200.0, layer=LAYER_CUT),
            CreaseSegment(0.0, 200.0, 0.0, 0.0, layer=LAYER_CUT),
        ]
        for ang in angles:
            segments.append(
                CreaseSegment(
                    cx,
                    cy,
                    cx + r * math.cos(math.radians(ang)),
                    cy + r * math.sin(math.radians(ang)),
                    layer=LAYER_CREASE,
                )
            )
        dieline = Dieline("bad-kawasaki", segments, (0.0, 0.0, 200.0, 200.0), {"panels": []})
        result = check_kawasaki_maekawa(dieline)
        self.assertFalse(result["passed"])
        self.assertIn("川崎", result["detail"])

    def test_kawasaki_boundary_vertex_is_exempt(self) -> None:
        # A crease whose endpoints both lie on the cut boundary creates two
        # boundary vertices of odd degree; they are exempt (the paper edge
        # closes the sector) and there is no interior vertex.
        segments = [
            CreaseSegment(0.0, 0.0, 100.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(100.0, 0.0, 100.0, 100.0, layer=LAYER_CUT),
            CreaseSegment(100.0, 100.0, 0.0, 100.0, layer=LAYER_CUT),
            CreaseSegment(0.0, 100.0, 0.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(50.0, 0.0, 50.0, 100.0, layer=LAYER_CREASE),
        ]
        dieline = Dieline("boundary", segments, (0.0, 0.0, 100.0, 100.0), {"panels": []})
        result = check_kawasaki_maekawa(dieline)
        self.assertTrue(result["passed"], result["detail"])

    def test_kawasaki_passes_with_no_creases(self) -> None:
        segments = [
            CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(10.0, 0.0, 10.0, 10.0, layer=LAYER_CUT),
            CreaseSegment(10.0, 10.0, 0.0, 10.0, layer=LAYER_CUT),
            CreaseSegment(0.0, 10.0, 0.0, 0.0, layer=LAYER_CUT),
        ]
        dieline = Dieline("nocrease", segments, (0.0, 0.0, 10.0, 10.0), {"panels": []})
        result = check_kawasaki_maekawa(dieline)
        self.assertTrue(result["passed"])


class FoldModelRobustnessTest(unittest.TestCase):
    def test_fold_model_json_serialisable(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                payload = build_fold_model(make_dieline(code)).to_dict()
                text = json.dumps(payload, ensure_ascii=False)
                self.assertIn("faces", json.loads(text))

    def test_fold_model_is_deterministic(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                first = build_fold_model(make_dieline(code)).to_dict()
                second = build_fold_model(make_dieline(code)).to_dict()
                self.assertEqual(first, second)

    def test_fold_model_handles_broken_dieline(self) -> None:
        dieline = Dieline("broken", [], (0.0, 0.0, 0.0, 0.0), {"panels": []})
        model = build_fold_model(dieline)
        self.assertFalse(model.available)
        self.assertNotEqual(model.status, "")
        self.assertEqual(model.faces, [])
        self.assertEqual(model.hinges, [])

    def test_fold_model_handles_cut_only_dieline(self) -> None:
        segments = [
            CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT),
            CreaseSegment(10.0, 0.0, 10.0, 10.0, layer=LAYER_CUT),
            CreaseSegment(10.0, 10.0, 0.0, 10.0, layer=LAYER_CUT),
            CreaseSegment(0.0, 10.0, 0.0, 0.0, layer=LAYER_CUT),
        ]
        dieline = Dieline("cutonly", segments, (0.0, 0.0, 10.0, 10.0), {"panels": []})
        model = build_fold_model(dieline)
        # A cut-only square is one material face but has no crease, so it is
        # not a foldable model.
        self.assertFalse(model.available)
        self.assertIn("hinge", model.status)


class PreviewHtmlTest(unittest.TestCase):
    def _html(self, code: str = "carton.box_v2.straight") -> str:
        spec = make_spec(code)
        return dieline_to_preview_html(render(spec), spec)

    def test_preview_html_has_doctype(self) -> None:
        self.assertTrue(self._html().lstrip().lower().startswith("<!doctype html>"))

    def test_preview_html_loads_three_from_cdn(self) -> None:
        page = self._html()
        self.assertIn("three.min.js", page)
        self.assertIn("cdnjs.cloudflare.com", page)

    def test_preview_html_has_fold_slider(self) -> None:
        page = self._html()
        self.assertIn('id="fold-slider"', page)
        self.assertIn('type="range"', page)
        self.assertIn("fold-value", page)

    def test_preview_html_has_3d_canvas(self) -> None:
        page = self._html()
        self.assertIn('id="fold-canvas"', page)
        self.assertIn("THREE.WebGLRenderer", page)

    def test_preview_html_embeds_2d_svg(self) -> None:
        page = self._html()
        self.assertIn("<svg", page)
        self.assertIn("Packaging dieline", page)

    def test_preview_html_embeds_fold_model(self) -> None:
        page = self._html()
        self.assertIn('"available": true', page)
        self.assertIn('"hinges"', page)
        self.assertIn('"faces"', page)

    def test_preview_html_is_parseable(self) -> None:
        class Parser(html.parser.HTMLParser):
            def error(self, message):  # pragma: no cover - defensive
                raise AssertionError(message)

        parser = Parser()
        parser.feed(self._html())
        parser.close()

    def test_preview_html_available_for_all_box_types(self) -> None:
        for code in BOX_TYPES:
            with self.subTest(code=code):
                page = self._html(code)
                self.assertIn("<!DOCTYPE html>", page)
                self.assertIn("fold-slider", page)

    def test_preview_html_on_broken_dieline_still_renders(self) -> None:
        dieline = Dieline("broken", [], (0.0, 0.0, 0.0, 0.0), {"panels": []})
        page = dieline_to_preview_html(dieline, None)
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn("fold-slider", page)


if __name__ == "__main__":
    unittest.main()
