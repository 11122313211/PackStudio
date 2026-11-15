"""viz.set_theme 主题切换（ROADMAP v0.5.4）。

覆盖：默认 dark、THEMES 契约、light 切换 token 生效、往返恢复、非法名
VizError、端到端绘图输出随主题变化（PNG 角像素色值）。
"""
from __future__ import annotations

from pathlib import Path

import numpy
import pytest
from PIL import Image

from packstudio.engines.color_engine.errors import VizError
from packstudio.engines.color_engine.viz import (GamutSample, THEMES, get_theme, plot_chromaticity,
                             set_theme)
from packstudio.engines.color_engine.viz.theme import TOKENS


@pytest.fixture(autouse=True)
def _restore_dark():
    """主题是进程级全局状态：每测后恢复 dark，避免跨测试污染。"""
    yield
    set_theme("dark")


def _make_sample(description: str, index: int) -> GamutSample:
    lab = numpy.array([[50.0, x, y] for x in (-40.0, 0.0, 40.0)
                       for y in (-40.0, 0.0, 40.0)]) + index
    return GamutSample(profile_path="p", description=description, lab=lab,
                       volume=0.0, vertices=0)


def test_default_theme_is_dark():
    """默认主题 dark（历史行为不变）。"""
    assert get_theme() == "dark"
    assert TOKENS["canvas"] == "#010102"


def test_themes_tuple_contract():
    """THEMES 契约：当前两主题，dark 为首（默认）。"""
    assert THEMES == ("dark", "light")
    assert THEMES[0] == "dark"


def test_set_theme_light_tokens():
    """set_theme("light")：当前名/token 同步切换，返回快照与 TOKENS 脱钩。"""
    snapshot = set_theme("light")
    assert get_theme() == "light"
    assert snapshot["canvas"] == "#ffffff"
    assert TOKENS["canvas"] == "#ffffff"
    assert TOKENS is not snapshot  # 快照是拷贝


def test_set_theme_roundtrip():
    """dark→light→dark 往返：token 精确复原。"""
    dark_ref = set_theme("dark")
    set_theme("light")
    assert get_theme() == "light"
    restored = set_theme("dark")
    assert get_theme() == "dark"
    assert restored == dark_ref  # token 快照精确复原
    assert TOKENS["canvas"] == "#010102"


def test_unknown_theme_raises():
    """非法主题名 → VizError，且不改变当前主题。"""
    with pytest.raises(VizError):
        set_theme("solarized")
    with pytest.raises(VizError):
        set_theme("")
    assert get_theme() == "dark"


def test_light_theme_plot_end_to_end(tmp_path: Path) -> None:
    """端到端：dark/light 下同一数据绘图的画布色不同（PNG 角像素）。"""
    sample = _make_sample("T", 0)
    dark_png = plot_chromaticity([sample], tmp_path / "d.png", dpi=72,
                                 show_spectral_locus=False)
    with Image.open(dark_png) as image:
        corner_dark = image.convert("RGB").getpixel((2, 2))

    set_theme("light")
    light_png = plot_chromaticity([sample], tmp_path / "l.png", dpi=72,
                                  show_spectral_locus=False)
    with Image.open(light_png) as image:
        corner_light = image.convert("RGB").getpixel((2, 2))

    assert sum(corner_dark) < 100  # 深色画布 #010102
    assert sum(corner_light) > 700  # 白画布
