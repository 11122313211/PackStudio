"""CMM 引擎：PIL.ImageCms（LittleCMS 2.19）封装（TECH-DESIGN §4.5）。

构建/应用分离模式：
- :class:`TransformSpec` 描述一次色彩变换的全部参数，同时作为缓存键；
  profile 以 ``(resolved_path, st_mtime_ns, st_size)`` 三元组或原始 bytes
  （blake2b 摘要）参与键比较，mtime 变化自动失效。
- :class:`TransformEngine` 按 spec 缓存已构建的 ``ImageCmsTransform``
  （手写 OrderedDict LRU，支持 clear 与命中率统计）。
- 所有 ``PIL.PyCMSError`` 一律包装为 :class:`~packstudio.engines.color_engine.errors.CmmError`
  （保留 ``__cause__``）。

环境事实（已在本机核实，Pillow 12.3.0 / lcms 2.19）：
- ``ImageCms.Intent`` 与 :class:`RenderingIntent` 同名同值（0-3）。
- ``ImageCms.Flags``：NOCACHE=64、GAMUTCHECK=4096、BLACKPOINTCOMPENSATION=8192、
  SOFTPROOFING=16384，与设计 §3.6 一致。
- device→Lab 直达须以 ``ImageCms.createProfile("LAB")`` 虚拟 profile 为目标端
  （对真实 profile 直接 outMode='LAB' 会 "cannot build transform"）。
- PIL ``'LAB'`` 模式 8bit 布局：L = round(L*255/100)，a/b 为**有符号字节**
  （二补码，-128..127），解码时按 int8 处理。
- ``ImageCmsTransform.apply`` 在输入模式不匹配时抛 ``ValueError``（非 PyCMSError），
  并会自动把输出 profile 字节写入 ``imOut.info["icc_profile"]``。
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace as _dataclass_replace
from enum import IntEnum
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Final, Literal

import numpy
from PIL import Image
from PIL import ImageCms

from packstudio.engines.color_engine.errors import (
    CmmError,
    ImagingError,
    IntentUnsupportedError,
    ProfileLoadError,
    ProfileMismatchError,
)

logger = logging.getLogger("icct.cmm.engine")

__all__ = [
    "RenderingIntent",
    "TransformSpec",
    "TransformEngine",
    "open_profile",
    "profile_names",
    "intent_supported",
    "transform_lab",
    "soft_proof",
    "check_out_of_gamut",
    "auto_proof_workers",
]


class RenderingIntent(IntEnum):
    """渲染意图（与 ``ImageCms.Intent`` 同名同值，设计 §3.6 已核实）。"""

    PERCEPTUAL = 0
    RELATIVE_COLORIMETRIC = 1
    SATURATION = 2
    ABSOLUTE_COLORIMETRIC = 3


# ---- 内部常量与帮助函数 -------------------------------------------------

_PROFILE_KEY_BYTES_LEN: Final[int] = 16  # blake2b 摘要长度（设计 §4.5）


def _pil_intent(intent: RenderingIntent | int) -> ImageCms.Intent:
    """项目枚举 → ``ImageCms.Intent``（值恒等映射）。"""
    return ImageCms.Intent(int(RenderingIntent(intent)))


def _digest(data: bytes) -> bytes:
    """内存 profile 的缓存键摘要（blake2b-128bit）。"""
    return hashlib.blake2b(data, digest_size=_PROFILE_KEY_BYTES_LEN).digest()


def _profile_key(source: str | os.PathLike[str]) -> tuple[str, int, int]:
    """文件 profile 的缓存键：``(resolved_path, st_mtime_ns, st_size)``。"""
    path = Path(os.fspath(source))
    try:
        stat = path.stat()
    except OSError as exc:
        raise ProfileLoadError(
            f"profile 文件不存在或不可访问: {path}", detail={"path": str(path), "errno": exc.errno}
        ) from exc
    try:
        resolved = str(path.resolve())
    except OSError:  # 防御：resolve 失败时退回原始路径
        resolved = str(path)
    return (resolved, stat.st_mtime_ns, stat.st_size)


def _normalize_key(value: tuple[str, int, int] | bytes | str | os.PathLike[str] | BinaryIO | None,
                   ) -> tuple[str, int, int] | bytes | None:
    """把任意 profile 来源归一化为可哈希键（设计 §4.5 缓存键约定）。"""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _digest(bytes(value))
    if isinstance(value, tuple):
        return value
    if hasattr(value, "read"):  # BinaryIO：一次性读入后按 bytes 处理
        return _digest(bytes(value.read()))
    return _profile_key(os.fspath(value))


def _header_prefix(value: Any, n: int) -> bytes:
    """读取 profile 来源的头部前 n 字节（bytes 直接切；路径读文件）。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value[:n])
    if isinstance(value, tuple):  # 预计算键 (path, mtime, size)
        path: str | os.PathLike[str] = value[0]
    elif hasattr(value, "read"):
        raise ProfileLoadError(
            "无法从文件对象读取 profile 头部；请传入 bytes 或路径",
            detail={"source": "<BinaryIO>"})
    else:
        path = os.fspath(value)
    try:
        with open(path, "rb") as handle:
            return handle.read(n)
    except OSError as exc:
        raise ProfileLoadError(
            f"profile 头部读取失败: {path}", detail={"path": str(path), "errno": exc.errno}
        ) from exc


