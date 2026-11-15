"""cmm.discovery 单元测试（TECH-DESIGN §4.6 / §7.1）。

核心扫描逻辑用纯文件系统 fixture（tmp 目录放假 .icc 文件）覆盖，
不依赖 icc/ 解析层；真实系统目录用例在目录缺失时 skip。
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from packstudio.engines.color_engine.cmm.discovery import (
    DiscoveredProfile,
    find_profile,
    scan_profiles,
    system_profile_dirs,
)

# ---------------------------------------------------------------------------
# 合成 fixture 帮助（纯文件系统，不依赖 icc/）
# ---------------------------------------------------------------------------
def _icc_header(*, device_class: bytes = b"mntr", color_space: bytes = b"RGB ",
                model: bytes = b"TST1", version: int = 0x02100000,
                tag_count: int = 0) -> bytes:
    """132 字节最小 ICC 头（128 header + 4 tag count，含 acsp 魔数与 D50 白点）。"""
    assert len(model) == 4, "model 必须为 4 字节签名（超出会撑乱头部布局）"
    header = bytearray(132)
    header[4:8] = b"\0\0\0\0"                       # preferred cmm
    header[8:12] = struct.pack(">I", version)
    header[12:16] = device_class
    header[16:20] = color_space
    header[20:24] = b"XYZ "                          # PCS
    header[24:36] = struct.pack(">6H", 2020, 1, 1, 0, 0, 0)  # date
    header[36:40] = b"acsp"                          # magic
    header[40:44] = b"\0\0\0\0"                      # platform
    header[48:52] = b"TKIT"                          # manufacturer
    header[52:56] = model                            # model（描述回退来源）
    header[64:68] = struct.pack(">I", 0)             # rendering intent
    # illuminant D50 s15Fixed16
    header[68:80] = struct.pack(">iii", 0x0000F6D6, 0x00010000, 0x0000D32D)
    header[128:132] = struct.pack(">I", tag_count)
    return bytes(header)


def _write_profile(path: Path, **kwargs: bytes) -> Path:
    data = _icc_header(**kwargs)
    path.write_bytes(data)
    return path


def _write_cicp_profile(path: Path, transfer: int) -> Path:
    """带 cicp tag 的合成 profile（20 字节 cicpType，primaries=9, tc=transfer）。"""
    header = bytearray(_icc_header(tag_count=1))
    entry = b"cicp" + struct.pack(">II", 144, 20)  # 表项在 132-143，tag 数据在 144 起
    payload = b"cicp" + b"\0" * 4 + bytes([9, transfer, 9, 1]) + b"\0" * 8
    assert len(entry) == 12 and len(payload) == 20
    path.write_bytes(bytes(header) + entry + payload)
    return path


@pytest.fixture()
def scan_dir(tmp_path: Path) -> Path:
    """构造混合内容目录：2 个合法头 + 1 个垃圾 + 隐藏 + 非法后缀 + 子目录。"""
    _write_profile(tmp_path / "alpha_monitor.icc", model=b"ALP1")
    _write_profile(tmp_path / "beta printer.ICM", device_class=b"prtr",
                   color_space=b"CMYK", model=b"BET2", version=0x04300000)
    (tmp_path / "garbage.icc").write_bytes(b"not an icc profile" * 3)
    (tmp_path / ".hidden.icm").write_bytes(_icc_header(model=b"HID9"))
    (tmp_path / "notes.txt").write_bytes(_icc_header(model=b"NOTX"))
    sub = tmp_path / "subdir"
    sub.mkdir()
    _write_profile(sub / "inner.icm", model=b"INN7")
    return tmp_path


# ---------------------------------------------------------------------------
# system_profile_dirs
# ---------------------------------------------------------------------------
def test_system_profile_dirs_existing_only() -> None:
    """返回列表且全部存在；Windows 下包含系统色彩目录（存在时）。"""
    dirs = system_profile_dirs()
    assert isinstance(dirs, list)
    assert all(d.is_dir() for d in dirs)
    win_dir = Path(r"C:\Windows\System32\spool\drivers\color")
    if win_dir.is_dir():
        assert win_dir in dirs


# ---------------------------------------------------------------------------
# scan_profiles（纯文件系统 fixture）
# ---------------------------------------------------------------------------
def test_scan_profiles_basic(scan_dir: Path) -> None:
    """合法头被解析（含大小写后缀），垃圾降级占位，非法后缀/隐藏/子目录被排除。"""
    found = scan_profiles([scan_dir])
    by_name = {Path(item.path).name: item for item in found}

    assert set(by_name) == {"alpha_monitor.icc", "beta printer.ICM", "garbage.icc"}

    alpha = by_name["alpha_monitor.icc"]
    assert isinstance(alpha, DiscoveredProfile)
    assert alpha.device_class == "mntr"
    assert alpha.color_space == "RGB "
    assert alpha.version == "2.1.0"
    assert alpha.is_hdr is False
    assert alpha.description == "ALP1"  # icc 层缺失/无 desc tag → header.model 回退
    assert alpha.file_size == 132

    beta = by_name["beta printer.ICM"]
    assert beta.device_class == "prtr"
    assert beta.color_space == "CMYK"
    assert beta.version == "4.3.0"

    garbage = by_name["garbage.icc"]
    assert garbage.description == "<unreadable>"
    assert garbage.device_class == "" and garbage.color_space == ""
    assert garbage.version == "" and garbage.is_hdr is False
    assert garbage.file_size == len(b"not an icc profile" * 3)


def test_scan_profiles_hidden_and_dedupe(scan_dir: Path) -> None:
    """follow_hidden 收录隐藏文件；重复传入同目录去重；结果按路径排序。"""
    assert ".hidden.icm" not in {Path(i.path).name for i in scan_profiles([scan_dir])}
    with_hidden = scan_profiles([scan_dir], follow_hidden=True)
    assert ".hidden.icm" in {Path(i.path).name for i in with_hidden}

    doubled = scan_profiles([scan_dir, scan_dir, scan_dir])
    assert len(doubled) == len(scan_profiles([scan_dir]))
    paths = [item.path for item in doubled]
    assert paths == sorted(paths)
    assert len(set(paths)) == len(paths)  # 绝对路径去重


def test_scan_profiles_deterministic_and_empty(tmp_path: Path) -> None:
    """空目录返回空元组；两次扫描结果一致。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert scan_profiles([empty]) == ()
    first = scan_profiles([tmp_path])
    second = scan_profiles([tmp_path])
    assert first == second


