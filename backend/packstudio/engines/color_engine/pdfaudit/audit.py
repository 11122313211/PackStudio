"""PDF 色彩审计（TECH-DESIGN §4.10 / §3.5）。

基于 pikepdf 递归遍历页面资源（含 Form XObject 嵌套与继承 Resources），
盘点色彩空间使用、OutputIntent 与嵌入 ICC profile、专色（Separation/DeviceN），
输出 :class:`PdfColorAuditResult`（字段名即 JSON 契约）。

健壮性约定：打不开/损坏/加密的 PDF 抛 :class:`PdfAuditError`；其余坏结构
（悬空引用、损坏 ICC 流等）一律降级为结果里的 ``warnings`` 字段。
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from typing import Any

import pikepdf

logger = logging.getLogger("icct.pdfaudit")

# ---------------------------------------------------------------------------
# 异常：优先项目统一层级（errors.py）；并行开发期该文件未就绪时使用本地等价
# 定义（构造签名与语义一致，errors.py 落地后自动切换到真实层级）。
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 分支取决于并行模块是否就绪
    from packstudio.engines.color_engine.errors import IccToolkitError, PdfAuditError
except ImportError:  # pragma: no cover - 并行开发脚手架
    class IccToolkitError(Exception):
        """所有工具异常的基类（errors.py 就绪前的本地等价定义）。"""

        code: str = "ICC_TOOLKIT_ERROR"

        def __init__(self, message: str, *,
                     detail: dict[str, Any] | None = None) -> None:
            super().__init__(message)
            self.detail: dict[str, Any] = dict(detail or {})

    class PdfAuditError(IccToolkitError):
        """PDF 审计失败（打不开、损坏等）。"""

        code: str = "PDF_AUDIT"


class _PdfEncryptedError(PdfAuditError):
    """加密 PDF 且口令缺失/无效。code="PDF_ENCRYPTED"（契约码）。"""

    code: str = "PDF_ENCRYPTED"


# ---------------------------------------------------------------------------
# icc 层（并行开发中）：缺失时降级为纯结构盘点（dest_profile/icc_profile=None
# 并在 warnings 中说明），保证审计主流程不因并行模块缺席而中断。
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 分支取决于并行模块是否就绪
    from packstudio.engines.color_engine.icc.inspect import ProfileInfo, load_profile
    ICC_AVAILABLE = True
except ImportError:  # pragma: no cover - 并行开发脚手架
    ProfileInfo = None  # type: ignore[assignment, misc]
    load_profile = None  # type: ignore[assignment]
    ICC_AVAILABLE = False

__all__ = [
    "OutputIntentInfo",
    "PageColorReport",
    "ImageColorEntry",
    "SpotColorEntry",
    "PdfColorAuditResult",
    "audit_pdf",
    "ICC_AVAILABLE",
]

# 设备色空间名（PDF 32000 基本色空间，非引用直接可用）
_DEVICE_SPACES: frozenset[str] = frozenset({"DeviceGray", "DeviceRGB", "DeviceCMYK"})
# 可直接按族名归类的色空间族
_SIMPLE_FAMILIES: frozenset[str] = frozenset({"CalRGB", "CalGray", "Lab", "Pattern"})
# 已知 Function 类型（PDF 32000 7.10）：0 采样 / 2 指数 / 3 拼接 / 4 PostScript
_KNOWN_FUNCTION_TYPES: frozenset[int] = frozenset({0, 2, 3, 4})
# /Parent 链最大深度（防畸形环）
_MAX_PARENT_DEPTH = 64


# ---------------------------------------------------------------------------
# 数据模型（TECH-DESIGN §3.5，字段名即 JSON 契约）
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class OutputIntentInfo:
    """单个 OutputIntent 字典的摘要。"""

    subtype: str                  # /S 值，如 'GTS_PDFA1'、'GTS_PDFX'、'ISO_PDFE'
    output_condition: str | None  # /OutputCondition
    identifier: str | None        # /OutputConditionIdentifier
    registry: str | None          # /RegistryName
    has_dest_profile: bool        # /DestOutputProfile 是否存在
    dest_profile: ProfileInfo | None    # 嵌入 DestOutputProfile 流解析摘要
    dest_profile_digest: str | None     # /DigestValue 十六进制（无则 None）

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名稳定契约，见 §2.3）。"""
        dest: Any = None
        if self.dest_profile is not None:
            as_json = getattr(self.dest_profile, "as_json", None)
            dest = as_json() if callable(as_json) else repr(self.dest_profile)
        return {
            "subtype": self.subtype,
            "output_condition": self.output_condition,
            "identifier": self.identifier,
            "registry": self.registry,
            "has_dest_profile": self.has_dest_profile,
            "dest_profile": dest,
            "dest_profile_digest": self.dest_profile_digest,
        }