_MODE_FOR_SPACE: Final[dict[str, str]] = {
    "RGB ": "RGB",
    "CMYK": "CMYK",
    "GRAY": "L",
    "Lab ": "LAB",
    "XYZ ": "LAB",
}

_ALPHA_OUT: Final[dict[str, str]] = {"RGB": "RGBA", "L": "LA"}


def _mode_of(value: tuple[str, int, int] | bytes | str | os.PathLike[str]) -> str:
    """由 profile 数据色空间（header 16-19 字节）决定 PIL 模式（设计 §4.5）。

    头部无效（不足 40 字节或魔数非 ``acsp``）→ ProfileLoadError（损坏）；
    魔数合法但色空间不在支持表 → CmmError。
    """
    header = _header_prefix(value, 40)
    if len(header) < 40 or header[36:40] != b"acsp":
        raise ProfileLoadError(
            "profile 头部无效（损坏或非 ICC profile）",
            detail={"header_bytes": len(header)},
        )
    space = header[16:20].decode("ascii", "replace")
    try:
        return _MODE_FOR_SPACE[space]
    except KeyError:
        raise CmmError(
            f"不支持的 profile 数据色空间: {space!r}",
            detail={"color_space": space},
        ) from None


def _open_source(value: Any) -> ImageCms.ImageCmsProfile:
    """打开 profile（bytes/路径/预计算键/文件对象），失败统一 ProfileLoadError。"""
    if isinstance(value, tuple):
        return open_profile(value[0])
    return open_profile(value)


def _compatible_base(im: Image.Image, in_mode: str) -> Image.Image:
    """返回与变换输入模式一致的基础图像（丢弃 alpha，由调用方决定是否拼回）。"""
    if im.mode == in_mode:
        return im
    if im.mode == in_mode + "A":  # RGB→RGBA、L→LA
        return im.convert(in_mode)
    if in_mode == "RGB" and im.mode == "P":
        return im.convert("RGB")
    raise ProfileMismatchError(
        f"图像模式 {im.mode} 与变换输入模式 {in_mode} 不兼容",
        detail={"image_mode": im.mode, "input_mode": in_mode},
    )


def _run_transform(transform: ImageCms.ImageCmsTransform, base: Image.Image) -> Image.Image:
    """应用变换；PyCMSError/ValueError 统一包装为 CmmError。"""
    try:
        return transform.apply(base)
    except (ImageCms.PyCMSError, ValueError) as exc:
        raise CmmError(
            f"LittleCMS 变换失败: {exc}",
            detail={"input_mode": base.mode, "output_mode": transform.outputMode},
        ) from exc


