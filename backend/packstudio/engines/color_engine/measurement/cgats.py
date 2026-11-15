"""CGATS.17 文本格式的解析与写出（TECH-DESIGN §4.8）。

CGATS.17 是印刷测量的标准交换格式（ISO 12647-x / Fogra characterization 数据
均以此发布）。结构为三段::

    KEY "value"                       ← 键值头（NUMBER_OF_FIELDS / NUMBER_OF_SETS 等）
    BEGIN_DATA_FORMAT
    SAMPLE_ID  CMYK_C  ...  LAB_L  LAB_A  LAB_B
    END_DATA_FORMAT
    BEGIN_DATA
    1  100.00  0.00  ...  55.00  -37.00  -50.00
    END_DATA

本模块支持的容错：UTF-8 BOM、CRLF/LF、制表符或空格分隔（可混用）、``#`` 注释行、
多余空行、双引号包裹的含空格值（meta 与数据行均支持）、GBK 编码文件
（UTF-8 严格解码失败时回退 GBK）。写出端保证与解析端往返一致
（浮点保留 4 位小数，values 读回后在 1e-4 内相等）。

公开 API：
    CgatsData / parse_cgats / write_cgats / cgats_from_lab
"""
from __future__ import annotations

import logging
import math
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import numpy as np

from packstudio.engines.color_engine.errors import CgatsParseError, MeasurementError

logger = logging.getLogger("icct.measurement")

__all__ = ["CgatsData", "parse_cgats", "write_cgats", "cgats_from_lab"]

_FORMAT_START: Final[str] = "BEGIN_DATA_FORMAT"
_FORMAT_END: Final[str] = "END_DATA_FORMAT"
_DATA_START: Final[str] = "BEGIN_DATA"
_DATA_END: Final[str] = "END_DATA"
# 文本列：不参与数值解析；SAMPLE_ID 进 patch_ids，SAMPLE_NAME 进 sample_names。
_TEXT_FIELDS: Final[frozenset[str]] = frozenset({"SAMPLE_ID", "SAMPLE_NAME"})
# 写出时由数据结构派生、不回写原值的 meta 键。
_DERIVED_META_KEYS: Final[tuple[str, ...]] = ("NUMBER_OF_FIELDS", "NUMBER_OF_SETS")

_WHITESPACE: Final[str] = " \t"


def _norm_name(name: str) -> str:
    """字段/键名归一化：去首尾与内部空白（折叠为下划线）并统一大写。"""
    return "_".join(name.upper().split())


def _decode(raw: bytes) -> str:
    """按设计 R4 解码：UTF-8（含 BOM）严格解码，失败回退 GBK。"""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


def _io_error(exc: OSError, path: str) -> MeasurementError:
    """把 OSError 转换为 MeasurementError（code="IO"，detail 带 path/errno）。"""
    err = MeasurementError(f"CGATS 文件读写失败: {path} ({exc})", detail={"path": path, "errno": getattr(exc, "errno", None)})
    err.code = "IO"
    return err


def _cgats_error(message: str, detail: dict[str, Any]) -> CgatsParseError:
    return CgatsParseError(message, detail=detail)


def _read_bytes(source: str | os.PathLike[str] | bytes) -> tuple[bytes, str | None]:
    """归一化输入来源为字节串。

    返回 ``(raw_bytes, path_or_None)``；字符串含换行时按文本内容处理
    （路径不会包含换行符），否则按文件路径读取。
    """
    if isinstance(source, bytes):
        return source, None
    if isinstance(source, str) and "\n" in source:
        return source.encode("utf-8"), None
    path = os.fspath(source)
    try:
        return pathlib.Path(path).read_bytes(), str(path)
    except OSError as exc:
        raise _io_error(exc, str(path)) from exc


