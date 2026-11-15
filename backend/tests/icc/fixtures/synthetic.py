"""合成 ICC profile 构造器（TECH-DESIGN §7.2 fixture 轨 1）。

零外部文件、可任意变异字节；供 header/tags/validate/inspect 测试复用，
也被 PDF 审计等模块的 fixture 借用（``build_matrix_trc_profile``）。

布局（约 400+ 字节）：
- header 128B：size 回填 / cmm=0 / version=入参 / class=入参 / ``RGB ``→``XYZ ``
  / date=2020-01-01 00:00:00 / magic=acsp / platform=0 / flags=0 / mfr=``TKIT``
  / model=``TST1`` / attributes=(0,0) / intent=0 / illuminant=D50 定点
  / creator=0 / id=0×16 / reserved=0；
- tag 表：desc / cprt / wtpt / rXYZ / gXYZ / bXYZ / rTRC + gTRC + bTRC（三者共享
  同一 offset/size，覆盖共享 tag 允许清单路径）+ 可选 cicp + 可选附加 tag；
- 每段 payload 4 字节对齐；``mutate`` 提供校验规则的变异输入源。

仅标准库依赖（pytest 仅在 ``require_system_profile`` 内惰性使用）。
"""
from __future__ import annotations

import pathlib
import struct

__all__ = [
    "SYSTEM_PROFILES",
    "D50_FIXED",
    "build_matrix_trc_profile",
    "mutate",
    "desc_type_payload",
    "text_type_payload",
    "mluc_type_payload",
    "xyz_type_payload",
    "curv_type_payload",
    "para_type_payload",
    "sig_type_payload",
    "mft1_payload",
    "mft2_payload",
    "mab_payload",
    "cicp_type_payload",
    "require_system_profile",
]

#: 真实系统 profile 参数化路径（不存在时由 ``require_system_profile`` 触发 skip）。
SYSTEM_PROFILES: dict[str, str] = {
    "sRGB": r"C:\Windows\System32\spool\drivers\color\sRGB Color Space Profile.icm",
    "AdobeRGB1998": r"C:\Windows\System32\spool\drivers\color\AdobeRGB1998.icc",
    "JapanColor2001Coated": r"C:\Windows\System32\spool\drivers\color\JapanColor2001Coated.icc",
    "RSWOP": r"C:\Windows\System32\spool\drivers\color\RSWOP.icm",
    "DisplayP3": r"C:\Windows\System32\spool\drivers\color\Display P3.icm",
}

#: D50 定点 s15Fixed16（设计 §7.2 指定值：0.9642…/1.0/0.8249…）。
D50_FIXED: tuple[int, int, int] = (0x0000F6D6, 0x00010000, 0x0000D32D)


def _s15(value: float) -> bytes:
    """float → s15Fixed16 大端 4 字节。"""
    return struct.pack(">i", int(round(value * 65536)))


def _pad(data: bytes) -> bytes:
    """补齐到 4 字节倍数。"""
    return data + b"\x00" * ((-len(data)) % 4)


# ---------------------------------------------------------------------------
# tag payload 工厂
# ---------------------------------------------------------------------------


def desc_type_payload(
    text: str,
    *,
    unicode_text: str | None = None,
    mac_text: str | None = None,
) -> bytes:
    """textDescriptionType（'desc'）payload。

    ASCII 串长含终止 NUL；Unicode 段为 语言码 u32 + 脚本码 u32 + 字节长 u32 +
    UTF-16BE 数据；Mac 段为 脚本码 u8 + 串长 u8（含终止 NUL）+ latin-1 数据。
    """
    ascii_bytes = text.encode("ascii", errors="replace") + b"\x00"
    out = b"desc" + b"\x00" * 4 + struct.pack(">I", len(ascii_bytes)) + ascii_bytes
    if unicode_text is None:
        out += struct.pack(">III", 0, 0, 0)
    else:
        uni = unicode_text.encode("utf-16-be")
        out += struct.pack(">III", 0, 0, len(uni)) + uni
    if mac_text is None:
        out += b"\x00\x00"
    else:
        mac = mac_text.encode("latin-1", errors="replace")
        out += b"\x00" + bytes([len(mac)]) + mac
    return out


def text_type_payload(text: str) -> bytes:
    """TextType（'text'）payload：7-bit ASCII + 终止 NUL。"""
    return b"text" + b"\x00" * 4 + text.encode("ascii", errors="replace") + b"\x00"