# ---- 数据模型 -----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TransformSpec:
    """一次色彩变换的完整描述，同时作为 :class:`TransformEngine` 的缓存键。

    Attributes:
        src_profile: 源 profile：``(resolved_path, mtime_ns, size)`` 三元组、
            原始 bytes、或路径（构建时自动归一化；BinaryIO 不支持）。
        dst_profile: 目标 profile，形式同上。
        intent: 渲染意图。
        bpc: 黑点补偿（BLACKPOINTCOMPENSATION）。
        proof_profile: 软打样/色域检查用的 proofing profile；None 表示普通变换。
        proof_intent: proofing 方向的渲染意图（默认绝对比色，设计 §3.6）。
        gamut_check: 是否启用 GAMUTCHECK（仅在 proof_profile 存在时生效）。
    """

    src_profile: tuple[str, int, int] | bytes
    dst_profile: tuple[str, int, int] | bytes
    intent: RenderingIntent = RenderingIntent.PERCEPTUAL
    bpc: bool = False
    proof_profile: tuple[str, int, int] | bytes | None = None
    proof_intent: RenderingIntent = RenderingIntent.ABSOLUTE_COLORIMETRIC
    gamut_check: bool = False

    def lcms_flags(self) -> ImageCms.Flags:
        """计算传给 LittleCMS 的 flags 组合。

        恒含 NOCACHE（缓存由 TransformEngine 管理，防 lcms 内部缓存膨胀）；
        BPC/GAMUTCHECK/SOFTPROOFING 按字段置位；GAMUTCHECK 仅在存在
        proofing profile 时生效（lcms 约束）。
        """
        flags = int(ImageCms.Flags.NOCACHE)
        if self.bpc:
            flags |= int(ImageCms.Flags.BLACKPOINTCOMPENSATION)
        if self.proof_profile is not None:
            flags |= int(ImageCms.Flags.SOFTPROOFING)
            if self.gamut_check:
                flags |= int(ImageCms.Flags.GAMUTCHECK)
        return ImageCms.Flags(flags)


def _normalize_spec(spec: TransformSpec) -> TransformSpec:
    """归一化 spec 为等价可哈希形态（路径→三元组、bytes→摘要、枚举收敛）。"""
    return TransformSpec(
        src_profile=_normalize_key(spec.src_profile),  # type: ignore[arg-type]
        dst_profile=_normalize_key(spec.dst_profile),  # type: ignore[arg-type]
        intent=RenderingIntent(int(spec.intent)),
        bpc=bool(spec.bpc),
        proof_profile=_normalize_key(spec.proof_profile),
        proof_intent=RenderingIntent(int(spec.proof_intent)),
        gamut_check=bool(spec.gamut_check),
    )


# ---- 模块级函数 ---------------------------------------------------------

def open_profile(source: bytes | str | os.PathLike[str] | BinaryIO) -> ImageCms.ImageCmsProfile:
    """打开 ICC profile，返回 ``ImageCms.ImageCmsProfile``。

    行为:
        bytes 直接从内存打开；路径先做存在性检查；文件对象透传给
        Pillow（其内部一次性读取）。所有 ``PyCMSError`` 包装为
        :class:`~packstudio.engines.color_engine.errors.ProfileLoadError`（保留 ``__cause__``）。
    参数:
        source: profile 原始字节、文件路径或二进制文件对象。
    返回:
        ``ImageCms.ImageCmsProfile``。
    异常:
        ProfileLoadError: 路径不存在、不可读或 profile 损坏。
    """
    detail_path: str
    if isinstance(source, (bytes, bytearray, memoryview)):
        stream: Any = io.BytesIO(bytes(source))
        detail_path = "<bytes>"
    elif hasattr(source, "read"):
        stream = source
        detail_path = str(getattr(source, "name", "<file-object>"))
    else:
        path = Path(os.fspath(source))
        if not path.is_file():
            raise ProfileLoadError(
                f"profile 文件不存在: {path}", detail={"path": str(path)}
            )
        stream = str(path)
        detail_path = str(path)
    try:
        return ImageCms.getOpenProfile(stream)
    except ImageCms.PyCMSError as exc:
        raise ProfileLoadError(
            f"profile 无法打开或已损坏: {exc}", detail={"path": detail_path}
        ) from exc


def profile_names(profile: ImageCms.ImageCmsProfile) -> tuple[str, str, str]:
    """返回 profile 的 ``(name, description, info)`` 三元组。

    行为:
        包装 ``ImageCms.getProfileName/Description/Info``；name/description
        去除首尾空白（lcms 常带换行），info 保持原文。
    参数:
        profile: 已打开的 profile。
    返回:
        ``(name, description, info)``。
    异常:
        CmmError: LittleCMS 读取失败。
    """
    try:
        name = ImageCms.getProfileName(profile).strip()
        description = ImageCms.getProfileDescription(profile).strip()
        info = ImageCms.getProfileInfo(profile)
    except ImageCms.PyCMSError as exc:
        raise CmmError(f"读取 profile 元信息失败: {exc}") from exc
    return (name, description, info)


