"""Unit tests for the dieline validation gate.

Covers ``validator.checks`` (the 14 deterministic checks) and
``validator.service`` (``validate_dieline`` / ``summarize``).
"""

from __future__ import annotations

import unittest

from packstudio.engines.packaging_engine.engine.cartons import BUILDERS
from packstudio.engines.packaging_engine.engine.render import render
from packstudio.engines.packaging_engine.models.ir import (
    LAYER_CREASE,
    LAYER_CUT,
    LAYER_GLUE,
    CreaseSegment,
    Dieline,
    StructureSpec,
)
from packstudio.engines.packaging_engine.validator.checks import checks
from packstudio.engines.packaging_engine.validator.service import (
    MANUAL_REVIEW_NOTES,
    summarize,
    validate_dieline,
)

L = 120.0
W = 60.0
H = 90.0

EXPECTED_CHECK_IDS = (
    "CUT_CREASE_SEPARATION",
    "TOPOLOGY_CONNECTED",
    "FOLD_ANGLE_FEASIBLE",
    "PANEL_COLLISION",
    "BOUNDS_SANITY",
    "NET_DIMENSION_CONSISTENCY",
    "GLUE_TAB_RANGE",
    "BLEED_OUTSIDE_CUT",
    "SAFE_MARGIN_RESPECTED",
    "CUT_LENGTH_POSITIVE",
    "CREASE_LENGTH_POSITIVE",
    "NO_SELF_INTERSECTION",
    "STYLE_CONSISTENCY",
    "KAWASAKI_MAEKAWA",
)


def make_spec(code: str = "carton.box_v2.straight", **kwargs) -> StructureSpec:
    data = {
        "model_code": code,
        "dimensions": {"length": L, "width": W, "height": H},
    }
    data.update(kwargs)
    return StructureSpec.from_dict(data)


def make_dieline(code: str = "carton.box_v2.straight", spec: StructureSpec | None = None) -> Dieline:
    return render(spec or make_spec(code))


def _check(report, check_id: str) -> dict:
    for result in report.checks:
        if result["id"] == check_id:
            return result
    raise AssertionError(f"check {check_id} missing from report")


def _ids(report) -> list[str]:
    return [c["id"] for c in report.checks]


class AllBoxTypesPassTest(unittest.TestCase):
    def test_every_box_type_passes_all_checks(self) -> None:
        for code in BUILDERS:
            with self.subTest(code=code):
                spec = make_spec(code)
                dl = render(spec)
                report = validate_dieline(dl, spec)
                self.assertTrue(report.passed, f"{code} failed: {_ids(report)}")
                self.assertEqual(len(report.checks), 14)
                self.assertEqual(report.manual_review, list(MANUAL_REVIEW_NOTES))

    def test_all_check_ids_present_in_results(self) -> None:
        report = validate_dieline(make_dieline(), make_spec())
        self.assertEqual(sorted(_ids(report)), sorted(EXPECTED_CHECK_IDS))
        self.assertEqual(len(report.checks), 14)

    def test_checks_function_returns_14_results(self) -> None:
        results = checks(make_dieline(), make_spec())
        self.assertEqual(len(results), 14)
        for result in results:
            self.assertIn("id", result)
            self.assertIn("name", result)
            self.assertIn("passed", result)
            self.assertIn("detail", result)

    def test_every_box_type_has_exactly_one_glue_fold(self) -> None:
        for code in BUILDERS:
            with self.subTest(code=code):
                dl = render(make_spec(code))
                glue_lines = [s for s in dl.segments if s.layer == LAYER_GLUE]
                self.assertEqual(len(glue_lines), 1)
                self.assertEqual(glue_lines[0].label, "GLUE_FOLD")

    def test_every_box_type_passes_cut_crease_separation(self) -> None:
        for code in BUILDERS:
            with self.subTest(code=code):
                report = validate_dieline(make_dieline(code), make_spec(code))
                self.assertTrue(_check(report, "CUT_CREASE_SEPARATION")["passed"])
                self.assertTrue(_check(report, "TOPOLOGY_CONNECTED")["passed"])
                self.assertTrue(_check(report, "NO_SELF_INTERSECTION")["passed"])


