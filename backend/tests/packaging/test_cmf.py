"""CMF 模块测试：工艺分类、兼容性、Provider 契约、plan_cmf / visual_qa。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from packstudio.engines.packaging_engine.cmf.compatibility import (
    compatibility_plan,
    compatibility_report,
    compatible,
    resolve_material,
)
from packstudio.engines.packaging_engine.cmf.finishes import (
    FINISH_TAXONOMY,
    finish_names_zh,
    resolve_finish,
)
from packstudio.engines.packaging_engine.cmf.providers import (
    BaseProvider,
    CustomRESTProvider,
    MockProvider,
    OpenAICompatibleProvider,
    ProviderRegistry,
)
from packstudio.engines.packaging_engine.cmf.service import plan_cmf, visual_qa
from packstudio.engines.packaging_engine.exceptions import ProviderError, RequestValidationError

REQUIRED_FINISHES = {
    "foil_gold",
    "foil_silver",
    "spot_uv",
    "emboss",
    "deboss",
    "gloss_lamination",
    "matte_lamination",
    "soft_touch",
    "holographic",
    "embossing_die",
    "screen_print",
}


def _make_artwork(directory: Path, name: str = "artwork.png") -> Path:
    target = directory / name
    target.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")
    return target


class FinishTaxonomyTests(unittest.TestCase):
    def test_taxonomy_not_empty(self) -> None:
        self.assertTrue(FINISH_TAXONOMY)

    def test_required_finishes_present(self) -> None:
        self.assertTrue(REQUIRED_FINISHES.issubset(set(FINISH_TAXONOMY)))

    def test_entries_have_required_fields(self) -> None:
        for key, entry in FINISH_TAXONOMY.items():
            self.assertEqual(entry["key"], key)
            self.assertTrue(entry["label_zh"])
            self.assertTrue(entry["en"])
            self.assertIsInstance(entry["params"], dict)
            self.assertTrue(entry["cost_hint"])
            self.assertTrue(entry["notes"])

    def test_finish_names_zh_unique(self) -> None:
        names = finish_names_zh()
        self.assertEqual(len(names), len(set(names)))

    def test_resolve_finish_aliases(self) -> None:
        self.assertEqual(resolve_finish("烫金"), "foil_gold")
        self.assertEqual(resolve_finish("局部UV"), "spot_uv")
        self.assertEqual(resolve_finish("spot uv"), "spot_uv")
        self.assertEqual(resolve_finish("丝网印刷"), "screen_print")
        self.assertEqual(resolve_finish("embossing"), "emboss")
        self.assertEqual(resolve_finish("不存在的工艺"), "")


class CompatibilityTests(unittest.TestCase):
    def test_resolve_material_aliases(self) -> None:
        self.assertEqual(resolve_material("白卡"), "white_card")
        self.assertEqual(resolve_material("瓦楞纸"), "corrugated")
        self.assertEqual(resolve_material("PET"), "plastic")
        self.assertEqual(resolve_material("塑胶"), "plastic")
        self.assertEqual(resolve_material("不存在的材料"), "")

    def test_white_card_supports_foil_uv_emboss(self) -> None:
        self.assertTrue(compatible("白卡", "烫金"))
        self.assertTrue(compatible("白卡", "局部UV"))
        self.assertTrue(compatible("白卡", "击凸"))

    def test_corrugated_supports_screen_print(self) -> None:
        self.assertTrue(compatible("瓦楞纸", "丝印"))

    def test_corrugated_foil_needs_precoat(self) -> None:
        report = compatibility_report("瓦楞纸", "烫金")
        self.assertEqual(report["status"], "precoat_required")
        self.assertTrue(report["compatible"])  # 默认允许有条件
        self.assertTrue(report["preconditions"])
        strict = compatibility_report("瓦楞纸", "烫金", allow_preconditions=False)
        self.assertFalse(strict["compatible"])

    def test_corrugated_lamination_unsupported(self) -> None:
        self.assertFalse(compatible("瓦楞纸", "覆膜光"))
        self.assertFalse(compatible("瓦楞纸", "覆膜触感"))

    def test_plastics_differ_from_paper(self) -> None:
        self.assertTrue(compatible("PET", "烫金"))
        self.assertTrue(compatible("塑胶", "局部UV"))
        self.assertTrue(compatible("塑胶", "压凹"))
        self.assertEqual(compatibility_report("PET", "烫金")["material_key"], "plastic")

    def test_unknown_inputs(self) -> None:
        self.assertFalse(compatible("未知", "烫金"))
        self.assertFalse(compatible("白卡", "未知工艺"))
        report = compatibility_report("未知", "烫金")
        self.assertFalse(report["compatible"])
        self.assertEqual(report["status"], "unknown")

    def test_compatibility_plan_flags_validation(self) -> None:
        plan = compatibility_plan("瓦楞纸", ["丝印", "烫金"])
        self.assertTrue(plan["all_compatible"])
        self.assertTrue(plan["requires_manufacturer_validation"])
        bad = compatibility_plan("瓦楞纸", ["覆膜光"])
        self.assertFalse(bad["all_compatible"])
        self.assertEqual(bad["blocking"], ["gloss_lamination"])


class ProviderContractTests(unittest.TestCase):
    def test_base_provider_is_abstract(self) -> None:
        with self.assertRaises(TypeError):
            BaseProvider()  # type: ignore[abstract]

    def test_mock_provider_labels_output(self) -> None:
        provider = MockProvider()
        self.assertTrue(provider.supports("image_generation"))
        for operation in ("image_generation", "visual_qa", "cost_estimate"):
            output = provider.invoke(operation, {})
            self.assertTrue(output["mock"])
            self.assertTrue(output["manual_review"])
            self.assertEqual(output["label"], "mock")
            self.assertIn("MOCK_OUTPUT_NOT_A_REAL_CMF_RENDER", output["warnings"])

    def test_unconfigured_openai_provider_raises(self) -> None:
        provider = OpenAICompatibleProvider(endpoint="", api_key_env="CMF_TEST_NONEXISTENT_KEY")
        with self.assertRaises(ProviderError) as ctx:
            provider.invoke("image_generation", {})
        self.assertEqual(ctx.exception.code, "PROVIDER_NOT_CONFIGURED")

    def test_unconfigured_custom_rest_provider_raises(self) -> None:
        provider = CustomRESTProvider(endpoint="", api_key_env="CMF_TEST_NONEXISTENT_KEY")
        with self.assertRaises(ProviderError) as ctx:
            provider.invoke("image_generation", {})
        self.assertEqual(ctx.exception.code, "PROVIDER_NOT_CONFIGURED")

    def test_registry_requires_capability(self) -> None:
        registry = ProviderRegistry()
        with self.assertRaises(ProviderError) as ctx:
            registry.resolve("image_generation", None, required=True)
        self.assertEqual(ctx.exception.code, "PROVIDER_UNAVAILABLE")
        self.assertIsNone(registry.resolve("image_generation", None, required=False))

    def test_registry_rejects_wrong_capability(self) -> None:
        class _TextOnly(BaseProvider):
            name = "text_only"
            provider_type = "stub"
            capabilities = frozenset({"text"})

            def invoke(self, operation, payload):  # pragma: no cover - never called
                return {}

        registry = ProviderRegistry([_TextOnly()])
        with self.assertRaises(ProviderError) as ctx:
            registry.resolve("image_generation", "text_only")
        self.assertEqual(ctx.exception.code, "PROVIDER_CAPABILITY_MISSING")


class ServiceTests(unittest.TestCase):
    def test_plan_cmf_writes_json_and_labels_mock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artwork = _make_artwork(tmp_path)
            plan = plan_cmf(
                artwork,
                {"length": 88, "width": 55, "height": 22, "unit": "mm"},
                "白卡",
                ["烫金", "局部UV", "不存在的工艺"],
                provider=MockProvider(),
                output_dir=tmp_path,
                structure="tobacco.hard_box_hinge_lid",
            )
            self.assertTrue(plan["provider"]["mock"])
            self.assertIn("MOCK_PROVIDER_USED_OUTPUT_IS_NOT_PRODUCTION_EVIDENCE", plan["warnings"])
            self.assertIn("UNRECOGNIZED_FINISH_INPUT", plan["warnings"])
            self.assertFalse(plan["production_file"])
            self.assertEqual(len(plan["finishes"]), 2)

            written = Path(plan["outputs"]["cmf_plan"])
            self.assertTrue(written.is_file())
            self.assertEqual(written.name, "cmf-plan.json")
            reloaded = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(reloaded["material"]["key"], "white_card")
            self.assertEqual(reloaded["dimensions"]["length"], 88.0)

    def test_plan_cmf_without_provider_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artwork = _make_artwork(Path(tmp))
            with self.assertRaises(ProviderError) as ctx:
                plan_cmf(
                    artwork,
                    {"length": 88, "width": 55, "height": 22},
                    "白卡",
                    ["烫金"],
                    registry=ProviderRegistry(),
                )
            self.assertEqual(ctx.exception.code, "PROVIDER_UNAVAILABLE")

    def test_plan_cmf_rejects_bad_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artwork = _make_artwork(Path(tmp))
            with self.assertRaises(RequestValidationError) as ctx:
                plan_cmf(artwork, {"length": 1, "width": 1, "height": 1}, "未知材质", ["烫金"], provider=MockProvider())
            self.assertEqual(ctx.exception.code, "UNKNOWN_MATERIAL")

    def test_visual_qa_mock_never_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artwork = _make_artwork(tmp_path)
            generated = tmp_path / "generated.png"
            generated.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            result = visual_qa(
                artwork,
                generated,
                {"length": 88, "width": 55, "height": 22},
                "白卡",
                ["烫金"],
                provider=MockProvider(),
            )
            self.assertTrue(result["mock"])
            self.assertTrue(result["manual_review"])
            self.assertFalse(result["passed"])
            self.assertEqual(result["recommended_action"], "manual_review")
            self.assertEqual(len(result["checks"]), 10)
            self.assertIn("MOCK_VISUAL_QA_REQUIRES_MANUAL_REVIEW", result["warnings"])

    def test_visual_qa_without_provider_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artwork = _make_artwork(tmp_path)
            generated = tmp_path / "generated.png"
            generated.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            with self.assertRaises(ProviderError):
                visual_qa(
                    artwork,
                    generated,
                    {"length": 88, "width": 55, "height": 22},
                    "白卡",
                    registry=ProviderRegistry(),
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