def intent_supported(profile: ImageCms.ImageCmsProfile, intent: RenderingIntent,
                     direction: Literal["input", "output", "proof"]) -> bool:
    """探测 profile 在指定方向上是否支持给定渲染意图。

    行为:
        包装 ``ImageCms.isIntentSupported``（lcms 返回 1/-1，映射 bool）。
    参数:
        profile: 已打开的 profile。
        intent: 渲染意图。
        direction: ``'input'`` | ``'output'`` | ``'proof'``。
    返回:
        支持为 True。
    异常:
        CmmError: LittleCMS 调用失败或方向非法。
    """
    directions: Final[dict[str, ImageCms.Direction]] = {
        "input": ImageCms.Direction.INPUT,
        "output": ImageCms.Direction.OUTPUT,
        "proof": ImageCms.Direction.PROOF,
    }
    try:
        dir_enum = directions[direction]
    except KeyError:
        raise CmmError(
            f"非法方向: {direction!r}", detail={"direction": str(direction)}
        ) from None
    try:
        result = ImageCms.isIntentSupported(profile, _pil_intent(intent), dir_enum)
    except (ImageCms.PyCMSError, TypeError, ValueError) as exc:
        raise CmmError(f"意图支持探测失败: {exc}") from exc
    return result == 1


# ---- TransformEngine ----------------------------------------------------

