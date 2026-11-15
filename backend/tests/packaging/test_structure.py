"""ECMA / FEFCO 结构标准库测试（Phase 2）。"""

from __future__ import annotations

import unittest

from packstudio.engines.packaging_engine.exceptions import KnowledgeMissingError
from packstudio.engines.packaging_engine.structure import (
    ECMA_STANDARDS,
    FEFCO_STANDARDS,
    STANDARD_LOOKUP,
    compatible_standards,
    dimension_rules,
    resolve_standard,
    standards_report,
    stacking_estimate,
)


class StandardTableTests(unittest.TestCase):
    def test_ecma_standards_non_empty(self) -> None:
        self.assertTrue(ECMA_STANDARDS)
        required = {"A20.20.01.01", "A20.20.03.01", "A01.55.00.01", "A01.50.00.00"}
        self.assertTrue(required.issubset(set(ECMA_STANDARDS)))

    def test_fefco_standards_non_empty(self) -> None:
        self.assertTrue(FEFCO_STANDARDS)
        required = {"0201", "0412", "0301"}
        self.assertTrue(required.issubset(set(FEFCO_STANDARDS)))

    def test_ecma_entry_fields(self) -> None:
        entry = ECMA_STANDARDS["A20.20.01.01"]
        for key in (
            "code",
            "name_en",
            "name_zh",
            "description",
            "default_parameters",
            "compatible_box_types",
            "notes",
        ):
            self.assertIn(key, entry)
        for param in (
            "shrink",
            "tuck_height",
            "glue_width",
            "bleed",
            "safe_margin",
            "board_thickness",
        ):
            self.assertIn(param, entry["default_parameters"])
        self.assertTrue(entry["compatible_box_types"])

    def test_fefco_entry_fields(self) -> None:
        entry = FEFCO_STANDARDS["0201"]
        for key in (
            "code",
            "name_en",
            "name_zh",
            "description",
            "default_parameters",
            "compatible_box_types",
            "notes",
        ):
            self.assertIn(key, entry)


class LookupTests(unittest.TestCase):
    def test_lookup_contains_both_families(self) -> None:
        types = {entry["standard_type"] for entry in STANDARD_LOOKUP.values()}
        self.assertEqual(types, {"ECMA", "FEFCO"})
        self.assertIn("A20.20.01.01", STANDARD_LOOKUP)
        self.assertIn("0201", STANDARD_LOOKUP)
        self.assertEqual(STANDARD_LOOKUP["0201"]["standard_type"], "FEFCO")

    def test_resolve_known_codes(self) -> None:
        for code in ("A20.20.01.01", "A20.20.03.01", "A01.55.00.01", "A01.50.00.00"):
            with self.subTest(code=code):
                self.assertEqual(resolve_standard(code)["code"], code)
        for code in ("0201", "0412", "0301"):
            with self.subTest(code=code):
                self.assertEqual(resolve_standard(code)["code"], code)

    def test_resolve_case_insensitive(self) -> None:
        self.assertEqual(resolve_standard("a20.20.01.01")["code"], "A20.20.01.01")
        self.assertEqual(resolve_standard("  A20.20.03.01  ")["code"], "A20.20.03.01")
        self.assertEqual(resolve_standard("0201")["standard_type"], "FEFCO")

    def test_resolve_unknown_raises(self) -> None:
        for bad in ("A99.99.99.99", "0202", "not-a-standard", "", "   "):
            with self.subTest(bad=bad):
                with self.assertRaises(KnowledgeMissingError) as ctx:
                    resolve_standard(bad)
                self.assertEqual(ctx.exception.code, "STANDARD_MISSING")


class CompatibilityTests(unittest.TestCase):
    def test_compatible_standards_known_boxes(self) -> None:
        known = (
            "carton.box_v2.straight",
            "carton.box_v2.lock_bottom",
            "carton.box_v2.same_direction_tuck",
            "carton.box_v2.mailer",
            "carton.box_v2.top_cover",
            "carton.box_v2.glue_bottom",
            "carton.box_v2.hang_tab",
            "carton.box_v2.carry_handle",
            "carton.box_v2.shipping_carton",
        )
        for code in known:
            with self.subTest(box=code):
                standards = compatible_standards(code)
                self.assertTrue(standards, f"{code} should map to a standard")
                self.assertTrue(all("standard_type" in item for item in standards))

    def test_compatible_registry_link_matches(self) -> None:
        codes = {item["code"] for item in compatible_standards("carton.box_v2.straight")}
        self.assertIn("A20.20.01.01", codes)
        codes = {item["code"] for item in compatible_standards("carton.box_v2.shipping_carton")}
        self.assertIn("0201", codes)

    def test_compatible_unknown_box_is_empty(self) -> None:
        self.assertEqual(compatible_standards("carton.box_v2.does_not_exist"), [])


class DimensionRuleTests(unittest.TestCase):
    def test_dimension_rules_expected_keys(self) -> None:
        rules = dimension_rules("A20.20.01.01")
        expected = {
            "standard_code",
            "standard_type",
            "min_aspect_ratio",
            "max_aspect_ratio",
            "recommended_board_thickness_mm",
            "typical_tuck_height_mm",
            "typical_glue_width_mm",
            "clearance_ratio",
            "requires_manufacturer_validation",
        }
        self.assertTrue(expected.issubset(set(rules)))
        self.assertIn("A20.20.01.01", rules["standard_code"])
        self.assertLess(rules["min_aspect_ratio"], rules["max_aspect_ratio"])
        self.assertTrue(rules["requires_manufacturer_validation"])

    def test_dimension_rules_unknown_raises(self) -> None:
        with self.assertRaises(KnowledgeMissingError):
            dimension_rules("A99.99.99.99")


class StackingEstimateTests(unittest.TestCase):
    def test_stacking_estimate_delegates_to_ista(self) -> None:
        result = stacking_estimate(200.0, 150.0, 120.0, 300.0, 1000.0)
        self.assertIsInstance(result, dict)
        self.assertTrue(result["requires_manufacturer_validation"])
        self.assertEqual(result["method"], "ISTA 3A simplified")
        # Legacy mm stack height (1000) is converted to metres and gsm to board_gsm.
        self.assertEqual(result["inputs"]["stack_height_m"], 1.0)
        self.assertEqual(result["inputs"]["board_gsm"], 300.0)
        # The placeholder is replaced by a numeric estimate.
        self.assertIsNotNone(result["estimated_compressive_strength_n"])
        self.assertIsNotNone(result["estimated_bct_kn"])
        self.assertIsNotNone(result["estimated_bct_kg"])
        self.assertIn("需厂商验证", result["notes"])


class ReportTests(unittest.TestCase):
    def test_standards_report_is_list(self) -> None:
        report = standards_report()
        self.assertIsInstance(report, list)
        self.assertTrue(report)
        self.assertEqual(len(report), len(ECMA_STANDARDS) + len(FEFCO_STANDARDS))
        self.assertTrue(all("standard_type" in item for item in report))
        for item in report:
            self.assertEqual(item["standard_type"], STANDARD_LOOKUP[item["code"]]["standard_type"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
