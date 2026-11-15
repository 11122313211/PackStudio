"""measurement/cgats.py 单元测试（TECH-DESIGN §7.1 测试矩阵 + 畸形输入报错路径）。

覆盖：Fogra39L 九色 fixture 解析已知值、字段归一化、分隔符/注释/BOM/CRLF/GBK
容错、写出与解析往返（1e-4）、cgats_from_lab 构造、全部报错路径的异常类型
（必须精确匹配设计 §2.1/§4.8：CgatsParseError / MeasurementError）。
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

from packstudio.engines.color_engine.errors import CgatsParseError, IccToolkitError, MeasurementError
from packstudio.engines.color_engine.measurement import CgatsData, cgats_from_lab, parse_cgats, write_cgats

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FOGRA39_9PATCH = FIXTURES / "fogra39_9patch.cgats"
LAB_ONLY = FIXTURES / "lab_only_names.cgats"

# 设计 §6.1 官方 characterization 数值（测试断言基准）
FOGRA39_ROWS: tuple[tuple[tuple[float, float, float, float], tuple[float, float, float]], ...] = (
    ((0.0, 0.0, 0.0, 0.0), (95.0, 0.0, -2.0)),      # paper
    ((100.0, 0.0, 0.0, 0.0), (55.0, -37.0, -50.0)),  # C
    ((0.0, 100.0, 0.0, 0.0), (48.0, 74.0, -3.0)),    # M
    ((0.0, 0.0, 100.0, 0.0), (89.0, -5.0, 93.0)),    # Y
    ((0.0, 0.0, 0.0, 100.0), (16.0, 0.0, 0.0)),      # K
    ((100.0, 100.0, 0.0, 0.0), (24.0, 22.0, -46.0)), # CM
    ((100.0, 0.0, 100.0, 0.0), (50.0, -65.0, 27.0)), # CY
    ((0.0, 100.0, 100.0, 0.0), (47.0, 68.0, 48.0)),  # MY
    ((100.0, 100.0, 100.0, 0.0), (23.0, 0.0, 0.0)),  # CMY
)

FULL_SKELETON = (
    'ORIGINATOR\t"t"\n'
    "BEGIN_DATA_FORMAT\n"
    "SAMPLE_ID\tLAB_L\tLAB_A\tLAB_B\n"
    "END_DATA_FORMAT\n"
    "BEGIN_DATA\n"
    "1\t95.00\t0.00\t-2.00\n"
    "END_DATA\n"
)


# ---------------------------------------------------------------------------
# 设计测试矩阵：test_parse_fogra39l_header
# ---------------------------------------------------------------------------


def test_parse_fogra39l_header() -> None:
    """解析 §6.1 数据首 9 行：paper L=95.00；C 行 a*=-37.00；meta/字段已知。"""
    data = parse_cgats(FOGRA39_9PATCH)
    assert data.meta["ORIGINATOR"] == "icc-toolkit fixtures"
    assert data.meta["DESCRIPTOR"].startswith("Fogra39 solid/overprint/paper targets")
    assert data.meta["NUMBER_OF_FIELDS"] == "8"
    assert data.meta["NUMBER_OF_SETS"] == "9"
    assert data.fields == (
        "SAMPLE_ID", "CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K", "LAB_L", "LAB_A", "LAB_B",
    )
    assert len(data.patch_ids) == 9
    assert data.values.shape == (9, 7)
    assert data.sample_names is None
    # paper 行
    assert data.values[0].tolist() == [0.0, 0.0, 0.0, 0.0, 95.00, 0.00, -2.00]
    assert data.patch_ids[0] == "1"
    # C 行：device 100/0/0/0，a* = -37.00
    assert data.values[1, :4].tolist() == [100.0, 0.0, 0.0, 0.0]
    assert data.values[1, 5] == pytest.approx(-37.00, abs=1e-9)
    # 全部 9 行与设计 §6.1 表一致
    for i, (device, lab) in enumerate(FOGRA39_ROWS):
        assert data.values[i, :4].tolist() == list(device)
        assert data.values[i, 4:7].tolist() == list(lab)


def test_parse_bom_crlf_and_gbk_bytes() -> None:
    """BOM/CRLF/GBK 容错：字节输入与文件输入等价；GBK 中文 meta 可解析。"""
    data_file = parse_cgats(FOGRA39_9PATCH.read_bytes())
    data_bom = parse_cgats(b"\xef\xbb\xbf" + FOGRA39_9PATCH.read_bytes())
    assert data_file.fields == data_bom.fields == parse_cgats(FOGRA39_9PATCH).fields

    gbk_text = 'DESCRIPTOR\t"涂布纸测试"\n' + FULL_SKELETON
    data_gbk = parse_cgats(gbk_text.encode("gbk"))
    assert data_gbk.meta["DESCRIPTOR"] == "涂布纸测试"
    assert data_gbk.values.shape == (1, 3)


def test_parse_str_content_and_path(tmp_path: pathlib.Path) -> None:
    """含换行的 str 按内容解析；不含换行的 str 按路径解析；两者结果一致。"""
    from_file = parse_cgats(FOGRA39_9PATCH)
    from_str = parse_cgats(FOGRA39_9PATCH.read_text(encoding="utf-8"))
    assert from_str.fields == from_file.fields
    assert np.allclose(from_str.values, from_file.values)
    assert from_str.meta == from_file.meta

    target = tmp_path / "copy.cgats"
    target.write_bytes(FOGRA39_9PATCH.read_bytes())
    from_path = parse_cgats(str(target))
    assert from_path.meta == from_file.meta


def test_fields_normalization_and_column_index() -> None:
    """字段名归一化：小写统一大写；meta 键空格折叠为下划线；column_index 大小写/空格不敏感。

    注：BEGIN_DATA_FORMAT 行按空白分词，字段名 token 本身不能含空格（含空格
    会被拆成多个字段，设计 §4.8 的"字段名去空格"作用于 token 归一化与 meta
    键）；values 不含 SAMPLE_ID 文本列，column_index 返回 values 的 0 基列索引。
    """
    content = (
        '"print condition"\t"ISO 12647-2 coated"\n'  # 引号包裹的 meta 键含空格 → PRINT_CONDITION
        "begin_data_format\n"
        "sample_id\tCMYK_c\tLab_L\tlab_a\tLAB_B\n"  # 混合大小写/混合分隔符
        "end_data_format\n"
        "begin_data\n"
        "p1   0.0   50.0   0.5   -1.5\n"
        "end_data\n"
    )
    data = parse_cgats(content)
    assert data.fields == ("SAMPLE_ID", "CMYK_C", "LAB_L", "LAB_A", "LAB_B")
    assert data.meta["PRINT_CONDITION"] == "ISO 12647-2 coated"
    assert data.patch_ids == ("p1",)
    assert data.values.tolist() == [[0.0, 50.0, 0.5, -1.5]]
    assert data.column_index("lab l") == 1  # LAB_L 是第 2 个数值列（0 基 1）
    assert data.column_index("CMYK_C") == 0  # 归一化后 CMYK_C 是第 1 个数值列（0 基 0）
    assert data.column_index("sample_id") is None  # 文本列不在 values 中


def test_separators_comments_and_blank_lines() -> None:
    """制表符/空格混用、# 注释行、多余空行均被容忍。"""
    content = (
        "# 头部注释\n"
        "\n"
        "ORIGINATOR  mixed   separators\n"
        "# 格式注释\n"
        "BEGIN_DATA_FORMAT\n"
        "\n"
        "SAMPLE_ID\tLAB_L  LAB_A\tLAB_B\n"
        "END_DATA_FORMAT\n"
        "\n\n"
        "NUMBER_OF_SETS\t2\n"
        "BEGIN_DATA\n"
        "# 数据注释\n"
        "1\t95.00\t0.00\t-2.00\n"
        "2    55.00   -37.00   -50.00\n"
        "\n"
        "END_DATA\n"
    )
    data = parse_cgats(content)
    assert data.meta["ORIGINATOR"] == "mixed separators"
    assert data.meta["NUMBER_OF_SETS"] == "2"
    assert data.fields == ("SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B")
    assert data.values.shape == (2, 3)
    assert data.values[1].tolist() == [55.0, -37.0, -50.0]