def mluc_type_payload(records: list[tuple[str, str, str]]) -> bytes:
    """multiLocalizedUnicodeType（'mluc'）payload。

    records 为 (语言码, 国家码, 文本) 列表；记录表每行 12 字节
    （串字节长 u32 + 串偏移 u32 + 保留 u32），记录尺寸固定 12。
    """
    table = b""
    blob = bytearray()
    for lang, country, text in records:
        rec = lang.encode("ascii") + country.encode("ascii") + text.encode("utf-16-be") + b"\x00\x00"
        table += struct.pack(">III", len(rec), 16 + len(records) * 12 + len(blob), 0)
        blob += rec
    return b"mluc" + b"\x00" * 4 + struct.pack(">II", len(records), 12) + table + bytes(blob)


def xyz_type_payload(values: tuple[float, float, float]) -> bytes:
    """XYZType（'XYZ '）payload：单组 s15Fixed16 三元组。"""
    return b"XYZ " + b"\x00" * 4 + b"".join(_s15(v) for v in values)


def curv_type_payload(*, gamma: float | None = None, values: list[float] | None = None) -> bytes:
    """CurveType（'curv'）payload。

    gamma 给定 → count=1（u8Fixed8）；values 给定 → count=len（u16 归一化表）；
    两者皆 None → count=0 恒等。
    """
    if gamma is not None:
        v = max(0, min(0xFFFF, int(round(gamma * 256))))
        return b"curv" + b"\x00" * 4 + struct.pack(">I", 1) + struct.pack(">H", v) + b"\x00\x00"
    if values is not None:
        table = b"".join(struct.pack(">H", max(0, min(0xFFFF, int(round(v * 65535))))) for v in values)
        return b"curv" + b"\x00" * 4 + struct.pack(">I", len(values)) + table
    return b"curv" + b"\x00" * 4 + struct.pack(">I", 0)


def para_type_payload(function_type: int, params: list[float]) -> bytes:
    """ParametricCurveType（'para'）payload：functionType u16 + n×s15Fixed16。"""
    return (
        b"para"
        + b"\x00" * 4
        + struct.pack(">H", function_type)
        + b"\x00\x00"
        + b"".join(_s15(v) for v in params)
    )


def sig_type_payload(signature: bytes) -> bytes:
    """SignatureType（'sig '）payload。"""
    return b"sig " + b"\x00" * 4 + signature.ljust(4, b"\x00")[:4]


def cicp_type_payload(colour_primaries: int, transfer: int, matrix: int, full_range: int) -> bytes:
    """cicpType（'cicp'）payload（20 字节，TECH-DESIGN §6.2 布局）。"""
    return b"cicp" + b"\x00" * 4 + bytes([colour_primaries, transfer, matrix, full_range & 0x01]) + b"\x00" * 8


def mft1_payload(*, in_ch: int = 3, out_ch: int = 3, grid: int = 17) -> bytes:
    """lut8Type（'mft1'）payload（ICC v4 布局）：保留4 + 8/9/10 通道与网格
    + 矩阵@12 + in×256 输入表 + g^in×out CLUT + out×256 输出表。"""
    n_clut = (grid**in_ch) * out_ch
    out = bytearray(b"mft1" + b"\x00" * 4 + bytes([in_ch, out_ch, grid]) + b"\x00")
    out += b"".join(_s15(v) for v in (1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0))
    out += bytes([min(255, i) for i in range(in_ch * 256)])
    out += bytes([128] * n_clut)
    out += bytes([min(255, i) for i in range(out_ch * 256)])
    return bytes(out)


def mft2_payload(
    *,
    in_ch: int = 3,
    out_ch: int = 3,
    grid: int = 9,
    in_entries: int = 256,
    out_entries: int = 256,
) -> bytes:
    """lut16Type（'mft2'）payload（ICC v4 布局）：矩阵@12 + entries@48 + u16 表@52。"""
    n_clut = (grid**in_ch) * out_ch
    out = bytearray(b"mft2" + b"\x00" * 4 + bytes([in_ch, out_ch, grid]) + b"\x00")
    out += b"".join(_s15(v) for v in (1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0))
    out += struct.pack(">HH", in_entries, out_entries)
    out += struct.pack(f">{in_ch * in_entries}H", *([i % 65536 for i in range(in_ch * in_entries)]))
    out += struct.pack(f">{n_clut}H", *([32768] * n_clut))
    out += struct.pack(f">{out_ch * out_entries}H", *([i % 65536 for i in range(out_ch * out_entries)]))
    return bytes(out)


