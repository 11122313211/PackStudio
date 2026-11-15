"""cmm.engine 单元测试（TECH-DESIGN §7.1 cmm/engine 行）。

真实系统 profile 参数化（缺失则 skip，Linux CI 上整体跳过而非失败）。
核心路径不依赖 icc/ 解析层。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy
import pytest
from PIL import Image, ImageCms

from packstudio.engines.color_engine.cmm.engine import (
    RenderingIntent,
    TransformEngine,
    TransformSpec,
    check_out_of_gamut,
    intent_supported,
    open_profile,
    profile_names,
    soft_proof,
    transform_lab,
)
from packstudio.engines.color_engine.errors import (
    CmmError,
    ImagingError,
    IntentUnsupportedError,
    ProfileLoadError,
    ProfileMismatchError,
)

_COLOR_DIR = Path(r"C:\Windows\System32\spool\drivers\color")
SYSTEM_PROFILES = {
    "sRGB": _COLOR_DIR / "sRGB Color Space Profile.icm",
    "AdobeRGB1998": _COLOR_DIR / "AdobeRGB1998.icc",
    "DisplayP3": _COLOR_DIR / "Display P3.icm",
}
JAPAN = _COLOR_DIR / "JapanColor2001Coated.icc"
RSWOP = _COLOR_DIR / "RSWOP.icm"


def _real_profile(key: str) -> Path:
    path = SYSTEM_PROFILES[key]
    if not path.is_file():
        pytest.skip(f"system profile not present: {path}")
    return path


def _cmyk_profile() -> Path:
    for candidate in (JAPAN, RSWOP):
        if candidate.is_file():
            return candidate
    pytest.skip("no CMYK system profile present (JapanColor2001Coated/RSWOP)")


def _gradient_rgb(width: int = 16, height: int = 16) -> Image.Image:
    """合成 RGB 渐变测试图（Pillow 生成）。"""
    xs = numpy.linspace(0, 255, width, dtype=numpy.float64)
    ys = numpy.linspace(0, 255, height, dtype=numpy.float64)
    r = numpy.tile(xs, (height, 1))
    g = numpy.tile(ys.reshape(-1, 1), (1, width))
    b = ((r + g) / 2.0).clip(0, 255)
    arr = numpy.stack([r, g, b], axis=-1).round().astype(numpy.uint8)
    return Image.fromarray(arr, "RGB")


# ---------------------------------------------------------------------------
# 枚举与元信息
# ---------------------------------------------------------------------------
def test_intent_enum_mapping() -> None:
    """4 渲染意图枚举 ↔ ImageCms.Intent 值恒等（0/1/2/3，设计已核实）。"""
    for intent in RenderingIntent:
        pil = ImageCms.Intent(int(intent))
        assert pil.value == intent.value
    assert [i.value for i in RenderingIntent] == [0, 1, 2, 3]
    assert RenderingIntent.RELATIVE_COLORIMETRIC.name == "RELATIVE_COLORIMETRIC"


@pytest.mark.parametrize("key", list(SYSTEM_PROFILES), ids=list(SYSTEM_PROFILES))
def test_open_profile_and_names(key: str) -> None:
    """open_profile 三种来源 + profile_names 三元组。"""
    path = _real_profile(key)
    prof_from_path = open_profile(path)
    prof_from_bytes = open_profile(path.read_bytes())
    prof_from_str = open_profile(str(path))
    name, description, info = profile_names(prof_from_path)
    assert name or description
    assert profile_names(prof_from_bytes) == (name, description, info)
    assert profile_names(prof_from_str) == (name, description, info)
    assert isinstance(info, str) and info


def test_open_profile_errors() -> None:
    """路径不存在 → ProfileLoadError；垃圾字节 → ProfileLoadError（保留 __cause__）。"""
    with pytest.raises(ProfileLoadError) as exc_info:
        open_profile(Path(os.devnull).parent / "no_such_profile_xyz.icc")
    assert exc_info.value.code == "PROFILE_LOAD"
    assert "path" in exc_info.value.detail

    with pytest.raises(ProfileLoadError) as exc_info2:
        open_profile(b"this is not an icc profile at all" * 4)
    assert exc_info2.value.code == "PROFILE_LOAD"
    assert isinstance(exc_info2.value.__cause__, ImageCms.PyCMSError)


def test_intent_supported() -> None:
    """isIntentSupported 包装：sRGB 三个方向均支持相对比色。"""
    srgb = open_profile(_real_profile("sRGB"))
    assert intent_supported(srgb, RenderingIntent.RELATIVE_COLORIMETRIC, "input") is True
    assert intent_supported(srgb, RenderingIntent.RELATIVE_COLORIMETRIC, "output") is True
    assert intent_supported(srgb, RenderingIntent.RELATIVE_COLORIMETRIC, "proof") is True
    with pytest.raises(CmmError):
        intent_supported(srgb, RenderingIntent.PERCEPTUAL, "sideways")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# transform_lab：已知值断言（设计 §7.1 test_white_to_lab）
# ---------------------------------------------------------------------------
def test_white_to_lab() -> None:
    """sRGB 白 (255,255,255) D50 相对意图 → Lab≈(100, 0, 0)（±0.5）。"""
    srgb = _real_profile("sRGB")
    strip = numpy.array([[255, 255, 255], [0, 0, 0]], dtype=numpy.uint8)
    lab = transform_lab(srgb, strip, RenderingIntent.RELATIVE_COLORIMETRIC, True)
    assert lab.shape == (2, 3)
    assert lab.dtype == numpy.float64
    assert abs(lab[0, 0] - 100.0) <= 0.5
    assert abs(lab[0, 1]) <= 0.5
    assert abs(lab[0, 2]) <= 0.5
    assert abs(lab[1, 0]) <= 0.5  # 黑点
    assert abs(lab[1, 1]) <= 0.5
    assert abs(lab[1, 2]) <= 0.5


def test_transform_lab_keyword_and_positional_forms() -> None:
    """跨模块契约：viz 位置参数形态与关键字形态结果一致。"""
    srgb = _real_profile("sRGB")
    strip = numpy.array([[255, 255, 255], [200, 30, 40]], dtype=numpy.uint8)
    lab_positional = transform_lab(srgb, strip, RenderingIntent.RELATIVE_COLORIMETRIC, True)
    lab_keyword = transform_lab(
        srgb, strip, intent=RenderingIntent.RELATIVE_COLORIMETRIC, bpc=True
    )
    numpy.testing.assert_array_equal(lab_positional, lab_keyword)
    # 默认参数形态（intent/bpc 均可省略）
    lab_default = transform_lab(srgb, strip)
    numpy.testing.assert_array_equal(lab_positional, lab_default)


@pytest.mark.parametrize("key", ["sRGB", "AdobeRGB1998", "DisplayP3"])
def test_transform_lab_profiles_ranges(key: str) -> None:
    """多 profile × 多色块：输出范围合法（L∈[0,100]，a/b∈[-128,127]）。"""
    profile = _real_profile(key)
    corners = numpy.array(
        [
            [0, 0, 0], [255, 0, 0], [0, 255, 0], [0, 0, 255],
            [255, 255, 0], [255, 0, 255], [0, 255, 255], [255, 255, 255],
            [128, 128, 128], [200, 30, 40],
        ],
        dtype=numpy.uint8,
    )
    lab = transform_lab(profile, corners)
    assert lab.shape == (10, 3)
    assert numpy.all((lab[:, 0] >= -0.01) & (lab[:, 0] <= 100.01))
    assert numpy.all((lab[:, 1] >= -128.0) & (lab[:, 1] <= 127.0))
    assert numpy.all((lab[:, 2] >= -128.0) & (lab[:, 2] <= 127.0))


def test_transform_lab_cmyk_strip() -> None:
    """CMYK 数值条带 (n,4)：JapanColor 纸白 → L≈100、a/b≈0。"""
    cmyk = _cmyk_profile()
    strip = numpy.array(
        [[0, 0, 0, 0], [0, 0, 0, 255], [255, 0, 0, 0]], dtype=numpy.uint8
    )
    lab = transform_lab(cmyk, strip, RenderingIntent.RELATIVE_COLORIMETRIC, True)
    assert lab.shape == (3, 3)
    assert abs(lab[0, 0] - 100.0) <= 0.5  # 纸白
    assert abs(lab[0, 1]) <= 0.5 and abs(lab[0, 2]) <= 0.5
    assert lab[1, 0] < 40.0  # 全黑
    assert lab[2, 1] < 0.0 and lab[2, 2] < 0.0  # 青色 a/b 为负


def test_transform_lab_image_input_and_bad_shape() -> None:
    """Image 输入与 (n,3) 数组结果一致；形状不符抛 ImagingError。"""
    srgb = _real_profile("sRGB")
    strip = numpy.array(
        [[[255, 255, 255], [0, 0, 0], [10, 200, 30]]], dtype=numpy.uint8
    )  # (1,3,3) → 1×3 RGB 图
    im = Image.fromarray(strip, "RGB")
    lab_from_image = transform_lab(srgb, im)
    lab_from_array = transform_lab(srgb, strip.reshape(3, 3))
    numpy.testing.assert_allclose(lab_from_image, lab_from_array, atol=0.0)

    with pytest.raises(ImagingError):
        transform_lab(srgb, numpy.zeros((4, 2), dtype=numpy.uint8))
    with pytest.raises(ImagingError):
        transform_lab(srgb, numpy.zeros((4, 4), dtype=numpy.uint8))  # RGB profile 配 4 通道


# ---------------------------------------------------------------------------
# TransformEngine：缓存、构建、应用
# ---------------------------------------------------------------------------
def test_transform_cache(tmp_path: Path) -> None:
    """同 spec 两次 build → 同一对象；mtime 变 → 新对象；恢复 mtime → 命中旧缓存。"""
    source = _real_profile("sRGB")
    local = tmp_path / "srgb.icm"
    shutil.copyfile(source, local)

    engine = TransformEngine(max_cache=8)
    spec_a = TransformSpec(
        src_profile=str(local), dst_profile=str(local),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    )
    t1 = engine.build_transform(spec_a)
    t2 = engine.build_transform(spec_a)
    assert t1 is t2

    # mtime 变化 → 键失效 → 重建
    st = local.stat()
    os.utime(local, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    t3 = engine.build_transform(spec_a)
    assert t3 is not t1

    # size 变化 → 键失效（内容不同同样失效）
    data = local.read_bytes()
    local.write_bytes(data + b"\x00" * 4)
    t4 = engine.build_transform(spec_a)
    assert t4 is not t1 and t4 is not t3
    info = engine.cache_info()
    assert info["misses"] == 3 and info["hits"] == 1 and info["entries"] == 3


def test_transform_cache_eviction(tmp_path: Path) -> None:
    """max_cache=2：构建第 3 个 spec 后最早的被逐出（重建得到新对象）。"""
    srgb = _real_profile("sRGB")
    engine = TransformEngine(max_cache=2)
    specs = [
        TransformSpec(src_profile=str(srgb), dst_profile=str(srgb),
                      intent=RenderingIntent.PERCEPTUAL),
        TransformSpec(src_profile=str(srgb), dst_profile=str(srgb),
                      intent=RenderingIntent.RELATIVE_COLORIMETRIC),
        TransformSpec(src_profile=str(srgb), dst_profile=str(srgb),
                      intent=RenderingIntent.SATURATION),
    ]
    first = engine.build_transform(specs[0])
    engine.build_transform(specs[1])
    engine.build_transform(specs[2])
    assert engine.build_transform(specs[0]) is not first  # 已被逐出重建
    assert engine.cache_info()["entries"] == 2


def test_build_transform_bytes_and_errors() -> None:
    """bytes profile 构建 OK；垃圾/不存在 → ProfileLoadError；cmm 错误保留 __cause__。"""
    srgb = _real_profile("sRGB")
    srgb_bytes = srgb.read_bytes()
    engine = TransformEngine()
    spec = TransformSpec(src_profile=srgb_bytes, dst_profile=srgb_bytes,
                         intent=RenderingIntent.RELATIVE_COLORIMETRIC)
    transform = engine.build_transform(spec)
    assert transform is not None

    bad = TransformSpec(src_profile=b"garbage" * 8, dst_profile=b"garbage" * 8)
    with pytest.raises(ProfileLoadError):
        engine.build_transform(bad)

    spec_missing = TransformSpec(src_profile=str(srgb.parent / "missing.icc"),
                                 dst_profile=str(srgb))
    with pytest.raises(ProfileLoadError):
        engine.build_transform(spec_missing)

    engine.clear_cache()
    assert engine.cache_info()["entries"] == 0


def test_lcms_flags_composition() -> None:
    """flags 组合：NOCACHE 恒在；BPC/SOFTPROOFING/GAMUTCHECK 按字段置位。"""
    base = TransformSpec(src_profile=b"x" * 8, dst_profile=b"y" * 8)
    assert int(base.lcms_flags()) == int(ImageCms.Flags.NOCACHE)

    with_bpc = TransformSpec(src_profile=b"x" * 8, dst_profile=b"y" * 8, bpc=True)
    assert int(with_bpc.lcms_flags()) & int(ImageCms.Flags.BLACKPOINTCOMPENSATION)
    assert int(with_bpc.lcms_flags()) & int(ImageCms.Flags.NOCACHE)

    with_proof = TransformSpec(src_profile=b"x" * 8, dst_profile=b"y" * 8,
                               proof_profile=b"z" * 8, gamut_check=True)
    flags = int(with_proof.lcms_flags())
    assert flags & int(ImageCms.Flags.SOFTPROOFING)
    assert flags & int(ImageCms.Flags.GAMUTCHECK)

    # 无 proof 时 GAMUTCHECK 不生效（lcms 约束）
    gamut_no_proof = TransformSpec(src_profile=b"x" * 8, dst_profile=b"y" * 8,
                                   gamut_check=True)
    assert not int(gamut_no_proof.lcms_flags()) & int(ImageCms.Flags.GAMUTCHECK)


def test_apply_rgba_alpha_preserved() -> None:
    """RGBA 输入：alpha 通道在变换后原样拼回，RGB 部分被变换。"""
    srgb = _real_profile("sRGB")
    adobe = _real_profile("AdobeRGB1998")
    im = _gradient_rgb(8, 8).convert("RGBA")
    alpha = numpy.asarray(im)[:, :, 3].copy()
    spec = TransformSpec(src_profile=str(srgb), dst_profile=str(adobe),
                         intent=RenderingIntent.RELATIVE_COLORIMETRIC)
    engine = TransformEngine()
    out = engine.apply(spec, im)
    assert out.mode == "RGBA"
    assert out.size == im.size
    numpy.testing.assert_array_equal(numpy.asarray(out)[:, :, 3], alpha)
    # 像素确实被变换过（sRGB→AdobeRGB 色域更大，饱和色数值会移动）
    assert not numpy.array_equal(numpy.asarray(out)[:, :, :3], numpy.asarray(im)[:, :, :3])


def test_apply_mode_mismatch() -> None:
    """CMYK 图像配 RGB profile spec → ProfileMismatchError（ImagingError 子类，code 稳定）。"""
    srgb = _real_profile("sRGB")
    engine = TransformEngine()
    spec = TransformSpec(src_profile=str(srgb), dst_profile=str(srgb))
    cmyk_im = Image.fromarray(numpy.zeros((2, 2, 4), dtype=numpy.uint8), "CMYK")
    with pytest.raises(ProfileMismatchError) as exc_info:
        engine.apply(spec, cmyk_im)
    assert exc_info.value.code == "PROFILE_MISMATCH"
    assert isinstance(exc_info.value, ImagingError)  # 契约兼容：捕获 ImagingError 仍有效
    assert exc_info.value.detail == {"image_mode": "CMYK", "input_mode": "RGB"}


def test_build_intent_unsupported_error_type() -> None:
    """IntentUnsupportedError 是 CmmError 子类且 code 稳定。"""
    assert issubclass(IntentUnsupportedError, CmmError)
    assert IntentUnsupportedError.code == "INTENT_UNSUPPORTED"


# ---------------------------------------------------------------------------
# BPC 效果（设计 §7.1 test_bpc_effect）
# ---------------------------------------------------------------------------
def test_bpc_effect() -> None:
    """深色 CMYK→RGB：bpc on/off 输出存在差异（非逐像素相等）。"""
    cmyk = _cmyk_profile()
    srgb = _real_profile("sRGB")
    dark_patches = numpy.array(
        [
            [40, 30, 30, 240],
            [60, 50, 45, 235],
            [10, 10, 10, 255],
            [20, 20, 20, 200],
        ],
        dtype=numpy.uint8,
    )
    im = Image.fromarray(dark_patches.reshape(1, 4, 4), "CMYK")
    spec_off = TransformSpec(src_profile=str(cmyk), dst_profile=str(srgb),
                             intent=RenderingIntent.RELATIVE_COLORIMETRIC, bpc=False)
    spec_on = TransformSpec(src_profile=str(cmyk), dst_profile=str(srgb),
                            intent=RenderingIntent.RELATIVE_COLORIMETRIC, bpc=True)
    engine = TransformEngine()
    out_off = numpy.asarray(engine.apply(spec_off, im)).reshape(-1, 3).astype(numpy.int16)
    out_on = numpy.asarray(engine.apply(spec_on, im)).reshape(-1, 3).astype(numpy.int16)
    assert not numpy.array_equal(out_off, out_on)


# ---------------------------------------------------------------------------
# 软打样与色域检查
# ---------------------------------------------------------------------------
def test_soft_proof_identity_and_stats() -> None:
    """device=display=sRGB：输出为 RGB 且 out_of_gamut_ratio == 0。"""
    srgb = _real_profile("sRGB")
    im = _gradient_rgb(12, 9)
    out, stats = soft_proof(im, str(srgb), str(srgb),
                            intent=RenderingIntent.RELATIVE_COLORIMETRIC, bpc=True)
    assert out.mode == "RGB"
    assert out.size == im.size
    assert stats["out_of_gamut_pixel_ratio"] == 0.0
    assert stats["out_of_gamut_pixels"] == 0
    assert stats["width"] == 12 and stats["height"] == 9
    assert stats["intent"] == "RELATIVE_COLORIMETRIC"
    assert stats["bpc"] is True


def test_soft_proof_into_larger_gamut() -> None:
    """sRGB→AdobeRGB：所有 sRGB 色均在 AdobeRGB 域内，越界率 0。"""
    srgb = _real_profile("sRGB")
    adobe = _real_profile("AdobeRGB1998")
    im = _gradient_rgb(10, 10)
    out, stats = soft_proof(im, str(srgb), str(adobe))
    assert out.mode == "RGB"
    assert stats["out_of_gamut_pixel_ratio"] == 0.0


def test_check_out_of_gamut_adobe_to_srgb() -> None:
    """AdobeRGB 饱和色 → sRGB 显示：部分越界；掩图/计数/占比一致。"""
    adobe = _real_profile("AdobeRGB1998")
    srgb = _real_profile("sRGB")
    im = _gradient_rgb(16, 16)  # 渐变含饱和红/绿区域
    mask, count, ratio = check_out_of_gamut(im, str(adobe), str(srgb),
                                            intent=RenderingIntent.RELATIVE_COLORIMETRIC)
    total = im.width * im.height
    assert mask.mode == "L"
    assert mask.size == im.size
    mask_arr = numpy.asarray(mask)
    assert 0 < count < total
    assert int((mask_arr == 255).sum()) == count
    assert abs(ratio - count / total) < 1e-12
    # 白/灰区域不应被标越界
    white_gray = Image.fromarray(numpy.array(
        [[[255, 255, 255], [128, 128, 128], [64, 64, 64]]], dtype=numpy.uint8), "RGB")
    mask2, count2, _ = check_out_of_gamut(white_gray, str(adobe), str(srgb))
    assert count2 == 0
    assert (numpy.asarray(mask2) == 0).all()


def test_check_out_of_gamut_identity() -> None:
    """device=display：无越界。"""
    srgb = _real_profile("sRGB")
    im = _gradient_rgb(8, 8)
    _mask, count, ratio = check_out_of_gamut(im, str(srgb), str(srgb))
    assert count == 0 and ratio == 0.0


# ---------------------------------------------------------------------------
# 变换缓存可观测性（ROADMAP v0.4.1，随 0.3.10）
# ---------------------------------------------------------------------------

def test_transform_cache_info_accessor() -> None:
    """transform_cache_info()：共享引擎统计——重复调用命中数递增、hit_rate∈[0,1]。"""
    import packstudio.engines.color_engine.cmm as cmm_pkg

    im = Image.new("CMYK", (16, 12), (30, 144, 200, 10))
    printer = JAPAN
    display = _real_profile("sRGB")

    before = cmm_pkg.transform_cache_info()
    soft_proof(im, str(printer), str(display))
    soft_proof(im, str(printer), str(display))
    after = cmm_pkg.transform_cache_info()

    assert set(after) == {"hits", "misses", "entries", "max_cache", "hit_rate"}
    assert after["hits"] > before["hits"]          # 第二次调用命中缓存
    assert 0.0 <= after["hit_rate"] <= 1.0
    assert after["entries"] <= after["max_cache"]


# ---------------------------------------------------------------------------
# 软打样并行分带（ROADMAP v0.4.9）
# ---------------------------------------------------------------------------
def test_auto_proof_workers_rules() -> None:
    """auto 三档：小图 1 / 大图 min(PROOF_MAX_WORKERS, cpu)与注入 cpu 生效。"""
    import os

    from packstudio.engines.color_engine.cmm.engine import PROOF_MAX_WORKERS, auto_proof_workers

    assert auto_proof_workers(1) == 1
    assert auto_proof_workers(23 * 1024 * 1024) == 1          # 阈值 24MB 之下
    big = 64 * 1024 * 1024
    assert auto_proof_workers(big) == min(PROOF_MAX_WORKERS, os.cpu_count() or 1)
    assert auto_proof_workers(big, cpu=2) == 2
    assert auto_proof_workers(big, cpu=8) == PROOF_MAX_WORKERS
    assert auto_proof_workers(0, threshold=0) == min(PROOF_MAX_WORKERS, os.cpu_count() or 1)
    # 显式 threshold 注入：低于阈值回归串行
    assert auto_proof_workers(big, threshold=128 * 1024 * 1024) == 1


def _cmyk_noise_image(w: int, h: int) -> Image.Image:
    """合成 CMYK 噪声+渐变图（numpy，通道独立；含 0/255 极值覆盖）。"""
    rng = numpy.random.default_rng(20261108)
    ys, xs = numpy.mgrid[0:h, 0:w]
    base = numpy.stack([
        (xs * 255 / max(1, w - 1)),
        (ys * 255 / max(1, h - 1)),
        ((xs + ys) * 255 / max(1, w + h - 2)),
        numpy.full((h, w), 128.0),
    ], axis=-1)
    noise = rng.integers(-30, 31, size=(h, w, 4))
    arr = (base + noise).clip(0, 255).astype(numpy.uint8)
    arr[0, 0] = (0, 0, 0, 0)
    arr[h - 1, w - 1] = (255, 255, 255, 255)
    return Image.fromarray(arr, "CMYK")


def test_soft_proof_parallel_identity() -> None:
    """恒等锁定：workers=4 分带并行输出与串行整图变换逐像素一致（图+OOG 统计）。"""
    printer = _cmyk_profile()
    srgb = _real_profile("sRGB")
    im = _cmyk_noise_image(160, 200)  # 128KB 解码，显式 workers 才走并行
    out_serial, stats_serial = soft_proof(im, str(printer), str(srgb), workers=1)
    out_parallel, stats_parallel = soft_proof(im, str(printer), str(srgb), workers=4)
    assert out_serial.mode == out_parallel.mode == "RGB"
    assert out_serial.tobytes() == out_parallel.tobytes()
    assert stats_parallel["workers"] == 4
    assert stats_serial["workers"] == 1
    assert stats_parallel["out_of_gamut_pixels"] == stats_serial["out_of_gamut_pixels"]
    assert stats_parallel["out_of_gamut_pixel_ratio"] == stats_serial["out_of_gamut_pixel_ratio"]


def test_soft_proof_auto_workers_small_image_serial() -> None:
    """小图（< 24MB 解码）workers=None → 自动选 1（串行路径）。"""
    printer = _cmyk_profile()
    srgb = _real_profile("sRGB")
    im = _cmyk_noise_image(64, 48)
    _out, stats = soft_proof(im, str(printer), str(srgb))
    assert stats["workers"] == 1


def test_check_out_of_gamut_parallel_identity() -> None:
    """OOG 诊断并行恒等：workers=4 掩图/计数与串行一致。"""
    from packstudio.engines.color_engine.cmm.engine import check_out_of_gamut

    printer = _cmyk_profile()
    srgb = _real_profile("sRGB")
    im = _cmyk_noise_image(96, 80)
    m1, c1, r1 = check_out_of_gamut(im, str(printer), str(srgb), workers=1)
    m4, c4, r4 = check_out_of_gamut(im, str(printer), str(srgb), workers=4)
    assert c1 == c4 and r1 == r4
    assert m1.tobytes() == m4.tobytes()