class TransformEngine:
    """构建/应用分离的色彩变换引擎，带手写 LRU 缓存。

    缓存键为归一化后的 :class:`TransformSpec`（profile 路径 + mtime + size
    参与键，bytes 用摘要参与；mtime 变化自动失效）。线程安全依赖 dict 的
    原子操作（批量转换在 ThreadPoolExecutor 下使用）。
    """

    def __init__(self, max_cache: int = 32) -> None:
        """初始化引擎。

        参数:
            max_cache: 缓存的变换数量上限；<=0 表示禁用缓存（每次重建）。
        """
        self._max_cache = int(max_cache)
        self._cache: OrderedDict[TransformSpec, ImageCms.ImageCmsTransform] = OrderedDict()
        self._hits = 0
        self._misses = 0

    # -- 构建 --
    def build_transform(self, spec: TransformSpec) -> ImageCms.ImageCmsTransform:
        """按 spec 构建变换（命中缓存时返回既有对象）。

        行为:
            归一化 spec 作为键查 LRU；未命中时打开两端 profile、先行探测
            意图支持（input/output 方向），再调用
            ``buildTransformFromOpenProfiles``（有 proof 时
            ``buildProofTransformFromOpenProfiles``）。构建与应用分离。
        参数:
            spec: 变换描述。
        返回:
            ``ImageCms.ImageCmsTransform``（同一 spec 命中时为同一对象）。
        异常:
            ProfileLoadError: profile 缺失/损坏。
            IntentUnsupportedError: 意图在指定方向不受支持。
            CmmError: LittleCMS 构建失败（PyCMSError 包装）。
        """
        if not isinstance(spec, TransformSpec):
            raise TypeError(f"需要 TransformSpec，得到 {type(spec).__name__}")
        key = _normalize_spec(spec)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self._hits += 1
            logger.debug("变换缓存命中: intent=%s bpc=%s proof=%s",
                         key.intent.name, key.bpc, key.proof_profile is not None)
            return cached
        self._misses += 1
        transform = self._build(spec)
        if self._max_cache > 0:
            self._cache[key] = transform
            while len(self._cache) > self._max_cache:
                self._cache.popitem(last=False)
        return transform

    def _build(self, spec: TransformSpec) -> ImageCms.ImageCmsTransform:
        """实际构建（使用 spec 原始值；bytes/路径均可读取）。"""
        in_mode = _mode_of(spec.src_profile)
        out_mode = _mode_of(spec.dst_profile)
        in_prof = _open_source(spec.src_profile)
        out_prof = _open_source(spec.dst_profile)
        if not intent_supported(in_prof, spec.intent, "input"):
            raise IntentUnsupportedError(
                f"源 profile 不支持意图 {RenderingIntent(spec.intent).name}（input 方向）",
                detail={"intent": RenderingIntent(spec.intent).name, "direction": "input"},
            )
        if not intent_supported(out_prof, spec.intent, "output"):
            raise IntentUnsupportedError(
                f"目标 profile 不支持意图 {RenderingIntent(spec.intent).name}（output 方向）",
                detail={"intent": RenderingIntent(spec.intent).name, "direction": "output"},
            )
        flags = spec.lcms_flags()
        try:
            if spec.proof_profile is not None:
                proof_prof = _open_source(spec.proof_profile)
                transform = ImageCms.buildProofTransformFromOpenProfiles(
                    in_prof, out_prof, proof_prof, in_mode, out_mode,
                    renderingIntent=_pil_intent(spec.intent),
                    proofRenderingIntent=_pil_intent(spec.proof_intent),
                    flags=flags,
                )
            else:
                transform = ImageCms.buildTransformFromOpenProfiles(
                    in_prof, out_prof, in_mode, out_mode,
                    renderingIntent=_pil_intent(spec.intent),
                    flags=flags,
                )
        except ImageCms.PyCMSError as exc:
            raise CmmError(
                f"LittleCMS 构建变换失败: {exc}",
                detail={
                    "input_mode": in_mode,
                    "output_mode": out_mode,
                    "intent": RenderingIntent(spec.intent).name,
                    "proof": spec.proof_profile is not None,
                },
            ) from exc
        logger.debug("构建变换: %s -> %s intent=%s flags=%s",
                     in_mode, out_mode, RenderingIntent(spec.intent).name, int(flags))
        return transform

    # -- 应用 --
    def apply(self, spec: TransformSpec, im: Image.Image) -> Image.Image:
        """把 spec 对应的变换应用到图像，返回新图像。

        行为:
            先 build（走缓存），再按设计 §4.5 处理模式兼容：RGB(A) 输入统一
            先转 'RGB'（alpha 在变换后原样拼回，不依赖 COPY_ALPHA flag）；
            模式不兼容抛 ProfileMismatchError。CMYK→RGB 输出模式为 'RGB'，反之 'CMYK'。
        参数:
            spec: 变换描述。
            im: 输入图像。
        返回:
            变换后的新图像（输出模式由目标 profile 色空间决定）。
        异常:
            ProfileMismatchError: 图像模式与变换输入模式不兼容。
            CmmError: LittleCMS 应用失败。
            ProfileLoadError / IntentUnsupportedError: 透传自 build。
        """
        if not isinstance(im, Image.Image):
            raise ImagingError(
                "apply 需要 PIL.Image.Image", detail={"type": type(im).__name__}
            )
        transform = self.build_transform(spec)
        in_mode = _mode_of(spec.src_profile)
        out_mode = _mode_of(spec.dst_profile)
        alpha = im.getchannel("A") if im.mode == in_mode + "A" else None
        base = _compatible_base(im, in_mode)
        out = _run_transform(transform, base)
        if alpha is not None:
            out = out.convert(_ALPHA_OUT[out_mode])
            out.putalpha(alpha)
        return out

    # -- 缓存管理 --
    def clear_cache(self) -> None:
        """清空变换缓存（命中率统计保留）。"""
        self._cache.clear()

    def cache_info(self) -> dict[str, int | float]:
        """返回缓存统计：``{"hits", "misses", "entries", "max_cache", "hit_rate"}``。

        hit_rate 为 0-1 浮点（总查找数为 0 时为 0.0）。
        """
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._cache),
            "max_cache": self._max_cache,
            "hit_rate": (self._hits / total) if total else 0.0,
        }


_ENGINE: Final[TransformEngine] = TransformEngine()
"""模块级共享引擎（供 soft_proof / check_out_of_gamut 及 imaging 层复用）。"""


# ---- 诊断与软打样 -------------------------------------------------------