def mab_payload(
    *,
    kind: bytes = b"mAB ",
    in_ch: int = 3,
    out_ch: int = 3,
    grid: tuple[int, ...] = (9, 9, 9),
    with_b: bool = True,
    with_matrix: bool = False,
    with_m: bool = False,
    with_clut: bool = True,
    with_a: bool = True,
    clut_precision_bits: int = 2,
) -> bytes:
    """lutAtoBType/lutBtoAType（'mAB '/'mBA '）payload（结构摘要用）。

    元素物理顺序固定 B → Matrix → M → CLUT → A（偏移表顺序一致，0=无此元素）。
    CLUT 子结构按 ICC v4 规范：每输入通道 1 字节网格点数 + 1 字节精度
    （1=8-bit / 2=16-bit，``clut_precision_bits`` 注入）+ 3 字节保留 + u16 数据。
    """
    header = bytearray(kind + b"\x00" * 4 + bytes([in_ch, out_ch]) + b"\x00\x00")
    body = bytearray()

    def place(element: bytes | None) -> int:
        if not element:
            return 0
        offset = 32 + len(body)
        body.extend(_pad(element))
        return offset

    b_curves = b"".join(curv_type_payload(gamma=2.2) for _ in range(in_ch)) if with_b else None
    m_curves = b"".join(para_type_payload(0, [2.4]) for _ in range(in_ch)) if with_m else None
    a_curves = b"".join(curv_type_payload(gamma=2.2) for _ in range(in_ch)) if with_a else None
    clut_element = None
    if with_clut:
        precision_byte = {8: 1, 16: 2}.get(clut_precision_bits, 2)
        n_data = (grid[0] ** in_ch) * out_ch
        clut_element = (
            bytes(grid[:in_ch])
            + bytes([precision_byte])
            + b"\x00" * 3
            + struct.pack(f">{n_data}H", *([32768] * n_data))
        )
    matrix_element = b"".join(_s15(v) for v in (1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0)) if with_matrix else None

    b_off = place(b_curves)
    mat_off = place(matrix_element)
    m_off = place(m_curves)
    clut_off = place(clut_element)
    a_off = place(a_curves)
    header += struct.pack(">5I", b_off, mat_off, m_off, clut_off, a_off)
    return bytes(header + body)


# ---------------------------------------------------------------------------
# 完整 profile 构造器与变异器
# ---------------------------------------------------------------------------


def build_matrix_trc_profile(
    *,
    version: int = 0x02100000,
    device_class: bytes = b"mntr",
    trc_gamma: float = 2.2,
    with_cicp: tuple[int, ...] | None = None,
    extra_tags: dict[bytes, bytes] | None = None,
) -> bytes:
    """构造最小可用的 v2 矩阵/TRC RGB profile（设计 §7.2 轨 1）。

    Args:
        version:     版本原始 uint32（默认 0x02100000 即 2.1.0）。
        device_class:  设备类 4 字节（默认 ``b"mntr"``）。
        trc_gamma:   TRC gamma（count=1 的 u8Fixed8 curv）。
        with_cicp:   (colour_primaries, transfer, matrix, full_range) → 追加 20 字节
                     cicpType tag；None 不追加。
        extra_tags:  附加 tag {签名: payload}（用于 mAB/mluc 等规则测试注入）。

    Returns:
        完整 profile 字节（header.size 与实际长度一致）。
    """
    payloads: list[tuple[bytes, bytes]] = [
        (b"desc", desc_type_payload("Synthetic Test RGB")),
        (b"cprt", text_type_payload("Public domain")),
        (b"wtpt", xyz_type_payload((0.9642, 1.0, 0.8249))),
        (b"rXYZ", xyz_type_payload((0.4360, 0.2225, 0.0139))),
        (b"gXYZ", xyz_type_payload((0.3851, 0.7169, 0.0971))),
        (b"bXYZ", xyz_type_payload((0.1431, 0.0606, 0.7141))),
        (b"rTRC", curv_type_payload(gamma=trc_gamma)),
    ]
    if with_cicp is not None:
        cp, tc, mc, fr = with_cicp
        payloads.append((b"cicp", cicp_type_payload(cp, tc, mc, fr)))
    if extra_tags:
        for sig, payload in extra_tags.items():
            sig4 = sig if len(sig) == 4 else sig.ljust(4, b"\x00")[:4]
            payloads.append((sig4, payload))

    # 表项固定：desc/cprt/wtpt/rXYZ/gXYZ/bXYZ/rTRC/gTRC/bTRC（gTRC、bTRC 与 rTRC
    # 共享同一 offset/size）+ cicp + 附加 tag。
    n_entries = 9 + len(payloads) - 7
    data_start = 132 + n_entries * 12

    blob = bytearray()
    offsets: dict[bytes, tuple[int, int]] = {}
    for sig, payload in payloads:
        offsets[sig] = (data_start + len(blob), len(payload))
        blob += _pad(payload)

    trc_off, trc_size = offsets[b"rTRC"]
    rows: list[tuple[bytes, int, int]] = [
        (b"desc", *offsets[b"desc"]),
        (b"cprt", *offsets[b"cprt"]),
        (b"wtpt", *offsets[b"wtpt"]),
        (b"rXYZ", *offsets[b"rXYZ"]),
        (b"gXYZ", *offsets[b"gXYZ"]),
        (b"bXYZ", *offsets[b"bXYZ"]),
        (b"rTRC", trc_off, trc_size),
        (b"gTRC", trc_off, trc_size),
        (b"bTRC", trc_off, trc_size),
    ]
    for sig, _payload in payloads[7:]:
        off, size = offsets[sig]
        rows.append((sig, off, size))

    total = data_start + len(blob)
    header = bytearray()
    header += struct.pack(">I", total)
    header += b"\x00" * 4
    header += struct.pack(">I", version)
    header += device_class.ljust(4, b"\x00")[:4]
    header += b"RGB "
    header += b"XYZ "
    header += struct.pack(">6H", 2020, 1, 1, 0, 0, 0)
    header += b"acsp"
    header += b"\x00" * 4
    header += struct.pack(">I", 0)
    header += b"TKIT"
    header += b"TST1"
    header += struct.pack(">II", 0, 0)
    header += struct.pack(">I", 0)
    header += struct.pack(">iii", *D50_FIXED)
    header += b"\x00" * 4
    header += b"\x00" * 16
    header += b"\x00" * 28
    if len(header) != 128:  # pragma: no cover —— 构造器自检
        raise AssertionError(f"header 长度异常：{len(header)}")

    table = b"".join(struct.pack(">4sII", *row) for row in rows)
    return bytes(header) + struct.pack(">I", n_entries) + table + bytes(blob)


