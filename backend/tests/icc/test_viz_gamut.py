"""viz.gamut 模块测试（TECH-DESIGN §7.1 viz 行）。

策略：
- 变换依赖 cmm 引擎（并行开发中），全部采样/体积/切片逻辑测试注入
  确定性 fake 变换（TransformFn），不依赖 cmm；
- 一条真实渲染冒烟（matplotlib Agg → PNG 落盘，断言文件存在且 >0 字节、
  可被 PIL 打开）；
- 默认变换（惰性导入 cmm）仅在 cmm 就绪时执行集成冒烟，未就绪自动 skip。
- matplotlib 强制 Agg 后端，无任何 GUI 依赖。
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import pytest

import numpy  # noqa: E402
from PIL import Image  # noqa: E402

from packstudio.engines.color_engine.viz import (GamutSample, VolumeSeries, WavelengthMark,  # noqa: E402
                             dominant_wavelength, estimate_gamut_volume,
                             gamut_overlap, plot_ab_slice,
                             plot_ab_slice_animation, plot_chromaticity,
                             plot_gamut_overlap, plot_volume_trend,
                             sample_cmyk_gamut, sample_rgb_gamut)
from packstudio.engines.color_engine.viz.gamut import RenderingIntent, VizError  # noqa: E402

matplotlib.use("Agg")  # 先于被测模块亦保证无 GUI

# 系统 profile（不存在则相关用例 skip，保证 Linux CI 可整体跳过）
_SYSTEM_DIR = Path(r"C:\Windows\System32\spool\drivers\color")
SRGB_PATH = _SYSTEM_DIR / "sRGB Color Space Profile.icm"
CMYK_PATH = _SYSTEM_DIR / "JapanColor2001Coated.icc"

requires_srgb = pytest.mark.skipif(not SRGB_PATH.exists(),
                                   reason="系统 sRGB profile 不存在")
requires_cmyk = pytest.mark.skipif(not CMYK_PATH.exists(),
                                   reason="系统 CMYK profile 不存在")


def _cmm_available() -> bool:
    try:
        import packstudio.engines.color_engine.cmm.engine  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# 确定性 fake 变换（TransformFn 契约：profile, values(n,k)∈[0,1], intent, bpc
# → Lab (n,3) float64）。线性满秩映射保证点云 3 维非退化、体积 > 0。
# ---------------------------------------------------------------------------
def fake_transform_rgb(profile: bytes | str, values: numpy.ndarray,
                       intent: object, bpc: bool) -> numpy.ndarray:
    calls = getattr(fake_transform_rgb, "calls", None)
    if calls is not None:
        calls.append({"profile": profile, "n": int(values.shape[0]),
                      "k": int(values.shape[1]), "intent": int(intent),
                      "bpc": bool(bpc)})
    r, g, b = values[:, 0], values[:, 1], values[:, 2]
    lightness = 100.0 * (0.3 * r + 0.6 * g + 0.1 * b)
    a_star = 125.0 * (r - g)
    b_star = 125.0 * (0.5 * (r + g) - b)
    return numpy.stack([lightness, a_star, b_star], axis=-1)


fake_transform_rgb.calls = []  # type: ignore[attr-defined]


def fake_transform_cmyk(profile: bytes | str, values: numpy.ndarray,
                        intent: object, bpc: bool) -> numpy.ndarray:
    calls = getattr(fake_transform_cmyk, "calls", None)
    if calls is not None:
        calls.append({"profile": profile, "n": int(values.shape[0]),
                      "k": int(values.shape[1])})
    c, m, y, k = values[:, 0], values[:, 1], values[:, 2], values[:, 3]
    lightness = 100.0 * (1.0 - 0.25 * c - 0.2 * m - 0.05 * y) * (1.0 - 0.7 * k)
    a_star = 90.0 * (m - c)
    b_star = 90.0 * (y - 0.5 * m)
    return numpy.stack([lightness, a_star, b_star], axis=-1)


fake_transform_cmyk.calls = []  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_fake_calls() -> None:
    fake_transform_rgb.calls.clear()  # type: ignore[attr-defined]
    fake_transform_cmyk.calls.clear()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 采样
# ---------------------------------------------------------------------------
class TestSampling:
    @requires_srgb
    def test_rgb_cubic_sampling(self) -> None:
        """density³ 立方采样：点数、形状、注入参数透传、体积>0（§4.11）。"""
        sample = sample_rgb_gamut(str(SRGB_PATH), density=5,
                                  transform=fake_transform_rgb)
        assert sample.lab.shape == (125, 3)
        assert sample.lab.dtype == numpy.float64
        assert numpy.isfinite(sample.lab).all()
        assert sample.volume > 0.0
        assert sample.vertices > 0
        assert sample.profile_path.endswith("sRGB Color Space Profile.icm")
        assert sample.description  # 描述可回退路径名，但必须非空
        call = fake_transform_rgb.calls[0]  # type: ignore[attr-defined]
        assert call["n"] == 125 and call["k"] == 3
        assert call["intent"] == 1  # 默认 RELATIVE_COLORIMETRIC
        assert call["bpc"] is True  # 默认 bpc

    @requires_srgb
    def test_rgb_intent_bpc_passthrough(self) -> None:
        sample = sample_rgb_gamut(str(SRGB_PATH), density=2, bpc=False,
                                  intent=RenderingIntent.PERCEPTUAL,
                                  transform=fake_transform_rgb)
        assert sample.lab.shape == (8, 3)
        call = fake_transform_rgb.calls[0]  # type: ignore[attr-defined]
        assert call["intent"] == 0 and call["bpc"] is False

    @requires_cmyk
    def test_cmyk_grid_sampling(self) -> None:
        """grid⁴ 网格采样：4 通道输入、点数与体积（§4.11）。"""
        sample = sample_cmyk_gamut(str(CMYK_PATH), grid=4,
                                   transform=fake_transform_cmyk)
        assert sample.lab.shape == (256, 3)
        assert sample.volume > 0.0
        assert sample.vertices > 0
        call = fake_transform_cmyk.calls[0]  # type: ignore[attr-defined]
        assert call["k"] == 4

    @requires_srgb
    def test_space_mismatch_cmyk_function(self) -> None:
        """RGB profile 传入 sample_cmyk_gamut → VizError code=SPACE_MISMATCH。"""
        with pytest.raises(VizError) as excinfo:
            sample_cmyk_gamut(str(SRGB_PATH), transform=fake_transform_cmyk)
        assert excinfo.value.code == "SPACE_MISMATCH"

    @requires_cmyk
    def test_space_mismatch_rgb_function(self) -> None:
        """对称守卫：CMYK profile 传入 sample_rgb_gamut → SPACE_MISMATCH。"""
        with pytest.raises(VizError) as excinfo:
            sample_rgb_gamut(str(CMYK_PATH), transform=fake_transform_rgb)
        assert excinfo.value.code == "SPACE_MISMATCH"

    @requires_srgb
    def test_parameter_guards(self) -> None:
        """density/grid 越界 → VizError（grid>8 提示代价，§4.11）。"""
        with pytest.raises(VizError):
            sample_rgb_gamut(str(SRGB_PATH), density=1, transform=fake_transform_rgb)
        with pytest.raises(VizError):
            sample_cmyk_gamut(str(CMYK_PATH), grid=9, transform=fake_transform_cmyk)
        with pytest.raises(VizError):
            sample_cmyk_gamut(str(CMYK_PATH), grid=1, transform=fake_transform_cmyk)

    def test_bytes_profile_input(self) -> None:
        """bytes profile：peek（头 16-19 字节 'RGB '）与占位回退，不抛异常。"""
        blob = b"\x00" * 16 + b"RGB " + b"\x00" * 40
        sample = sample_rgb_gamut(blob, density=2, transform=fake_transform_rgb)
        assert sample.profile_path == "<bytes>"
        assert sample.description == "<memory profile>"
        assert sample.lab.shape == (8, 3)


# ---------------------------------------------------------------------------
# 体积估算
# ---------------------------------------------------------------------------
class TestVolume:
    def test_unit_cube_volume(self) -> None:
        """单位立方 8 顶点 → 体积 1.0（已知值）。"""
        cube = numpy.array([[x, y, z] for x in (0.0, 1.0) for y in (0.0, 1.0)
                            for z in (0.0, 1.0)])
        assert estimate_gamut_volume(cube) == pytest.approx(1.0)

    def test_degenerate_coplanar_is_zero(self) -> None:
        """共面点云 → 0.0（log WARNING，不抛，§4.11）。"""
        flat = numpy.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                            [1.0, 1.0, 0.0], [0.5, 0.5, 0.0], [0.2, 0.3, 0.0]])
        assert estimate_gamut_volume(flat) == 0.0

    def test_too_few_points_is_zero(self) -> None:
        """点数 < 5 → 0.0。"""
        assert estimate_gamut_volume(numpy.zeros((4, 3))) == 0.0
        assert estimate_gamut_volume(numpy.zeros((0, 3))) == 0.0

    def test_nan_rows_dropped(self) -> None:
        """非有限行被过滤，不污染体积。"""
        cube = numpy.array([[x, y, z] for x in (0.0, 1.0) for y in (0.0, 1.0)
                            for z in (0.0, 1.0)])
        polluted = numpy.vstack([cube,
                                 [[numpy.nan, 0.0, 0.0], [0.0, numpy.inf, 1.0]]])
        assert estimate_gamut_volume(polluted) == pytest.approx(1.0)

    @requires_srgb
    def test_gamut_sample_frozen(self) -> None:
        """GamutSample frozen+slots 契约（§2.4）。"""
        sample = sample_rgb_gamut(str(SRGB_PATH), density=2,
                                  transform=fake_transform_rgb)
        with pytest.raises(Exception):  # noqa: B017 - FrozenInstanceError
            sample.volume = 1.0  # type: ignore[misc]
        with pytest.raises(Exception):  # noqa: B017 - slots 拒绝新属性
            sample.unknow_field = 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 绘图（真实渲染冒烟：Agg → PNG 落盘）
# ---------------------------------------------------------------------------
def _make_sample(description: str, seed: int) -> GamutSample:
    rng = numpy.random.default_rng(seed)
    lab = rng.uniform(low=[10.0, -60.0, -60.0], high=[95.0, 60.0, 60.0],
                      size=(400, 3))
    return GamutSample(profile_path=f"<synthetic-{seed}>", description=description,
                       lab=lab, volume=0.0, vertices=0)


class TestPlots:
    def test_chromaticity_png_smoke(self, tmp_path: Path) -> None:
        """色度图冒烟：文件存在、>0 字节、后缀规范、PIL 可打开（§7.1）。"""
        samples = [_make_sample("Fake sRGB", 1), _make_sample("Fake AdobeRGB", 2)]
        out = plot_chromaticity(samples, tmp_path / "xy.png", dpi=72)
        assert out == tmp_path / "xy.png"
        assert out.exists() and out.stat().st_size > 0
        with Image.open(out) as image:
            assert image.format == "PNG"
            image.verify()

    def test_chromaticity_suffix_enforced(self, tmp_path: Path) -> None:
        """非 .png 后缀自动替换为 .png（§4.11 返回值约定）。"""
        out = plot_chromaticity([_make_sample("A", 3)], tmp_path / "no_suffix",
                                dpi=72, show_spectral_locus=False)
        assert out.name == "no_suffix.png"
        assert out.exists() and out.stat().st_size > 0

    def test_chromaticity_svg_smoke(self, tmp_path: Path) -> None:
        """SVG 矢量导出（v0.5.3）：内容为 XML 文本、含 <svg 根元素。"""
        samples = [_make_sample("Fake sRGB", 6), _make_sample("Fake AdobeRGB", 7)]
        out = plot_chromaticity(samples, tmp_path / "xy.svg", dpi=72,
                                show_spectral_locus=False)
        assert out == tmp_path / "xy.svg"
        assert out.exists() and out.stat().st_size > 0
        raw = out.read_bytes()
        assert raw[:5] == b"<?xml"
        assert b"<svg" in raw[:2000]
        assert not raw.startswith(b"\x89PNG")

    def test_svg_format_forces_suffix(self, tmp_path: Path) -> None:
        """format="svg" 强制改写 .png 后缀为 .svg（两函数一致）。"""
        out = plot_chromaticity([_make_sample("A", 8)], tmp_path / "kept.png",
                                dpi=72, format="svg", show_spectral_locus=False)
        assert out.name == "kept.svg"
        assert out.read_bytes()[:5] == b"<?xml"
        out2 = plot_ab_slice([_make_sample("B", 9)], tmp_path / "ab.png",
                             band=25.0, dpi=72, format="svg")
        assert out2.name == "ab.svg"
        assert out2.read_bytes()[:5] == b"<?xml"

    def test_bad_format_raises(self, tmp_path: Path) -> None:
        """format 非 png/svg → VizError（白名单）。"""
        with pytest.raises(VizError):
            plot_chromaticity([_make_sample("A", 10)], tmp_path / "x.jpg",
                              dpi=72, format="jpg")
        with pytest.raises(VizError):
            plot_ab_slice([_make_sample("A", 11)], tmp_path / "x.webp",
                          band=25.0, format="webp")

    def test_ab_slice_png_smoke(self, tmp_path: Path) -> None:
        """a-b 切片冒烟：文件存在、>0 字节、PIL 可打开。"""
        samples = [_make_sample("Fake JapanColor", 4)]
        out = plot_ab_slice(samples, tmp_path / "ab.png", lightness=50.0,
                            band=25.0, dpi=72)
        assert out.exists() and out.stat().st_size > 0
        with Image.open(out) as image:
            assert image.format == "PNG"
            image.verify()

    def test_ab_slice_band_exclusion(self, tmp_path: Path,
                                      caplog: pytest.LogCaptureFixture) -> None:
        """带内无点的样本 log WARNING 后跳过，图仍由其他样本产出。"""
        in_band = numpy.array([[50.0, x, y] for x in (-40.0, 0.0, 40.0)
                               for y in (-40.0, 0.0, 40.0)])
        far = numpy.array([[5.0, 0.0, 0.0], [6.0, 10.0, 10.0]])  # L*≈5 不在带内
        near = GamutSample(profile_path="p1", description="InBand", lab=in_band,
                           volume=0.0, vertices=0)
        away = GamutSample(profile_path="p2", description="OutOfBand", lab=far,
                           volume=0.0, vertices=0)
        out = plot_ab_slice([near, away], tmp_path / "slice.png", lightness=50.0,
                            band=5.0, dpi=72)
        assert out.exists() and out.stat().st_size > 0
        assert any("OutOfBand" in message and "无样本点" in message
                   for message in caplog.messages)

    def test_chromaticity_empty_profiles_raises(self, tmp_path: Path) -> None:
        """空样本列表 → VizError。"""
        with pytest.raises(VizError):
            plot_chromaticity([], tmp_path / "empty.png")

    def test_ab_slice_bad_band_raises(self, tmp_path: Path) -> None:
        """band ≤ 0 → VizError。"""
        with pytest.raises(VizError):
            plot_ab_slice([_make_sample("A", 5)], tmp_path / "x.png", band=0.0)

    def test_backend_is_agg(self) -> None:
        """模块级 matplotlib.use('Agg')：无 GUI 依赖（§4.11 硬约束）。"""
        matplotlib.use("Agg", force=True)
        import packstudio.engines.color_engine.viz.gamut  # noqa: F401  重新触达模块
        assert matplotlib.get_backend().lower() == "agg"


# ---------------------------------------------------------------------------
# 色域交集/并集（ROADMAP v0.5.2）：凸包半空间交精确算法，立方体解析解对照
# ---------------------------------------------------------------------------
class TestOverlap:
    @staticmethod
    def _cube(lo: float) -> GamutSample:
        """单位立方体 [lo, lo+1]³（8 顶点），凸包体积恒为 1。"""
        lab = numpy.array([[x, y, z] for x in (lo, lo + 1.0)
                           for y in (lo, lo + 1.0)
                           for z in (lo, lo + 1.0)])
        return GamutSample(profile_path="c", description=f"cube{lo}", lab=lab,
                           volume=1.0, vertices=8)

    def test_overlap_half_shifted_exact(self) -> None:
        """[0,1]³ × [0.5,1.5]³ 交集体积 = 0.125（解析解，精确算法验证）。"""
        a, b = self._cube(0.0), self._cube(0.5)
        ov = gamut_overlap(a, b)
        assert ov.intersection_volume == pytest.approx(0.125, abs=1e-9)
        assert ov.volume_a == pytest.approx(1.0)
        assert ov.volume_b == pytest.approx(1.0)
        # 并集 = 合并点云凸包体积，恒 ≥ max(双方) 且 ≥ 交集
        assert ov.union_volume >= 1.0
        assert ov.union_volume >= ov.intersection_volume
        assert 0.0 < ov.overlap_ratio < 1.0
        assert ov.profile_a == "c" and ov.profile_b == "c"

    def test_overlap_disjoint_zero(self) -> None:
        """不相交（间隔 10）→ 交集 0.0、重合率 0.0。"""
        ov = gamut_overlap(self._cube(0.0), self._cube(10.0))
        assert ov.intersection_volume == 0.0
        assert ov.overlap_ratio == 0.0
        assert ov.union_volume > 0.0

    def test_overlap_identical_ratio_one(self) -> None:
        """同一立方体 → 交集=并集=体积，重合率 1.0。"""
        a = self._cube(2.0)
        ov = gamut_overlap(a, a)
        assert ov.intersection_volume == pytest.approx(1.0, abs=1e-9)
        assert ov.union_volume == pytest.approx(1.0, abs=1e-9)
        assert ov.overlap_ratio == pytest.approx(1.0)

    def test_overlap_degenerate_clouds_zero(self) -> None:
        """点数 < 5（凸包退化）→ 交集 0.0，不抛异常。"""
        thin_a = GamutSample(profile_path="p", description="thin", lab=numpy.zeros((3, 3)),
                             volume=0.0, vertices=0)
        thin_b = GamutSample(profile_path="p", description="thin", lab=numpy.ones((4, 3)),
                             volume=0.0, vertices=0)
        ov = gamut_overlap(thin_a, thin_b)
        assert ov.intersection_volume == 0.0
        assert ov.volume_a == 0.0 and ov.volume_b == 0.0
        assert ov.union_volume == 0.0  # 合并后 7 点但共线，凸包退化
        assert ov.overlap_ratio == 0.0

    def test_plot_gamut_overlap_smoke(self, tmp_path: Path) -> None:
        """重叠切片冒烟：PNG 落盘且 PIL 可打开。"""
        a, b = self._cube(0.0), self._cube(0.5)
        out = plot_gamut_overlap(a, b, tmp_path / "ov.png", lightness=0.5,
                                 band=1.0, dpi=72)
        assert out == tmp_path / "ov.png"
        assert out.exists() and out.stat().st_size > 0
        with Image.open(out) as image:
            assert image.format == "PNG"
            image.verify()

    def test_plot_gamut_overlap_svg_and_bad_format(self, tmp_path: Path) -> None:
        """format="svg" 强制换后缀；白名单外 → VizError。"""
        out = plot_gamut_overlap(self._cube(0.0), self._cube(0.5),
                                 tmp_path / "ov.png", lightness=0.5,
                                 band=1.0, dpi=72, format="svg")
        assert out.name == "ov.svg"
        assert out.read_bytes()[:5] == b"<?xml"
        with pytest.raises(VizError):
            plot_gamut_overlap(self._cube(0.0), self._cube(0.5),
                               tmp_path / "x.gif", band=30.0, format="gif")
        with pytest.raises(VizError):
            plot_gamut_overlap(self._cube(0.0), self._cube(0.5),
                               tmp_path / "x.png", band=0.0)


# ---------------------------------------------------------------------------
# 体积趋势曲线（ROADMAP v0.5.10）：多次测量的色域体积变化
# ---------------------------------------------------------------------------
class TestVolumeTrend:
    @staticmethod
    def _series() -> list[VolumeSeries]:
        return [
            VolumeSeries(name="Press Day1-3", points=(
                ("D1", 95000.0), ("D2", 93800.0), ("D3", 94100.0))),
            VolumeSeries(name="Proof", points=(
                ("D1", 90100.0), ("D2", 90400.0), ("D3", 90250.0))),
        ]

    def test_volume_trend_png_smoke(self, tmp_path: Path) -> None:
        """冒烟：双系列 PNG 落盘且 PIL 可打开。"""
        out = plot_volume_trend(self._series(), tmp_path / "trend.png", dpi=72)
        assert out == tmp_path / "trend.png"
        assert out.exists() and out.stat().st_size > 0
        with Image.open(out) as image:
            assert image.format == "PNG"
            image.verify()

    def test_volume_trend_svg_and_bad_format(self, tmp_path: Path) -> None:
        """format="svg" 矢量导出；白名单外 → VizError。"""
        out = plot_volume_trend(self._series(), tmp_path / "trend.png",
                                dpi=72, format="svg")
        assert out.name == "trend.svg"
        assert out.read_bytes()[:5] == b"<?xml"
        with pytest.raises(VizError):
            plot_volume_trend(self._series(), tmp_path / "x.bmp", format="bmp")

    def test_volume_trend_validation(self, tmp_path: Path) -> None:
        """空序列 / 空点 / 非有限体积 → VizError。"""
        with pytest.raises(VizError):
            plot_volume_trend([], tmp_path / "x.png")
        with pytest.raises(VizError):
            plot_volume_trend([VolumeSeries(name="s", points=())],
                              tmp_path / "x.png")
        with pytest.raises(VizError):
            plot_volume_trend([VolumeSeries(name="s", points=(("D1", float("nan")),))],
                              tmp_path / "x.png")
        with pytest.raises(VizError):
            plot_volume_trend([VolumeSeries(name="s", points=(("D1", float("inf")),))],
                              tmp_path / "x.png")

    def test_volume_trend_ticks_and_legend(self, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
        """x 刻度 = 首系列批次标签；图例 = 系列名。"""
        from matplotlib import pyplot as plt

        original = plt.subplots
        holder: dict = {}

        def spy(*args, **kw):
            fig, ax = original(*args, **kw)
            holder["ax"] = ax
            return fig, ax

        monkeypatch.setattr(plt, "subplots", spy)
        plot_volume_trend(self._series(), tmp_path / "trend.png", dpi=60)
        ax = holder["ax"]
        assert [t.get_text() for t in ax.get_xticklabels()] == ["D1", "D2", "D3"]
        legend = ax.get_legend()
        assert legend is not None
        assert {t.get_text() for t in legend.get_texts()} == {"Press Day1-3", "Proof"}


# ---------------------------------------------------------------------------
# 图例统一色票命名（ROADMAP v0.5.8）：P<n> · description，多图一致
# ---------------------------------------------------------------------------
class TestLegendNaming:
    @staticmethod
    def _legend_texts(monkeypatch: pytest.MonkeyPatch, plot_fn, tmp_path: Path,
                      **kwargs) -> list[str]:
        """捕获绘图函数内部 ax 的图例文本（matplotlib legend 文本列表）。"""
        from matplotlib import pyplot as plt

        original = plt.subplots
        holder: dict = {}

        def spy(*args, **kw):
            fig, ax = original(*args, **kw)
            holder["ax"] = ax
            return fig, ax

        monkeypatch.setattr(plt, "subplots", spy)
        plot_fn([_make_sample("Fake sRGB", 41), _make_sample("Fake AdobeRGB", 42)],
                tmp_path / "legend.png", dpi=60, **kwargs)
        legend = holder["ax"].get_legend()
        assert legend is not None
        return [t.get_text() for t in legend.get_texts()]

    def test_legend_label_format(self) -> None:
        """P<n> 从 1 起，与系列色序号一致。"""
        from packstudio.engines.color_engine.viz.gamut import _legend_label
        assert _legend_label(0, "sRGB") == "P1 · sRGB"
        assert _legend_label(2, "CMYK") == "P3 · CMYK"

    def test_chromaticity_legend_prefixed(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
        """色度图：profile 项带 P<n> 前缀，D50 白点项不带。"""
        texts = self._legend_texts(monkeypatch, plot_chromaticity, tmp_path,
                                   show_spectral_locus=False)
        assert "P1 · Fake sRGB" in texts
        assert "P2 · Fake AdobeRGB" in texts
        assert "D50 white point" in texts

    def test_ab_slice_legend_prefixed(self, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """a-b 切片：P<n> 前缀 + 保留带宽后缀。"""
        texts = self._legend_texts(monkeypatch, plot_ab_slice, tmp_path,
                                   lightness=50.0, band=45.0)
        assert "P1 · Fake sRGB (L*≈50±45)" in texts

    def test_overlap_legend_prefixed(self, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
        """重叠切片：样本项带 P<n> 前缀，交集顶点项不带。"""
        from matplotlib import pyplot as plt

        original = plt.subplots
        holder: dict = {}

        def spy(*args, **kw):
            fig, ax = original(*args, **kw)
            holder["ax"] = ax
            return fig, ax

        monkeypatch.setattr(plt, "subplots", spy)
        plot_gamut_overlap(_make_sample("Fake sRGB", 41),
                           _make_sample("Fake AdobeRGB", 42),
                           tmp_path / "legend.png", dpi=60,
                           lightness=50.0, band=45.0)
        legend = holder["ax"].get_legend()
        assert legend is not None
        texts = [t.get_text() for t in legend.get_texts()]
        assert "P1 · Fake sRGB (L*≈50±45)" in texts
        assert "P2 · Fake AdobeRGB (L*≈50±45)" in texts
        assert "交集多面体顶点（带内投影）" in texts


# ---------------------------------------------------------------------------
# 等亮度切片 L* 扫描动画 GIF（ROADMAP v0.5.6）
# ---------------------------------------------------------------------------
class TestAbSliceAnimation:
    def test_animation_gif_smoke(self, tmp_path: Path) -> None:
        """GIF 冒烟：帧数 = steps、动态标志、PIL 可读。"""
        samples = [_make_sample("Fake sRGB", 31), _make_sample("Fake JapanColor", 32)]
        out = plot_ab_slice_animation(samples, tmp_path / "anim.gif",
                                      steps=5, band=45.0, dpi=60, interval_ms=100)
        assert out == tmp_path / "anim.gif"
        assert out.exists() and out.stat().st_size > 0
        with Image.open(out) as image:
            assert image.format == "GIF"
            assert image.is_animated
            assert image.n_frames == 5
            assert image.size[0] > 0 and image.size[1] > 0

    def test_animation_suffix_forced(self, tmp_path: Path) -> None:
        """非 .gif 后缀强制替换为 .gif。"""
        out = plot_ab_slice_animation([_make_sample("A", 33)], tmp_path / "x.png",
                                      steps=3, band=45.0, dpi=60, interval_ms=100)
        assert out.name == "x.gif"
        with Image.open(out) as image:
            assert image.format == "GIF" and image.n_frames == 3

    def test_animation_validation_raises(self, tmp_path: Path) -> None:
        """参数校验：帧数越界 / L* 范围非法 / band / 帧间隔 → VizError。"""
        sample = _make_sample("A", 34)
        for kwargs in ({"steps": 1}, {"steps": 65}, {"steps": True},
                       {"l_start": 95.0, "l_end": 5.0},
                       {"l_start": -1.0}, {"l_end": 101.0},
                       {"band": 0.0}, {"interval_ms": 0}):
            merged = {"band": 45.0, "dpi": 60, "interval_ms": 100, **kwargs}
            with pytest.raises(VizError):
                plot_ab_slice_animation([sample], tmp_path / "x.gif", **merged)


# ---------------------------------------------------------------------------
# 主波长/补色波长（ROADMAP v0.5.5）：射线-轨迹解析求交，550nm 解析解对照
# ---------------------------------------------------------------------------
class TestWavelength:
    @staticmethod
    def _locus_point(wavelength: float) -> tuple[float, float]:
        from packstudio.engines.color_engine.viz.gamut import (_SPECTRAL_LOCUS_WAVELENGTHS,
                                           _SPECTRAL_LOCUS_XY)
        index = int(numpy.argmin(numpy.abs(_SPECTRAL_LOCUS_WAVELENGTHS
                                           - wavelength)))
        return float(_SPECTRAL_LOCUS_XY[index, 0]), float(_SPECTRAL_LOCUS_XY[index, 1])

    @staticmethod
    def _d50() -> tuple[float, float]:
        from packstudio.engines.color_engine.viz.gamut import _D50_XY
        return _D50_XY

    def test_dominant_on_white_locus_line_exact(self) -> None:
        """P 在 W→locus(550nm) 连线上 0.9 处 → 主波长精确 = 550nm。"""
        wx, wy = self._d50()
        lx, ly = self._locus_point(550.0)
        mark = dominant_wavelength(wx + 0.9 * (lx - wx), wy + 0.9 * (ly - wy))
        assert mark.kind == "dominant"
        assert mark.wavelength == pytest.approx(550.0, abs=1e-6)
        assert mark.hit_x == pytest.approx(lx, abs=1e-9)
        assert mark.hit_y == pytest.approx(ly, abs=1e-9)
        assert mark.label == "550.0 nm"

    def test_purple_region_complementary(self) -> None:
        """紫弦中点方向（紫区）→ 补色波长，label 带「补」前缀。"""
        from packstudio.engines.color_engine.viz.gamut import _SPECTRAL_LOCUS_XY
        wx, wy = self._d50()
        chord_mid = ((_SPECTRAL_LOCUS_XY[0, 0] + _SPECTRAL_LOCUS_XY[-1, 0]) / 2.0,
                     (_SPECTRAL_LOCUS_XY[0, 1] + _SPECTRAL_LOCUS_XY[-1, 1]) / 2.0)
        mark = dominant_wavelength(wx + 0.5 * (chord_mid[0] - wx),
                                   wy + 0.5 * (chord_mid[1] - wy))
        assert mark.kind == "complementary"
        assert 360.0 <= mark.wavelength <= 700.0
        assert mark.label.startswith("补 ")
        assert mark.hit_x is not None and mark.hit_y is not None

    def test_achromatic_point(self) -> None:
        """P 与白点重合 → achromatic，wavelength/hit 均 None。"""
        wx, wy = self._d50()
        mark = dominant_wavelength(wx, wy)
        assert mark.kind == "achromatic"
        assert mark.wavelength is None and mark.hit_x is None
        assert mark.label == "achromatic"
        assert isinstance(mark, WavelengthMark)

    def test_plot_chromaticity_show_wavelengths_smoke(self, tmp_path: Path) -> None:
        """show_wavelengths=True：标注增强项开启，PNG 正常落盘。"""
        samples = [_make_sample("Fake sRGB", 21), _make_sample("Fake AdobeRGB", 22)]
        out = plot_chromaticity(samples, tmp_path / "wl.png", dpi=72,
                                show_spectral_locus=True, show_wavelengths=True)
        assert out == tmp_path / "wl.png"
        assert out.exists() and out.stat().st_size > 0
        with Image.open(out) as image:
            assert image.format == "PNG"
            image.verify()


# ---------------------------------------------------------------------------
# 默认变换（cmm 集成冒烟，cmm 并行就绪后自动生效）与导出面
# ---------------------------------------------------------------------------
class TestPackageContract:
    def test_package_exports(self) -> None:
        """__init__ 导出面与设计 §2.5 一致。"""
        import packstudio.engines.color_engine.viz as pkg

        assert set(pkg.__all__) == {"GamutSample", "GamutOverlap", "VolumeSeries",
                                    "WavelengthMark",
                                    "TransformFn",
                                    "sample_rgb_gamut", "sample_cmyk_gamut",
                                    "estimate_gamut_volume", "gamut_overlap",
                                    "dominant_wavelength",
                                    "plot_chromaticity", "plot_ab_slice",
                                    "plot_ab_slice_animation",
                                    "plot_gamut_overlap", "plot_volume_trend",
                                    "SUPPORTED_TRANSFERS", "TRANSFER_LABELS",
                                    "CICP_TO_TRANSFER",
                                    "pq_eotf", "pq_oetf", "hlg_oetf",
                                    "hlg_inverse_oetf", "srgb_oetf",
                                    "gamma22_oetf",
                                    "transfer_curve_points",
                                    "reference_points",
                                    "THEMES", "get_theme", "set_theme"}


@pytest.mark.skipif(not _cmm_available(), reason="cmm 引擎并行开发中未就绪")
@requires_srgb
class TestDefaultTransformIntegration:
    def test_rgb_with_cmm_default(self) -> None:
        """默认路径（不注入 transform）：cmm.transform_lab → Lab 点云体积>0。"""
        sample = sample_rgb_gamut(str(SRGB_PATH), density=6)
        assert sample.lab.shape == (216, 3)
        assert numpy.isfinite(sample.lab).all()
        assert sample.volume > 0.0
        assert sample.vertices > 0
        assert sample.description


# ---------------------------------------------------------------------------
# 光谱轨迹内置常量（ROADMAP v0.4.3，随 0.3.11）
# ---------------------------------------------------------------------------

def test_spectral_locus_embedded_constant() -> None:
    """内置轨迹常量：形状/端点/有限性锁定（360-700nm 5nm 步进 = 69 点）。"""
    from packstudio.engines.color_engine.viz.gamut import _spectral_locus_xy

    locus = _spectral_locus_xy()
    assert locus is not None and locus.shape == (69, 2)
    assert numpy.isfinite(locus).all()
    numpy.testing.assert_allclose(locus[0], [0.175560, 0.005294], atol=1e-6)
    numpy.testing.assert_allclose(locus[-1], [0.734690, 0.265310], atol=1e-6)
    # 单峰结构：y 最大值出现在绿区（520-540nm 附近，行 32-36）
    peak = int(numpy.argmax(locus[:, 1]))
    assert 30 <= peak <= 38


def test_viz_gamut_no_colour_import() -> None:
    """v0.4.3 解耦锁：gamut.py 全文（含函数内）禁止 import colour。"""
    import ast

    gamut_path = (
        Path(__file__).resolve().parents[2] / "packstudio" / "engines" / "color_engine" / "viz" / "gamut.py"
    )
    tree = ast.parse(gamut_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "colour", \
                    f"gamut.py 出现 colour 导入（行 {node.lineno}）——绘图路径已解耦，勿回退"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "colour", \
                f"gamut.py 出现 colour 导入（行 {node.lineno}）——绘图路径已解耦，勿回退"
