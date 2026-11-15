"""measurement/verify.py 单元测试（TECH-DESIGN §7.1 verify 测试矩阵 + §4.9 契约）。

覆盖：
- ΔE00 已知值（Sharma 经典校验例 ≈2.0425）与 colour-science 直接对照、L clip、
  形状校验（test_delta_e00_known / test_delta_e00_*）；
- Fogra39 内置参考表 §6.1 表值抽查与 ISO12647_2_TOLERANCES（test_reference_table）；
- verify_reference 纯 Lab 快捷路径（恒等判定、失败行、行数校验）；
- verify_cgats 纯 Lab 文件（SAMPLE_ID 名称对齐、额外 patch 排除、无 Lab 列/
  无参考来源/对齐失败等异常码）；
- 参考 Lab 注入路径（lab_transform：device 标度 0-100 透传、intent/bpc 透传、
  返回形状/有限性校验、tolerance 覆盖）；
- profile 驱动真实集成（JapanColor2001Coated 恒等测量 mean ΔE00 < 0.6、sRGB
  RGB 设备列路径；系统 profile 缺失则 skip）；
- PASS/WARNING/FAIL 判定阈值边界（overall_verdict + VERDICT_WARNING_MARGIN）；
- summarize / as_json 序列化契约。

8bit Lab 量化（设计 §8 R5）：经 cmm.transform_lab 的 Lab 为 8bit 量化值
（L 步进 ≈0.39、a/b 步进 1.0）——恒等测量测试中两侧取同一量化输出，ΔE00=0；
对 ISO 容差（≥3.0）不致失判。
"""
from __future__ import annotations

import json
import pathlib

import colour
import numpy as np
import pytest

