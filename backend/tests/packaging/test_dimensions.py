"""Tests for the dimension conversion module (finished inner/outer <-> blank)."""

from __future__ import annotations

import unittest

from packstudio.engines.packaging_engine.dimensions import (
    blank_from_finished,
    convert_dimensions,
    finished_from_blank,
    material_usage,
)
from packstudio.engines.packaging_engine.exceptions import RequestValidationError
from packstudio.engines.packaging_engine.models.ir import StructureSpec


def make_spec(**overrides) -> StructureSpec:
    params = {
        "model_code": "carton.box_v2.lock_bottom",
        "dimensions": {"length": 100.0, "width": 60.0, "height": 40.0},
        "board_thickness": 0.3,
        "tuck_height": 12.0,
        "glue_width": 11.0,
    }
    params.update(overrides)
    return StructureSpec.from_dict(params)


class FinishedInnerOuterTests(unittest.TestCase):
    def test_inner_to_outer_adds_two_thicknesses(self) -> None:
        result = convert_dimensions(
            {"length": 100.0, "width": 60.0, "height": 40.0},
            "finished_inner",
            "finished_outer",
            board_thickness=0.3,
        )
        self.assertAlmostEqual(result["length"], 100.6, places=6)
        self.assertAlmostEqual(result["width"], 60.6, places=6)
        self.assertAlmostEqual(result["height"], 40.6, places=6)
        self.assertEqual(result["unit"], "mm")
        self.assertEqual(result["dimension_type"], "finished_outer")

    def test_outer_to_inner_subtracts_two_thicknesses(self) -> None:
        result = convert_dimensions(
            {"length": 100.6, "width": 60.6, "height": 40.6},
            "finished_outer",
            "finished_inner",
            board_thickness=0.3,
        )
        self.assertAlmostEqual(result["length"], 100.0, places=6)
        self.assertAlmostEqual(result["width"], 60.0, places=6)
        self.assertAlmostEqual(result["height"], 40.0, places=6)
        self.assertEqual(result["dimension_type"], "finished_inner")

    def test_roundtrip_inner_outer_inner(self) -> None:
        inner = {"length": 100.0, "width": 60.0, "height": 40.0}
        outer = convert_dimensions(inner, "finished_inner", "finished_outer", 0.4)
        back = convert_dimensions(outer, "finished_outer", "finished_inner", 0.4)
        self.assertEqual(back, {**inner, "unit": "mm", "dimension_type": "finished_inner"})

    def test_default_board_thickness_is_0_3(self) -> None:
        result = convert_dimensions(
            {"length": 100.0, "width": 60.0, "height": 40.0},
            "finished_inner",
            "finished_outer",
        )
        self.assertAlmostEqual(result["length"], 100.6, places=6)


class BlankTests(unittest.TestCase):
    def test_blank_from_finished_includes_allowances(self) -> None:
        spec = make_spec()
        result = blank_from_finished(100.0, 60.0, 40.0, spec)
        self.assertAlmostEqual(result["length"], 100.0 + 2 * 12.0 + 11.0, places=6)
        self.assertAlmostEqual(result["width"], 60.0 + 2 * 12.0, places=6)
        self.assertAlmostEqual(result["height"], 40.0, places=6)
        self.assertEqual(result["dimension_type"], "blank")

    def test_blank_height_is_unchanged(self) -> None:
        spec = make_spec()
        result = blank_from_finished(100.0, 60.0, 40.0, spec)
        self.assertEqual(result["height"], 40.0)

    def test_blank_uses_spec_tuck_and_glue(self) -> None:
        spec = make_spec(tuck_height=20.0, glue_width=8.0)
        result = blank_from_finished(100.0, 60.0, 40.0, spec)
        self.assertAlmostEqual(result["length"], 100.0 + 2 * 20.0 + 8.0, places=6)

    def test_convert_inner_to_blank_uses_default_allowances(self) -> None:
        result = convert_dimensions(
            {"length": 100.0, "width": 60.0, "height": 40.0},
            "finished_inner",
            "blank",
        )
        self.assertAlmostEqual(result["length"], 135.0, places=6)
        self.assertAlmostEqual(result["width"], 84.0, places=6)
        self.assertEqual(result["height"], 40.0)

    def test_convert_blank_to_inner(self) -> None:
        result = convert_dimensions(
            {"length": 135.0, "width": 84.0, "height": 40.0},
            "blank",
            "finished_inner",
        )
        self.assertAlmostEqual(result["length"], 100.0, places=6)
        self.assertAlmostEqual(result["width"], 60.0, places=6)

    def test_convert_outer_to_blank(self) -> None:
        result = convert_dimensions(
            {"length": 100.6, "width": 60.6, "height": 40.6},
            "finished_outer",
            "blank",
        )
        self.assertAlmostEqual(result["length"], 135.6, places=6)
        self.assertAlmostEqual(result["width"], 84.6, places=6)
        self.assertEqual(result["height"], 40.6)

    def test_finished_from_blank_inverse(self) -> None:
        spec = make_spec()
        blank = blank_from_finished(100.0, 60.0, 40.0, spec)
        finished = finished_from_blank(blank["length"], blank["width"], blank["height"], spec)
        self.assertAlmostEqual(finished["length"], 100.0, places=6)
        self.assertAlmostEqual(finished["width"], 60.0, places=6)
        self.assertAlmostEqual(finished["height"], 40.0, places=6)
        self.assertEqual(finished["dimension_type"], "finished_inner")


