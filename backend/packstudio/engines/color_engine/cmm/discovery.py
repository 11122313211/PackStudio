"""系统 ICC profile 目录扫描（TECH-DESIGN §4.6）。

核心扫描逻辑自包含（纯文件系统 + 头部字节解析），单测用 tmp 目录放假
.icc 文件即可覆盖，不依赖 icc/ 解析层；profile 描述名惰性导入 icc 层
（``packstudio.engines.color_engine.icc.tags``）获取，icc/ 缺席或解析失败时回退 header.model。
"""
from __future__ import annotations

import logging
import os
import sys
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger("icct.cmm.discovery")

__all__ = [
    "DiscoveredProfile",
    "system_profile_dirs",
    "scan_profiles",
    "find_profile",
    "scan_cache_info",
    "scan_cache_clear",
]

_PROFILE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".icm", ".icc", ".gmmp", ".cdmp", ".camp"}
)
"""收录的文件后缀（Windows WCS 模型文件 .gmmp/.cdmp/.camp 一并收录）。

说明：设计 §3.6 的 DiscoveredProfile 无"类型"字段，后缀区分体现在
path 后缀本身；非 ICC 主档（gmmp 等）inspect 报 IccParseError 属预期
（设计附录 B）。
"""

_MAGIC: Final[bytes] = b"acsp"
_MIN_HEADER: Final[int] = 132  # 128 头 + 4 tag count
_UNREADABLE: Final[str] = "<unreadable>"


@dataclass(frozen=True, slots=True)
class DiscoveredProfile:
    """一个被发现的 profile 文件（TECH-DESIGN §3.6）。

    Attributes:
        path: 文件绝对路径字符串。
        file_size: 文件字节数。
        description: 描述名（icc 层解析；失败回退 header.model，
            完全不可读为 ``"<unreadable>"``）。
        device_class: 头部 12-15 字节设备类签名（如 ``"mntr"``；不可读为 ``""``）。
        color_space: 头部 16-19 字节数据色空间签名（如 ``"RGB "``；不可读为 ``""``）。
        version: 头部版本号字符串（如 ``"2.1.0"``；不可读为 ``""``）。
        is_hdr: 快速 HDR 判定（cicp 传输特性 ∈ {16,18} 或存在 ADGC/HAGC tag）。
    """

    path: str
    file_size: int
    description: str
    device_class: str
    color_space: str
    version: str
    is_hdr: bool

    def as_json(self) -> dict[str, Any]:
        """JSON 契约输出（字段名 = dataclass 字段名，设计 §2.3）。"""
        return {
            "path": self.path,
            "file_size": self.file_size,
            "description": self.description,
            "device_class": self.device_class,
            "color_space": self.color_space,
            "version": self.version,
            "is_hdr": self.is_hdr,
        }


def system_profile_dirs() -> list[Path]:
    """返回当前平台上实际存在的系统 profile 目录列表。

    行为:
        Windows: ``%SystemRoot%\\System32\\spool\\drivers\\color``；
        macOS: ``/Library/ColorSync/Profiles``、``~/Library/ColorSync/Profiles``；
        Linux: ``/usr/share/color/icc``、``/usr/local/share/color/icc``、
        ``~/.local/share/color/icc``。不存在的目录自动剔除。
    返回:
        存在的目录列表（``Path``）。
    异常:
        不抛（目录探测失败视为不存在）。
    """
    dirs: list[Path] = []
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        dirs.append(Path(root) / "System32" / "spool" / "drivers" / "color")
    elif sys.platform == "darwin":
        dirs.append(Path("/Library/ColorSync/Profiles"))
        dirs.append(Path.home() / "Library" / "ColorSync" / "Profiles")
    else:
        dirs.extend((
            Path("/usr/share/color/icc"),
            Path("/usr/local/share/color/icc"),
            Path.home() / ".local" / "share" / "color" / "icc",
        ))
    return [d for d in dirs if d.is_dir()]