def _split_row(line: str, lineno: int, path: str | None) -> list[str]:
    """按空白切分一行；双引号包裹的片段视为单个 token（可含空格）。"""
    tokens: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch in _WHITESPACE:
            i += 1
            continue
        if ch == '"':
            end = line.find('"', i + 1)
            if end == -1:
                raise _cgats_error(f"第 {lineno} 行存在未闭合的双引号", {"path": path, "line": lineno})
            tokens.append(line[i + 1 : end])
            i = end + 1
        else:
            j = i
            while j < n and line[j] not in _WHITESPACE:
                j += 1
            tokens.append(line[i:j])
            i = j
    return tokens


def _parse_meta_line(line: str, lineno: int, path: str | None, meta: dict[str, str]) -> None:
    """解析键值头行 ``KEY "value"``；无引号的裸值（如 NUMBER_OF_FIELDS 8）同样接受。"""
    tokens = _split_row(line, lineno, path)
    if not tokens:
        return
    key = _norm_name(tokens[0])
    meta[key] = " ".join(tokens[1:]) if len(tokens) > 1 else ""


@dataclass(slots=True)
class CgatsData:
    """一次 CGATS.17 解析结果（TECH-DESIGN §3.4）。

    Attributes:
        meta: 键值头（ORIGINATOR/DESCRIPTOR/INSTRUMENTATION/PRINT_CONDITIONS/
            NUMBER_OF_FIELDS/NUMBER_OF_SETS/...），键已归一化大写。
        fields: BEGIN_DATA_FORMAT 行的完整字段名（归一化大写、原列序），
            如 ``("SAMPLE_ID","CMYK_C",...,"LAB_L","LAB_A","LAB_B")``。
        patch_ids: SAMPLE_ID 列；缺失时自动 "1".."n"。
        values: 数值列矩阵，shape ``(n, k)`` float64；不含 SAMPLE_ID /
            SAMPLE_NAME 等文本列，列序 = fields 中按原序剔除文本列后的顺序。
            （设计允许文本列存在于 fields 而不入 values；对齐用 :meth:`column_index`。）
        sample_names: SAMPLE_NAME 列（若存在于文件），否则 None。

    不变量：``values.shape[0] == len(patch_ids)``；values 列数 == fields 中
    非文本列数；列名匹配大小写不敏感去空格。
    """

    meta: dict[str, str]
    fields: tuple[str, ...]
    patch_ids: tuple[str, ...]
    values: np.ndarray = field(compare=False)  # ndarray 参与 eq 会产生歧义，比较时跳过
    sample_names: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.values.ndim != 2:
            raise MeasurementError(
                "CgatsData.values 必须是二维数组 (n, k)",
                detail={"shape": list(self.values.shape)},
            )
        numeric_count = sum(1 for f in self.fields if f not in _TEXT_FIELDS)
        if numeric_count != self.values.shape[1]:
            raise MeasurementError(
                "CgatsData 不变量破坏：values 列数与 fields 的数值列数不一致",
                detail={"values_cols": int(self.values.shape[1]), "numeric_fields": numeric_count},
            )
        if self.values.shape[0] != len(self.patch_ids):
            raise MeasurementError(
                "CgatsData 不变量破坏：values.shape[0] != len(patch_ids)",
                detail={"rows": int(self.values.shape[0]), "patch_ids": len(self.patch_ids)},
            )
        if self.sample_names is not None and len(self.sample_names) != len(self.patch_ids):
            raise MeasurementError(
                "CgatsData 不变量破坏：len(sample_names) != len(patch_ids)",
                detail={"sample_names": len(self.sample_names), "patch_ids": len(self.patch_ids)},
            )

    def column_index(self, name: str) -> int | None:
        """按归一化字段名查 values 列索引（大小写/空格不敏感）。

        文本列（SAMPLE_ID/SAMPLE_NAME）不在 values 中，返回 None。
        """
        want = _norm_name(name)
        col = 0
        for f in self.fields:
            if f in _TEXT_FIELDS:
                continue
            if f == want:
                return col
            col += 1
        return None

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名即稳定契约）。"""
        return {
            "meta": dict(self.meta),
            "fields": list(self.fields),
            "patch_ids": list(self.patch_ids),
            "sample_names": list(self.sample_names) if self.sample_names is not None else None,
            "values": self.values.tolist(),
        }


def parse_cgats(source: str | os.PathLike[str] | bytes) -> CgatsData:
    """解析 CGATS.17 文本。

    行为：
        解析三段结构——键值头（KEY "value"）、BEGIN_DATA_FORMAT…END_DATA_FORMAT
        字段行、BEGIN_DATA…END_DATA 数据行。支持 CRLF/LF、制表符或空格分隔、
        ``#`` 注释行、BOM、多余空行；字段名归一化为大写并保留原列序。
        SAMPLE_ID 列（非数值也接受）存入 patch_ids；SAMPLE_NAME 列存入
        sample_names；其余列必须可解析为有限浮点数。NUMBER_OF_FIELDS /
        NUMBER_OF_SETS 存入 meta 并与实际结构一致性校验。

    参数:
        source: 文件路径（str/PathLike）或已读入的文本/字节内容；
            含换行的 str 直接按内容处理。

    返回:
        CgatsData

    异常:
        CgatsParseError: 结构缺失（缺 END_DATA 等）、列数不一致、0 行、
            数值解析失败（detail 带 1 基行号）。
        MeasurementError: 路径读取失败（code="IO"）。
    """
    raw, path = _read_bytes(source)
    text = _decode(raw)
    started = time.perf_counter()

    meta: dict[str, str] = {}
    fields: list[str] = []
    patch_ids: list[str] = []
    sample_names: list[str] = []
    rows: list[list[float]] = []
    has_sample_id = False
    has_sample_name = False

    state = "header"  # header → format → post_format → data → done
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        upper = line.upper()
        detail = {"path": path, "line": lineno}

        if state == "header":
            if upper == _FORMAT_START:
                state = "format"
            elif upper in (_FORMAT_END, _DATA_START, _DATA_END):
                raise _cgats_error(f"第 {lineno} 行出现 {_DATA_START}，但尚未定义 BEGIN_DATA_FORMAT 字段行", detail)
            else:
                _parse_meta_line(line, lineno, path, meta)
        elif state == "format":
            if upper == _FORMAT_END:
                if not fields:
                    raise _cgats_error("BEGIN_DATA_FORMAT 内没有任何字段", detail)
                if len(set(fields)) != len(fields):
                    dupes = sorted({f for f in fields if fields.count(f) > 1})
                    raise _cgats_error(f"字段名重复: {dupes}", detail)
                state = "post_format"
            elif upper in (_FORMAT_START, _DATA_START, _DATA_END):
                raise _cgats_error(f"第 {lineno} 行在字段行区段内出现意外关键字 {upper}", detail)
            else:
                fields.extend(_norm_name(tok) for tok in _split_row(line, lineno, path))
        elif state == "post_format":
            if upper == _DATA_START:
                state = "data"
            elif upper in (_FORMAT_START, _FORMAT_END, _DATA_END):
                raise _cgats_error(f"第 {lineno} 行出现意外关键字 {upper}", detail)
            else:
                _parse_meta_line(line, lineno, path, meta)
        elif state == "data":
            if upper == _DATA_END:
                state = "done"
            elif upper in (_FORMAT_START, _FORMAT_END, _DATA_START):
                raise _cgats_error(f"第 {lineno} 行在数据区段内出现意外关键字 {upper}", detail)
            else:
                tokens = _split_row(line, lineno, path)
                if len(tokens) != len(fields):
                    raise _cgats_error(
                        f"第 {lineno} 行列数不一致: 实际 {len(tokens)}，字段行定义 {len(fields)}",
                        detail,
                    )
                row: list[float] = []
                for f, tok in zip(fields, tokens):
                    if f == "SAMPLE_ID":
                        patch_ids.append(tok)
                        has_sample_id = True
                    elif f == "SAMPLE_NAME":
                        sample_names.append(tok)
                        has_sample_name = True
                    else:
                        try:
                            value = float(tok)
                        except ValueError:
                            raise _cgats_error(f"第 {lineno} 行字段 {f} 数值解析失败: {tok!r}", detail) from None
                        if not math.isfinite(value):
                            raise _cgats_error(f"第 {lineno} 行字段 {f} 含非有限数值: {tok!r}", detail)
                        row.append(value)
                rows.append(row)
        else:  # done
            raise _cgats_error(f"END_DATA 之后存在意外内容（第 {lineno} 行）", detail)

    if state == "data" or state == "format":
        raise _cgats_error(
            f"缺少 {'END_DATA' if state == 'data' else 'END_DATA_FORMAT'} 终止行", {"path": path}
        )
    if not fields:
        raise _cgats_error("缺少 BEGIN_DATA_FORMAT/END_DATA_FORMAT 字段行", {"path": path})
    if not rows:
        raise _cgats_error("数据区段没有任何数据行（0 行）", {"path": path})

    # NUMBER_OF_FIELDS / NUMBER_OF_SETS 一致性校验
    for key, expect, what in (
        ("NUMBER_OF_FIELDS", len(fields), "字段数"),
        ("NUMBER_OF_SETS", len(rows), "数据行数"),
    ):
        if key in meta:
            try:
                declared = int(meta[key])
            except ValueError:
                raise _cgats_error(f"{key} 不是整数: {meta[key]!r}", {"path": path}) from None
            if declared != expect:
                raise _cgats_error(f"{key} 与实际{what}不一致: 声明 {declared}，实际 {expect}", {"path": path})

    values = np.asarray(rows, dtype=np.float64)
    if values.shape[1] == 0:
        raise _cgats_error("没有任何数值数据列（仅文本列无法构成测量数据）", {"path": path})
    ids = tuple(patch_ids) if has_sample_id else tuple(str(i + 1) for i in range(values.shape[0]))

    data = CgatsData(
        meta=meta,
        fields=tuple(fields),
        patch_ids=ids,
        values=values,
        sample_names=tuple(sample_names) if has_sample_name else None,
    )
    logger.debug(
        "CGATS 解析完成: %s (%d patches, %d fields, %.1f ms)",
        path or "<content>", values.shape[0], len(fields), (time.perf_counter() - started) * 1000.0,
    )
    return data


def _quote_meta(value: str) -> str:
    """meta 值写出：含空白或为空时加双引号。"""
    return _quote_cell(value)


def _quote_cell(value: str) -> str:
    """数据单元格写出：含空白时加双引号（保证重解析后仍是单 token）。"""
    if value == "" or any(ch in _WHITESPACE for ch in value):
        return f'"{value}"'
    return value


def _check_writable(data: CgatsData) -> None:
    if not np.isfinite(data.values).all():
        raise MeasurementError("CgatsData.values 含非有限值（NaN/Inf），无法写出", detail={"path": None})
    if data.values.shape[0] != len(data.patch_ids):
        raise MeasurementError("CgatsData 不变量破坏：行数与 patch_ids 不一致", detail={"path": None})


def write_cgats(
    data: CgatsData,
    path: pathlib.Path,
    *,
    fmt: Literal["cgats17", "iso28178"] = "cgats17",
) -> None:
    """把 CgatsData 写为 CGATS.17 文本文件。

    行为：
        写出键值头（NUMBER_OF_FIELDS/NUMBER_OF_SETS 按数据结构派生）、
        BEGIN_DATA_FORMAT/END_DATA_FORMAT 字段行与 BEGIN_DATA/END_DATA
        数据行；制表符分隔，UTF-8 编码，``\\n`` 行尾。浮点保留 4 位小数，
        与 :func:`parse_cgats` 往返一致（values 读回后在 1e-4 内相等）。
        目标目录不存在时自动创建。

    参数:
        data: 待写出的数据。
        path: 目标文件路径。
        fmt: 仅支持 "cgats17"；"iso28178"（CxF XML）为 v1 未实现占位。

    异常:
        MeasurementError: fmt 不支持（code="FMT_UNSUPPORTED"）、数据含
            非有限值或不变量破坏、写出 IO 失败（code="IO"）。
    """
    if fmt != "cgats17":
        err = MeasurementError(
            "仅支持 cgats17 写出；iso28178（CxF XML）为 v1 未实现占位", detail={"fmt": fmt}
        )
        err.code = "FMT_UNSUPPORTED"
        raise err
    _check_writable(data)

    lines: list[str] = []
    for key, value in data.meta.items():
        if key in _DERIVED_META_KEYS:
            continue
        lines.append(f"{key}\t{_quote_meta(value)}")
    lines.append(f"NUMBER_OF_FIELDS\t{len(data.fields)}")
    lines.append(f"NUMBER_OF_SETS\t{data.values.shape[0]}")
    lines.append(_FORMAT_START)
    lines.append("\t".join(data.fields))
    lines.append(_FORMAT_END)
    lines.append(_DATA_START)

    for i in range(data.values.shape[0]):
        cells: list[str] = []
        num_idx = 0
        for f in data.fields:
            if f == "SAMPLE_ID":
                cells.append(_quote_cell(data.patch_ids[i]))
            elif f == "SAMPLE_NAME":
                cells.append(_quote_cell(data.sample_names[i] if data.sample_names is not None else data.patch_ids[i]))
            else:
                cells.append(f"{data.values[i, num_idx]:.4f}")
                num_idx += 1
        lines.append("\t".join(cells))
    lines.append(_DATA_END)

    target = pathlib.Path(path)
    if str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        raise _io_error(exc, str(target)) from exc
    logger.debug("CGATS 写出完成: %s (%d patches)", target, data.values.shape[0])


def cgats_from_lab(
    lab: np.ndarray,
    patch_ids: list[str] | None = None,
    meta: dict[str, str] | None = None,
) -> CgatsData:
    """由 Lab 矩阵构造 CgatsData（供报告回写）。

    行为：
        构造 ``fields=("SAMPLE_ID","LAB_L","LAB_A","LAB_B")`` 的数据；
        patch_ids 缺省自动 "1".."n"；meta 缺省为空 dict。输入数组被复制，
        不与调用方共享内存。

    参数:
        lab: shape ``(n, 3)`` float64（一维长度 3 视为单行）；L 允许 0-100。
        patch_ids: 长度必须等于 n。
        meta: 原样拷贝进结果。

    返回:
        CgatsData

    异常:
        MeasurementError: 形状非法（code="BAD_LAB_SHAPE"）、patch_ids 长度
            不符（code="BAD_PATCH_IDS"）、含非有限值（code="BAD_LAB_VALUE"）。
    """
    arr = np.asarray(lab, dtype=np.float64)
    if arr.ndim == 1 and arr.shape[0] == 3:
        arr = arr.reshape(1, 3)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise _measurement_error(
            "lab 必须是形状 (n, 3) 的数组", "BAD_LAB_SHAPE", {"shape": list(arr.shape)}
        )
    if not np.isfinite(arr).all():
        raise _measurement_error("lab 含非有限值（NaN/Inf）", "BAD_LAB_VALUE", {})
    n = arr.shape[0]
    ids = tuple(patch_ids) if patch_ids is not None else tuple(str(i + 1) for i in range(n))
    if len(ids) != n:
        raise _measurement_error(
            "patch_ids 长度与 lab 行数不一致", "BAD_PATCH_IDS", {"ids": len(ids), "rows": n}
        )
    return CgatsData(
        meta=dict(meta) if meta else {},
        fields=("SAMPLE_ID", "LAB_L", "LAB_A", "LAB_B"),
        patch_ids=ids,
        values=arr.copy(),
        sample_names=None,
    )


def _measurement_error(message: str, code: str, detail: dict[str, Any]) -> MeasurementError:
    """构造带稳定机器码的 MeasurementError（code 为设计契约的一部分）。"""
    err = MeasurementError(message, detail=detail)
    err.code = code
    return err
