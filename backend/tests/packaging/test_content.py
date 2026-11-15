"""Tests for the content layout module."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from packstudio.engines.packaging_engine.content.fields import (
    PACKAGING_FIELDS,
    STATUS_MISSING,
    STATUS_USER_PROVIDED,
    build_fields,
)
from packstudio.engines.packaging_engine.content.layout import layout_content
from packstudio.engines.packaging_engine.content.models import ContentBrief
from packstudio.engines.packaging_engine.models.ir import (
    LAYER_BLEED,
    LAYER_CUT,
    LAYER_CREASE,
    LAYER_GUIDE,
    LAYER_SAFETY,
    CreaseSegment,
    Dieline,
    StructureSpec,
)
from packstudio.engines.packaging_engine.xmlsafe import safe_fromstring

SVG_NS = "http://www.w3.org/2000/svg"

CUT_BOX = (
    (0.0, 0.0, 120.0, 0.0),
    (120.0, 0.0, 120.0, 180.0),
    (120.0, 180.0, 0.0, 180.0),
    (0.0, 180.0, 0.0, 0.0),
)


def make_dieline() -> Dieline:
    segments: list[CreaseSegment] = []
    for x1, y1, x2, y2 in CUT_BOX:
        segments.append(CreaseSegment(x1, y1, x2, y2, LAYER_CUT, "solid", 0.4))
    segments.append(CreaseSegment(60.0, 2.0, 60.0, 178.0, LAYER_CREASE, "dashed", 0.3))
    segments.append(CreaseSegment(0.0, 90.0, 120.0, 90.0, LAYER_CREASE, "dashed", 0.3))
    return Dieline("carton.box_v2.straight", tuple(segments), (0.0, 0.0, 120.0, 180.0), {})


def make_spec() -> StructureSpec:
    return StructureSpec.from_dict(
        {
            "model_code": "carton.box_v2.straight",
            "dimensions": {"length": 100.0, "width": 50.0, "height": 80.0},
        }
    )


def _cut_paths(svg_text: str) -> list[str]:
    root = safe_fromstring(svg_text)
    paths: list[str] = []
    for group in root.iter():
        if group.attrib.get("id") == "layer-cut":
            for child in group:
                if child.tag.rsplit("}", 1)[-1] == "path":
                    paths.append(child.attrib["d"])
    return paths


def _point_in_rect(px: float, py: float, rect, tol: float = 0.05) -> bool:
    x, y, w, h = rect
    return (
        x - tol <= px <= x + w + tol
        and y - tol <= py <= y + h + tol
    )


class ContentFieldRulesTest(unittest.TestCase):
    def test_missing_required_fields_become_placeholders(self) -> None:
        brief = ContentBrief.from_dict({"product_name": "测试产品"})
        by_key = {field.key: field for field in brief.fields}
        self.assertEqual(by_key["product_name"].value, "测试产品")
        self.assertEqual(by_key["product_name"].status, STATUS_USER_PROVIDED)
        self.assertEqual(
            by_key["brand"].value, "[待提供：品牌]",
            "缺失的品牌必须是显式占位符，而不是猜测值。",
        )
        self.assertTrue(by_key["brand"].is_placeholder)
        self.assertEqual(by_key["brand"].status, STATUS_MISSING)
        self.assertEqual(
            by_key["net_weight"].value, "[待提供：净含量]"
        )

    def test_missing_certification_is_not_invented(self) -> None:
        brief = ContentBrief.from_dict({"product_name": "测试产品", "brand": "测试品牌"})
        certification = brief.field_for("certification")
        self.assertIsNotNone(certification)
        self.assertEqual(certification.value, "[待确认：认证]")
        self.assertTrue(certification.is_placeholder)
        serialized = json.dumps(brief.to_dict(), ensure_ascii=False)
        for invented in ("ISO", "QS", "SC", "HACCP", "有机", "GMP"):
            self.assertNotIn(invented, serialized)

    def test_provided_certification_is_verbatim(self) -> None:
        brief = ContentBrief.from_dict({"certification": "ISO 9001:2015"})
        certification = brief.field_for("certification")
        self.assertEqual(certification.value, "ISO 9001:2015")
        self.assertEqual(certification.status, STATUS_USER_PROVIDED)
        self.assertFalse(certification.is_placeholder)

    def test_all_canonical_fields_have_metadata(self) -> None:
        keys = {field.key for field in PACKAGING_FIELDS}
        for expected in (
            "product_name", "brand", "manufacturer", "license", "standard_no",
            "certification", "ingredients", "efficacy", "net_weight",
            "warnings", "barcode", "traceability", "date",
        ):
            self.assertIn(expected, keys)
        for field in PACKAGING_FIELDS:
            self.assertTrue(field.label_zh)
            self.assertIn("[", field.placeholder)
            self.assertIn("]", field.placeholder)
            self.assertTrue(field.required or field.placeholder)

    def test_build_fields_does_not_invent_values(self) -> None:
        fields = build_fields({})
        self.assertTrue(all(field.is_placeholder for field in fields))
        for field in fields:
            self.assertEqual(field.value, field.placeholder)


class LayoutContentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = make_spec()
        self.dieline = make_dieline()
        self.brief = ContentBrief.from_dict(
            {
                "product_name": "测试产品",
                "brand": "测试品牌",
                "net_weight": "500g",
            }
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_generates_all_outputs(self) -> None:
        result = layout_content(self.dieline, self.brief, self.spec, self.out)
        for name in (
            "content-layout.svg",
            "content-spec.json",
            "source-report.md",
            "missing-fields.md",
            "review-checklist.md",
        ):
            self.assertIn(name, result.paths)
            self.assertTrue((self.out / name).is_file(), name)

    def test_missing_fields_report_lists_placeholders(self) -> None:
        result = layout_content(self.dieline, self.brief, self.spec, self.out)
        missing_text = (self.out / "missing-fields.md").read_text(encoding="utf-8")
        self.assertIn("[待提供：生产企业名称]", missing_text)
        self.assertIn("[待确认：认证]", missing_text)
        self.assertIn("[待确认：许可证]", missing_text)
        spec_payload = json.loads((self.out / "content-spec.json").read_text(encoding="utf-8"))
        by_key = {field["key"]: field for field in spec_payload["fields"]}
        self.assertEqual(by_key["brand"]["value"], "测试品牌")
        self.assertFalse(by_key["brand"]["is_placeholder"])
        self.assertEqual(by_key["manufacturer"]["value"], "[待提供：生产企业名称]")
        self.assertTrue(by_key["manufacturer"]["is_placeholder"])
        self.assertGreater(result.missing_count, 0)

    def test_source_report_marks_user_and_placeholder(self) -> None:
        layout_content(self.dieline, self.brief, self.spec, self.out)
        source_text = (self.out / "source-report.md").read_text(encoding="utf-8")
        self.assertIn("用户提供字段", source_text)
        self.assertIn("占位符字段", source_text)
        self.assertIn("测试产品", source_text)

    def test_svg_is_valid_and_ids_unique(self) -> None:
        result = layout_content(self.dieline, self.brief, self.spec, self.out)
        svg_text = (self.out / "content-layout.svg").read_text(encoding="utf-8")
        root = safe_fromstring(svg_text)
        ids = [el.attrib.get("id") for el in root.iter() if el.attrib.get("id")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(result.validation["checks"]["svg_xml_valid"])
        self.assertTrue(result.validation["checks"]["unique_ids"])

    def test_cut_lines_untouched(self) -> None:
        result = layout_content(self.dieline, self.brief, self.spec, self.out)
        svg_text = (self.out / "content-layout.svg").read_text(encoding="utf-8")
        rendered = set(_cut_paths(svg_text))
        expected = {f"M {x1:g} {y1:g} L {x2:g} {y2:g}" for x1, y1, x2, y2 in CUT_BOX}
        self.assertEqual(rendered, expected, "刀线几何必须原样保留。")
        self.assertTrue(result.validation["checks"]["cut_layer_untouched"])

    def test_no_text_on_cut_lines(self) -> None:
        result = layout_content(self.dieline, self.brief, self.spec, self.out)
        spec_payload = json.loads((self.out / "content-spec.json").read_text(encoding="utf-8"))
        placements = spec_payload["placements"]
        self.assertTrue(placements)
        cut_segments = [s for s in self.dieline.segments if s.layer == LAYER_CUT]
        self.assertEqual(len(cut_segments), 4)

        samples = 200
        for placement in placements:
            box = placement["text_box"]
            for segment in cut_segments:
                for index in range(samples + 1):
                    t = index / samples
                    px = segment.x1 + (segment.x2 - segment.x1) * t
                    py = segment.y1 + (segment.y2 - segment.y1) * t
                    self.assertFalse(
                        _point_in_rect(px, py, box),
                        f"文字 {placement['field_id']} 落在刀线上。",
                    )

    def test_validation_flags(self) -> None:
        result = layout_content(self.dieline, self.brief, self.spec, self.out)
        checks = result.validation["checks"]
        for key in (
            "svg_xml_valid",
            "cut_layer_untouched",
            "unique_ids",
            "no_text_on_cut_lines",
            "all_fields_have_status",
            "all_fields_have_source",
        ):
            self.assertTrue(checks[key], key)
        self.assertIn("REQUIRES_COMPLIANCE_REVIEW", result.warnings)
        self.assertIn("MISSING_CONTENT_FIELDS", result.warnings)


if __name__ == "__main__":
    unittest.main()