def scan_profiles(dirs: Iterable[Path] | None = None,
                  *, follow_hidden: bool = False) -> tuple[DiscoveredProfile, ...]:
    """扫描目录（非递归）收集 ICC profile 文件。

    行为:
        对每个目录逐条目探测：仅收录 :data:`_PROFILE_SUFFIXES` 后缀的常规
        文件（Windows 下含 .icm/.icc/.gmmp/.cdmp/.camp）；``follow_hidden``
        为 False 时跳过 ``.`` 开头的隐藏文件。每个文件快速读头：魔数非
        ``acsp`` 或不足 132 字节 → 占位条目（仅 path/size，description=
        ``"<unreadable>"``）。去重按解析后的绝对路径；结果按路径排序保证
        确定性。单目录/单文件失败记 WARNING 后继续，不抛异常。
    参数:
        dirs: 待扫描目录；None 时使用 :func:`system_profile_dirs`。
        follow_hidden: 是否收录隐藏文件（``.`` 前缀）。
    返回:
        ``tuple[DiscoveredProfile, ...]``（按路径排序）。
    异常:
        不抛。
    """
    if dirs is None:
        dirs = system_profile_dirs()
    seen: set[str] = set()
    out: list[DiscoveredProfile] = []
    for directory in dirs:
        dir_path = Path(directory)
        try:
            with os.scandir(os.fspath(dir_path)) as iterator:
                entries = list(iterator)
        except OSError as exc:
            logger.warning("跳过不可读目录 %s: %s", dir_path, exc)
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                name = entry.name
                if not follow_hidden and name.startswith("."):
                    continue
                if Path(name).suffix.lower() not in _PROFILE_SUFFIXES:
                    continue
                path = Path(entry.path)
                try:
                    resolved = str(path.resolve())
                except OSError:
                    resolved = str(path)
                if resolved in seen:
                    continue
                seen.add(resolved)
                out.append(_probe_file_cached(path))
            except OSError as exc:
                logger.warning("跳过无法访问的文件 %s: %s", getattr(entry, "path", "?"), exc)
                continue
    out.sort(key=lambda item: item.path)
    return tuple(out)


def find_profile(name: str, dirs: Iterable[Path] | None = None) -> Path | None:
    """按名称/描述查找 profile 文件（TECH-DESIGN §4.6）。

    行为:
        先对扫描结果做文件名匹配：不区分大小写的**精确文件名命中**优先
        （含"去掉后缀后精确相等"），其次为文件名**包含**命中；两者皆无时
        对 description 做包含匹配。同一优先级内按路径排序取首个，保证
        确定性。未命中返回 None。
    参数:
        name: 查找名（如 ``"sRGB"``、``"AdobeRGB1998.icc"``）。
        dirs: 扫描目录；None 时使用系统目录。
    返回:
        命中的 ``Path``；未命中 None。
    异常:
        不抛。
    """
    key = str(name).strip().casefold()
    if not key:
        return None
    exact: list[DiscoveredProfile] = []
    by_name: list[DiscoveredProfile] = []
    by_desc: list[DiscoveredProfile] = []
    for item in scan_profiles(dirs):
        filename = Path(item.path).name.casefold()
        if filename == key or Path(filename).stem == key:
            exact.append(item)
        elif key in filename:
            by_name.append(item)
        elif key in item.description.casefold():
            by_desc.append(item)
    candidates = exact or by_name or by_desc
    if not candidates:
        return None
    return Path(sorted(candidates, key=lambda c: c.path)[0].path)


# ---- 内部实现 -----------------------------------------------------------

def _placeholder(path: str, file_size: int) -> DiscoveredProfile:
    """不可读文件的占位条目（设计 §4.6：仅填 path/size）。"""
    return DiscoveredProfile(
        path=path, file_size=file_size, description=_UNREADABLE,
        device_class="", color_space="", version="", is_hdr=False,
    )


def _probe_file(path: Path) -> DiscoveredProfile:
    """读取单个文件并解析头部（失败降级为占位条目）。"""
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        logger.warning("stat 失败 %s: %s", path, exc)
        return _placeholder(str(path), 0)
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning("读取失败 %s: %s", path, exc)
        return _placeholder(str(path), file_size)
    return _probe_data(str(path), file_size, data)


# ---------------------------------------------------------------------------
# 扫描索引缓存（ROADMAP v0.4.5）：每文件粒度，(path, mtime_ns, size) 键
# ---------------------------------------------------------------------------
# mtime/size 任一变化 → 键失配自动重新探测；文件删除后旧条目不再被查询，
# 容量上限 LRU 逐出。进程级共享（WebUI/CLI 同进程多次扫描受益）。

_SCAN_CACHE_CAP: Final[int] = 4096
_scan_cache: OrderedDict[tuple[str, int, int], DiscoveredProfile] = OrderedDict()
_scan_cache_hits = 0
_scan_cache_misses = 0