class MaterialUsageTests(unittest.TestCase):
    def test_area_is_length_times_width(self) -> None:
        result = material_usage(200.0, 150.0)
        self.assertAlmostEqual(result["blank_area_mm2"], 30000.0, places=6)
        self.assertEqual(result["unit"], "mm2")

    def test_area_with_board_gsm(self) -> None:
        # 200 mm x 150 mm = 30000 mm^2 = 0.03 m^2; 300 g/m^2 -> 9 g.
        result = material_usage(200.0, 150.0, board_gsm=300.0)
        self.assertAlmostEqual(result["blank_area_mm2"], 30000.0, places=6)
        self.assertAlmostEqual(result["estimated_sheet_mass_g"], 9.0, places=6)


class ErrorHandlingTests(unittest.TestCase):
    def test_missing_dimension_key_raises(self) -> None:
        with self.assertRaises(RequestValidationError) as ctx:
            convert_dimensions(
                {"length": 100.0, "width": 60.0}, "finished_inner", "finished_outer"
            )
        self.assertEqual(ctx.exception.code, "MISSING_REQUIRED_FIELD")

    def test_non_dict_raises(self) -> None:
        with self.assertRaises(RequestValidationError):
            convert_dimensions("nope", "finished_inner", "finished_outer")  # type: ignore[arg-type]

    def test_non_positive_dimension_raises(self) -> None:
        with self.assertRaises(RequestValidationError) as ctx:
            convert_dimensions(
                {"length": 0.0, "width": 60.0, "height": 40.0},
                "finished_inner",
                "finished_outer",
            )
        self.assertEqual(ctx.exception.code, "INVALID_DIMENSIONS")

    def test_unknown_dimension_type_raises(self) -> None:
        with self.assertRaises(RequestValidationError) as ctx:
            convert_dimensions(
                {"length": 100.0, "width": 60.0, "height": 40.0},
                "finished_inner",
                "teleported",
            )
        self.assertEqual(ctx.exception.code, "INVALID_DIMENSION_TYPE")

    def test_invalid_board_thickness_raises(self) -> None:
        with self.assertRaises(RequestValidationError) as ctx:
            convert_dimensions(
                {"length": 100.0, "width": 60.0, "height": 40.0},
                "finished_inner",
                "finished_outer",
                board_thickness=0.0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_BOARD_THICKNESS")

    def test_non_numeric_dimension_raises(self) -> None:
        with self.assertRaises(RequestValidationError):
            convert_dimensions(
                {"length": "abc", "width": 60.0, "height": 40.0},
                "finished_inner",
                "finished_outer",
            )

    def test_same_type_conversion_is_identity(self) -> None:
        inner = {"length": 100.0, "width": 60.0, "height": 40.0}
        result = convert_dimensions(inner, "finished_inner", "finished_inner")
        self.assertAlmostEqual(result["length"], 100.0, places=6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