class BrokenDielineTest(unittest.TestCase):
    def test_zero_length_cut_segment_fails_cut_length_positive(self) -> None:
        dl = Dieline(
            model_code="broken",
            segments=[
                CreaseSegment(0.0, 0.0, 0.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 0.0, 10.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 10.0, 0.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(0.0, 10.0, 0.0, 0.0, layer=LAYER_CUT),
            ],
            bounds=(0.0, 0.0, 10.0, 10.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertFalse(report.passed)
        self.assertFalse(_check(report, "CUT_LENGTH_POSITIVE")["passed"])

    def test_zero_length_crease_segment_fails_crease_length_positive(self) -> None:
        dl = Dieline(
            model_code="broken",
            segments=[
                CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 0.0, 10.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 10.0, 0.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(0.0, 10.0, 0.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(2.0, 2.0, 2.0, 2.0, layer=LAYER_CREASE),
            ],
            bounds=(0.0, 0.0, 10.0, 10.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertFalse(_check(report, "CREASE_LENGTH_POSITIVE")["passed"])

    def test_cut_crease_crossing_fails_separation(self) -> None:
        # Closed square cut contour with a crease line crossing it transversely.
        dl = Dieline(
            model_code="broken",
            segments=[
                CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 0.0, 10.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 10.0, 0.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(0.0, 10.0, 0.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(5.0, -5.0, 5.0, 15.0, layer=LAYER_CREASE),
            ],
            bounds=(0.0, 0.0, 10.0, 10.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertFalse(report.passed)
        self.assertFalse(_check(report, "CUT_CREASE_SEPARATION")["passed"])

    def test_collinear_cut_crease_is_not_a_crossing(self) -> None:
        # A crease that runs exactly along a cut edge must be tolerated.
        dl = Dieline(
            model_code="ok",
            segments=[
                CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 0.0, 10.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 10.0, 0.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(0.0, 10.0, 0.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CREASE),
            ],
            bounds=(0.0, 0.0, 10.0, 10.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertTrue(_check(report, "CUT_CREASE_SEPARATION")["passed"])

    def test_dangling_endpoint_fails_topology(self) -> None:
        dl = Dieline(
            model_code="broken",
            segments=[
                CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 0.0, 10.0, 10.0, layer=LAYER_CUT),
                CreaseSegment(10.0, 10.0, 5.0, 10.0, layer=LAYER_CUT),
            ],
            bounds=(0.0, 0.0, 10.0, 10.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertFalse(report.passed)
        self.assertFalse(_check(report, "TOPOLOGY_CONNECTED")["passed"])

    def test_bad_bounds_fail_bounds_sanity(self) -> None:
        dl = Dieline(
            model_code="broken",
            segments=[CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT)],
            bounds=(5.0, 0.0, 5.0, 0.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertFalse(_check(report, "BOUNDS_SANITY")["passed"])

    def test_style_inconsistency_is_detected(self) -> None:
        dl = Dieline(
            model_code="broken",
            segments=[CreaseSegment(0.0, 0.0, 10.0, 0.0, layer=LAYER_CUT, style="dashed")],
            bounds=(0.0, 0.0, 10.0, 0.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertFalse(_check(report, "STYLE_CONSISTENCY")["passed"])


class SpecHandlingTest(unittest.TestCase):
    def test_spec_none_skips_spec_dependent_checks(self) -> None:
        dl = make_dieline()
        report = validate_dieline(dl, None)
        self.assertTrue(report.passed)
        self.assertEqual(len(report.checks), 14)
        self.assertTrue(_check(report, "NET_DIMENSION_CONSISTENCY")["passed"])
        self.assertTrue(_check(report, "BLEED_OUTSIDE_CUT")["passed"])
        self.assertTrue(_check(report, "SAFE_MARGIN_RESPECTED")["passed"])
        self.assertTrue(_check(report, "GLUE_TAB_RANGE")["passed"])
        self.assertIn("跳过", _check(report, "NET_DIMENSION_CONSISTENCY")["detail"])
        self.assertIn("无出血要求", _check(report, "BLEED_OUTSIDE_CUT")["detail"])

    def test_net_dimension_consistency_passes_for_carton(self) -> None:
        spec = make_spec("carton.box_v2.straight")
        report = validate_dieline(render(spec), spec)
        self.assertTrue(_check(report, "NET_DIMENSION_CONSISTENCY")["passed"])

    def test_glue_width_out_of_range_fails_glue_tab_range(self) -> None:
        spec = make_spec("carton.box_v2.straight", glue_width=40.0)
        report = validate_dieline(render(spec), spec)
        self.assertFalse(_check(report, "GLUE_TAB_RANGE")["passed"])

    def test_glue_width_in_range_passes(self) -> None:
        spec = make_spec("carton.box_v2.straight", glue_width=11.0)
        report = validate_dieline(render(spec), spec)
        self.assertTrue(_check(report, "GLUE_TAB_RANGE")["passed"])

    def test_spec_required_for_net_dimension_check(self) -> None:
        # A dieline whose net width disagrees with a supplied spec must fail.
        spec = make_spec("carton.box_v2.straight")
        dl = render(spec)
        # Change only the right edge so the net width diverges from the spec.
        shifted = Dieline(
            model_code=dl.model_code,
            segments=list(dl.segments),
            bounds=(dl.bounds[0], dl.bounds[1], dl.bounds[2] + 100.0, dl.bounds[3]),
            metadata=dl.metadata,
        )
        report = validate_dieline(shifted, spec)
        self.assertFalse(_check(report, "NET_DIMENSION_CONSISTENCY")["passed"])


class SummarizeTest(unittest.TestCase):
    def test_summarize_success_counts(self) -> None:
        spec = make_spec()
        report = validate_dieline(render(spec), spec)
        summary = summarize(report)
        self.assertTrue(summary["passed"])
        self.assertEqual(summary["total"], 14)
        self.assertEqual(summary["passed_count"], 14)
        self.assertEqual(summary["failed"], [])

    def test_summarize_failure_counts(self) -> None:
        dl = Dieline(
            model_code="broken",
            segments=[CreaseSegment(0.0, 0.0, 0.0, 0.0, layer=LAYER_CUT)],
            bounds=(0.0, 0.0, 10.0, 10.0),
            metadata={"panels": []},
        )
        summary = summarize(validate_dieline(dl))
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["total"], 14)
        self.assertEqual(summary["passed_count"], 13)
        self.assertIn("CUT_LENGTH_POSITIVE", summary["failed"])

    def test_summarize_matches_report_checks(self) -> None:
        spec = make_spec()
        report = validate_dieline(render(spec), spec)
        summary = summarize(report)
        self.assertEqual(summary["total"], len(report.checks))
        self.assertEqual(
            summary["passed_count"], sum(1 for c in report.checks if c["passed"])
        )


class ManualReviewTest(unittest.TestCase):
    def test_manual_review_notes_present(self) -> None:
        spec = make_spec()
        dl = render(spec)
        self.assertEqual(dl.metadata["review_required"], "REQUIRES_MANUFACTURER_REVIEW")
        report = validate_dieline(dl, spec)
        self.assertEqual(report.manual_review, list(MANUAL_REVIEW_NOTES))
        for note in MANUAL_REVIEW_NOTES:
            self.assertIn(note, report.manual_review)

    def test_manual_review_appends_failed_check_ids(self) -> None:
        dl = Dieline(
            model_code="broken",
            segments=[CreaseSegment(0.0, 0.0, 0.0, 0.0, layer=LAYER_CUT)],
            bounds=(0.0, 0.0, 10.0, 10.0),
            metadata={"panels": []},
        )
        report = validate_dieline(dl)
        self.assertTrue(any("CUT_LENGTH_POSITIVE" in note for note in report.manual_review))
        self.assertEqual(len(report.manual_review), len(MANUAL_REVIEW_NOTES) + 1)

    def test_manual_review_present_for_every_box_type(self) -> None:
        for code in BUILDERS:
            with self.subTest(code=code):
                spec = make_spec(code)
                report = validate_dieline(render(spec), spec)
                self.assertTrue(report.manual_review)


if __name__ == "__main__":
    unittest.main()