def test_scan_profiles_missing_dir_no_raise(tmp_path: Path) -> None:
    """不存在的目录被跳过，不抛异常。"""
    result = scan_profiles([tmp_path / "does_not_exist"])
    assert result == ()


def test_scan_profiles_hdr_cicp(tmp_path: Path) -> None:
    """cicp tc=16(PQ)/18(HLG) → is_hdr=True；tc=1 → False；ADGC 存在 → True。"""
    pq = _write_cicp_profile(tmp_path / "pq.icc", transfer=16)
    hlg = _write_cicp_profile(tmp_path / "hlg.icc", transfer=18)
    sdr = _write_cicp_profile(tmp_path / "sdr.icc", transfer=1)
    adgc_header = _icc_header(tag_count=1)
    adgc_entry = b"ADGC" + struct.pack(">II", 144, 8)
    (tmp_path / "adgc.icc").write_bytes(adgc_header + adgc_entry + b"\0" * 8)
    for path, expected in ((pq, True), (hlg, True), (sdr, False),
                           (tmp_path / "adgc.icc", True)):
        found = scan_profiles([tmp_path])
        entry = next(item for item in found if Path(item.path).name == path.name)
        assert entry.is_hdr is expected, path.name
    pq.unlink(); hlg.unlink(); sdr.unlink()


def test_scan_profiles_gmmp_suffix(tmp_path: Path) -> None:
    """.gmmp 等 WCS 后缀被收录（魔数合法则解析，否则占位）。"""
    (tmp_path / "model.gmmp").write_bytes(b"\x00" * 200)  # 无 acsp → 占位
    (tmp_path / "real.camp").write_bytes(_icc_header(model=b"CAMP"))
    found = {Path(i.path).name: i for i in scan_profiles([tmp_path])}
    assert set(found) == {"model.gmmp", "real.camp"}
    assert found["model.gmmp"].description == "<unreadable>"
    assert found["real.camp"].description == "CAMP"


# ---------------------------------------------------------------------------
# 真实系统目录（存在才跑）
# ---------------------------------------------------------------------------
def test_scan_real_dirs() -> None:
    """Windows 系统 profile 目录扫描：至少含 sRGB / AdobeRGB1998（存在时）。"""
    dirs = system_profile_dirs()
    if not dirs:
        pytest.skip("no system profile dirs on this machine")
    found = scan_profiles()
    names = {Path(item.path).name for item in found}
    assert any("sRGB" in n for n in names)
    assert any("AdobeRGB1998" in n for n in names)
    for item in found:
        if item.description != "<unreadable>":
            assert item.color_space  # 合法头部条目必有色空间签名