@dataclass(frozen=True, slots=True)
class ImageColorEntry:
    """单个图像 XObject 的色彩信息。"""

    name: str                     # XObject 资源名（无前导斜杠）
    page: int                     # 1 基页码
    color_space: str              # 'DeviceRGB'|'ICCBased'|'Indexed(DeviceRGB)'|…
    icc_profile: ProfileInfo | None  # ICCBased 流摘要（非 ICCBased / 解析失败为 None）
    width: int | None
    height: int | None
    bpc: int | None               # /BitsPerComponent

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名稳定契约）。"""
        icc: Any = None
        if self.icc_profile is not None:
            as_json = getattr(self.icc_profile, "as_json", None)
            icc = as_json() if callable(as_json) else repr(self.icc_profile)
        return {
            "name": self.name,
            "page": self.page,
            "color_space": self.color_space,
            "icc_profile": icc,
            "width": self.width,
            "height": self.height,
            "bpc": self.bpc,
        }


@dataclass(frozen=True, slots=True)
class SpotColorEntry:
    """单个专色（Separation 名称或 DeviceN 成员）。"""

    name: str                     # 专色名
    space: str                    # 'Separation' | 'DeviceN'
    alternate: str                # /Alternate 空间标签（如 'DeviceGray'、'Lab'）
    alternate_lab: tuple[float, float, float] | None
    tint_transform_readable: bool  # /TintTransform 为已知 Function 类型

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名稳定契约）。"""
        return {
            "name": self.name,
            "space": self.space,
            "alternate": self.alternate,
            "alternate_lab": None if self.alternate_lab is None
            else list(self.alternate_lab),
            "tint_transform_readable": self.tint_transform_readable,
        }


@dataclass(frozen=True, slots=True)
class PageColorReport:
    """单页色彩资源盘点。"""

    page: int
    color_spaces_used: dict[str, int]  # 各类型出现次数（定义 + 图像引用）
    fills_strokes_noted: bool          # v1 仅资源级盘点，恒 False（不解析内容流）
    images: tuple[ImageColorEntry, ...]

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名稳定契约）。"""
        return {
            "page": self.page,
            "color_spaces_used": dict(self.color_spaces_used),
            "fills_strokes_noted": self.fills_strokes_noted,
            "images": [image.as_json() for image in self.images],
        }


@dataclass(frozen=True, slots=True)
class PdfColorAuditResult:
    """audit_pdf 的完整结果。"""

    path: str
    pdf_version: str
    page_count: int
    encrypted: bool
    output_intents: tuple[OutputIntentInfo, ...]
    pages: tuple[PageColorReport, ...]
    spots: tuple[SpotColorEntry, ...]
    warnings: tuple[str, ...]
    summary: dict[str, int]

    def as_json(self) -> dict[str, Any]:
        """JSON 序列化（字段名稳定契约，供 CLI --json / Web API 直接使用）。"""
        return {
            "path": self.path,
            "pdf_version": self.pdf_version,
            "page_count": self.page_count,
            "encrypted": self.encrypted,
            "output_intents": [oi.as_json() for oi in self.output_intents],
            "pages": [page.as_json() for page in self.pages],
            "spots": [spot.as_json() for spot in self.spots],
            "warnings": list(self.warnings),
            "summary": dict(self.summary),
        }


# ---------------------------------------------------------------------------
# pikepdf 对象的小工具
# ---------------------------------------------------------------------------
def _name_str(value: object) -> str | None:
    """pikepdf.Name → 无前导斜杠字符串；其余 None。"""
    if isinstance(value, pikepdf.Name):
        return str(value).removeprefix("/")
    return None


def _str_or_none(value: object) -> str | None:
    """Name/String/标量 → 人读字符串；None/不可转换 → None。"""
    if value is None:
        return None
    if isinstance(value, pikepdf.Name):
        return str(value).removeprefix("/")
    if isinstance(value, pikepdf.String):
        return str(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _safe_int(value: object) -> int | None:
    """pikepdf 整数对象 → int；其余 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    return None