def transform_lab(profile: bytes | str | os.PathLike[str],
                  im_or_array: Image.Image | numpy.ndarray,
                  intent: RenderingIntent = RenderingIntent.RELATIVE_COLORIMETRIC,
                  bpc: bool = True) -> numpy.ndarray:
    """设备值 → Lab 直达诊断变换（设计 §4.5；viz 模块的跨模块契约）。

    行为:
        以 ``ImageCms.createProfile("LAB")`` 为虚拟目标端构建
        device→'LAB' 变换并应用，再把 PIL 8bit Lab 重排为真实刻度：
        L = L8*100/255；a/b 按**有符号字节**解码（-128..127）。
        这是 8bit 量化路径（L 步进约 0.39、a/b 步进 1.0），verify/采样
        场景可接受（设计 §8 R5）。

        跨模块契约（viz.gamut._default_transform 的调用形态，冻结）::

            v8 = round(clip(values01, 0, 1) * 255).astype(uint8)   # (n, k)
            lab = transform_lab(profile, v8, RenderingIntent(...), bool(bpc))
            # lab: (n, 3) float64, L∈[0,100], a/b∈[-128,127]

        兼容两种调用形态：``transform_lab(p, v, intent, bpc)``（viz 位置参数）
        与 ``transform_lab(p, v, intent=..., bpc=...)``（关键字）。
    参数:
        profile: 设备 profile（路径或 bytes）。
        im_or_array: PIL 图像（模式须与 profile 色空间一致，RGBA/LA 自动剥离
            alpha），或形状 ``(n, k)`` 的 uint8 数值条带（0-255 刻度；
            k=3 → RGB、k=4 → CMYK、k=1 → GRAY）。
        intent: 渲染意图（默认相对比色）。
        bpc: 黑点补偿（默认开）。
    返回:
        ``(n, 3)`` float64 Lab 数组，L∈[0,100]、a/b∈[-128,127]。
    异常:
        ProfileLoadError: profile 缺失/损坏。
        ImagingError: 数组形状与 profile 色空间不符。
        CmmError: LittleCMS 变换失败。
    """
    src = profile
    in_mode = _mode_of(src)
    n_channels = {"RGB": 3, "CMYK": 4, "L": 1, "LAB": 3}[in_mode]
    if isinstance(im_or_array, Image.Image):
        base = _compatible_base(im_or_array, in_mode)
    else:
        arr = numpy.asarray(im_or_array)
        if arr.ndim != 2 or arr.shape[1] != n_channels:
            raise ImagingError(
                f"数组形状 {arr.shape} 与 profile 输入（(n, {n_channels})）不符",
                detail={"shape": list(arr.shape), "expected_channels": n_channels},
            )
        if arr.shape[0] == 0:
            return numpy.zeros((0, 3), dtype=numpy.float64)
        arr8 = numpy.clip(arr, 0, 255).round().astype(numpy.uint8)
        base = Image.fromarray(arr8.reshape(1, arr.shape[0], n_channels), mode=in_mode)
    in_prof = _open_source(src)
    flags = int(ImageCms.Flags.NOCACHE)
    if bpc:
        flags |= int(ImageCms.Flags.BLACKPOINTCOMPENSATION)
    try:
        transform = ImageCms.buildTransformFromOpenProfiles(
            in_prof, ImageCms.createProfile("LAB"), in_mode, "LAB",
            renderingIntent=_pil_intent(intent), flags=ImageCms.Flags(flags),
        )
    except ImageCms.PyCMSError as exc:
        raise CmmError(
            f"构建 device→Lab 变换失败: {exc}",
            detail={"input_mode": in_mode, "intent": RenderingIntent(intent).name},
        ) from exc
    out = _run_transform(transform, base)
    lab8 = numpy.asarray(out, dtype=numpy.uint8).reshape(-1, 3)
    lab = numpy.empty((lab8.shape[0], 3), dtype=numpy.float64)
    lab[:, 0] = lab8[:, 0] * (100.0 / 255.0)
    lab[:, 1:3] = lab8[:, 1:3].astype(numpy.int8)
    return lab


# ---------------------------------------------------------------------------
# 软打样并行分带（ROADMAP v0.4.9）
# ---------------------------------------------------------------------------

#: 自动并行的解码尺寸阈值（字节）：低于此值整图单次变换（线程池调度开销
#: 不值）；实测依据：6MP RGB（约 17MB 解码）单次变换 ~80ms，24MB 起并行收益
#: 明显大于开销。
PROOF_PARALLEL_THRESHOLD_BYTES: Final[int] = 24 * 1024 * 1024

#: 自动并行的最大线程数（本机 4 核实测 k=4 共 3.15×，GIL 已被 Pillow C 层释放）。
PROOF_MAX_WORKERS: Final[int] = 4


def auto_proof_workers(decoded_bytes: int, *, threshold: int | None = None,
                       cpu: int | None = None) -> int:
    """按解码尺寸选择软打样并行线程数（v0.4.9 三档规则）。

    规则：``decoded_bytes >= threshold`` → ``min(PROOF_MAX_WORKERS, cpu)``；
    否则 1（整图单次变换）。``threshold``/``cpu`` 可注入（测试用）。
    """
    limit = PROOF_PARALLEL_THRESHOLD_BYTES if threshold is None else int(threshold)
    ncpu = (os.cpu_count() or 1) if cpu is None else int(cpu)
    if int(decoded_bytes) >= limit:
        return max(1, min(PROOF_MAX_WORKERS, ncpu))
    return 1


