"""cmm 包：ImageCms 封装引擎与系统 profile 扫描（TECH-DESIGN §2.5 导出面）。"""
from __future__ import annotations

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
from packstudio.engines.color_engine.cmm.discovery import (
    DiscoveredProfile,
    find_profile,
    scan_cache_clear,
    scan_cache_info,
    scan_profiles,
    system_profile_dirs,
)

def transform_cache_info() -> dict[str, int | float]:
    """模块级共享引擎的变换缓存统计（v0.4.1 可观测性）。

    返回 ``{"hits", "misses", "entries", "max_cache", "hit_rate"}``；
    hit_rate 为 0-1 浮点。供 CLI ``--timing`` 与诊断层读取。
    """
    return _ENGINE_SHARED.cache_info()


# 共享单例绑定（engine 模块的进程级缓存实例；访问器在调用时读取，顺序无关）
from packstudio.engines.color_engine.cmm.engine import _ENGINE as _ENGINE_SHARED  # noqa: E402

__all__ = [
    # engine
    "RenderingIntent",
    "TransformSpec",
    "TransformEngine",
    "open_profile",
    "profile_names",
    "intent_supported",
    "transform_lab",
    "soft_proof",
    "check_out_of_gamut",
    "transform_cache_info",
    # discovery
    "DiscoveredProfile",
    "system_profile_dirs",
    "scan_profiles",
    "find_profile",
    "scan_cache_info",
    "scan_cache_clear",
]