def _safe_get(obj: object, key: str) -> object:
    """从 Dictionary/Stream 安全取键；任何异常吞掉返回 None。"""
    if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
        return None
    try:
        return obj.get(key)
    except Exception:  # noqa: BLE001 - 坏结构一律降级
        return None


def _is_dict_like(obj: object) -> bool:
    return isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream))


def _resources_chain(node: object) -> list[pikepdf.Dictionary]:
    """收集节点自身与 /Parent 链上的 Resources 字典（子在前）。

    页面属性继承（PDF 32000 7.7.3.4）：页面树任意节点的 /Resources 均可被
    后代继承。返回的链中靠前者优先。带深度上限防畸形环。
    """
    chain: list[pikepdf.Dictionary] = []
    current = node
    depth = 0
    while _is_dict_like(current) and depth < _MAX_PARENT_DEPTH:
        res = _safe_get(current, "/Resources")
        if _is_dict_like(res):
            chain.append(res)  # type: ignore[arg-type]
        current = _safe_get(current, "/Parent")
        depth += 1
    return chain


def _object_key(obj: object) -> tuple[int, int]:
    """对象唯一键（objgen），用于同一页遍历内的环防护。"""
    try:
        return obj.objgen  # type: ignore[union-attr, return-value]
    except Exception:  # noqa: BLE001
        return (id(obj), 0)


# ---------------------------------------------------------------------------
# 色彩空间分类
# ---------------------------------------------------------------------------
def _classify_space(value: object, cs_defs: dict[str, object],
                    warnings: list[str], context: str,
                    visited: set[str] | None = None) -> tuple[str, object]:
    """把 PDF 色彩空间值归类为 (标签, 载荷)。

    标签用于计数与 :class:`ImageColorEntry`；载荷是 Separation/DeviceN/Indexed
    数组或 ICCBased 流（供专色收集与 ICC 解析）。名称引用按 cs_defs 展开，
    ``visited`` 防引用环，悬空引用降级为 'Unknown' + warning。
    """
    if isinstance(value, pikepdf.Name):
        bare = str(value).removeprefix("/")
        if bare in _DEVICE_SPACES or bare in _SIMPLE_FAMILIES:
            return bare, None
        marked = f"/{bare}"
        seen = visited if visited is not None else set()
        if marked in seen:
            warnings.append(f"ColorSpace 名称引用循环: {marked} ({context})")
            return "Unknown", None
        seen.add(marked)
        if marked in cs_defs:
            return _classify_space(cs_defs[marked], cs_defs, warnings, context, seen)
        warnings.append(f"ColorSpace 引用悬空: {marked} ({context})")
        return "Unknown", None
    if isinstance(value, pikepdf.Array) and len(value) >= 1:
        family = _name_str(value[0])
        if family == "ICCBased":
            stream = value[1] if len(value) > 1 else None
            return "ICCBased", stream if _is_dict_like(stream) else None
        if family in ("Separation", "DeviceN"):
            return family, value
        if family == "Indexed":
            base_value = value[1] if len(value) > 1 else None
            base_label, _ = _classify_space(base_value, cs_defs, warnings,
                                            context, visited)
            return f"Indexed({base_label})", value
        if family is not None and family in _SIMPLE_FAMILIES:
            return family, value
        # 未知族原样保留字符串，不映射为 unknown 丢弃（§2.3.4）
        return family or "Unknown", value
    return "Unknown", None


def _count_label(label: str) -> str:
    """计数键：'Indexed(DeviceRGB)' → 'Indexed'（§3.5 color_spaces_used 约定）。"""
    return label.split("(", 1)[0]


def _bump(used: dict[str, int], label: str) -> None:
    key = _count_label(label)
    used[key] = used.get(key, 0) + 1


