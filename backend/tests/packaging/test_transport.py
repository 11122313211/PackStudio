"""Tests for the simplified ISTA 3A stacking / compression estimate."""

from __future__ import annotations

import unittest

from packstudio.engines.packaging_engine.exceptions import RequestValidationError
from packstudio.engines.packaging_engine.structure import stacking_estimate as standards_stacking_estimate
from packstudio.engines.packaging_engine.transport import stacking_estimate
from packstudio.engines.packaging_engine.transport.ista import estimate_k_from_gsm


class FormulaTests(unittest.TestCase):
    def test_f_equals_k_times_height_difference_times_area(self) -> None:
        # length=200 mm, width=150 mm, height=120 mm, H=1.0 m, k=1000 N/m^2.
        # F = 1000 * (1.0 - 0.12) * 0.15 * 0.2 = 26.4 N.
        result = stacking_estimate(
            length_mm=200.0,
            width_mm=150.0,
            height_mm=120.0,
            stack_height_m=1.0,
            unit_weight_kg=1.0,
            k_factor=1000.0,
        )
        expected = 1000.0 * (1.0 - 0.12) * 0.15 * 0.2
        self.assertAlmostEqual(result["estimated_compressive_strength_n"], expected, places=9)

    def test_bct_units_are_derived_from_f(self) -> None:
        f = 26.4
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, 1.0, k_factor=1000.0)
        self.assertAlmostEqual(result["estimated_bct_kn"], f / 1000.0, places=9)
        self.assertAlmostEqual(result["estimated_bct_kg"], f / 9.81, places=9)

    def test_explicit_k_factor_overrides_gsm(self) -> None:
        result = stacking_estimate(
            200.0, 150.0, 120.0, 1.0, 1.0, k_factor=250.0, board_gsm=999.0
        )
        self.assertAlmostEqual(result["inputs"]["k_estimate"], 250.0, places=9)


class KEstimationTests(unittest.TestCase):
    def test_k_estimated_from_gsm(self) -> None:
        result = stacking_estimate(
            200.0, 150.0, 120.0, 1.0, 1.0, board_gsm=300.0
        )
        self.assertAlmostEqual(result["inputs"]["k_estimate"], 0.5 * 300.0 * 1e-3, places=12)
        self.assertAlmostEqual(
            result["estimated_compressive_strength_n"],
            0.5 * 300.0 * 1e-3 * (1.0 - 0.12) * 0.15 * 0.2,
            places=12,
        )

    def test_estimate_k_from_gsm_function(self) -> None:
        self.assertAlmostEqual(estimate_k_from_gsm(300.0), 0.15, places=12)
        self.assertAlmostEqual(estimate_k_from_gsm(600.0), 0.30, places=12)

    def test_invalid_gsm_raises(self) -> None:
        with self.assertRaises(RequestValidationError):
            estimate_k_from_gsm(0.0)


class BoundaryTests(unittest.TestCase):
    def test_stack_height_equal_to_height_gives_zero(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 0.12, 1.0, k_factor=1000.0)
        self.assertEqual(result["estimated_compressive_strength_n"], 0.0)

    def test_stack_height_below_height_clamps_to_zero(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 0.10, 1.0, k_factor=1000.0)
        self.assertEqual(result["estimated_compressive_strength_n"], 0.0)

    def test_zero_strength_has_no_safety_factor(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 0.12, 1.0, k_factor=1000.0)
        self.assertIsNone(result["safety_factor"])
        self.assertIsNone(result["recommended_stack_layers"])


class SafetyFactorTests(unittest.TestCase):
    def test_safety_factor_is_stack_load_over_bct(self) -> None:
        # units above = (1.0 - 0.12) / 0.12; load = mass * g * units_above.
        f = 26.4
        units_above = (1.0 - 0.12) / 0.12
        expected = (1.0 * 9.81 * units_above) / f
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, 1.0, k_factor=1000.0)
        self.assertAlmostEqual(result["safety_factor"], expected, places=9)

    def test_recommended_layers_is_int(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, 1.0, k_factor=1000.0)
        self.assertIsInstance(result["recommended_stack_layers"], int)
        self.assertGreaterEqual(result["recommended_stack_layers"], 1)


class NoneHandlingTests(unittest.TestCase):
    def test_missing_k_and_gsm_gives_none_estimates(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, 1.0)
        self.assertIsNone(result["estimated_compressive_strength_n"])
        self.assertIsNone(result["estimated_bct_kn"])
        self.assertIsNone(result["estimated_bct_kg"])
        self.assertIsNone(result["safety_factor"])
        self.assertIsNone(result["recommended_stack_layers"])

    def test_missing_unit_weight_gives_none_safety(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, None, k_factor=1000.0)
        self.assertIsNotNone(result["estimated_compressive_strength_n"])
        self.assertIsNone(result["safety_factor"])
        self.assertIsNone(result["recommended_stack_layers"])


class MetadataTests(unittest.TestCase):
    def test_method_and_validation_flag(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, 1.0, k_factor=1000.0)
        self.assertEqual(result["method"], "ISTA 3A simplified")
        self.assertTrue(result["requires_manufacturer_validation"])

    def test_inputs_are_recorded(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, 1.0, k_factor=1000.0)
        self.assertEqual(result["inputs"]["length_mm"], 200.0)
        self.assertEqual(result["inputs"]["width_mm"], 150.0)
        self.assertEqual(result["inputs"]["height_mm"], 120.0)
        self.assertEqual(result["inputs"]["stack_height_m"], 1.0)

    def test_notes_warn_in_chinese(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 1.0, 1.0, k_factor=1000.0)
        self.assertIn("需厂商验证", result["notes"])


class StandardsDelegationTests(unittest.TestCase):
    def test_standards_stacking_estimate_delegates(self) -> None:
        result = standards_stacking_estimate(200.0, 150.0, 120.0, 300.0, 1000.0)
        self.assertEqual(result["method"], "ISTA 3A simplified")
        self.assertTrue(result["requires_manufacturer_validation"])
        # Legacy stack_height was in mm (1000 -> 1.0 m) and gsm passed as board_gsm.
        self.assertEqual(result["inputs"]["stack_height_m"], 1.0)
        self.assertEqual(result["inputs"]["board_gsm"], 300.0)
        self.assertIsNotNone(result["estimated_compressive_strength_n"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