def test_scan_real_dir_filtering_semantics() -> None:
    """真实目录下后缀分布符合设计清单（.icm/.icc/.gmmp/.cdmp/.camp）。"""
    dirs = system_profile_dirs()
    if not dirs:
        pytest.skip("no system profile dirs on this machine")
    found = scan_profiles()
    suffixes = {Path(item.path).suffix.lower() for item in found}
    assert suffixes <= {".icm", ".icc", ".gmmp", ".cdmp", ".camp"}


# ---------------------------------------------------------------------------
# find_profile
# ---------------------------------------------------------------------------
def test_find_profile_exact_and_substring(scan_dir: Path) -> None:
    """精确命中优先于包含命中；不区分大小写。"""
    exact = find_profile("ALPHA_MONITOR.ICC", [scan_dir])
    assert exact is not None
    assert Path(exact).name == "alpha_monitor.icc"

    # "printer" 同时出现在 beta 文件名与 alpha 描述?（否）→ 文件名包含命中
    by_substring = find_profile("printer", [scan_dir])
    assert by_substring is not None
    assert Path(by_substring).name == "beta printer.ICM"


def test_find_profile_exact_priority(scan_dir: Path) -> None:
    """文件名精确相等（含去后缀相等）优先于仅包含命中。"""
    _write_profile(scan_dir / "alpha.icm", model=b"ALP2")
    hit = find_profile("alpha", [scan_dir])
    assert hit is not None
    assert Path(hit).name == "alpha.icm"  # 精确（去后缀）胜出 "alpha_monitor.icc"


def test_find_profile_by_description(tmp_path: Path) -> None:
    """文件名不含关键词时按 description 匹配。"""
    _write_profile(tmp_path / "x1.icm", model=b"ZEB1")
    hit = find_profile("zeb1", [tmp_path])
    assert hit is not None
    assert Path(hit).name == "x1.icm"


def test_find_profile_miss(tmp_path: Path) -> None:
    """未命中返回 None；空名返回 None。"""
    assert find_profile("nonexistent-xyz", [tmp_path]) is None
    assert find_profile("", [tmp_path]) is None


def test_find_profile_uses_system_dirs_when_none() -> None:
    """dirs=None 时走系统目录：能找到 sRGB（存在时）。"""
    if not system_profile_dirs():
        pytest.skip("no system profile dirs on this machine")
    hit = find_profile("sRGB")
    assert hit is not None
    assert hit.suffix.lower() in {".icm", ".icc"}


# ---------------------------------------------------------------------------
# 扫描索引缓存（ROADMAP v0.4.5，随 0.3.13）
# ---------------------------------------------------------------------------

def test_scan_cache_reuse_hit_counts(scan_dir: Path) -> None:
    """同目录重复扫描：命中计数递增、结果逐项一致。"""
    from packstudio.engines.color_engine.cmm import scan_cache_clear, scan_cache_info, scan_profiles

    scan_cache_clear()
    first = scan_profiles([scan_dir])
    second = scan_profiles([scan_dir])
    info = scan_cache_info()
    assert first == second
    assert info["misses"] >= 2          # 首轮全探测
    assert info["hits"] >= 2            # 二轮全命中
    assert 0.0 <= info["hit_rate"] <= 1.0
    assert info["entries"] == info["cap"] or info["entries"] <= info["cap"]
    scan_cache_clear()
    assert scan_cache_info()["entries"] == 0


def test_scan_cache_mtime_invalidation(scan_dir: Path) -> None:
    """mtime 变化 → 该文件重新探测（miss 递增），未变文件继续命中。"""
    import os

    from packstudio.engines.color_engine.cmm import scan_cache_clear, scan_cache_info, scan_profiles

    scan_cache_clear()
    scan_profiles([scan_dir])
    scan_profiles([scan_dir])
    before = scan_cache_info()

    targets = sorted(p for p in scan_dir.iterdir()
                     if not p.name.startswith(".")
                     and p.suffix.lower() in (".icc", ".icm"))
    os.utime(targets[0], ns=(1_000_000_000, 1_000_000_000))
    scan_profiles([scan_dir])
    after = scan_cache_info()

    assert after["misses"] == before["misses"] + 1   # 仅被改 mtime 的文件重探
    assert after["hits"] >= before["hits"]           # 其余仍命中


def test_scan_cache_info_shape() -> None:
    """scan_cache_info 键集与 hit_rate 语义。"""
    from packstudio.engines.color_engine.cmm import scan_cache_info

    info = scan_cache_info()
    assert set(info) == {"hits", "misses", "entries", "cap", "hit_rate"}
    assert 0.0 <= info["hit_rate"] <= 1.0