# ---------------------------------------------------------------------------
# ICC 流解析（受 icc 层可用性保护）
# ---------------------------------------------------------------------------
def _load_icc_summary(data: bytes, warnings: list[str], context: str) -> ProfileInfo | None:  # type: ignore[name-defined]
    """解析嵌入 ICC 字节为 ProfileInfo；失败降级为 None + warning。"""
    if not ICC_AVAILABLE or load_profile is None:  # pragma: no cover - 并行期分支
        warnings.append(f"icc 模块不可用，{context} 未解析嵌入 profile")
        return None
    try:
        return load_profile(data)  # type: ignore[misc]
    except Exception as exc:  # noqa: BLE001 - 单个流损坏不影响整体审计
        warnings.append(f"{context} 嵌入 ICC 流损坏或无法解析: {exc}")
        return None


# ---------------------------------------------------------------------------
# OutputIntent
# ---------------------------------------------------------------------------
def _digest_hex(value: object) -> str | None:
    """/DigestValue 字节串 → 十六进制；不可转换时给文本表示。"""
    if value is None:
        return None
    try:
        return bytes(value).hex()
    except Exception:  # noqa: BLE001
        return _str_or_none(value)


def _collect_output_intents(root: object,
                            warnings: list[str]) -> tuple[OutputIntentInfo, ...]:
    """枚举 Root.OutputIntents（§4.10 步骤 2）。"""
    ois = _safe_get(root, "/OutputIntents")
    if ois is None:
        return ()
    if isinstance(ois, pikepdf.Array):
        items: list[object] = list(ois)
    elif _is_dict_like(ois):
        items = [ois]  # 畸形（单字典）宽容处理
    else:
        warnings.append("Root.OutputIntents 类型异常，已跳过")
        return ()
    result: list[OutputIntentInfo] = []
    for index, oi in enumerate(items):
        if not _is_dict_like(oi):
            warnings.append(f"OutputIntents[{index}] 不是字典，已跳过")
            continue
        subtype = _name_str(_safe_get(oi, "/S")) or ""
        dest = _safe_get(oi, "/DestOutputProfile")
        has_dest = _is_dict_like(dest)
        dest_profile: ProfileInfo | None = None  # type: ignore[name-defined]
        if has_dest:
            try:
                data = dest.read_bytes()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                data = None
                warnings.append(
                    f"OutputIntents[{index}] DestOutputProfile 流不可读: {exc}")
            if data is not None:
                dest_profile = _load_icc_summary(
                    data, warnings, f"OutputIntents[{index}] DestOutputProfile")
        result.append(OutputIntentInfo(
            subtype=subtype,
            output_condition=_str_or_none(_safe_get(oi, "/OutputCondition")),
            identifier=_str_or_none(_safe_get(oi, "/OutputConditionIdentifier")),
            registry=_str_or_none(_safe_get(oi, "/RegistryName")),
            has_dest_profile=has_dest,
            dest_profile=dest_profile,
            dest_profile_digest=_digest_hex(_safe_get(oi, "/DigestValue")),
        ))
        if subtype and not has_dest:
            warnings.append(f"OutputIntent {subtype} 缺少 DestOutputProfile")
    return tuple(result)


# ---------------------------------------------------------------------------
# 专色收集
# ---------------------------------------------------------------------------
def _function_readable(fn: object) -> bool:
    """/TintTransform 是否为已知 Function 类型（0/2/3/4，流或字典形态均可）。"""
    if not _is_dict_like(fn):
        return False
    ftype = _safe_int(_safe_get(fn, "/FunctionType"))
    return ftype in _KNOWN_FUNCTION_TYPES


def _spot_lab_from_function(fn: object) -> tuple[float, float, float] | None:
    """tint=1.0 时经 FunctionType 2（指数插值）函数求得的 Lab 外观值。

    x=1.0 时结果恒为 C1（含 Range 裁剪）；其余函数类型不求值返回 None
    （数值变换由 cmm/lcms 域负责，见 §8 R1 同款降级策略）。
    """
    if not _is_dict_like(fn):
        return None
    if _safe_int(_safe_get(fn, "/FunctionType")) != 2:
        return None
    c1 = _safe_get(fn, "/C1")
    if not isinstance(c1, pikepdf.Array) or len(c1) != 3:
        return None
    try:
        values = [float(v) for v in c1]
    except (TypeError, ValueError):
        return None
    rng = _safe_get(fn, "/Range")
    if isinstance(rng, pikepdf.Array) and len(rng) == 6:
        try:
            limits = [float(v) for v in rng]
        except (TypeError, ValueError):
            limits = None
        if limits is not None:
            values = [min(max(v, limits[2 * i]), limits[2 * i + 1])
                      for i, v in enumerate(values)]
    return (values[0], values[1], values[2])


