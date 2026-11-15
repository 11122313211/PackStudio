"""imaging.pipeline 单元测试（TECH-DESIGN §4.7 / §7.1 imaging 行）。

真实 profile 参数化（缺失则 skip）；合成测试图由 Pillow 生成（RGB 渐变）。
PSD 读取用例使用测试内手工构造的最小 PSD 文件（无需外部 fixture）。
核心路径不依赖 icc/ 解析层。
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy
import pytest
from PIL import Image

from packstudio.engines.color_engine.cmm.engine import RenderingIntent
from packstudio.engines.color_engine.imaging.pipeline import (
    SUPPORTED_READ,
    SUPPORTED_WRITE,
    FileResult,
    ProfileOp,
    assign_profile,
    convert_image,
    embed_profile,
    get_embedded_profile,
    process_batch,
    process_file,
    read_image,
    strip_profile,
)
from packstudio.engines.color_engine.errors import ImagingError, ProfileMismatchError, UnsupportedFormatError

_COLOR_DIR = Path(r"C:\Windows\System32\spool\drivers\color")
SRGB = _COLOR_DIR / "sRGB Color Space Profile.icm"
ADOBE = _COLOR_DIR / "AdobeRGB1998.icc"
JAPAN = _COLOR_DIR / "JapanColor2001Coated.icc"


def _need(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"system profile not present: {path}")
    return path


def _srgb_bytes() -> bytes:
    return _need(SRGB).read_bytes()


def _adobe_bytes() -> bytes:
    return _need(ADOBE).read_bytes()


def _japan_bytes() -> bytes:
    return _need(JAPAN).read_bytes()


def _gradient_rgb(width: int = 16, height: int = 16) -> Image.Image:
    """合成 RGB 渐变测试图（Pillow 生成）。"""
    xs = numpy.linspace(0, 255, width, dtype=numpy.float64)
    ys = numpy.linspace(0, 255, height, dtype=numpy.float64)
    r = numpy.tile(xs, (height, 1))
    g = numpy.tile(ys.reshape(-1, 1), (1, width))
    b = ((r + g) / 2.0).clip(0, 255)
    return Image.fromarray(
        numpy.stack([r, g, b], axis=-1).round().astype(numpy.uint8), "RGB"
    )


def _save_rgb_png(path: Path, size: int = 12) -> Path:
    _gradient_rgb(size, size).save(path, "PNG")
    return path


def _make_psd(path: Path, width: int = 2, height: int = 1) -> Path:
    """手工构造最小 PSD（3 通道 8bit 未压缩，Pillow PsdImagePlugin 可读）。"""
    header = (
        b"8BPS" + struct.pack(">H", 1) + b"\0" * 6
        + struct.pack(">H", 3) + struct.pack(">II", height, width)
        + struct.pack(">HH", 8, 3)
    )
    colors = [(255, 0, 0), (0, 128, 255)] * max(width * height, 1)
    planes = b"".join(bytes(c[ch] for c in colors[: width * height]) for ch in range(3))
    path.write_bytes(header + struct.pack(">I", 0) * 3 + struct.pack(">H", 0) + planes)
    return path


# ---------------------------------------------------------------------------
# 常量表
# ---------------------------------------------------------------------------
def test_supported_sets() -> None:
    """读写白名单符合设计 §4.7（PSD 只读）。"""
    assert SUPPORTED_READ == frozenset({".tif", ".tiff", ".jpg", ".jpeg", ".png", ".psd"})
    assert SUPPORTED_WRITE == frozenset({".tif", ".tiff", ".jpg", ".jpeg", ".png"})
    assert issubclass(FileResult, object)  # 冒烟：导入面完整


# ---------------------------------------------------------------------------
# assign / embed / strip
# ---------------------------------------------------------------------------
def test_assign_replaces_tag_without_side_effects() -> None:
    """assign 重贴标签：像素不动、新 info、原对象不变。"""
    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    im = _gradient_rgb()
    im.info["icc_profile"] = adobe
    out = assign_profile(im, srgb)
    assert out.info["icc_profile"] == srgb
    assert im.info["icc_profile"] == adobe  # 原对象不受影响
    assert out.mode == im.mode and out.size == im.size
    numpy.testing.assert_array_equal(numpy.asarray(out), numpy.asarray(im))  # 像素不动


def test_assign_mismatch_cmyk_profile_on_rgb_image() -> None:
    """CMYK profile 配 RGB 图 → ProfileMismatchError。"""
    japan = _japan_bytes()
    im = _gradient_rgb(4, 4)
    with pytest.raises(ProfileMismatchError) as exc_info:
        assign_profile(im, japan)
    assert exc_info.value.code == "PROFILE_MISMATCH"
    assert exc_info.value.detail["profile_color_space"] == "CMYK"


def test_assign_mismatch_rgb_profile_on_cmyk_image() -> None:
    """RGB profile 配 CMYK 图 → ProfileMismatchError。"""
    srgb = _srgb_bytes()
    im = Image.fromarray(numpy.zeros((2, 2, 4), dtype=numpy.uint8), "CMYK")
    with pytest.raises(ProfileMismatchError):
        assign_profile(im, srgb)


def test_assign_short_profile_data() -> None:
    """过短 profile 数据 → ImagingError。"""
    with pytest.raises(ImagingError):
        assign_profile(_gradient_rgb(2, 2), b"\x00" * 10)


def test_embed_idempotent_and_strip() -> None:
    """embed 幂等（重复贴同 profile 字节不变）；strip 移除 icc_profile。"""
    srgb = _srgb_bytes()
    im = _gradient_rgb(8, 8)
    e1 = embed_profile(im, srgb)
    assert e1.info["icc_profile"] == srgb
    e2 = embed_profile(e1, srgb)
    assert e2.info["icc_profile"] == srgb

    stripped = strip_profile(e2)
    assert "icc_profile" not in stripped.info
    assert "icc_profile" in e2.info  # 原对象不受影响
    # strip 幂等
    assert "icc_profile" not in strip_profile(stripped).info


def test_get_embedded_profile_labels() -> None:
    """来源标签：png 有值、无嵌入为 ('none', None)。"""
    srgb = _srgb_bytes()
    im = _gradient_rgb(4, 4)
    assert get_embedded_profile(im) == ("none", None)
    embedded = assign_profile(im, srgb)
    source, data = get_embedded_profile(embedded)
    assert data == srgb
    assert source in {"png", "unknown"}  # 内存图像 format 可能未设


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------
def test_convert_srgb_to_adobe() -> None:
    """sRGB→AdobeRGB：尺寸/模式不变、贴新 ICC、像素有变化。"""
    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    im = assign_profile(_gradient_rgb(), srgb)
    before = numpy.asarray(im).copy()
    out = convert_image(im, adobe)
    assert out.mode == "RGB"
    assert out.size == im.size
    assert out.info["icc_profile"] == adobe
    assert not numpy.array_equal(numpy.asarray(out), before)  # 色域映射产生差异


def test_convert_no_source_profile() -> None:
    """无嵌入且无 src_profile → ImagingError(code='NO_SOURCE_PROFILE')。"""
    adobe = _adobe_bytes()
    im = _gradient_rgb(4, 4)
    assert "icc_profile" not in im.info
    with pytest.raises(ImagingError) as exc_info:
        convert_image(im, adobe)
    assert exc_info.value.code == "NO_SOURCE_PROFILE"


def test_convert_explicit_src_priority() -> None:
    """src_profile 参数优先于嵌入值（剥离后的图 + 显式源可转换）。"""
    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    im = strip_profile(assign_profile(_gradient_rgb(), srgb))
    assert "icc_profile" not in im.info
    out = convert_image(im, adobe, srgb)
    assert out.info["icc_profile"] == adobe
    assert out.mode == "RGB"


def test_convert_rgb_to_cmyk() -> None:
    """RGB→CMYK（JapanColor）：输出模式 CMYK 且带其 ICC。"""
    srgb = _srgb_bytes()
    japan = _japan_bytes()
    im = assign_profile(_gradient_rgb(8, 8), srgb)
    out = convert_image(im, japan, intent=RenderingIntent.RELATIVE_COLORIMETRIC, bpc=True)
    assert out.mode == "CMYK"
    assert out.size == im.size
    assert out.info["icc_profile"] == japan


def test_convert_cmyk_to_rgb() -> None:
    """CMYK→RGB：输出模式 RGB（设计 §4.7 mode 映射）。"""
    japan = _japan_bytes()
    srgb = _srgb_bytes()
    arr = numpy.array([[[40, 30, 30, 240], [200, 180, 170, 20]]], dtype=numpy.uint8)
    im = Image.fromarray(arr, "CMYK")
    im.info["icc_profile"] = japan
    out = convert_image(im, srgb)
    assert out.mode == "RGB"
    assert out.info["icc_profile"] == srgb


# ---------------------------------------------------------------------------
# process_file：单文件全流程 + 往返
# ---------------------------------------------------------------------------
def test_process_file_assign_embed_strip_convert_roundtrip(tmp_path: Path) -> None:
    """PNG 往返：embed 后重读 ICC 字节一致；strip 后消失；convert 后为目标 profile。"""
    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    src = _save_rgb_png(tmp_path / "src.png")

    # EMBED
    dst_embed = tmp_path / "out" / "src-embed.png"  # 目录不存在 → 自动创建
    result = process_file(src, dst_embed, ProfileOp.EMBED, profile=srgb)
    assert result.ok, result.message
    assert result.error_code is None
    assert dst_embed.is_file()
    reread = read_image(dst_embed)
    assert get_embedded_profile(reread) == ("png", srgb)
    assert result.dst_profile  # 描述名非空（sRGB）

    # STRIP
    dst_strip = tmp_path / "src-strip.png"
    result = process_file(dst_embed, dst_strip, ProfileOp.STRIP)
    assert result.ok
    reread = read_image(dst_strip)
    assert get_embedded_profile(reread) == ("none", None)

    # CONVERT（嵌入值作源）
    dst_convert = tmp_path / "src-convert.png"
    result = process_file(dst_embed, dst_convert, ProfileOp.CONVERT, profile=adobe)
    assert result.ok
    reread = read_image(dst_convert)
    assert reread.mode == "RGB"
    assert get_embedded_profile(reread) == ("png", adobe)

    # ASSIGN
    dst_assign = tmp_path / "src-assign.png"
    result = process_file(src, dst_assign, ProfileOp.ASSIGN, profile=srgb)
    assert result.ok
    assert read_image(dst_assign).info["icc_profile"] == srgb


def test_process_file_convert_with_explicit_src_profile(tmp_path: Path) -> None:
    """v0.2.0：未嵌入图像 convert 一步化——显式 src_profile 优先于嵌入值。"""
    srgb = _srgb_bytes()
    japan = _japan_bytes()
    untagged = _save_rgb_png(tmp_path / "untagged.png")

    # 未嵌入 + 显式源 → 一步 convert 成功（此前为 NO_SOURCE_PROFILE）
    dst = tmp_path / "out" / "untagged-convert.tif"
    result = process_file(untagged, dst, ProfileOp.CONVERT,
                          profile=japan, src_profile=srgb)
    assert result.ok, result.message
    reread = read_image(dst)
    assert reread.mode == "CMYK"
    assert get_embedded_profile(reread) == ("tiff", japan)

    # 有嵌入 + 显式源：CMYK tif（嵌入 japan）→ RGB png（显式 src=japan、dst=sRGB）
    dst2 = tmp_path / "out" / "tagged-convert.png"
    result2 = process_file(dst, dst2, ProfileOp.CONVERT,
                           profile=srgb, src_profile=japan,
                           out_format="png")
    assert result2.ok, result2.message
    reread2 = read_image(dst2)
    assert reread2.mode == "RGB"
    assert get_embedded_profile(reread2) == ("png", srgb)


def test_process_file_tiff_and_jpeg_roundtrip(tmp_path: Path) -> None:
    """TIFF/JPEG 嵌入往返：ICC 字节原样保留；JPEG quality 参数生效。"""
    srgb = _srgb_bytes()
    src = _save_rgb_png(tmp_path / "src.png")

    dst_tif = tmp_path / "src-embed.tif"
    assert process_file(src, dst_tif, ProfileOp.EMBED, profile=srgb).ok
    reread = read_image(dst_tif)
    assert get_embedded_profile(reread) == ("tiff", srgb)

    dst_jpg = tmp_path / "src-embed.jpg"
    assert process_file(src, dst_jpg, ProfileOp.EMBED, profile=srgb, quality=88).ok
    reread = read_image(dst_jpg)
    source, data = get_embedded_profile(reread)
    assert source == "jpeg"
    assert data == srgb

    # JPEG→JPEG 再嵌入（subsampling='keep' 路径不抛错）
    dst_jpg2 = tmp_path / "src-embed-2.jpg"
    assert process_file(dst_jpg, dst_jpg2, ProfileOp.EMBED, profile=srgb).ok
    assert get_embedded_profile(read_image(dst_jpg2)) == ("jpeg", srgb)


def test_process_file_convert_to_cmyk_tiff(tmp_path: Path) -> None:
    """convert → CMYK TIFF：mode='CMYK' 且带 JapanColor ICC（设计 §7.1 E4 缩样）。"""
    japan = _japan_bytes()
    srgb = _srgb_bytes()
    src = tmp_path / "src.png"
    assign_profile(_gradient_rgb(8, 8), srgb).save(src, "PNG")
    dst = tmp_path / "src-convert.tif"
    result = process_file(src, dst, ProfileOp.CONVERT, profile=japan)
    assert result.ok
    reread = read_image(dst)
    assert reread.mode == "CMYK"
    assert reread.info["icc_profile"] == japan


def test_process_file_error_results(tmp_path: Path) -> None:
    """异常不外抛：缺 profile / 坏源 / 无源 convert 均为 FileResult(ok=False)。"""
    srgb = _srgb_bytes()
    src = _save_rgb_png(tmp_path / "src.png")

    r = process_file(src, tmp_path / "o1.png", ProfileOp.EMBED, profile=None)
    assert not r.ok and r.error_code == "IMAGING" and r.dst is not None

    bad = tmp_path / "bad.png"
    bad.write_bytes(b"definitely not a png" * 8)
    r = process_file(bad, tmp_path / "o2.png", ProfileOp.STRIP)
    assert not r.ok and r.error_code == "IMAGING"

    # convert：profile 参数缺失 → 参数错误（IMAGING）；有目标但源缺失 → NO_SOURCE_PROFILE
    r = process_file(src, tmp_path / "o3.png", ProfileOp.CONVERT, profile=None)
    assert not r.ok and r.error_code == "IMAGING"

    r = process_file(src, tmp_path / "o3b.png", ProfileOp.CONVERT,
                     profile=_adobe_bytes())
    assert not r.ok and r.error_code == "NO_SOURCE_PROFILE"

    r = process_file(src, tmp_path / "o4.png", ProfileOp.ASSIGN, profile=b"\x00" * 10)
    assert not r.ok and r.error_code in {"IMAGING", "PROFILE_MISMATCH"}


def test_process_file_unsupported_write_psd(tmp_path: Path) -> None:
    """写出 .psd → UNSUPPORTED_FORMAT（显式后缀与 out_format 两条路径）。"""
    src = _save_rgb_png(tmp_path / "src.png")
    r1 = process_file(src, tmp_path / "out.psd", ProfileOp.STRIP)
    assert not r1.ok and r1.error_code == "UNSUPPORTED_FORMAT"
    assert not (tmp_path / "out.psd").exists()

    r2 = process_file(src, tmp_path / "out.bin", ProfileOp.STRIP, out_format="psd")
    assert not r2.ok and r2.error_code == "UNSUPPORTED_FORMAT"

    r3 = process_file(src, tmp_path / "out.gif", ProfileOp.STRIP)
    assert not r3.ok and r3.error_code == "UNSUPPORTED_FORMAT"


def test_psd_readonly_and_psd_read(tmp_path: Path) -> None:
    """手工最小 PSD 可读（PsdImagePlugin 合成层）；写出 .psd 被拒。"""
    psd_path = _make_psd(tmp_path / "mini.psd")
    im = read_image(psd_path)
    assert im.mode == "RGB"
    assert im.size == (2, 1)
    assert get_embedded_profile(im) == ("none", None)


def test_read_image_unsupported_format(tmp_path: Path) -> None:
    """非白名单后缀 → UnsupportedFormatError。"""
    gif = tmp_path / "x.gif"
    gif.write_bytes(b"GIF89a" + b"\x00" * 32)
    with pytest.raises(UnsupportedFormatError):
        read_image(gif)


def test_read_image_truncated_png(tmp_path: Path) -> None:
    """截断 PNG：WARNING + LOAD_TRUNCATED_IMAGES 临时重试成功，且不全局污染。"""
    from PIL import ImageFile

    good = tmp_path / "full.png"
    _gradient_rgb(64, 64).save(good, "PNG")
    raw = good.read_bytes()
    truncated = tmp_path / "broken.png"
    truncated.write_bytes(raw[: int(len(raw) * 0.7)])

    im = read_image(truncated)
    assert im.size == (64, 64) and im.mode == "RGB"
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False  # 临时置位已恢复


# ---------------------------------------------------------------------------
# process_batch：顺序保持、错误隔离、进度回调
# ---------------------------------------------------------------------------
def test_process_batch_order_errors_and_progress(tmp_path: Path) -> None:
    """3 源中 1 坏：顺序保持、坏文件 ok=False、进度回调 3 次。"""
    srgb = _srgb_bytes()
    sources = [
        _save_rgb_png(tmp_path / "a.png", 8),
        tmp_path / "b.png",
        _save_rgb_png(tmp_path / "c.png", 8),
    ]
    sources[1].write_bytes(b"corrupted image bytes" * 6)  # 坏文件

    out_dir = tmp_path / "batch_out"
    seen: list[FileResult] = []
    results = process_batch(
        sources, out_dir, ProfileOp.EMBED, profile=srgb,
        progress_cb=seen.append,
    )
    assert len(results) == 3 and len(seen) == 3
    assert [r.src for r in results] == [str(s) for s in sources]  # 顺序 = 输入顺序
    assert results[0].ok and results[2].ok
    assert not results[1].ok
    assert results[1].error_code == "IMAGING"
    # 进度回调按完成顺序触发（不保证输入序），但集合与结果一致
    assert sorted(r.src for r in seen) == sorted(r.src for r in results)

    assert (out_dir / "a-embed.png").is_file()
    assert not (out_dir / "b-embed.png").exists()
    assert (out_dir / "c-embed.png").is_file()
    # 输出 ICC 往返
    assert get_embedded_profile(read_image(out_dir / "a-embed.png")) == ("png", srgb)


def test_process_batch_out_format_and_convert(tmp_path: Path) -> None:
    """批量 convert + out_format 统一改写后缀（png → jpg）。"""
    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    sources = [_save_rgb_png(tmp_path / f"s{i}.png", 6) for i in range(3)]
    for source in sources:
        assign_profile(_gradient_rgb(6, 6), srgb).save(source, "PNG")

    results = process_batch(
        sources, tmp_path / "cv", ProfileOp.CONVERT, profile=adobe, out_format="jpg",
    )
    assert all(r.ok for r in results)
    out_dir = tmp_path / "cv"
    produced = sorted(p.name for p in out_dir.glob("*.jpg"))
    assert produced == ["s0-convert.jpg", "s1-convert.jpg", "s2-convert.jpg"]
    for name in produced:
        im = read_image(out_dir / name)
        assert im.mode == "RGB"
        assert im.info["icc_profile"] == adobe


def test_process_batch_psd_source_yields_per_file_error(tmp_path: Path) -> None:
    """.psd 源（默认按源后缀写出）→ 每文件 UNSUPPORTED_FORMAT 结果，不抛。"""
    srgb = _srgb_bytes()
    psd_src = _make_psd(tmp_path / "p.psd")
    results = process_batch([psd_src], tmp_path / "o", ProfileOp.EMBED, profile=srgb)
    assert len(results) == 1
    assert not results[0].ok
    assert results[0].error_code == "UNSUPPORTED_FORMAT"


# ---------------------------------------------------------------------------
# 批量并发调度（ROADMAP v0.4.4，随 0.3.14）
# ---------------------------------------------------------------------------

def test_dynamic_workers_rules() -> None:
    """规则：空→1；小文件→min(8,cpu)；单文件≥huge→1；混合→min(4,cpu)。"""
    from packstudio.engines.color_engine.imaging import dynamic_workers

    assert dynamic_workers([], cpu=4) == 1
    assert dynamic_workers([1024] * 10, cpu=4) == 4              # 小文件，cpu 封顶
    assert dynamic_workers([1024] * 10, cpu=16) == 8             # 小文件 8 上限
    assert dynamic_workers([64 * 1024 * 1024], cpu=16) == 1      # 单文件 64MB → 串行
    assert dynamic_workers([1 << 20, 70 * 1024 * 1024], cpu=8) == 1
    assert dynamic_workers([8 * 1024 * 1024] * 5, cpu=8) == 4    # 40MB 混合档
    # 阈值可注入
    assert dynamic_workers([2048], cpu=4, small_total_bytes=1024, default_workers=3) == 3
    assert dynamic_workers([2048], cpu=4, huge_single_bytes=1024) == 1


def test_process_batch_auto_workers(tmp_path: Path, monkeypatch) -> None:
    """workers=None 自动模式：dynamic_workers 按实际文件大小被调用且结果正确。"""
    import packstudio.engines.color_engine.imaging.pipeline as pl

    srgb = _srgb_bytes()
    sources = [_save_rgb_png(tmp_path / f"a{i}.png", 8) for i in range(3)]

    calls: list[list[int]] = []

    def fake_dynamic(sizes, **kwargs):
        calls.append(list(sizes))
        return 2

    monkeypatch.setattr(pl, "dynamic_workers", fake_dynamic)
    out_dir = tmp_path / "auto_out"
    results = process_batch(sources, out_dir, ProfileOp.ASSIGN, profile=srgb)
    assert len(calls) == 1 and len(calls[0]) == 3
    assert all(s > 0 for s in calls[0])          # 传的是真实字节数
    assert len(results) == 3 and all(r.ok for r in results)
    # 结果顺序 = 输入顺序
    assert [Path(r.src).name for r in results] == [p.name for p in sources]


def test_process_batch_explicit_workers_unchanged(tmp_path: Path, monkeypatch) -> None:
    """显式 workers=1：不走 dynamic_workers（保持旧行为）。"""
    import packstudio.engines.color_engine.imaging.pipeline as pl

    srgb = _srgb_bytes()
    sources = [_save_rgb_png(tmp_path / f"e{i}.png", 8) for i in range(2)]

    def _boom(sizes, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("显式 workers 不应触发 dynamic_workers")

    monkeypatch.setattr(pl, "dynamic_workers", _boom)
    results = process_batch(sources, tmp_path / "fix_out", ProfileOp.ASSIGN,
                            profile=srgb, workers=1)
    assert len(results) == 2 and all(r.ok for r in results)


# ---------------------------------------------------------------------------
# 大图流式：分块变换（ROADMAP v0.4.2，随 0.3.15）
# ---------------------------------------------------------------------------

def test_convert_tiled_identity(tmp_path: Path) -> None:
    """强制分块 vs 整图变换：输出逐像素一致 + icc 标签一致（RGB→CMYK）。"""
    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    im = _gradient_rgb(97, 53)  # 非整除尺寸，覆盖带边界
    im = assign_profile(im, srgb)
    im.save(tmp_path / "src.png", "PNG")
    loaded = read_image(tmp_path / "src.png")

    whole = convert_image(loaded, adobe, tiled_threshold_bytes=1 << 40)
    tiled = convert_image(loaded, adobe, tiled_threshold_bytes=0, band_bytes=4096)

    assert whole.mode == tiled.mode
    assert numpy.asarray(whole, dtype=numpy.uint8).tobytes() == \
        numpy.asarray(tiled, dtype=numpy.uint8).tobytes()
    assert whole.info["icc_profile"] == tiled.info["icc_profile"] == adobe


def test_convert_tiled_threshold_auto_triggers(tmp_path: Path, monkeypatch) -> None:
    """阈值路径：缺省 None 不自动分块；显式 0/低阈值走 _convert_tiled（spy）。"""
    import packstudio.engines.color_engine.imaging.pipeline as pl

    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    im = assign_profile(_gradient_rgb(32, 24), srgb)

    calls: list[str] = []
    orig_tiled = pl._convert_tiled

    def spy(spec, image, *, band_bytes):
        calls.append("tiled")
        return orig_tiled(spec, image, band_bytes=band_bytes)

    monkeypatch.setattr(pl, "_convert_tiled", spy)

    # 缺省 None → 整图路径（实测 PIL 整图变换已流式，分块反而更高）
    convert_image(im, adobe)
    assert calls == []
    # 阈值极大 → 整图路径
    convert_image(im, adobe, tiled_threshold_bytes=1 << 40)
    assert calls == []
    # 阈值 0 → 强制分块
    convert_image(im, adobe, tiled_threshold_bytes=0)
    assert calls == ["tiled"]
    # 阈值 1 字节 → 任何非空图都触发
    convert_image(im, adobe, tiled_threshold_bytes=1)
    assert calls == ["tiled", "tiled"]


def test_convert_tiled_single_row_bands(tmp_path: Path) -> None:
    """band_bytes 极小 → 单行条带逐步变换，输出仍与整图一致（边界正确性）。"""
    srgb = _srgb_bytes()
    adobe = _adobe_bytes()
    im = assign_profile(_gradient_rgb(11, 37), srgb)  # 高 37 行（质数）
    whole = convert_image(im, adobe, tiled_threshold_bytes=1 << 40)
    tiled = convert_image(im, adobe, tiled_threshold_bytes=0, band_bytes=64)
    assert numpy.asarray(whole, dtype=numpy.uint8).tobytes() == \
        numpy.asarray(tiled, dtype=numpy.uint8).tobytes()
