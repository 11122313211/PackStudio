"""Tests for the BOM / SOP / review modules."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from packstudio.engines.packaging_engine.bom.bom import generate_bom
from packstudio.engines.packaging_engine.bom.models import BOMItem, SOPStep
from packstudio.engines.packaging_engine.bom.review import reconcile_bom_sop
from packstudio.engines.packaging_engine.bom.review_template import build_review_payload, build_review_template
from packstudio.engines.packaging_engine.bom.sop import generate_sop
from packstudio.engines.packaging_engine.content.models import ContentBrief
from packstudio.engines.packaging_engine.models.ir import StructureSpec

EXPECTED_STEPS = ("印前", "排版", "印刷", "印后工艺", "模切", "糊盒", "质检")


def make_spec() -> StructureSpec:
    return StructureSpec.from_dict(
        {
            "model_code": "carton.box_v2.lock_bottom",
            "dimensions": {"length": 120.0, "width": 60.0, "height": 90.0},
            "material": "",
        }
    )


class BOMTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = make_spec()
        self.brief = ContentBrief.from_dict({"product_name": "测试产品"})
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_generate_bom_writes_csv_and_json(self) -> None:
        payload = generate_bom(self.spec, self.brief, self.out)
        self.assertTrue((self.out / "bom.csv").is_file())
        self.assertTrue((self.out / "bom.json").is_file())
        self.assertEqual(payload["action"], "bom")
        self.assertGreaterEqual(payload["summary"]["item_count"], 6)

    def test_bom_csv_is_valid_utf8(self) -> None:
        generate_bom(self.spec, self.brief, self.out)
        raw = (self.out / "bom.csv").read_bytes()
        text = raw.decode("utf-8")  # raises UnicodeDecodeError if not valid UTF-8
        header = text.splitlines()[0]
        self.assertIn("name", header)
        self.assertIn("effective_qty", header)
        self.assertIn("纸板/卡纸", text)
        self.assertIn("印刷油墨", text)

    def test_bom_json_has_required_categories(self) -> None:
        payload = generate_bom(self.spec, self.brief, self.out)
        names = {item["name"] for item in payload["items"]}
        for required in (
            "纸板/卡纸",
            "印刷油墨",
            "表面处理/涂层",
            "模切刀版",
            "糊盒胶水",
            "人工工时",
        ):
            self.assertIn(required, names)
        self.assertTrue(payload["summary"]["placeholder_count"] >= 1)
        stored = json.loads((self.out / "bom.json").read_text(encoding="utf-8"))
        self.assertEqual(len(stored["items"]), len(payload["items"]))

    def test_bom_item_effective_qty_applies_loss(self) -> None:
        item = BOMItem("纸", "spec", 10.0, "m²", loss_rate=0.1)
        self.assertAlmostEqual(item.effective_qty(), 11.0, places=6)


class SOPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = make_spec()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_generate_sop_writes_markdown_with_all_steps(self) -> None:
        payload = generate_sop(self.spec, self.out)
        self.assertTrue((self.out / "sop.md").is_file())
        markdown = (self.out / "sop.md").read_text(encoding="utf-8")
        self.assertEqual(
            [step["title"] for step in payload["steps"]], list(EXPECTED_STEPS)
        )
        for step in payload["steps"]:
            self.assertIn(step["title"], markdown)
        self.assertIn("设备", markdown)

    def test_sop_steps_have_equipment_placeholders(self) -> None:
        payload = generate_sop(self.spec, self.out)
        for step in payload["steps"]:
            self.assertIn("[待确认：", step["equipment"])
        self.assertEqual(payload["summary"]["step_count"], 7)
        self.assertEqual(payload["summary"]["placeholder_count"], 7)


class ReconcileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = make_spec()
        self.brief = ContentBrief.from_dict({"product_name": "测试产品"})
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.bom = generate_bom(self.spec, self.brief, self.out)
        self.sop = generate_sop(self.spec, self.out)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_consistent_bom_and_sop(self) -> None:
        review = reconcile_bom_sop(self.bom, self.sop)
        self.assertTrue(review["ok"], review["mismatches"])
        self.assertEqual(review["summary"]["mismatch_count"], 0)
        self.assertEqual(review["summary"]["bom_only_count"], 0)
        self.assertEqual(review["summary"]["sop_only_count"], 0)

    def test_detects_deliberate_qty_and_spec_mismatch(self) -> None:
        for item in self.bom["items"]:
            if item["name"] == "纸板/卡纸":
                item["qty"] = 999.0
                item["spec"] = "XYZ 350g 白卡"
        review = reconcile_bom_sop(self.bom, self.sop)
        self.assertFalse(review["ok"])
        kinds = {mismatch["kind"] for mismatch in review["mismatches"]}
        topics = {mismatch["topic"] for mismatch in review["mismatches"]}
        self.assertIn("qty", kinds)
        self.assertIn("spec", kinds)
        self.assertIn("paper", topics)

    def test_detects_bom_only_and_sop_only(self) -> None:
        self.bom["items"].append(
            {"name": "神秘辅料", "spec": "[待确认：规格]", "qty": 1.0, "unit": "个"}
        )
        review = reconcile_bom_sop(self.bom, self.sop)
        self.assertTrue(any(item["topic"] is None for item in review["bom_only"]))

        self.bom["items"] = [
            item for item in self.bom["items"] if item["name"] != "印刷油墨"
        ]
        review = reconcile_bom_sop(self.bom, self.sop)
        self.assertTrue(any(item["topic"] == "ink" for item in review["sop_only"]))
        self.assertIn("ink", {mismatch["topic"] for mismatch in review["mismatches"]})

    def test_writes_review_markdown(self) -> None:
        review = reconcile_bom_sop(self.bom, self.sop)
        review_path = Path(review["files"]["bom_sop_review_md"])
        self.assertTrue(review_path.is_file())
        text = review_path.read_text(encoding="utf-8")
        self.assertIn("BOM / SOP 一致性复核", text)
        self.assertIn("BOM-only", text)
        self.assertIn("SOP-only", text)


class ReviewTemplateTest(unittest.TestCase):
    def test_template_contains_required_columns(self) -> None:
        template = build_review_template()
        for column in ("问题", "根因", "改进项", "责任人", "状态"):
            self.assertIn(column, template)

    def test_template_renders_items(self) -> None:
        from packstudio.engines.packaging_engine.bom.models import ReviewItem

        template = build_review_template(
            [ReviewItem("翘边", "胶水开放时间过长", "改用快干胶", "张三", "open")]
        )
        self.assertIn("翘边", template)
        self.assertIn("张三", template)
        payload = build_review_payload(
            [ReviewItem("翘边", "胶水开放时间过长", "改用快干胶", "张三", "open")]
        )
        self.assertEqual(payload["summary"]["item_count"], 1)


if __name__ == "__main__":
    unittest.main()