from fixtures.synthetic import require_system_profile
from packstudio.engines.color_engine.cmm.engine import RenderingIntent, transform_lab
from packstudio.engines.color_engine.errors import MeasurementError
from packstudio.engines.color_engine.measurement import (
    REFERENCE_TABLES,
    CgatsData,
    DeltaEStats,
    IsoVerdict,
    PatchVerdict,
    VerifyResult,
    delta_e_00,
    overall_verdict,
    parse_cgats,
    reference_table,
    summarize,
    verify_cgats,
    verify_reference,
    verify_report_to_cgats,
    channel_diagnostics,
)
from packstudio.engines.color_engine.measurement.verify import (
    ISO12647_2_TOLERANCES,
    VERDICT_WARNING_MARGIN,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FOGRA39_9PATCH = FIXTURES / "fogra39_9patch.cgats"
LAB_ONLY = FIXTURES / "lab_only_names.cgats"

TABLE = REFERENCE_TABLES["Fogra39"]
TARGETS = tuple(p.target for p in TABLE.patches)

# 设计 §6.1 官方 characterization 数值（测试断言基准）
FOGRA39_LAB: dict[str, tuple[float, float, float]] = {
    "paper": (95.0, 0.0, -2.0),
    "C": (55.0, -37.0, -50.0),
    "M": (48.0, 74.0, -3.0),
    "Y": (89.0, -5.0, 93.0),
    "K": (16.0, 0.0, 0.0),
    "CM": (24.0, 22.0, -46.0),
    "CY": (50.0, -65.0, 27.0),
    "MY": (47.0, 68.0, 48.0),
    "CMY": (23.0, 0.0, 0.0),
}


def _device_matrix() -> np.ndarray:
    """参考表全部 device 码，shape (9, 4)（CGATS 0-100 标度）。"""
    return np.asarray([p.device for p in TABLE.patches], dtype=np.float64)


def _cmyk_cgats(lab: np.ndarray, patch_ids: tuple[str, ...] = TARGETS) -> CgatsData:
    """由 device 码 + Lab 矩阵构造带 CMYK 设备列的测量数据（供注入/profile 路径）。"""
    lab = np.asarray(lab, dtype=np.float64)
    return CgatsData(
        meta={},
        fields=("SAMPLE_ID", "CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K", "LAB_L", "LAB_A", "LAB_B"),
        patch_ids=patch_ids,
        values=np.hstack([_device_matrix(), lab]),
    )


def _lab_cgats(lab: np.ndarray, patch_ids: tuple[str, ...]) -> CgatsData:
    """构造仅含 Lab 列的测量数据。"""
    return CgatsData(
        meta={},
        fields=("SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B"),
        patch_ids=patch_ids,
        values=np.asarray(lab, dtype=np.float64),
    )


def _synthetic_result(delta_e: float, tolerance: float, *, with_iso: bool = True) -> VerifyResult:
    """构造单 patch 的 VerifyResult（用于 overall_verdict 阈值边界的精确控制）。

    stats.max == delta_e；iso 行（若有）ΔE00=delta_e、容差=tolerance；
    passed 语义与 _build_result 一致（iso 全过 且 stats.max <= 达标线）。
    """
    patch = PatchVerdict(
        patch_id="p1",
        measured_lab=(50.0, 0.0, 0.0),
        reference_lab=(50.0, 0.0, 0.0),
        delta_e_76=delta_e,
        delta_e_00=delta_e,
        device=None,
    )
    stats = DeltaEStats(
        count=1,
        mean=delta_e,
        max=delta_e,
        stdev=0.0,
        percentiles={f"p{k}": delta_e for k in (50, 90, 95, 99)},
        worst=(patch,),
    )
    if with_iso:
        iso: tuple[IsoVerdict, ...] | None = (
            IsoVerdict(
                patch_id="p1",
                target="C",
                measured_lab=(50.0, 0.0, 0.0),
                reference_lab=FOGRA39_LAB["C"],
                delta_e_00=delta_e,
                tolerance=tolerance,
                passed=bool(delta_e <= tolerance),
            ),
        )
        passed = bool(delta_e <= tolerance and stats.max <= tolerance)
    else:
        iso = None
        passed = bool(stats.max <= tolerance)
    return VerifyResult(
        profile_path="",
        profile_description="",
        intent="",
        bpc=False,
        reference_name="Fogra39" if with_iso else None,
        patches=(patch,),
        stats=stats,
        iso=iso,
        passed=passed,
        tolerance=tolerance,
    )


# ---------------------------------------------------------------------------
# ΔE00（设计测试矩阵：test_delta_e00_known）
# ---------------------------------------------------------------------------


def test_delta_e00_known() -> None:
    """经典校验例（Sharma 2005 数据 #1）：ΔE00 ≈ 2.0425。"""
    lab1 = np.array([[50.0, 2.6772, -79.7751]])
    lab2 = np.array([[50.0, 0.0, -82.7485]])
    assert float(delta_e_00(lab1, lab2)[0]) == pytest.approx(2.0425, abs=2e-4)


def test_delta_e00_matches_colour_science() -> None:
    """随机 Lab 对与 colour.delta_E(method='CIE 2000') 逐点一致。"""
    rng = np.random.default_rng(20261107)
    lab1 = np.column_stack(
        [rng.uniform(0.0, 100.0, 64), rng.uniform(-128.0, 127.0, 64), rng.uniform(-128.0, 127.0, 64)]
    )
    lab2 = np.column_stack(
        [rng.uniform(0.0, 100.0, 64), rng.uniform(-128.0, 127.0, 64), rng.uniform(-128.0, 127.0, 64)]
    )
    expected = colour.delta_E(lab1, lab2, method="CIE 2000")
    got = delta_e_00(lab1, lab2)
    assert got.shape == (64,)
    assert got.dtype == np.float64
    assert np.allclose(got, np.asarray(expected, dtype=np.float64), rtol=0.0, atol=1e-12)


def test_delta_e00_input_normalization() -> None:
    """一维输入视为单行；L<0 clip 到 0；形状不一致 → BAD_LAB_SHAPE。"""
    single = delta_e_00(np.array([50.0, 2.6772, -79.7751]), np.array([50.0, 0.0, -82.7485]))
    assert single.shape == (1,)
    assert float(single[0]) == pytest.approx(2.0425, abs=2e-4)

    # L=-5 → 仅 L 列 clip 为 0（b/a 不动）：与手工 clip 后的 colour 结果一致
    a = np.array([[-5.0, 10.0, -10.0]])
    b = np.array([[0.0, 0.0, 0.0]])
    a_clipped = a.copy()
    a_clipped[:, 0] = np.clip(a_clipped[:, 0], 0.0, None)
    expected = colour.delta_E(a_clipped, b, method="CIE 2000")
    assert np.allclose(delta_e_00(a, b), np.asarray(expected, dtype=np.float64))
    # 输入不被就地修改（clip 作用于副本）
    assert a[0, 0] == -5.0

    with pytest.raises(MeasurementError) as excinfo:
        delta_e_00(np.zeros((2, 3)), np.zeros((3, 3)))
    assert excinfo.value.code == "BAD_LAB_SHAPE"


# ---------------------------------------------------------------------------
# 内置参考表（设计测试矩阵：test_reference_table）
# ---------------------------------------------------------------------------


def test_reference_table() -> None:
    """reference_table('Fogra39')：9 patch、值 = §6.1 表、容差 = ISO12647_2_TOLERANCES。"""
    table = reference_table("Fogra39")
    assert table.name == "Fogra39"
    assert table.device_fields == ("CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K")
    assert len(table.patches) == 9
    device_expect = {
        "paper": (0.0, 0.0, 0.0, 0.0),
        "C": (100.0, 0.0, 0.0, 0.0),
        "M": (0.0, 100.0, 0.0, 0.0),
        "Y": (0.0, 0.0, 100.0, 0.0),
        "K": (0.0, 0.0, 0.0, 100.0),
        "CM": (100.0, 100.0, 0.0, 0.0),
        "CY": (100.0, 0.0, 100.0, 0.0),
        "MY": (0.0, 100.0, 100.0, 0.0),
        "CMY": (100.0, 100.0, 100.0, 0.0),
    }
    for patch in table.patches:
        assert patch.device == device_expect[patch.target], patch.target
        assert patch.lab == FOGRA39_LAB[patch.target], patch.target
    assert {p.target: p.tolerance for p in table.patches if p.target == "K"} == {"K": ISO12647_2_TOLERANCES["k"]}
    assert ISO12647_2_TOLERANCES == {"cmy": 5.0, "k": 4.0, "paper": 3.0}
    # 每行容差与目标组一致：K=4.0、paper=3.0、其余（含叠印）=5.0
    for patch in table.patches:
        if patch.target == "paper":
            assert patch.tolerance == 3.0
        elif patch.target == "K":
            assert patch.tolerance == 4.0
        else:
            assert patch.tolerance == 5.0


def test_reference_table_lookup() -> None:
    """大小写不敏感与别名（fogra39l）；未知名 → NO_REFERENCE。"""
    assert reference_table("fogra39") is TABLE
    assert reference_table("FOGRA39L") is TABLE
    assert reference_table(" Fogra39 ") is TABLE
    with pytest.raises(MeasurementError) as excinfo:
        reference_table("Fogra51")
    assert excinfo.value.code == "NO_REFERENCE"
    assert "Fogra51" in str(excinfo.value)


# ---------------------------------------------------------------------------
# verify_reference：纯 Lab 快捷路径
# ---------------------------------------------------------------------------


def test_verify_reference_identity() -> None:
    """以参考表自身 Lab 输入 → ΔE00 全 0、全部通过（含容差边界 <= 语义）。"""
    lab = np.asarray([FOGRA39_LAB[t] for t in TARGETS], dtype=np.float64)
    result = verify_reference(lab)
    assert result.reference_name == "Fogra39"
    assert result.profile_path == ""
    assert result.profile_description == ""
    assert result.intent == ""
    assert result.bpc is False
    assert [v.patch_id for v in result.patches] == list(TARGETS)
    assert result.stats.count == 9
    assert result.stats.max == pytest.approx(0.0, abs=1e-12)
    assert result.stats.mean == pytest.approx(0.0, abs=1e-12)
    assert result.iso is not None and len(result.iso) == 9
    for verdict, target in zip(result.iso, TARGETS):
        assert verdict.target == target
        assert verdict.tolerance == {p.target: p.tolerance for p in TABLE.patches}[target]
        assert verdict.passed is True
    assert result.passed is True
    assert result.tolerance == 5.0  # 达标线 = 参考表最大容差
    assert overall_verdict(result) == "PASS"


def test_verify_reference_failure_rows() -> None:
    """单行（C）人为偏移 → 仅 C 判 FAIL，stats/worst 正确，整体 passed=False。"""
    lab = np.asarray([FOGRA39_LAB[t] for t in TARGETS], dtype=np.float64)
    lab[TARGETS.index("C")] = (62.0, -32.0, -45.0)  # 与 lab_only fixture 的 C 行一致
    result = verify_reference(lab)
    assert result.passed is False
    iso_by_target = {v.target: v for v in result.iso}
    assert iso_by_target["C"].passed is False
    assert iso_by_target["C"].delta_e_00 > iso_by_target["C"].tolerance
    for target in TARGETS:
        if target != "C":
            assert iso_by_target[target].passed is True
    assert result.stats.max == pytest.approx(iso_by_target["C"].delta_e_00, rel=1e-12)
    assert result.stats.worst[0].patch_id == "C"
    assert result.stats.worst == tuple(sorted(result.patches, key=lambda v: v.delta_e_00, reverse=True)[:5])


def test_verify_reference_input_errors() -> None:
    """行数不符 → REFERENCE_MISMATCH；形状非法 → BAD_LAB_SHAPE；未知表 → NO_REFERENCE。"""
    with pytest.raises(MeasurementError) as excinfo:
        verify_reference(np.zeros((8, 3)))
    assert excinfo.value.code == "REFERENCE_MISMATCH"
    with pytest.raises(MeasurementError) as excinfo:
        verify_reference(np.zeros((9, 4)))
    assert excinfo.value.code == "BAD_LAB_SHAPE"
    with pytest.raises(MeasurementError) as excinfo:
        verify_reference(np.zeros((9, 3)), reference="Nope")
    assert excinfo.value.code == "NO_REFERENCE"


def test_verify_reference_de00_consistent_with_colour() -> None:
    """verify_reference 的 ΔE00 与 colour-science 直接计算一致（抽样对照）。"""
    lab = np.asarray([FOGRA39_LAB[t] for t in TARGETS], dtype=np.float64)
    lab[3] = (85.0, -8.0, 88.0)  # Y 行偏移
    result = verify_reference(lab)
    ref = np.asarray([FOGRA39_LAB[t] for t in TARGETS], dtype=np.float64)
    expected = colour.delta_E(lab, ref, method="CIE 2000")
    for verdict, exp in zip(result.patches, np.asarray(expected, dtype=np.float64)):
        assert verdict.delta_e_00 == pytest.approx(float(exp), rel=1e-12)


# ---------------------------------------------------------------------------
# verify_cgats：纯 Lab 文件路径（名称对齐）
# ---------------------------------------------------------------------------


def test_verify_cgats_lab_only_name_alignment() -> None:
    """lab_only fixture：按 SAMPLE_ID 与 target 名对齐，EXTRA 排除，C 行超差。"""
    data = parse_cgats(LAB_ONLY)
    result = verify_cgats(data)  # 默认 reference="Fogra39"，无 profile
    assert result.reference_name == "Fogra39"
    assert result.profile_path == ""
    # EXTRA（不在参考表）被排除，其余 9 行按参考表顺序对齐
    assert [v.patch_id for v in result.patches] == list(TARGETS)
    assert result.stats.count == 9
    assert result.iso is not None and len(result.iso) == 9
    iso_by_target = {v.target: v for v in result.iso}
    # paper 轻微偏移（0.5/0.5/0.5）→ 通过；C 明显偏移 → 超差
    assert iso_by_target["paper"].passed is True
    assert iso_by_target["C"].passed is False
    assert result.passed is False
    assert result.stats.worst[0].patch_id == "C"
    assert result.tolerance == 5.0


def test_verify_cgats_lab_only_with_profile_falls_back_to_table() -> None:
    """无 device 列时即使给了 profile 也回退内置表（无 device 值无法变换）。"""
    data = parse_cgats(LAB_ONLY)
    result = verify_cgats(data, "unused.icc", reference="Fogra39")
    assert result.reference_name == "Fogra39"
    assert result.stats.count == 9
    assert result.passed is False  # C 行超差仍成立


def test_verify_cgats_missing_lab_columns() -> None:
    """缺 LAB_L/A/B 任一列 → NO_LAB。"""
    data = CgatsData(
        meta={},
        fields=("SAMPLE_ID", "CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"),
        patch_ids=("1",),
        values=np.zeros((1, 4)),
    )
    with pytest.raises(MeasurementError) as excinfo:
        verify_cgats(data)
    assert excinfo.value.code == "NO_LAB"


def test_verify_cgats_no_reference_source() -> None:
    """无 device 列 + 无 reference → NO_REFERENCE（缺参考来源）。"""
    data = _lab_cgats(np.zeros((3, 3)), ("a", "b", "c"))
    with pytest.raises(MeasurementError) as excinfo:
        verify_cgats(data, reference=None)
    assert excinfo.value.code == "NO_REFERENCE"


def test_verify_cgats_reference_mismatch() -> None:
    """名称对不上且行数不等 → REFERENCE_MISMATCH。"""
    data = _lab_cgats(np.zeros((10, 3)), tuple(f"x{i}" for i in range(10)))
    with pytest.raises(MeasurementError) as excinfo:
        verify_cgats(data)
    assert excinfo.value.code == "REFERENCE_MISMATCH"


def test_verify_cgats_row_order_alignment() -> None:
    """名称不可用但行数与参考表一致 → 按行序一一对应。"""
    lab = np.asarray([FOGRA39_LAB[t] for t in TARGETS], dtype=np.float64)
    data = _lab_cgats(lab, tuple(str(i) for i in range(9)))  # SAMPLE_ID 为纯数字
    result = verify_cgats(data)
    assert result.stats.max == pytest.approx(0.0, abs=1e-12)
    assert result.passed is True


# ---------------------------------------------------------------------------
# verify_cgats：参考 Lab 注入路径（lab_transform）
# ---------------------------------------------------------------------------


def test_verify_cgats_injected_transform() -> None:
    """注入 fake 变换：device 以 CGATS 0-100 标度透传、intent/bpc 透传、ISO 按设备码对齐。"""
    data = parse_cgats(FOGRA39_9PATCH)
    calls: list[tuple[object, np.ndarray, object, object]] = []

    def fake_transform(profile, device, intent, bpc):  # noqa: ANN001, ANN202
        arr = np.asarray(device, dtype=np.float64)
        calls.append((profile, arr.copy(), intent, bpc))
        out = np.empty((arr.shape[0], 3), dtype=np.float64)
        for i, row in enumerate(arr):
            for patch in TABLE.patches:
                if np.allclose(patch.device, row, atol=1e-3):
                    out[i] = patch.lab
                    break
            else:
                out[i] = (50.0, 0.0, 0.0)
        return out

    result = verify_cgats(
        data,
        "fake-profile.icc",
        intent=RenderingIntent.PERCEPTUAL,
        bpc=False,
        lab_transform=fake_transform,
    )
    assert len(calls) == 1
    prof, device, intent, bpc = calls[0]
    assert prof == "fake-profile.icc"
    assert device.shape == (9, 4)
    assert device[0].tolist() == [0.0, 0.0, 0.0, 0.0]
    assert device[1].tolist() == [100.0, 0.0, 0.0, 0.0]  # CGATS 0-100（未换算 0-255）
    assert intent == RenderingIntent.PERCEPTUAL
    assert bpc is False
    # fake 返回参考表 Lab，实测 Lab 恰为 §6.1 值 → 恒等
    assert result.stats.max == pytest.approx(0.0, abs=1e-12)
    assert result.passed is True
    assert result.reference_name == "Fogra39"
    assert result.iso is not None and len(result.iso) == 9  # device 码全对齐
    assert result.intent == "perceptual"
    assert result.profile_path == "fake-profile.icc"
    assert result.profile_description == ""  # fake profile 无法加载 → 降级 ""
    assert result.patches[1].device == (100.0, 0.0, 0.0, 0.0)


def test_verify_cgats_injected_transform_bad_output() -> None:
    """注入变换返回形状非法/非有限值 → TRANSFORM_BAD_SHAPE / TRANSFORM_BAD_VALUE。"""
    data = parse_cgats(FOGRA39_9PATCH)
    with pytest.raises(MeasurementError) as excinfo:
        verify_cgats(data, "fake.icc", lab_transform=lambda p, d, i, b: np.zeros((9, 2)))
    assert excinfo.value.code == "TRANSFORM_BAD_SHAPE"
    with pytest.raises(MeasurementError) as excinfo:
        verify_cgats(
            data,
            "fake.icc",
            lab_transform=lambda p, d, i, b: np.full((9, 3), np.nan),
        )
    assert excinfo.value.code == "TRANSFORM_BAD_VALUE"


def test_verify_cgats_tolerance_override() -> None:
    """tolerance 覆盖 ISO 判定容差与整体达标线；逐行 passed 与 ΔE00<=tol 一致。"""
    data = parse_cgats(FOGRA39_9PATCH)

    def shifted(profile, device, intent, bpc):  # noqa: ANN001, ANN202
        table = reference_table("Fogra39")
        out = np.asarray([p.lab for p in table.patches], dtype=np.float64)
        out = out.copy()
        out[:, 0] += 5.0  # L 整体 +5：高 L 行（纸白）因 ΔE00 亮度加权仍可能在容差内
        return out

    result = verify_cgats(data, "fake.icc", tolerance=3.0, lab_transform=shifted)
    assert result.tolerance == 3.0
    assert result.iso is not None
    assert all(v.tolerance == 3.0 for v in result.iso)
    # 逐行判定与 "ΔE00 <= tolerance" 一致；整体 failed；纸白（高 L、SL 加权）通过
    for verdict in result.iso:
        assert verdict.passed == (verdict.delta_e_00 <= verdict.tolerance)
    assert result.passed is False
    assert any(not v.passed for v in result.iso)
    paper = next(v for v in result.iso if v.target == "paper")
    assert paper.passed is True  # ΔL=5 在 L≈95 处 ΔE00≈2.9 < 3.0

    ok = verify_cgats(
        data, "fake.icc", tolerance=3.0,
        lab_transform=lambda p, d, i, b: np.asarray([pp.lab for pp in TABLE.patches]),
    )
    assert ok.passed is True  # 恒等 → ΔE00=0 <= 3.0


def test_verify_cgats_intent_labels() -> None:
    """intent 标签稳定映射（0-3 → perceptual/…/absolute_colorimetric）。"""
    data = parse_cgats(FOGRA39_9PATCH)
    labels = {
        RenderingIntent.PERCEPTUAL: "perceptual",
        RenderingIntent.RELATIVE_COLORIMETRIC: "relative_colorimetric",
        RenderingIntent.SATURATION: "saturation",
        RenderingIntent.ABSOLUTE_COLORIMETRIC: "absolute_colorimetric",
    }
    for intent, label in labels.items():
        result = verify_cgats(data, "fake.icc", intent=intent, lab_transform=lambda p, d, i, b: np.asarray([p.lab for p in TABLE.patches]))
        assert result.intent == label
        assert result.bpc is True  # 默认 bpc 开


# ---------------------------------------------------------------------------
# verify_cgats：profile 驱动真实集成（系统 profile 缺失 → skip）
# ---------------------------------------------------------------------------


def test_verify_synth() -> None:
    """设计测试矩阵：JapanColor 推导参考 → 恒等测量 → mean ΔE00 < 0.6（8bit 量化内）。"""
    profile = require_system_profile("JapanColor2001Coated")
    device = _device_matrix()
    # 与 verify 内部一致的 CGATS→PIL 标度换算（CMYK 0-100 → 0-255）
    dev255 = np.rint(device * 255.0 / 100.0)
    ref_lab = transform_lab(str(profile), dev255, RenderingIntent.RELATIVE_COLORIMETRIC, True)
    assert ref_lab.shape == (9, 3)
    assert np.isfinite(ref_lab).all()

    measured = _cmyk_cgats(ref_lab)
    result = verify_cgats(
        measured,
        str(profile),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
        bpc=True,
        reference="Fogra39",
    )
    assert result.stats.count == 9
    assert result.stats.mean < 0.6
    assert result.stats.max < 0.6
    # 恒等测量 + ΔE00=0 → ISO 全过
    assert result.iso is not None and len(result.iso) == 9
    assert all(v.passed for v in result.iso)
    assert result.passed is True
    assert result.reference_name == "Fogra39"
    assert result.profile_path == str(profile)
    assert result.profile_description == "Japan Color 2001 Coated"
    assert result.intent == "relative_colorimetric"
    assert result.bpc is True


def test_verify_cgats_rgb_profile_identity() -> None:
    """sRGB + RGB 设备列：恒等测量 ΔE00=0；RGB 列与 CMYK 参考表码不匹配 → ISO 空表。"""
    profile = require_system_profile("sRGB")
    rgb = np.asarray(
        [[0.0, 0.0, 0.0], [255.0, 255.0, 255.0], [255.0, 0.0, 0.0],
         [0.0, 255.0, 0.0], [0.0, 0.0, 255.0], [128.0, 128.0, 128.0]],
        dtype=np.float64,
    )
    ref_lab = transform_lab(str(profile), rgb, RenderingIntent.RELATIVE_COLORIMETRIC, True)
    data = CgatsData(
        meta={},
        fields=("SAMPLE_ID", "RGB_R", "RGB_G", "RGB_B", "LAB_L", "LAB_A", "LAB_B"),
        patch_ids=tuple(str(i + 1) for i in range(rgb.shape[0])),
        values=np.hstack([rgb, ref_lab]),
    )
    result = verify_cgats(data, str(profile), reference="Fogra39")
    assert result.stats.max == pytest.approx(0.0, abs=1e-12)
    assert result.iso == ()  # 内置表为 CMYK 码，RGB 行无 ISO 判定（空表非 None）
    assert result.passed is True
    assert result.reference_name == "Fogra39"
    assert result.profile_path == str(profile)


def test_verify_cgats_profile_shift_fails_iso() -> None:
    """真实 profile 路径下实测整体 L+10 → 全部 ISO 行超差（ΔE00 ≥ 10/1.7 ≈ 6 > 5）。"""
    profile = require_system_profile("JapanColor2001Coated")
    device = _device_matrix()
    dev255 = np.rint(device * 255.0 / 100.0)
    ref_lab = transform_lab(str(profile), dev255, RenderingIntent.RELATIVE_COLORIMETRIC, True)

    measured = _cmyk_cgats(ref_lab + np.asarray([10.0, 0.0, 0.0]))  # 实测 L 整体 +10
    result = verify_cgats(measured, str(profile), reference="Fogra39")
    assert result.passed is False
    assert result.stats.max > 5.0
    assert result.iso is not None and len(result.iso) == 9
    assert all(not v.passed for v in result.iso)  # ΔL=10 对全部容差（3/4/5）均超差
    # worst 与 stats 一致
    assert result.stats.worst[0].delta_e_00 == pytest.approx(result.stats.max, rel=1e-12)


# ---------------------------------------------------------------------------
# overall_verdict：PASS / WARNING / FAIL 阈值边界
# ---------------------------------------------------------------------------


def test_overall_verdict_boundaries() -> None:
    """容差 5.0：恰等→PASS；超差 ≤1.0（含恰 1.0）→WARNING；>1.0→FAIL。"""
    assert VERDICT_WARNING_MARGIN == 1.0
    assert overall_verdict(_synthetic_result(5.0, 5.0)) == "PASS"  # 上边界：恰等判过
    assert overall_verdict(_synthetic_result(5.0 + 1e-9, 5.0)) == "WARNING"
    assert overall_verdict(_synthetic_result(5.5, 5.0)) == "WARNING"
    assert overall_verdict(_synthetic_result(6.0, 5.0)) == "WARNING"  # 超出量恰为 1.0 → 仍 WARNING
    assert overall_verdict(_synthetic_result(6.0 + 1e-9, 5.0)) == "FAIL"
    assert overall_verdict(_synthetic_result(10.0, 5.0)) == "FAIL"


def test_overall_verdict_stats_only() -> None:
    """无 ISO 行（iso=None）时仅按 stats.max vs tolerance 判定。"""
    assert overall_verdict(_synthetic_result(4.0, 4.0, with_iso=False)) == "PASS"
    assert overall_verdict(_synthetic_result(4.3, 4.0, with_iso=False)) == "WARNING"
    assert overall_verdict(_synthetic_result(5.5, 4.0, with_iso=False)) == "FAIL"


def test_overall_verdict_pass_short_circuits() -> None:
    """result.passed=True 时直接 PASS（即使个别字段构造上越界）。"""
    result = _synthetic_result(5.0, 5.0)
    assert overall_verdict(result) == "PASS"


# ---------------------------------------------------------------------------
# 序列化契约
# ---------------------------------------------------------------------------


def test_summarize_contract() -> None:
    """summarize：stats + iso 表 + worst 5，且可直接 JSON 序列化。"""
    lab = np.asarray([FOGRA39_LAB[t] for t in TARGETS], dtype=np.float64)
    lab[TARGETS.index("C")] = (62.0, -32.0, -45.0)
    payload = summarize(verify_reference(lab))
    assert set(payload) == {"stats", "iso", "worst"}
    assert payload["stats"]["count"] == 9
    assert set(payload["stats"]["percentiles"]) == {"p50", "p90", "p95", "p99"}
    assert len(payload["worst"]) <= 5
    assert payload["worst"][0]["patch_id"] == "C"
    json.dumps(payload, ensure_ascii=False)  # 必须可直接序列化


def test_verify_result_as_json_contract() -> None:
    """VerifyResult.as_json 字段名即稳定契约（设计 §3.4 + additive tolerance）。"""
    result = verify_reference(np.asarray([FOGRA39_LAB[t] for t in TARGETS]))
    payload = result.as_json()
    assert set(payload) == {
        "profile_path", "profile_description", "intent", "bpc", "reference_name",
        "patches", "stats", "iso", "passed", "tolerance",
        "channel_diagnostics",  # v0.3.7 起附加（additive）
    }
    assert set(payload["stats"]) == {"count", "mean", "max", "stdev", "percentiles", "worst"}
    assert set(payload["patches"][0]) == {
        "patch_id", "measured_lab", "reference_lab", "delta_e_76", "delta_e_00", "device",
    }
    assert set(payload["iso"][0]) == {
        "patch_id", "target", "measured_lab", "reference_lab", "delta_e_00", "tolerance", "passed",
    }
    assert isinstance(payload["passed"], bool)
    assert isinstance(payload["tolerance"], float)
    json.dumps(payload, ensure_ascii=False)


def test_public_api_surface_verify() -> None:
    """§2.5 导出面契约（verify 部分）+ 附加公开项可得。"""
    import packstudio.engines.color_engine.measurement as pkg

    for name in (
        "ReferenceSet", "PatchVerdict", "DeltaEStats", "VerifyResult", "IsoVerdict",
        "delta_e_00", "verify_cgats", "verify_reference", "reference_table",
        "REFERENCE_TABLES", "ISO12647_2_TOLERANCES", "summarize", "overall_verdict",
        "verify_report_to_cgats",
    ):
        assert hasattr(pkg, name), name


# ---------------------------------------------------------------------------
# 验证报告 → CGATS.17 回写（ROADMAP v0.3.2 余项，随 0.3.2 发布）
# ---------------------------------------------------------------------------


def _report_dict(device: list[float] | None = None) -> dict:
    """构造最小合法报告 dict（与 VerifyResult.as_json() 的 patches 结构一致）。"""
    return {
        "reference_name": "Fogra39",
        "profile_path": "",
        "patches": [
            {"patch_id": "C", "measured_lab": [55.1, -36.8, -50.2],
             "reference_lab": [55.0, -37.0, -50.0],
             "delta_e_76": 0.36, "delta_e_00": 0.31, "device": device},
            {"patch_id": "K", "measured_lab": [16.2, 0.1, 0.0],
             "reference_lab": [16.0, 0.0, 0.0],
             "delta_e_76": 0.22, "delta_e_00": 0.20, "device": device},
        ],
    }


def test_verify_report_to_cgats_cmyk_roundtrip(tmp_path) -> None:
    """CMYK 设备列 + 实测/参考 Lab + ΔE 列全结构，write→parse 往返一致。"""
    from packstudio.engines.color_engine.measurement import parse_cgats, verify_report_to_cgats, write_cgats

    data = verify_report_to_cgats(_report_dict([100.0, 0.0, 0.0, 0.0]))
    assert data.fields == (
        "SAMPLE_ID", "CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K",
        "LAB_L", "LAB_A", "LAB_B", "REF_L", "REF_A", "REF_B",
        "DELTA_E_76", "DELTA_E_00",
    )
    assert data.values.shape == (2, 12)
    assert data.meta["ORIGINATOR"].startswith("icc-toolkit ")
    assert data.meta["REFERENCE"] == "Fogra39"
    assert "DESCRIPTOR" in data.meta
    out = tmp_path / "report.cgats"
    write_cgats(data, out)
    back = parse_cgats(out.read_bytes())
    assert back.fields == data.fields
    np.testing.assert_allclose(back.values, data.values, atol=1e-4)
    assert back.patch_ids == ("C", "K")


def test_verify_report_to_cgats_device_naming() -> None:
    """设备列名按元数映射：3 → RGB_R/G/B、2 → DEV_0/DEV_1。"""
    rgb = verify_report_to_cgats(_report_dict([255.0, 0.0, 0.0]))
    assert rgb.fields[1:4] == ("RGB_R", "RGB_G", "RGB_B")
    generic = verify_report_to_cgats(_report_dict([10.0, 20.0]))
    assert generic.fields[1:3] == ("DEV_0", "DEV_1")


def test_verify_report_to_cgats_device_none() -> None:
    """device 全 null：无设备列，8 列数值结构。"""
    data = verify_report_to_cgats(_report_dict(None))
    assert data.fields == (
        "SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B", "REF_L", "REF_A", "REF_B",
        "DELTA_E_76", "DELTA_E_00",
    )
    assert data.values.shape == (2, 8)


def test_verify_report_to_cgats_from_real_result() -> None:
    """真实 verify_cgats 结果回写：行数与 patches 一致、LAB 列等于实测值。"""
    result = verify_cgats(parse_cgats(FOGRA39_9PATCH.read_bytes()),
                          reference="Fogra39", tolerance=3.0)
    data = verify_report_to_cgats(result.as_json())
    assert data.values.shape[0] == 9
    lab = data.column_index("LAB_L")
    assert lab is not None
    expected = np.asarray([p.measured_lab for p in result.patches])
    np.testing.assert_allclose(data.values[:, lab:lab + 3], expected, atol=1e-4)


def test_verify_report_to_cgats_errors() -> None:
    """非法报告 → MeasurementError(code=BAD_REPORT)，detail 指明字段。"""
    good = _report_dict([100.0, 0.0, 0.0, 0.0])
    cases: list[object] = [
        "not a dict",
        {},
        {**good, "patches": []},
        {**good, "patches": ["x"]},
        {**good, "patches": [{**good["patches"][0], "patch_id": ""}]},
        {**good, "patches": [{k: v for k, v in good["patches"][0].items()
                              if k != "measured_lab"}]},
        {**good, "patches": [{**good["patches"][0], "measured_lab": [1, 2]}]},
        {**good, "patches": [{**good["patches"][0], "delta_e_00": "abc"}]},
        {**good, "patches": [{**good["patches"][0], "device": [1, 2, 3, 4]},
                             {**good["patches"][1], "device": [1, 2]}]},
    ]
    for report in cases:
        with pytest.raises(MeasurementError) as ei:
            verify_report_to_cgats(report)
        assert ei.value.code == "BAD_REPORT"


# ---------------------------------------------------------------------------
# 通道级诊断（ROADMAP v0.3.7，随 0.3.6 发布）
# ---------------------------------------------------------------------------


def _diag_result(devices, de00s):
    """构造带 device 的 VerifyResult（Lab 无意义，仅 ΔE00 参与诊断）。"""
    patches = tuple(
        PatchVerdict(patch_id=f"p{i}", measured_lab=(50.0, 0.0, 0.0),
                     reference_lab=(50.0, 0.0, 0.0),
                     delta_e_76=d, delta_e_00=d, device=dev)
        for i, (dev, d) in enumerate(zip(devices, de00s)))
    stats = DeltaEStats(count=len(patches), mean=float(np.mean(de00s)),
                        max=float(max(de00s)), stdev=0.0,
                        percentiles={"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0},
                        worst=())
    return VerifyResult(profile_path="", profile_description="", intent="", bpc=False,
                        reference_name="Fogra39", patches=patches, stats=stats,
                        iso=None, passed=False, tolerance=3.0)


def test_channel_diagnostics_none_without_device() -> None:
    """无 device 列 / RGB 3 通道 → None（UI 据此隐藏面板）。"""
    # 纯 Lab 测量数据（无 device 列）
    table = reference_table("Fogra39")
    lab = np.asarray([p.lab for p in table.patches])
    lab_only = CgatsData(
        meta={}, fields=("SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B"),
        patch_ids=tuple(p.target for p in table.patches), values=lab)
    result = verify_cgats(lab_only, reference="Fogra39", tolerance=5.0)
    assert channel_diagnostics(result) is None

    rgb = _diag_result([(255.0, 0.0, 0.0), (0.0, 255.0, 0.0)], [1.0, 2.0])
    assert channel_diagnostics(rgb) is None


def test_channel_diagnostics_cmyk_stats() -> None:
    """CMYK device：覆盖/激活/固体色统计正确。"""
    result = _diag_result(
        [(100.0, 0.0, 0.0, 0.0), (0.0, 100.0, 0.0, 0.0),
         (0.0, 0.0, 100.0, 0.0), (0.0, 0.0, 0.0, 100.0), (100.0, 100.0, 100.0, 0.0)],
        [1.0, 3.5, 1.2, 0.8, 2.9])
    diag = channel_diagnostics(result)
    assert diag is not None
    by_name = {c["channel"]: c for c in diag["channels"]}
    assert by_name["C"]["active_count"] == 2       # C 与 CMY 两块激活
    assert by_name["C"]["coverage_pct"] == 1.0
    assert by_name["C"]["mean_de"] == pytest.approx(1.95)
    assert by_name["C"]["solid_de"] == pytest.approx(1.0)
    assert by_name["K"]["active_count"] == 1


def test_channel_diagnostics_hint_rules() -> None:
    """提示规则：显著最高通道点名；均衡时均衡文案。"""
    standout = _diag_result(
        [(100.0, 0.0, 0.0, 0.0), (0.0, 100.0, 0.0, 0.0), (0.0, 0.0, 100.0, 0.0),
         (0.0, 0.0, 0.0, 100.0)],
        [1.0, 3.5, 1.2, 0.8])
    hints = channel_diagnostics(standout)["hints"]
    assert any("M 通道" in h for h in hints)

    balanced = _diag_result(
        [(100.0, 0.0, 0.0, 0.0), (0.0, 100.0, 0.0, 0.0), (0.0, 0.0, 100.0, 0.0),
         (0.0, 0.0, 0.0, 100.0)],
        [1.0, 1.1, 0.9, 1.2])
    hints_b = channel_diagnostics(balanced)["hints"]
    assert any("均衡" in h for h in hints_b)


def test_verify_as_json_includes_channel_diagnostics() -> None:
    """as_json 附加字段（additive 契约）：键存在；带 device 的 fixture 出 4 通道。"""
    result = verify_cgats(parse_cgats(FOGRA39_9PATCH.read_bytes()),
                          reference="Fogra39", tolerance=5.0)
    payload = result.as_json()
    assert "channel_diagnostics" in payload
    diag = payload["channel_diagnostics"]
    assert diag is not None and len(diag["channels"]) == 4
    assert {c["channel"] for c in diag["channels"]} == {"C", "M", "Y", "K"}