def _parse_entries(data: bytes) -> list[tuple[bytes, int, int]]:
    """读取（合法范围内的）tag 表项，供 mutate 重建。"""
    (count,) = struct.unpack_from(">I", data, 128)
    entries: list[tuple[bytes, int, int]] = []
    for i in range(count):
        base = 132 + 12 * i
        if base + 12 > len(data):
            break
        entries.append(struct.unpack_from(">4sII", data, base))
    return entries


def mutate(
    data: bytes,
    *,
    truncate: int | None = None,
    flip_magic: bool = False,
    overlap_tags: bool = False,
    remove_tag: bytes | str | None = None,
) -> bytes:
    """按需变异 profile 字节（TECH-DESIGN §7.2 mutate）。

    Args:
        truncate:     截断到前 n 字节。
        flip_magic:   破坏偏移 36 的魔数（→ ``XXXX``）。
        overlap_tags: 令第 2 个 tag 表项指向第 1 个 tag 的存储区间（制造重叠）。
        remove_tag:   从 tag 表移除指定签名（数据块重排、header.size 回填）。

    Returns:
        变异后的字节（不修改入参）。
    """
    out = bytes(data)
    if truncate is not None:
        out = out[:truncate]
    if flip_magic:
        out = out[:36] + b"XXXX" + out[40:]
    if overlap_tags:
        entries = _parse_entries(out)
        if len(entries) >= 2:
            sig1, _off1, _size1 = entries[1]
            _sig0, off0, size0 = entries[0]
            rebuilt = list(entries)
            rebuilt[1] = (sig1, off0, size0)
            table = b"".join(struct.pack(">4sII", *row) for row in rebuilt)
            out = out[:128] + struct.pack(">I", len(rebuilt)) + table + out[132 + len(entries) * 12 :]
    if remove_tag is not None:
        sig4 = remove_tag if isinstance(remove_tag, bytes) else remove_tag.encode("ascii")
        sig4 = sig4.ljust(4, b"\x00")[:4]
        keep = [row for row in _parse_entries(out) if row[0] != sig4]
        data_start = 132 + len(keep) * 12
        blob = bytearray()
        new_rows: list[tuple[bytes, int, int]] = []
        for sig, off, size in keep:
            payload = out[off : off + size]
            new_rows.append((sig, data_start + len(blob), size))
            blob += _pad(payload)
        header = bytearray(out[:128])
        struct.pack_into(">I", header, 0, data_start + len(blob))
        out = (
            bytes(header)
            + struct.pack(">I", len(keep))
            + b"".join(struct.pack(">4sII", *row) for row in new_rows)
            + bytes(blob)
        )
    return out


def require_system_profile(name: str) -> pathlib.Path:
    """取真实系统 profile 路径；不存在则 ``pytest.skip``（Linux CI 整体跳过）。

    Args:
        name: :data:`SYSTEM_PROFILES` 的键（如 ``"sRGB"``）。

    Returns:
        存在的 profile 路径。
    """
    import pytest

    path = pathlib.Path(SYSTEM_PROFILES[name])
    if not path.exists():
        pytest.skip(f"system profile not present: {path}")
    return path