def _append_spot(spots: list[SpotColorEntry], entry: SpotColorEntry) -> None:
    """按 (space, name) 去重保序收录。"""
    if all((s.space, s.name) != (entry.space, entry.name) for s in spots):
        spots.append(entry)


def _register_spot(payload: object, cs_defs: dict[str, object],
                   spots: list[SpotColorEntry], warnings: list[str],
                   context: str) -> None:
    """从 Separation/DeviceN 数组提取专色条目（§4.10 步骤 5）。"""
    if not isinstance(payload, pikepdf.Array) or len(payload) < 3:
        return
    family = _name_str(payload[0])
    if family == "Separation":
        name = _str_or_none(payload[1]) or "<unnamed>"
        alternate, _ = _classify_space(payload[2], cs_defs, warnings, context)
        fn = payload[3] if len(payload) > 3 else None
        lab = _spot_lab_from_function(fn) if alternate == "Lab" else None
        _append_spot(spots, SpotColorEntry(
            name=name, space="Separation", alternate=alternate,
            alternate_lab=lab, tint_transform_readable=_function_readable(fn)))
    elif family == "DeviceN":
        names = payload[1]
        alternate, _ = _classify_space(payload[2], cs_defs, warnings, context)
        fn = payload[3] if len(payload) > 3 else None
        readable = _function_readable(fn)
        if isinstance(names, pikepdf.Array):
            for member in names:
                member_name = _str_or_none(member) or "<unnamed>"
                _append_spot(spots, SpotColorEntry(
                    name=member_name, space="DeviceN", alternate=alternate,
                    alternate_lab=None, tint_transform_readable=readable))


# ---------------------------------------------------------------------------
# 页面遍历
# ---------------------------------------------------------------------------
def _image_entry(name: str, image_obj: object, page_no: int,
                 cs_defs: dict[str, object], warnings: list[str],
                 ) -> tuple[ImageColorEntry, str]:
    """读取图像 XObject → (ImageColorEntry, 计数标签)。"""
    cs_value = _safe_get(image_obj, "/ColorSpace")
    image_mask = _safe_get(image_obj, "/ImageMask")
    context = f"页面 {page_no} 图像 {name}"
    if image_mask is True:
        # ImageMask 图像无 /ColorSpace
        return (ImageColorEntry(name=name, page=page_no, color_space="ImageMask",
                                icc_profile=None, width=_safe_int(_safe_get(image_obj, "/Width")),
                                height=_safe_int(_safe_get(image_obj, "/Height")),
                                bpc=_safe_int(_safe_get(image_obj, "/BitsPerComponent"))),
                "ImageMask")
    label, payload = _classify_space(cs_value, cs_defs, warnings, context)
    icc_profile: ProfileInfo | None = None  # type: ignore[name-defined]
    if label == "ICCBased" and payload is not None:
        try:
            data = payload.read_bytes()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            data = None
            warnings.append(f"图像 {name} 的 ICCBased 流不可读（页面 {page_no}）: {exc}")
        if data is not None:
            icc_profile = _load_icc_summary(
                data, warnings, f"页面 {page_no} 图像 {name} 的 ICCBased 流")
    return (ImageColorEntry(
        name=name, page=page_no, color_space=label, icc_profile=icc_profile,
        width=_safe_int(_safe_get(image_obj, "/Width")),
        height=_safe_int(_safe_get(image_obj, "/Height")),
        bpc=_safe_int(_safe_get(image_obj, "/BitsPerComponent"))), label)


