"""viz 主题（ROADMAP v0.5.4）：公开 set_theme()/get_theme() 绘图主题切换。

主题 = 一组语义色 token（canvas/ink 系/hairline 系/series 数据可视化系列色），
与 WebUI Linear token 同源（DESIGN.md §4.12 映射）。gamut 绘图函数在绘制时
实时读取 :data:`TOKENS`；``set_theme()`` **原地**清空并重填同一 dict 对象，
已导入的引用自动跟随（无需 reload），下一次绘制即生效，并返回应用后的
token 快照供观测。默认主题 dark（与历史行为一致）。
"""
from __future__ import annotations

from typing import Any, Final

from packstudio.engines.color_engine.errors import VizError

THEMES: Final[tuple[str, ...]] = ("dark", "light")

# dark：Linear 深色基调（历史默认；与 WebUI token 同源）
_DARK: Final[dict[str, Any]] = {
    "canvas": "#010102",
    "hairline": "#23252a",
    "hairline_strong": "#34343a",
    "ink": "#f7f8f8",
    "ink_muted": "#d0d6e0",
    "ink_subtle": "#8a8f98",
    "ink_tertiary": "#62666d",
    # 数据可视化系列色：深色画布上按辨识度排序（lavender-hover / 亮化 success /
    # 扩展语义 warning / 青绿 / error / ink-muted），多 profile 可区分
    "series": ("#828fff", "#4cb782", "#f5a623", "#50e3c2", "#eb5757", "#d0d6e0"),
}
# light：同构反相（白画布 + 深墨），系列色按浅底辨识度调深
_LIGHT: Final[dict[str, Any]] = {
    "canvas": "#ffffff",
    "hairline": "#d9d9de",
    "hairline_strong": "#b9b9c0",
    "ink": "#1a1b1e",
    "ink_muted": "#3f434a",
    "ink_subtle": "#6b7280",
    "ink_tertiary": "#9ca3af",
    "series": ("#4c5bd4", "#2e7d5b", "#b57508", "#0f9d8f", "#c53a3a", "#4a4f57"),
}

_name: str = "dark"
# 活引用：set_theme 原地更新此 dict，gamut 绘图函数经模块导入读取实时值。
# 约定只读——主题变更的唯一合法途径是 set_theme()。
TOKENS: Final[dict[str, Any]] = dict(_DARK)


def set_theme(name: str) -> dict[str, Any]:
    """切换 viz 绘图主题（ROADMAP v0.5.4）。

    行为:
        校验 ``name`` ∈ :data:`THEMES`；把对应主题 token 原地写入
        :data:`TOKENS`（同一 dict 对象，已导入引用自动跟随），此后 gamut
        绘图函数的每次绘制读取当前值。不影响 matplotlib rcParams 的字体
        设置（gamut 的 CJK 回退链独立于主题）。
    参数:
        name: 主题名——"dark"（默认深色画布）或 "light"。
    返回:
        dict[str, Any]: 应用后的 token 快照（浅拷贝，与 TOKENS 脱钩）。
    异常:
        VizError: name 不在 THEMES。
    """
    global _name
    if name not in THEMES:
        raise VizError(
            f"未知主题: {name!r}（支持 {'/'.join(THEMES)}）",
            detail={"theme": str(name), "supported": list(THEMES)},
        )
    _name = name
    src = _DARK if name == "dark" else _LIGHT
    TOKENS.clear()
    TOKENS.update(src)
    return dict(TOKENS)


def get_theme() -> str:
    """返回当前主题名（默认 "dark"）。"""
    return _name
