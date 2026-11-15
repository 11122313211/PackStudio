"""pdfaudit 模块测试（TECH-DESIGN §7.1 pdfaudit 行 + §7.4 fixture）。

fixture 由 tests/fixtures/make_pdf_fixtures.py 程序化生成（session 级、
写入 pytest 临时目录，不留仓库内二进制）。

说明：pdfaudit 依赖的 icc/（ProfileInfo/load_profile）由并行开发交付。
ICC 就绪时断言嵌入 profile 的完整解析；未就绪时断言降级路径
（dest_profile/icc_profile 为 None 且 warnings 说明）——两种状态测试均绿。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# --- 导入引导：fixtures 目录 ---
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_FIXTURES))

import make_pdf_fixtures as mpf  # noqa: E402

from packstudio.engines.color_engine.pdfaudit import (ImageColorEntry, OutputIntentInfo,  # noqa: E402
                                  PageColorReport, PdfColorAuditResult,
                                  SpotColorEntry, audit_pdf)
from packstudio.engines.color_engine.pdfaudit.audit import ICC_AVAILABLE, PdfAuditError  # noqa: E402


@pytest.fixture(scope="session")
def pdf_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """session 级生成全部 PDF fixture（§7.4：程序化生成，不留二进制）。"""
    return mpf.generate_all(tmp_path_factory.mktemp("pdf_fixtures"))


# ---------------------------------------------------------------------------
# OutputIntent 与嵌入 ICC profile
# ---------------------------------------------------------------------------
class TestOutputIntent:
    def test_output_intent_gts_pdfa1(self, pdf_fixtures: dict[str, Path]) -> None:
        """with_oi fixture：output_intents 长度 1、subtype='GTS_PDFA1'（§7.1）。"""
        result = audit_pdf(pdf_fixtures["with_oi"])
        assert result.page_count == 1
        assert not result.encrypted
        assert len(result.output_intents) == 1
        oi = result.output_intents[0]
        assert isinstance(oi, OutputIntentInfo)
        assert oi.subtype == "GTS_PDFA1"
        assert oi.identifier == "SyntheticRGB"
        assert oi.output_condition is not None and "Synthetic" in oi.output_condition
        assert oi.registry == "http://www.color.org"
        assert oi.has_dest_profile is True
        # 内置 DigestValue = bytes(range(16)) → 固定 hex
        assert oi.dest_profile_digest == bytes(range(16)).hex()

    def test_dest_profile_parsed_or_degraded(self,
                                              pdf_fixtures: dict[str, Path]) -> None:
        """DestOutputProfile：ICC 就绪时解析为 N=3 RGB；未就绪时降级 + warning。"""
        result = audit_pdf(pdf_fixtures["with_oi"])
        oi = result.output_intents[0]
        if ICC_AVAILABLE:
            assert oi.dest_profile is not None
            assert oi.dest_profile.header.color_space == "RGB "  # N=3 等价断言
            assert "Synthetic" in oi.dest_profile.description
            assert not any("icc 模块不可用" in w for w in result.warnings)
        else:  # 并行开发期：结构盘点照常，嵌入 profile 降级为 None + warning
            assert oi.dest_profile is None
            assert any("DestOutputProfile" in w and "icc 模块不可用" in w
                       for w in result.warnings)

    def test_no_dest_profile_intent(self, tmp_path: Path) -> None:
        """有 /S 但缺 DestOutputProfile → warning（§4.10 步骤 6）。"""
        import pikepdf

        src = tmp_path / "bare_oi.pdf"
        mpf.make_rgb_pdf(src, with_output_intent=False)
        with pikepdf.open(src, allow_overwriting_input=True) as pdf:
            oi = pikepdf.Dictionary(Type=pikepdf.Name.OutputIntent,
                                    S=pikepdf.Name.GTS_PDFX,
                                    OutputConditionIdentifier=pikepdf.String("Bare"))
            pdf.Root.OutputIntents = pikepdf.Array([oi])
            pdf.save(src)
        result = audit_pdf(src)
        assert len(result.output_intents) == 1
        assert result.output_intents[0].subtype == "GTS_PDFX"
        assert result.output_intents[0].has_dest_profile is False
        assert any("缺少 DestOutputProfile" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 页面色彩空间与图像
# ---------------------------------------------------------------------------
class TestPagesAndImages:
    def test_rgb_page_audit(self, pdf_fixtures: dict[str, Path]) -> None:
        """无 OI 的 RGB PDF：DeviceRGB 页 + 图像条目 + no OutputIntent warning。"""
        result = audit_pdf(pdf_fixtures["no_oi"])
        assert isinstance(result, PdfColorAuditResult)
        assert result.page_count == 1
        assert len(result.output_intents) == 0
        assert any("no OutputIntent" in w for w in result.warnings)

        page = result.pages[0]
        assert isinstance(page, PageColorReport)
        assert page.color_spaces_used.get("DeviceRGB") == 1
        assert page.fills_strokes_noted is False  # v1 不解析内容流

        assert len(page.images) == 1
        image = page.images[0]
        assert isinstance(image, ImageColorEntry)
        assert image.page == 1
        assert image.color_space == "DeviceRGB"
        assert image.width == 64 and image.height == 64 and image.bpc == 8
        assert image.icc_profile is None  # DeviceRGB 图像无嵌入 ICC

        assert result.summary["device_rgb_pages"] == 1
        assert result.summary["device_cmyk_pages"] == 0
        assert result.summary["images"] == 1

    def test_cmyk_page_audit(self, pdf_fixtures: dict[str, Path]) -> None:
        """CMYK fixture：DeviceCMYK 图像 → device_cmyk_pages=1。"""
        result = audit_pdf(pdf_fixtures["cmyk_spots"])
        page = result.pages[0]
        assert page.color_spaces_used.get("DeviceCMYK") == 1
        assert page.color_spaces_used.get("Separation") == 2
        assert page.color_spaces_used.get("DeviceN") == 1
        assert len(page.images) == 1
        assert page.images[0].name == "Im0"
        assert page.images[0].color_space == "DeviceCMYK"
        assert result.summary["device_cmyk_pages"] == 1


# ---------------------------------------------------------------------------
# 专色
# ---------------------------------------------------------------------------
class TestSpots:
    def test_separation_and_devicen_spots(self, pdf_fixtures: dict[str, Path]) -> None:
        """Separation 'SpotA' 收录为 spots[0]；DeviceN 成员展开（§7.1）。"""
        result = audit_pdf(pdf_fixtures["cmyk_spots"])
        spots = result.spots
        assert len(spots) == 4
        assert spots[0].name == "SpotA"
        assert spots[0].space == "Separation"
        assert spots[0].alternate == "DeviceGray"
        assert spots[0].alternate_lab is None  # Gray alternate 无 Lab 外观值
        assert spots[0].tint_transform_readable is True  # FunctionType 2

        spot_b = next(s for s in spots if s.name == "SpotB")
        assert spot_b.space == "Separation"
        assert spot_b.alternate == "Lab"
        # tint=1.0 → FunctionType 2 的 C1=[50, 70, -30]
        assert spot_b.alternate_lab == (50.0, 70.0, -30.0)

        device_n = [s for s in spots if s.space == "DeviceN"]
        assert {s.name for s in device_n} == {"SpotA", "SpotC"}
        assert all(s.alternate == "DeviceGray" for s in device_n)

        assert result.summary["spot_colors"] == len(spots)

    def test_spots_dedup_across_definitions(self, tmp_path: Path) -> None:
        """同一专色重复定义（定义 + 图像引用）只收录一次。"""
        import pikepdf

        src = tmp_path / "dup_spot.pdf"
        mpf.make_cmyk_spots_pdf(src)
        with pikepdf.open(src, allow_overwriting_input=True) as pdf:
            raw = pdf.pages[0].obj
            image = raw.Resources.XObject.Im0
            image.ColorSpace = pikepdf.Name("/CS0")  # 图像改用 SpotA 分离色
            pdf.save(src)
        result = audit_pdf(src)
        sep_a = [s for s in result.spots
                 if s.name == "SpotA" and s.space == "Separation"]
        assert len(sep_a) == 1


# ---------------------------------------------------------------------------
# 边界路径（嵌套 Form / 悬空引用 / 损坏 ICC）
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_nested_form_and_warnings(self, pdf_fixtures: dict[str, Path]) -> None:
        """嵌套 Form XObject 递归 + 悬空引用 + 损坏 ICCBased 流（§4.10 步骤 6）。"""
        result = audit_pdf(pdf_fixtures["edge_cases"])
        page = result.pages[0]
        assert len(page.images) == 4
        by_name = {image.name: image for image in page.images}
        # 嵌套在 Form 内的 DeviceGray 图像被找到，页码仍为 1
        assert by_name["Im1"].color_space == "DeviceGray"
        assert by_name["Im1"].page == 1
        assert by_name["Im0"].color_space == "DeviceRGB"
        assert by_name["Im2"].color_space == "Unknown"  # 悬空引用
        assert by_name["Im3"].color_space == "ICCBased"
        if ICC_AVAILABLE:
            assert by_name["Im3"].icc_profile is None  # 64 字节非 ICC → 损坏

        joined = "\n".join(result.warnings)
        assert "ColorSpace 引用悬空" in joined
        assert "/NoSuchCS" in joined
        assert "ICCBased" in joined  # 损坏流或 icc 缺席均有说明
        assert page.color_spaces_used.get("DeviceGray") == 1
        assert page.color_spaces_used.get("Unknown") == 1
        assert result.summary["images"] == 4
        assert result.summary["device_rgb_pages"] == 1


# ---------------------------------------------------------------------------
# 加密与异常层级
# ---------------------------------------------------------------------------
class TestErrors:
    def test_encrypted_without_password(self, pdf_fixtures: dict[str, Path]) -> None:
        """加密 PDF 无口令 → PdfAuditError code='PDF_ENCRYPTED'（§7.1）。"""
        with pytest.raises(PdfAuditError) as excinfo:
            audit_pdf(pdf_fixtures["encrypted"])
        assert excinfo.value.code == "PDF_ENCRYPTED"
        assert isinstance(excinfo.value.detail, dict)

    def test_encrypted_with_password(self, pdf_fixtures: dict[str, Path]) -> None:
        """携带口令可审计：encrypted=True、RGB 页照常盘点。"""
        result = audit_pdf(pdf_fixtures["encrypted"], password=mpf.ENCRYPTED_PASSWORD)
        assert result.encrypted is True
        assert result.page_count == 1
        assert result.summary["device_rgb_pages"] == 1

    def test_missing_file(self, tmp_path: Path) -> None:
        """文件不存在 → PdfAuditError，detail 带 path。"""
        missing = tmp_path / "missing.pdf"
        with pytest.raises(PdfAuditError) as excinfo:
            audit_pdf(missing)
        assert excinfo.value.code == "PDF_AUDIT"
        assert excinfo.value.detail.get("path") == str(missing)

    def test_corrupt_file(self, tmp_path: Path) -> None:
        """损坏文件 → PdfAuditError。"""
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"this is not a pdf at all")
        with pytest.raises(PdfAuditError) as excinfo:
            audit_pdf(bad)
        assert excinfo.value.code == "PDF_AUDIT"


# ---------------------------------------------------------------------------
# 输入形态与 JSON 契约
# ---------------------------------------------------------------------------
class TestInputAndJson:
    def test_bytes_input_matches_path(self, pdf_fixtures: dict[str, Path]) -> None:
        """bytes 输入与路径输入盘点结果一致（§4.10 签名支持两种来源）。"""
        by_path = audit_pdf(pdf_fixtures["with_oi"])
        by_bytes = audit_pdf(pdf_fixtures["with_oi"].read_bytes())
        assert by_bytes.path == "<bytes>"
        assert by_bytes.summary == by_path.summary
        assert by_bytes.output_intents[0].subtype == by_path.output_intents[0].subtype
        assert (by_bytes.pages[0].color_spaces_used
                == by_path.pages[0].color_spaces_used)

    def test_as_json_contract(self, pdf_fixtures: dict[str, Path]) -> None:
        """as_json() 字段名即 §3.5 契约，可直接 json.dumps。"""
        result = audit_pdf(pdf_fixtures["with_oi"])
        payload = result.as_json()
        assert list(payload) == ["path", "pdf_version", "page_count", "encrypted",
                                 "output_intents", "pages", "spots", "warnings",
                                 "summary"]
        text = json.dumps(payload, ensure_ascii=False)
        loaded = json.loads(text)
        assert loaded["output_intents"][0]["subtype"] == "GTS_PDFA1"
        assert loaded["summary"]["device_rgb_pages"] == 1
        assert loaded["pages"][0]["images"][0]["color_space"] == "DeviceRGB"
        # 多页数据模型序列化往返
        page_payload = result.pages[0].as_json()
        assert set(page_payload) == {"page", "color_spaces_used",
                                     "fills_strokes_noted", "images"}
        spot = SpotColorEntry(name="X", space="Separation", alternate="Lab",
                              alternate_lab=(50.0, 0.0, 0.0),
                              tint_transform_readable=True)
        assert json.loads(json.dumps(spot.as_json()))["alternate_lab"] == [50.0, 0.0, 0.0]

    def test_package_exports(self) -> None:
        """__init__ 导出面与设计 §2.5 一致。"""
        import packstudio.engines.color_engine.pdfaudit as pkg

        assert set(pkg.__all__) == {"OutputIntentInfo", "PageColorReport",
                                    "ImageColorEntry", "SpotColorEntry",
                                    "PdfColorAuditResult", "audit_pdf"}