def _visit_xobject(name: str, xobj: object, page_no: int, cs_defs: dict[str, object],
                   chain: list[pikepdf.Dictionary], used: dict[str, int],
                   images: list[ImageColorEntry], spots: list[SpotColorEntry],
                   warnings: list[str], visited: set[tuple[int, int]]) -> None:
    """递归访问 XObject：Image 生成条目，Form 递归其 Resources（环防护）。"""
    if not _is_dict_like(xobj):
        warnings.append(f"XObject {name}（页面 {page_no}）不是流，已跳过")
        return
    key = _object_key(xobj)
    if key in visited:
        return
    visited.add(key)
    subtype = _name_str(_safe_get(xobj, "/Subtype"))
    if subtype == "Image":
        entry, label = _image_entry(name, xobj, page_no, cs_defs, warnings)
        images.append(entry)
        _bump(used, label)
        context = f"页面 {page_no} 图像 {name}"
        cs_value = _safe_get(xobj, "/ColorSpace")
        _, payload = _classify_space(cs_value, cs_defs, warnings, context)
        _register_spot(payload, cs_defs, spots, warnings, context)
        return
    if subtype == "Form":
        # Form 的 /Resources 缺省时继承页面链（PDF 32000 8.10.2）
        form_res = _safe_get(xobj, "/Resources")
        sub_chain = ([form_res] if _is_dict_like(form_res) else []) + chain
        sub_defs = dict(cs_defs)
        for res in sub_chain:
            cs = _safe_get(res, "/ColorSpace")
            if not _is_dict_like(cs):
                continue
            for key_name in sorted(str(k) for k in cs.keys()):
                if key_name not in sub_defs:
                    sub_defs[key_name] = cs[key_name]
        for def_name, def_value in sub_defs.items():
            if def_name not in cs_defs:  # 仅新并入的定义计一次
                label, payload = _classify_space(
                    def_value, sub_defs, warnings,
                    f"页面 {page_no} Form {name} /{def_name.lstrip('/')}")
                _bump(used, label)
                _register_spot(payload, sub_defs, spots, warnings,
                               f"页面 {page_no} Form {name} {def_name}")
        sub_visited = visited  # 同页共享环防护集
        sub_xo = None
        for res in sub_chain:
            xo = _safe_get(res, "/XObject")
            if _is_dict_like(xo):
                sub_xo = xo
                break
        if sub_xo is not None:
            for child_name in sorted(str(k) for k in sub_xo.keys()):
                _visit_xobject(child_name.lstrip("/"), sub_xo[child_name],
                               page_no, sub_defs, sub_chain, used, images, spots,
                               warnings, sub_visited)
        return
    # 其他 Subtype（如 /PS）v1 不处理