def _apply_bands_parallel(spec: TransformSpec, im: Image.Image, *,
                          workers: int) -> Image.Image:
    """横向条带并行执行变换（ROADMAP v0.4.9）。

    带数 = workers（每线程一带，行数 ceil 均分）；各带并发调用
    :meth:`TransformEngine.apply`——引擎 LRU 命中同一 transform 对象，
    LittleCMS ``cmsDoTransform`` 为逐点变换且可重入（实测多线程共用同一
    transform 对象并发调用安全，k=4 达 3.15×），带间输出与整图变换逐像素
    一致（有测试锁定）。``executor.map`` 保序消费：首个完成结果确定输出
    模式后建整幅画布，其余按序 paste。
    """
    width, height = im.size
    band_rows = max(1, (height + workers - 1) // workers)
    bands: list[tuple[int, Image.Image]] = []
    y0 = 0
    while y0 < height:
        y1 = min(height, y0 + band_rows)
        bands.append((y0, im.crop((0, y0, width, y1))))
        y0 = y1
    out: Image.Image | None = None
    worker_count = max(1, min(workers, len(bands)))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        for (y_off, _band), converted in zip(
            bands, pool.map(partial(_ENGINE.apply, spec), (band for _y, band in bands))
        ):
            if out is None:
                out = Image.new(converted.mode, (width, height))
            out.paste(converted, (0, y_off))
    if out is None:  # 0 像素图（防御；与 imaging._convert_tiled 同）
        out = _ENGINE.apply(spec, im)
    return out


def soft_proof(im: Image.Image, device_profile: str | bytes | os.PathLike[str],
               display_profile: str | bytes | os.PathLike[str],
               intent: RenderingIntent = RenderingIntent.RELATIVE_COLORIMETRIC,
               bpc: bool = True, *,
               workers: int | None = None) -> tuple[Image.Image, dict[str, Any]]:
    """软打样（设计 §4.5 数据流 C）。

    行为:
        主路径为 lcms 原生单段 proof 变换：
        ``in=device → out=display，proof=device，SOFTPROOFING flag``
        （模拟设备色在显示器上的呈现，色差小于两段式）。
        out-of-gamut 统计由 :func:`check_out_of_gamut` 的诊断路径提供。
        两段式（device→Lab→display）保留为诊断路径，见 transform_lab。
        v0.4.9 多线程 proof：``workers=None``（默认）按解码尺寸自动——
        ≥ :data:`PROOF_PARALLEL_THRESHOLD_BYTES` 时横向条带并行
        （:data:`PROOF_MAX_WORKERS` 上限，主变换与 OOG 诊断的三次变换
        全部并行）；``>=2`` 强制并行分带；``<=1`` 整图单次变换。并行输出
        与整图变换逐像素一致（恒等锁定测试）。
    参数:
        im: 处于 device 色空间的图像（如 CMYK 印刷图；RGB 亦可）。
        device_profile: 图像所在设备 profile。
        display_profile: 模拟目标（显示器）profile。
        intent: 渲染意图（同时用于模拟与回显方向）。
        bpc: 黑点补偿。
        workers: 并行线程数；None=按解码尺寸自动（≥24MB 并行），
                 >=2 强制分带并行，<=1 整图串行。
    返回:
        ``(软打样 RGB 图, 统计 dict)``；统计含 ``out_of_gamut_pixel_ratio``、
        ``out_of_gamut_pixels``、``width``、``height``、``intent``、``bpc``、
        ``workers``。
    异常:
        ProfileLoadError / IntentUnsupportedError / CmmError / ProfileMismatchError。
    """
    intent = RenderingIntent(int(intent))
    decoded = int(im.width) * int(im.height) * max(1, len(im.getbands()))
    n_workers = auto_proof_workers(decoded) if workers is None else max(1, int(workers))
    spec = TransformSpec(
        src_profile=_normalize_key(device_profile),  # type: ignore[arg-type]
        dst_profile=_normalize_key(display_profile),  # type: ignore[arg-type]
        intent=intent,
        bpc=bool(bpc),
        proof_profile=_normalize_key(device_profile),  # type: ignore[arg-type]
        proof_intent=intent,
        gamut_check=False,
    )
    if n_workers > 1:
        out = _apply_bands_parallel(spec, im, workers=n_workers)
    else:
        out = _ENGINE.apply(spec, im)
    _mask, oog_count, oog_ratio = check_out_of_gamut(
        im, device_profile, display_profile, intent=intent, bpc=bool(bpc),
        workers=n_workers,
    )
    stats: dict[str, Any] = {
        "out_of_gamut_pixel_ratio": oog_ratio,
        "out_of_gamut_pixels": oog_count,
        "width": int(im.width),
        "height": int(im.height),
        "intent": intent.name,
        "bpc": bool(bpc),
        "workers": int(n_workers),
    }
    logger.info("软打样完成: %dx%d out_of_gamut=%.4f workers=%d",
                im.width, im.height, oog_ratio, n_workers)
    return out, stats


def check_out_of_gamut(im: Image.Image, device_profile: str | bytes | os.PathLike[str],
                       display_profile: str | bytes | os.PathLike[str],
                       intent: RenderingIntent = RenderingIntent.RELATIVE_COLORIMETRIC,
                       bpc: bool = True, *,
                       workers: int | None = None) -> tuple[Image.Image, int, float]:
    """越界（out-of-gamut）掩图诊断（设计 §4.5 GAMUTCHECK 路线）。

    行为:
        构建 proof 变换 ``in=device → out=display，proof=display``，
        分别以 ``SOFTPROOFING`` 与 ``SOFTPROOFING|GAMUTCHECK`` flags 各跑一次；
        lcms 把越界像素替换为报警色，因此"两次输出不同的像素"即越界像素
        （等价于读取 lcms 的逐像素 gamut 判定，且不依赖报警色取值）。
        语义：设备色中显示器无法复现的部分。
        v0.4.9：``workers`` 语义同 :func:`soft_proof`（None=自动/≥2 并行分带/
        ≤1 整图）——两次全图变换各自并行。
    参数:
        im: 处于 device 色空间的图像。
        device_profile: 图像所在设备 profile。
        display_profile: 模拟目标（显示器）profile。
        intent: 渲染意图。
        bpc: 黑点补偿。
        workers: 并行线程数；None=按解码尺寸自动，>=2 强制并行，<=1 整图。
    返回:
        ``(掩图, 越界像素数, 占比)``；掩图为 'L' 模式、与输入同尺寸，
        越界处=255，其余=0；占比 = 越界数 / 总像素（空图为 0.0）。
    异常:
        ProfileLoadError / IntentUnsupportedError / CmmError / ProfileMismatchError。
    """
    intent = RenderingIntent(int(intent))
    decoded = int(im.width) * int(im.height) * max(1, len(im.getbands()))
    n_workers = auto_proof_workers(decoded) if workers is None else max(1, int(workers))
    key_display = _normalize_key(display_profile)  # type: ignore[arg-type]
    base_spec = TransformSpec(
        src_profile=_normalize_key(device_profile),  # type: ignore[arg-type]
        dst_profile=key_display,
        intent=intent,
        bpc=bool(bpc),
        proof_profile=key_display,
        proof_intent=intent,
        gamut_check=False,
    )
    flagged_spec = _dataclass_replace(base_spec, gamut_check=True)
    apply_once = (lambda spec, image: _apply_bands_parallel(spec, image, workers=n_workers)) \
        if n_workers > 1 else (lambda spec, image: _ENGINE.apply(spec, image))
    out_plain = numpy.asarray(apply_once(base_spec, im))
    out_flag = numpy.asarray(apply_once(flagged_spec, im))
    if out_plain.shape != out_flag.shape:
        raise CmmError(
            "GAMUTCHECK 两次输出尺寸不一致",
            detail={"plain": list(out_plain.shape), "flagged": list(out_flag.shape)},
        )
    diff = numpy.any(out_plain != out_flag, axis=-1)
    total = int(diff.size)
    count = int(diff.sum())
    ratio = (count / total) if total else 0.0
    mask = diff.astype(numpy.uint8) * 255
    mask_img = Image.fromarray(mask.reshape(out_plain.shape[0], out_plain.shape[1]), mode="L")
    logger.debug("gamut 检查: %d/%d 越界", count, total)
    return (mask_img, count, ratio)