def scan_cache_info() -> dict[str, int | float]:
    """扫描索引缓存统计：``{"hits", "misses", "entries", "cap", "hit_rate"}``。

    hit_rate 为 0-1 浮点（总查找数为 0 时为 0.0）。
    """
    total = _scan_cache_hits + _scan_cache_misses
    return {
        "hits": _scan_cache_hits,
        "misses": _scan_cache_misses,
        "entries": len(_scan_cache),
        "cap": _SCAN_CACHE_CAP,
        "hit_rate": (_scan_cache_hits / total) if total else 0.0,
    }


def scan_cache_clear() -> None:
    """清空扫描索引缓存与计数（测试/诊断用）。"""
    global _scan_cache_hits, _scan_cache_misses
    _scan_cache.clear()
    _scan_cache_hits = 0
    _scan_cache_misses = 0


def _probe_file_cached(path: Path) -> DiscoveredProfile:
    """带索引缓存的文件探测：stat 键命中直接复用，未命中探测并入库。"""
    global _scan_cache_hits, _scan_cache_misses
    try:
        st = path.stat()
    except OSError:
        # stat 失败交给 _probe_file 走统一 WARNING+占位路径（不缓存占位）
        _scan_cache_misses += 1
        return _probe_file(path)
    key = (str(path), st.st_mtime_ns, st.st_size)
    cached = _scan_cache.get(key)
    if cached is not None:
        _scan_cache_hits += 1
        _scan_cache.move_to_end(key)
        return cached
    _scan_cache_misses += 1
    entry = _probe_file(path)
    _scan_cache[key] = entry
    _scan_cache.move_to_end(key)
    while len(_scan_cache) > _SCAN_CACHE_CAP:
        _scan_cache.popitem(last=False)
    return entry


def _probe_data(path: str, file_size: int, data: bytes) -> DiscoveredProfile:
    """从完整字节解析头部字段（自包含，不依赖 icc/）。"""
    if len(data) < _MIN_HEADER or data[36:40] != _MAGIC:
        logger.debug("非 ICC 主档或头部不完整: %s", path)
        return _placeholder(path, file_size)
    device_class = data[12:16].decode("ascii", "replace")
    color_space = data[16:20].decode("ascii", "replace")
    raw_version = int.from_bytes(data[8:12], "big")
    major = (raw_version >> 24) & 0xFF
    minor = (raw_version >> 20) & 0x0F
    bugfix = (raw_version >> 16) & 0x0F
    version = f"{major}.{minor}.{bugfix}"
    return DiscoveredProfile(
        path=path,
        file_size=file_size,
        description=_profile_description(data),
        device_class=device_class,
        color_space=color_space,
        version=version,
        is_hdr=_quick_is_hdr(data),
    )


def _profile_description(data: bytes) -> str:
    """profile 描述名：惰性导入 icc 层解析，失败回退 header.model。"""
    description = ""
    try:
        from packstudio.engines.color_engine.icc.tags import get_profile_description, parse_tag_table

        description = str(get_profile_description(data, parse_tag_table(data)))
    except Exception as exc:  # noqa: BLE001 - icc/ 缺席或解析失败均回退
        logger.debug("icc 层描述解析不可用（回退 header.model）: %s", exc)
    if not description or description == "<unnamed>":
        model = data[52:56].decode("ascii", "replace").strip("\x00 ").strip()
        description = model or "<unnamed>"
    return description


def _quick_is_hdr(data: bytes) -> bool:
    """快速 HDR 判定（设计 §4.4 规则的自包含轻量版）。

    存在 ``ADGC``/``HAGC`` tag 即 True；存在 ``cicp`` tag 且其传输特性
    码点 ∈ {16(PQ), 18(HLG)} 为 True；其余 False。任何解析异常 → False。
    """
    try:
        count = int.from_bytes(data[128:132], "big")
        if count == 0:
            return False
        max_count = len(data) // 12 + 2  # 与 icc 层同款的畸形 count 上限
        if count > max_count:
            logger.debug("tag count 畸形（%d），HDR 判定按截断处理", count)
            count = max_count
        for index in range(count):
            base = 132 + index * 12
            if base + 12 > len(data):
                return False
            signature = data[base:base + 4]
            if signature in (b"ADGC", b"HAGC"):
                return True
            if signature == b"cicp":
                offset = int.from_bytes(data[base + 4:base + 8], "big")
                size = int.from_bytes(data[base + 8:base + 12], "big")
                if (offset + size <= len(data) and size >= 10
                        and data[offset:offset + 4] == b"cicp"):
                    if data[offset + 9] in (16, 18):  # transfer_characteristics
                        return True
        return False
    except Exception:  # noqa: BLE001 - 防御式：任何异常按"非 HDR"处理
        return False