def _audit_page(page_obj: object, page_no: int, spots: list[SpotColorEntry],
                warnings: list[str]) -> PageColorReport:
    """盘点单页：Resources/ColorSpace 定义 + XObject（含 Form 嵌套）。"""
    used: dict[str, int] = {}
    images: list[ImageColorEntry] = []
    chain = _resources_chain(page_obj)
    cs_defs: dict[str, object] = {}
    for res in chain:
        cs = _safe_get(res, "/ColorSpace")
        if not _is_dict_like(cs):
            continue
        # PDF 字典键序不保证，按键名排序保证遍历确定性
        for key_name in sorted(str(k) for k in cs.keys()):
            if key_name not in cs_defs:
                cs_defs[key_name] = cs[key_name]
    # 资源级定义各计一次
    for def_name, def_value in cs_defs.items():
        label, payload = _classify_space(
            def_value, cs_defs, warnings,
            f"页面 {page_no} /{def_name.lstrip('/')}")
        _bump(used, label)
        _register_spot(payload, cs_defs, spots, warnings,
                       f"页面 {page_no} {def_name}")
    # XObject 遍历（子链优先取最近定义）
    visited: set[tuple[int, int]] = set()
    seen_xo: set[int] = set()
    for res in chain:
        xo = _safe_get(res, "/XObject")
        if not _is_dict_like(xo) or id(xo) in seen_xo:
            continue
        seen_xo.add(id(xo))
        for child_name in sorted(str(k) for k in xo.keys()):
            _visit_xobject(child_name.lstrip("/"), xo[child_name], page_no,
                           cs_defs, chain, used, images, spots, warnings, visited)
    return PageColorReport(page=page_no, color_spaces_used=used,
                           fills_strokes_noted=False, images=tuple(images))


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def audit_pdf(path: str | os.PathLike[str] | bytes, *,
              password: str | None = None) -> PdfColorAuditResult:
    """审计 PDF 的色彩资源（TECH-DESIGN §4.10）。

    行为:
        1. pikepdf 打开（加密且口令缺失/无效 → PdfAuditError code="PDF_ENCRYPTED"；
           打不开/损坏/不存在 → PdfAuditError code="PDF_AUDIT"）。
        2. 枚举 Root.OutputIntents，读 /S、/OutputCondition、
           /OutputConditionIdentifier、/RegistryName、/DestOutputProfile（流字节
           交给 icc.inspect 解析摘要）与 /DigestValue。
        3. 逐页合并继承 Resources，盘点 Resources/ColorSpace 定义与图像
           XObject 的实际色彩空间（DeviceRGB/DeviceGray/DeviceCMYK/ICCBased/
           Separation/DeviceN/Indexed(基空间)/Pattern/CalRGB/CalGray/Lab/…），
           递归进入 Form XObject（引用环防护）。
        4. 收集全文档专色（Separation 名称与 DeviceN 成员），/Alternate 为 Lab
           且 tint transform 为 FunctionType 2 时求 tint=1.0 的 Lab 外观值。
        5. 汇总 warnings（无 OutputIntent、OI 缺 DestOutputProfile、引用悬空、
           ICC 流损坏、icc 模块不可用）与 summary 计数。

    参数:
        path: PDF 路径或原始字节。
        password: 加密 PDF 口令。
    返回:
        PdfColorAuditResult: 数据模型（字段名即 JSON 契约）。
    异常:
        PdfAuditError: 打不开/损坏/加密（code 见上）；其余坏结构一律降级为
        warnings 字段，不抛异常。
    """
    is_bytes = isinstance(path, (bytes, bytearray, memoryview))
    path_repr = "<bytes>" if is_bytes else str(os.fspath(path))
    source: Any = io.BytesIO(bytes(path)) if is_bytes else os.fspath(path)
    try:
        pdf = pikepdf.open(source, password=password or "")
    except pikepdf.PasswordError as exc:
        raise _PdfEncryptedError(
            f"PDF 已加密且口令缺失或无效: {path_repr}",
            detail={"path": path_repr}) from exc
    except pikepdf.PdfError as exc:
        raise PdfAuditError(
            f"PDF 无法打开或已损坏: {path_repr} ({exc})",
            detail={"path": path_repr}) from exc
    except OSError as exc:
        raise PdfAuditError(
            f"PDF 文件不可读: {path_repr}",
            detail={"path": path_repr, "errno": exc.errno}) from exc

    warnings: list[str] = []
    spots: list[SpotColorEntry] = []
    pdf_version = str(pdf.pdf_version)
    encrypted = bool(pdf.is_encrypted)
    try:
        output_intents = _collect_output_intents(pdf.Root, warnings)
        if not output_intents:
            warnings.append("no OutputIntent")
        pages: list[PageColorReport] = []
        for page_no, page in enumerate(pdf.pages, start=1):
            try:
                pages.append(_audit_page(page.obj, page_no, spots, warnings))
            except Exception as exc:  # noqa: BLE001 - 单页损坏降级
                logger.warning("页面 %d 盘点失败: %s", page_no, exc)
                warnings.append(f"页面 {page_no} 盘点失败: {exc}")
                pages.append(PageColorReport(page=page_no, color_spaces_used={},
                                             fills_strokes_noted=False, images=()))
    finally:
        pdf.close()

    if not ICC_AVAILABLE:  # pragma: no cover - 并行期分支
        warnings.append("icc 模块不可用，嵌入 ICC profile 未解析（结构盘点已完成）")

    summary = {
        "device_rgb_pages": sum(1 for p in pages
                                if p.color_spaces_used.get("DeviceRGB", 0) > 0),
        "device_cmyk_pages": sum(1 for p in pages
                                 if p.color_spaces_used.get("DeviceCMYK", 0) > 0),
        "icc_pages": sum(1 for p in pages
                         if p.color_spaces_used.get("ICCBased", 0) > 0),
        "spot_colors": len(spots),
        "images": sum(len(p.images) for p in pages),
    }
    logger.info("PDF 审计完成: %s pages=%d intents=%d spots=%d warnings=%d",
                path_repr, len(pages), len(output_intents), len(spots),
                len(warnings))
    return PdfColorAuditResult(
        path=path_repr,
        pdf_version=pdf_version,
        page_count=len(pages),
        encrypted=encrypted,
        output_intents=output_intents,
        pages=tuple(pages),
        spots=tuple(spots),
        warnings=tuple(dict.fromkeys(warnings)),
        summary=summary,
    )
