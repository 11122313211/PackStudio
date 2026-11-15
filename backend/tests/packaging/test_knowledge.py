"""知识库加载器测试：烟包/塑胶结构、Markdown 工艺、缺失条目。"""

from __future__ import annotations

import unittest

from packstudio.engines.packaging_engine.exceptions import KnowledgeMissingError
from packstudio.engines.packaging_engine.knowledge import (
    DOMAIN_DIRS,
    KNOWLEDGE_DIR,
    list_domains,
    list_knowledge,
    load_knowledge,
)


class LoaderBasicsTests(unittest.TestCase):
    def test_domains(self) -> None:
        self.assertIn("tobacco", DOMAIN_DIRS)
        self.assertIn("plastics", DOMAIN_DIRS)
        self.assertIn("transport", DOMAIN_DIRS)
        self.assertIn("tobacco", list_domains())

    def test_knowledge_dir_exists(self) -> None:
        self.assertTrue(KNOWLEDGE_DIR.is_dir())

    def test_list_knowledge_tobacco(self) -> None:
        names = list_knowledge("tobacco")
        self.assertIn("structures", names)
        self.assertIn("process", names)

    def test_list_knowledge_plastics(self) -> None:
        names = list_knowledge("plastics")
        self.assertIn("structures", names)
        self.assertIn("process", names)


class StructureKnowledgeTests(unittest.TestCase):
    def test_tobacco_structures(self) -> None:
        data = load_knowledge("tobacco", "structures")
        self.assertEqual(data["domain"], "tobacco")
        codes = {item["code"] for item in data["structures"]}
        self.assertEqual(
            codes,
            {
                "tobacco.soft_pack",
                "tobacco.hard_box_hinge_lid",
                "tobacco.hard_box_side_open",
                "tobacco.carton",
            },
        )
        for item in data["structures"]:
            self.assertTrue(item["name_zh"])
            self.assertTrue(item["panels"])
            self.assertIn("typical_dimensions", item)
            self.assertTrue(item["requires_manufacturer_validation"])

    def test_plastics_structures(self) -> None:
        data = load_knowledge("plastics", "structures")
        self.assertEqual(data["domain"], "plastics")
        codes = {item["code"] for item in data["structures"]}
        self.assertEqual(
            codes,
            {"plastics.thermoform", "plastics.injection", "plastics.blow_mold"},
        )
        for item in data["structures"]:
            self.assertTrue(item["requires_manufacturer_validation"])
            self.assertIn("dimension_tolerance_rules", item)

    def test_structures_with_explicit_extension(self) -> None:
        data = load_knowledge("tobacco", "structures.json")
        self.assertEqual(data["name"], "structures")

    def test_tobacco_process_is_markdown(self) -> None:
        data = load_knowledge("tobacco", "process")
        self.assertEqual(data["format"], "markdown")
        self.assertIn("需厂商验证", data["content"])
        self.assertIn("CRD", data["content"])
        self.assertIn("拉线防伪", data["content"])

    def test_plastics_process_is_markdown(self) -> None:
        data = load_knowledge("plastics", "process")
        self.assertEqual(data["format"], "markdown")
        self.assertIn("需厂商验证", data["content"])

    def test_transport(self) -> None:
        data = load_knowledge("transport", "transport")
        self.assertEqual(data["domain"], "transport")
        self.assertIn("stacking", data)
        self.assertIn("compressive_strength_estimate", data)
        self.assertTrue(data["requires_manufacturer_validation"])
        self.assertTrue(data["compressive_strength_estimate"]["placeholder"])


class MissingKnowledgeTests(unittest.TestCase):
    def test_missing_entry_raises(self) -> None:
        with self.assertRaises(KnowledgeMissingError) as ctx:
            load_knowledge("tobacco", "does_not_exist")
        self.assertEqual(ctx.exception.code, "KNOWLEDGE_MISSING")
        self.assertEqual(ctx.exception.details["domain"], "tobacco")
        self.assertEqual(ctx.exception.details["name"], "does_not_exist")

    def test_unknown_domain_raises(self) -> None:
        with self.assertRaises(KnowledgeMissingError) as ctx:
            load_knowledge("unknown_domain", "structures")
        self.assertEqual(ctx.exception.code, "KNOWLEDGE_DOMAIN_UNKNOWN")

    def test_empty_inputs_raise(self) -> None:
        with self.assertRaises(KnowledgeMissingError):
            load_knowledge("", "structures")
        with self.assertRaises(KnowledgeMissingError):
            load_knowledge("tobacco", "")

    def test_missing_domain_lists_nothing(self) -> None:
        self.assertEqual(list_knowledge("tobacco"), ["process", "structures"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