def test_quoted_values_in_meta_and_rows() -> None:
    """双引号包裹的含空格值：meta 与数据行（SAMPLE_NAME）均支持。"""
    data = parse_cgats(LAB_ONLY)
    assert data.meta["DESCRIPTOR"] == "Lab-only measurement, name-aligned to Fogra39"
    assert data.sample_names == (
        "Paper white", "Cyan solid", "Magenta solid", "Yellow solid", "Black solid",
        "Blue overprint", "Green overprint", "Red overprint", "Three-color CMY",
        "Not in reference",
    )
    assert data.patch_ids == ("paper", "C", "M", "Y", "K", "CM", "CY", "MY", "CMY", "EXTRA")
    assert data.values.shape == (10, 3)


def test_parse_auto_patch_ids() -> None:
    """无 SAMPLE_ID 列时 patch_ids 自动补 "1".."n"。"""
    content = (
        "BEGIN_DATA_FORMAT\n"
        "LAB_L LAB_A LAB_B\n"
        "END_DATA_FORMAT\n"
        "BEGIN_DATA\n"
        "1 2 3\n"
        "4 5 6\n"
        "END_DATA\n"
    )
    data = parse_cgats(content)
    assert data.fields == ("LAB_L", "LAB_A", "LAB_B")
    assert data.patch_ids == ("1", "2")


# ---------------------------------------------------------------------------
# 往返（设计测试矩阵：test_roundtrip）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", [FOGRA39_9PATCH, LAB_ONLY], ids=["cmyk", "lab_only"])
def test_roundtrip(fixture: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """write→parse 数值全等（1e-4）；fields/patch_ids/sample_names/meta 保持。"""
    original = parse_cgats(fixture)
    target = tmp_path / "out" / "roundtrip.cgats"  # 同时覆盖父目录自动创建
    write_cgats(original, target)
    assert target.exists()

    reloaded = parse_cgats(target)
    assert reloaded.fields == original.fields
    assert reloaded.patch_ids == original.patch_ids
    assert reloaded.sample_names == original.sample_names
    assert np.allclose(reloaded.values, original.values, rtol=0.0, atol=1e-4)
    # meta：原条目全部保留（NUMBER_OF_FIELDS/NUMBER_OF_SETS 由数据派生重写）
    for key, value in original.meta.items():
        assert reloaded.meta[key] == value


def test_roundtrip_float_precision(tmp_path: pathlib.Path) -> None:
    """4 位小数输出保证 1e-4 内往返（含边界值）。"""
    data = cgats_from_lab(
        np.array([[0.123456, -12.34567, 999.99995], [0.00005, 100.0, -0.00005]]),
        meta={"DESCRIPTOR": "precision"},
    )
    target = tmp_path / "precision.cgats"
    write_cgats(data, target)
    reloaded = parse_cgats(target)
    assert np.allclose(reloaded.values, data.values, rtol=0.0, atol=1e-4)


def test_roundtrip_from_lab(tmp_path: pathlib.Path) -> None:
    """cgats_from_lab 构造的数据写出后可原样读回。"""
    data = cgats_from_lab(np.array([[95.0, 0.0, -2.0], [55.0, -37.0, -50.0]]))
    target = tmp_path / "lab.cgats"
    write_cgats(data, target)
    reloaded = parse_cgats(target)
    assert reloaded.fields == ("SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B")
    assert reloaded.patch_ids == ("1", "2")
    assert reloaded.sample_names is None
    assert np.allclose(reloaded.values, data.values, rtol=0.0, atol=1e-4)


# ---------------------------------------------------------------------------
# cgats_from_lab
# ---------------------------------------------------------------------------


def test_cgats_from_lab_meta_passthrough() -> None:
    lab = np.array([[50.0, 2.6772, -79.7751]])
    data = cgats_from_lab(lab, patch_ids=["ref1"], meta={"DESCRIPTOR": "x"})
    assert data.meta == {"DESCRIPTOR": "x"}
    assert data.patch_ids == ("ref1",)
    data.values[0, 0] = 999.0  # 不影响调用方数组（复制语义）
    assert lab[0, 0] == 50.0


def test_cgats_from_lab_single_row_reshape() -> None:
    data = cgats_from_lab(np.array([95.0, 0.0, -2.0]))
    assert data.values.shape == (1, 3)
    assert data.patch_ids == ("1",)


def test_cgats_from_lab_invalid_inputs() -> None:
    with pytest.raises(MeasurementError) as excinfo:
        cgats_from_lab(np.zeros((2, 4)))
    assert excinfo.value.code == "BAD_LAB_SHAPE"
    with pytest.raises(MeasurementError) as excinfo:
        cgats_from_lab(np.zeros((2, 3)), patch_ids=["only-one"])
    assert excinfo.value.code == "BAD_PATCH_IDS"
    with pytest.raises(MeasurementError) as excinfo:
        cgats_from_lab(np.array([[float("nan"), 0.0, 0.0]]))
    assert excinfo.value.code == "BAD_LAB_VALUE"


def test_cgats_data_invariant_enforced() -> None:
    """不变量破坏（行数与 patch_ids 不符）在构造时即报 MeasurementError。"""
    with pytest.raises(MeasurementError):
        CgatsData(
            meta={},
            fields=("SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B"),
            patch_ids=("1", "2"),
            values=np.zeros((1, 3)),
        )


# ---------------------------------------------------------------------------
# 报错路径（异常类型必须精确匹配设计）
# ---------------------------------------------------------------------------


def test_error_missing_end_data(tmp_path: pathlib.Path) -> None:
    with pytest.raises(CgatsParseError):
        parse_cgats(FULL_SKELETON.replace("END_DATA\n", ""))
    target = tmp_path / "t.cgats"
    target.write_text(FULL_SKELETON.replace("END_DATA\n", ""), encoding="utf-8")
    with pytest.raises(CgatsParseError):
        parse_cgats(target)


def test_error_missing_end_data_format() -> None:
    content = 'ORIGINATOR "t"\nBEGIN_DATA_FORMAT\nSAMPLE_ID LAB_L LAB_A LAB_B\n'
    with pytest.raises(CgatsParseError) as excinfo:
        parse_cgats(content)
    assert isinstance(excinfo.value, MeasurementError)
    assert excinfo.value.code == "CGATS_PARSE"


def test_error_data_before_format() -> None:
    with pytest.raises(CgatsParseError):
        parse_cgats("BEGIN_DATA\n1 2 3\nEND_DATA\n")


def test_error_zero_data_rows() -> None:
    content = (
        "BEGIN_DATA_FORMAT\nSAMPLE_ID LAB_L LAB_A LAB_B\nEND_DATA_FORMAT\n"
        "NUMBER_OF_SETS 0\nBEGIN_DATA\nEND_DATA\n"
    )
    with pytest.raises(CgatsParseError) as excinfo:
        parse_cgats(content)
    assert "0 行" in str(excinfo.value)


def test_error_column_count_mismatch_with_line_number() -> None:
    content = FULL_SKELETON.replace("1\t95.00\t0.00\t-2.00", "1\t95.00\t0.00")
    with pytest.raises(CgatsParseError) as excinfo:
        parse_cgats(content)
    # FULL_SKELETON 共 7 行：1=meta 2=BEGIN_DATA_FORMAT 3=字段行 4=END_DATA_FORMAT
    # 5=BEGIN_DATA 6=数据行 7=END_DATA；报错定位在数据行（1 基文件行号）= 6
    assert excinfo.value.detail["line"] == 6
    assert "列数不一致" in str(excinfo.value)


def test_error_numeric_parse_failure_line_number() -> None:
    content = FULL_SKELETON.replace("1\t95.00\t0.00\t-2.00", "1\tabc\t0.00\t-2.00")
    with pytest.raises(CgatsParseError) as excinfo:
        parse_cgats(content)
    assert excinfo.value.detail["line"] == 6  # 数据行是第 6 行（1 基文件行号）
    assert "数值解析失败" in str(excinfo.value)
    assert "abc" in str(excinfo.value)


def test_error_non_finite_numeric() -> None:
    content = FULL_SKELETON.replace("1\t95.00\t0.00\t-2.00", "1\tnan\t0.00\t-2.00")
    with pytest.raises(CgatsParseError):
        parse_cgats(content)


def test_error_number_of_fields_mismatch() -> None:
    content = FULL_SKELETON.replace('ORIGINATOR\t"t"\n', "NUMBER_OF_FIELDS 9\n")
    with pytest.raises(CgatsParseError) as excinfo:
        parse_cgats(content)
    assert "NUMBER_OF_FIELDS" in str(excinfo.value)


def test_error_number_of_sets_mismatch() -> None:
    content = FULL_SKELETON.replace('ORIGINATOR\t"t"\n', "NUMBER_OF_SETS 5\n")
    with pytest.raises(CgatsParseError):
        parse_cgats(content)


def test_error_number_of_fields_not_integer() -> None:
    content = FULL_SKELETON.replace('ORIGINATOR\t"t"\n', "NUMBER_OF_FIELDS abc\n")
    with pytest.raises(CgatsParseError):
        parse_cgats(content)


def test_error_duplicate_fields() -> None:
    content = (
        "BEGIN_DATA_FORMAT\nSAMPLE_ID LAB_L LAB_L\nEND_DATA_FORMAT\n"
        "BEGIN_DATA\n1 2 3\nEND_DATA\n"
    )
    with pytest.raises(CgatsParseError) as excinfo:
        parse_cgats(content)
    assert "重复" in str(excinfo.value)


def test_error_unexpected_content_after_end_data() -> None:
    content = FULL_SKELETON + "ORIGINATOR trailing\n"
    with pytest.raises(CgatsParseError):
        parse_cgats(content)


def test_error_unterminated_quote() -> None:
    content = FULL_SKELETON.replace('ORIGINATOR\t"t"\n', 'ORIGINATOR\t"unclosed\n')
    with pytest.raises(CgatsParseError):
        parse_cgats(content)


def test_error_missing_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(MeasurementError) as excinfo:
        parse_cgats(tmp_path / "nope.cgats")
    assert excinfo.value.code == "IO"
    assert excinfo.value.detail["path"].endswith("nope.cgats")


def test_exception_hierarchy_codes() -> None:
    """异常层级与稳定机器码符合设计 §2.1。"""
    assert issubclass(CgatsParseError, MeasurementError)
    assert issubclass(MeasurementError, IccToolkitError)
    assert MeasurementError("x").code == "MEASUREMENT"
    assert CgatsParseError("x").code == "CGATS_PARSE"


# ---------------------------------------------------------------------------
# 写出端行为
# ---------------------------------------------------------------------------


def test_write_iso28178_unsupported(tmp_path: pathlib.Path) -> None:
    data = cgats_from_lab(np.zeros((1, 3)))
    with pytest.raises(MeasurementError) as excinfo:
        write_cgats(data, tmp_path / "out.xml", fmt="iso28178")
    assert excinfo.value.code == "FMT_UNSUPPORTED"


def test_write_rejects_non_finite(tmp_path: pathlib.Path) -> None:
    data = cgats_from_lab(np.array([[50.0, 0.0, 0.0]]))
    data.values[0, 0] = float("nan")
    with pytest.raises(MeasurementError):
        write_cgats(data, tmp_path / "nan.cgats")


def test_cgats_as_json_contract() -> None:
    data = parse_cgats(LAB_ONLY)
    payload = data.as_json()
    assert set(payload) == {"meta", "fields", "patch_ids", "sample_names", "values"}
    assert payload["fields"] == list(data.fields)
    assert isinstance(payload["values"][0][0], float)
    json.dumps(payload, ensure_ascii=False)  # 必须可直接序列化


def test_public_api_surface() -> None:
    """§2.5 导出面契约：设计清单内名称全部可得。"""
    import packstudio.engines.color_engine.measurement as pkg

    for name in (
        "CgatsData", "parse_cgats", "write_cgats", "cgats_from_lab",
        "ReferenceSet", "PatchVerdict", "DeltaEStats", "VerifyResult", "IsoVerdict",
        "delta_e_00", "verify_cgats", "verify_reference", "reference_table",
        "REFERENCE_TABLES",
    ):
        assert hasattr(pkg, name), name
