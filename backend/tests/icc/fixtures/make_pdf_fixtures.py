"""PDF 色彩审计 fixture 生成脚本（TECH-DESIGN §7.4）。

程序化生成、不留二进制。产物共 5 份：

====================  =====================================================
with_oi.pdf           RGB 图像页 + GTS_PDFA1 OutputIntent
                      （DestOutputProfile 为内置合成 RGB ICC，含 DigestValue）
no_oi.pdf             RGB 图像页，无 OutputIntent（warning 路径）
cmyk_spots.pdf        手工构造：DeviceCMYK 图像 + Separation SpotA/SpotB
                      （SpotB /Alternate 为 Lab）+ DeviceN（SpotA/SpotC）
edge_cases.pdf        嵌套 Form XObject（内含 DeviceGray 图像）+ 悬空
                      ColorSpace 引用 + 损坏 ICCBased 流（warning 路径合集）
encrypted.pdf         加密 PDF（R=6 / AES-256，user/owner 口令均为 "x"）
====================  =====================================================

说明：
- 合成 RGB ICC profile 在本文件内用 struct 自包含构造（v2 矩阵/TRC 最小体，
  与设计 §7.2 轨 1 ``build_matrix_trc_profile`` 等价），不依赖并行开发中的
  ``tests/fixtures/synthetic.py``，保证本脚本可独立运行。
- Pillow 生成 RGB PDF（图像 XObject 走 /DeviceRGB，无 OutputIntent）；
  pikepdf 负责手工构造最小 PDF 与 OutputIntent/Separation/DeviceN。

CLI 用法::

    python -X utf8 tests/fixtures/make_pdf_fixtures.py <outdir>
"""

from __future__ import annotations

import pathlib
import struct
import sys

import pikepdf
from PIL import Image

__all__ = [
    "build_rgb_icc_profile",
    "make_rgb_pdf",
    "make_cmyk_spots_pdf",
    "make_edge_cases_pdf",
    "make_encrypted_pdf",
    "generate_all",
    "main",
]

ENCRYPTED_PASSWORD = "x"

# ICC v2 最小 RGB 矩阵/TRC profile 所需常量（sRGB D50 矩阵系数，见设计 §7.2）
_D50 = (0.9642, 1.0, 0.8249)
_RXYZ = (0.4360, 0.2225, 0.0139)
_GXYZ = (0.3851, 0.7169, 0.0971)
_BXYZ = (0.1431, 0.0606, 0.7141)


def _s15f16(value: float) -> bytes:
    """ICCs15Fixed16 编码（>i，65536 缩放）。"""
    return struct.pack(">i", int(round(value * 65536.0)))


def _tag_desc(text: str) -> bytes:
    """textDescriptionType（'desc'）完整布局：ASCII + 空 Unicode + 空 Mac。"""
    ascii_blob = text.encode("ascii") + b"\x00"
    out = b"desc" + b"\x00" * 4 + struct.pack(">I", len(ascii_blob)) + ascii_blob
    out += struct.pack(">I", 0)  # Unicode language code
    out += struct.pack(">H", 0)  # Unicode script code
    out += struct.pack(">I", 0)  # Unicode count（无 Unicode 串）
    out += struct.pack(">H", 0)  # Macintosh script code
    out += struct.pack(">B", 0)  # Macintosh count
    out += b"\x00" * 67          # Macintosh string（固定 67 字节）
    return out


def _tag_text(text: str) -> bytes:
    """textType（'text'）：7-bit ASCII + 终止 \\0。"""
    return b"text" + b"\x00" * 4 + text.encode("ascii") + b"\x00"


def _tag_xyz(values: tuple[float, float, float]) -> bytes:
    """XYZType（'XYZ '）：保留 4 字节 + 3×s15Fixed16。"""
    return b"XYZ " + b"\x00" * 4 + b"".join(_s15f16(v) for v in values)


def _tag_curv_gamma(gamma: float) -> bytes:
    """curveType（'curv'）：count=1 → u8Fixed8 gamma（2.2 → 0x0233）。"""
    return b"curv" + b"\x00" * 4 + struct.pack(">IH", 1, int(round(gamma * 256.0)))


def build_rgb_icc_profile(description: str = "Synthetic Test RGB",
                          gamma: float = 2.2) -> bytes:
    """构造最小可解析的 v2 RGB 矩阵/TRC ICC profile（约 0.5 KB）。

    布局遵循设计 §7.2 轨 1：header 128 字节 + 9 项 tag 表
    （gTRC/bTRC 与 rTRC 共享同一 offset/size，即共享 tag 白名单路径）。
    供 OutputIntent /DestOutputProfile 与 ICCBased 流使用。

    参数:
        description: 'desc' tag 文本。
        gamma: TRC gamma（u8Fixed8 编码）。
    返回:
        bytes: 完整 profile 字节。
    """
    curv = _tag_curv_gamma(gamma)
    unique_tags: list[tuple[bytes, bytes]] = [
        (b"desc", _tag_desc(description)),
        (b"cprt", _tag_text("Public domain")),
        (b"wtpt", _tag_xyz(_D50)),
        (b"rXYZ", _tag_xyz(_RXYZ)),
        (b"gXYZ", _tag_xyz(_GXYZ)),
        (b"bXYZ", _tag_xyz(_BXYZ)),
        (b"rTRC", curv),
    ]
    # tag 表共 9 项：gTRC/bTRC 复用 rTRC 的 offset/size
    tag_count = len(unique_tags) + 2
    table_size = 4 + 12 * tag_count

    header = b"".join([
        struct.pack(">I", 0),            # 0-3   size 占位，最后回填
        b"\x00" * 4,                     # 4-7   preferred CMM
        struct.pack(">I", 0x02100000),   # 8-11  version 2.1.0
        b"mntr",                         # 12-15 device class
        b"RGB ",                         # 16-19 color space
        b"XYZ ",                         # 20-23 PCS
        struct.pack(">6H", 2020, 1, 1, 0, 0, 0),  # 24-35 date
        b"acsp",                         # 36-39 magic
        b"\x00" * 4,                     # 40-43 platform
        struct.pack(">I", 0),            # 44-47 flags
        b"TKIT",                         # 48-51 manufacturer
        b"TST1",                         # 52-55 model
        b"\x00" * 8,                     # 56-63 attributes
        struct.pack(">I", 0),            # 64-67 rendering intent
        _s15f16(_D50[0]), _s15f16(_D50[1]), _s15f16(_D50[2]),  # 68-79 illuminant
        b"\x00" * 4,                     # 80-83 creator
        b"\x00" * 16,                    # 84-99 profile id
        b"\x00" * 28,                    # 100-127 reserved
    ])
    assert len(header) == 128

    entries: list[tuple[bytes, int, int]] = []
    body_parts: list[bytes] = []
    cursor = 128 + table_size
    for sig, data in unique_tags:
        pad = (-cursor) % 4
        if pad:
            body_parts.append(b"\x00" * pad)
            cursor += pad
        entries.append((sig, cursor, len(data)))
        body_parts.append(data)
        cursor += len(data)
    # gTRC/bTRC 共享 rTRC
    r_off, r_size = entries[-1][1], entries[-1][2]
    entries.append((b"gTRC", r_off, r_size))
    entries.append((b"bTRC", r_off, r_size))

    table = struct.pack(">I", tag_count)
    table += b"".join(struct.pack(">4sII", sig, off, size) for sig, off, size in entries)

    profile = header + table + b"".join(body_parts)
    return struct.pack(">I", len(profile)) + profile[4:]


def make_rgb_pdf(out_path: pathlib.Path, *, with_output_intent: bool = True,
                 with_digest: bool = True) -> pathlib.Path:
    """Pillow 生成 RGB 图像页 PDF；可选补 GTS_PDFA1 OutputIntent。

    Pillow 存 PDF 走 DeviceRGB 图像 XObject（/ColorSpace /DeviceRGB 直接标在
    图像上，无 Resources/ColorSpace 字典、无 OutputIntent），再由 pikepdf
    按设计 §7.4 注入 OutputIntent。

    参数:
        out_path: 输出路径。
        with_output_intent: False 时返回无 OutputIntent 的版本（warning 路径）。
        with_digest: OutputIntent 是否携带 /DigestMethod + /DigestValue。
    返回:
        pathlib.Path: out_path 本身。
    """
    Image.new("RGB", (64, 64), (200, 30, 40)).save(out_path, format="PDF")
    if not with_output_intent:
        return out_path
    icc_bytes = build_rgb_icc_profile()
    with pikepdf.open(out_path, allow_overwriting_input=True) as pdf:
        stream = pdf.make_stream(icc_bytes)
        stream.N = 3
        stream.Range = pikepdf.Array([0, 1, 0, 1, 0, 1])
        stream.Alternate = pikepdf.Name.DeviceRGB
        intent_kwargs: dict[str, object] = {}
        if with_digest:
            intent_kwargs["DigestMethod"] = pikepdf.Name.MD5
            intent_kwargs["DigestValue"] = pikepdf.String(bytes(range(16)))
        oi = pikepdf.Dictionary(
            Type=pikepdf.Name.OutputIntent,
            S=pikepdf.Name.GTS_PDFA1,
            OutputCondition=pikepdf.String("Synthetic RGB rendering condition"),
            OutputConditionIdentifier=pikepdf.String("SyntheticRGB"),
            RegistryName=pikepdf.String("http://www.color.org"),
            DestOutputProfile=stream,
            **intent_kwargs,  # type: ignore[arg-type]
        )
        pdf.Root.OutputIntents = pikepdf.Array([oi])
        pdf.save(out_path)
    return out_path


def _make_stream_object(pdf: pikepdf.Pdf, data: bytes, *, subtype: pikepdf.Name,
                        color_space: object, width: int, height: int,
                        bpc: int = 8) -> pikepdf.Stream:
    stream = pdf.make_stream(data)
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = subtype
    if color_space is not None:
        stream.ColorSpace = color_space
    stream.Width = width
    stream.Height = height
    stream.BitsPerComponent = bpc
    return stream


def _separation(name: str, alternate: object, tint: pikepdf.Stream) -> pikepdf.Array:
    return pikepdf.Array([pikepdf.Name.Separation,
                          pikepdf.Name(f"/{name}"),
                          alternate, tint])


def make_cmyk_spots_pdf(out_path: pathlib.Path) -> pathlib.Path:
    """手工构造 CMYK + 专色最小 PDF（设计 §7.4 第 3 步的扩展版）。

    页面资源：
    - /Im0: DeviceCMYK 图像（4×4×4 字节原始数据）。
    - /CS0: [/Separation /SpotA /DeviceGray <FunctionType 2 流>]
    - /CS1: [/Separation /SpotB [/Lab 0 100 -128 127 -128 127] <FunctionType 2 流>]
      （tint=1.0 → C1=[50, 70, -30]，即 SpotB 的 Lab 外观值）
    - /CS2: [/DeviceN [/SpotA /SpotC] /DeviceGray <FunctionType 4 流>]
    内容流引用各资源（审计 v1 只盘点资源级，不解析内容流）。

    返回:
        pathlib.Path: out_path 本身。
    """
    pdf = pikepdf.new()
    w = h = 4
    img_data = bytes((i * 7 + 3) % 256 for i in range(w * h * 4))
    img = _make_stream_object(pdf, img_data, subtype=pikepdf.Name.Image,
                              color_space=pikepdf.Name.DeviceCMYK,
                              width=w, height=h)

    tint_gray = pikepdf.Stream(pdf, b"", FunctionType=2,
                               Domain=pikepdf.Array([0, 1]),
                               Range=pikepdf.Array([0, 1]),
                               C0=pikepdf.Array([0]),
                               C1=pikepdf.Array([1]), N=1)
    tint_lab = pikepdf.Stream(pdf, b"", FunctionType=2,
                              Domain=pikepdf.Array([0, 1]),
                              Range=pikepdf.Array([0, 100, -128, 127, -128, 127]),
                              C0=pikepdf.Array([0, 0, 0]),
                              C1=pikepdf.Array([50, 70, -30]), N=1)
    tint_devn = pikepdf.Stream(pdf, b"{ pop pop 0.5 }", FunctionType=4,
                               Domain=pikepdf.Array([0, 1, 0, 1]),
                               Range=pikepdf.Array([0, 1]))

    cs0 = _separation("SpotA", pikepdf.Name.DeviceGray, tint_gray)
    cs1 = pikepdf.Array([
        pikepdf.Name.Separation,
        pikepdf.Name("/SpotB"),
        pikepdf.Array([pikepdf.Name.Lab, 0, 100, -128, 127, -128, 127]),
        tint_lab,
    ])
    cs2 = pikepdf.Array([
        pikepdf.Name.DeviceN,
        pikepdf.Array([pikepdf.Name("/SpotA"), pikepdf.Name("/SpotC")]),
        pikepdf.Name.DeviceGray,
        tint_devn,
    ])

    page = pdf.add_blank_page(page_size=(200, 200))
    content = (b"q 100 0 0 100 20 20 cm /Im0 Do Q\n"
               b"/CS0 cs 0.5 scn 130 20 60 60 re f\n"
               b"/CS1 cs 0.2 0.8 scn 20 130 60 60 re f\n"
               b"/CS2 cs 0.2 0.8 scn 130 130 60 60 re f\n")
    page.Contents = pdf.make_stream(content)
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=img),
        ColorSpace=pikepdf.Dictionary(CS0=cs0, CS1=cs1, CS2=cs2),
    )
    pdf.save(out_path)
    return out_path


def make_edge_cases_pdf(out_path: pathlib.Path) -> pathlib.Path:
    """构造审计边界用例 PDF（全部 warning 路径 + 嵌套 XObject 遍历）。

    页面资源：
    - /Im0: DeviceRGB 直接图像
    - /Fm0: Form XObject，内含 /Im1（DeviceGray 图像）→ 测嵌套递归
    - /Im2: /ColorSpace /NoSuchCS（悬空引用 → warning）
    - /Im3: [/ICCBased <64 字节非 ICC 流>] → ICC 流损坏 → warning

    返回:
        pathlib.Path: out_path 本身。
    """
    pdf = pikepdf.new()
    img_rgb = _make_stream_object(pdf, bytes(4 * 4 * 3), subtype=pikepdf.Name.Image,
                                  color_space=pikepdf.Name.DeviceRGB, width=4, height=4)
    img_gray = _make_stream_object(pdf, bytes(4 * 4), subtype=pikepdf.Name.Image,
                                   color_space=pikepdf.Name.DeviceGray, width=4, height=4)
    img_dangle = _make_stream_object(pdf, bytes(4 * 4 * 3), subtype=pikepdf.Name.Image,
                                     color_space=pikepdf.Name("/NoSuchCS"),
                                     width=4, height=4)
    bad_icc = pdf.make_stream(b"\x00" * 64)
    bad_icc.N = 3
    img_icc = _make_stream_object(
        pdf, bytes(4 * 4 * 3), subtype=pikepdf.Name.Image,
        color_space=pikepdf.Array([pikepdf.Name.ICCBased, bad_icc]),
        width=4, height=4)

    form = pdf.make_stream(b"q 50 0 0 50 25 25 cm /Im1 Do Q")
    form.Type = pikepdf.Name.XObject
    form.Subtype = pikepdf.Name.Form
    form.BBox = pikepdf.Array([0, 0, 100, 100])
    form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=img_gray))

    page = pdf.add_blank_page(page_size=(200, 200))
    page.Contents = pdf.make_stream(
        b"q 50 0 0 50 10 10 cm /Im0 Do Q "
        b"q 50 0 0 50 60 10 cm /Fm0 Do Q "
        b"q 50 0 0 50 10 60 cm /Im2 Do Q "
        b"q 50 0 0 50 60 60 cm /Im3 Do Q Q")
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=img_rgb, Fm0=form, Im2=img_dangle, Im3=img_icc),
        ColorSpace=pikepdf.Dictionary(),
    )
    pdf.save(out_path)
    return out_path


def make_encrypted_pdf(out_path: pathlib.Path,
                       password: str = ENCRYPTED_PASSWORD) -> pathlib.Path:
    """生成加密 PDF（R=6 / AES-256，user 与 owner 口令相同）。

    异常路径 fixture：无口令打开 → pikepdf.PasswordError → PdfAuditError
    (code="PDF_ENCRYPTED")；携带口令可正常审计且 encrypted=True。

    返回:
        pathlib.Path: out_path 本身。
    """
    Image.new("RGB", (32, 32), (10, 120, 220)).save(out_path, format="PDF")
    with pikepdf.open(out_path, allow_overwriting_input=True) as pdf:
        pdf.save(out_path,
                 encryption=pikepdf.Encryption(owner=password, user=password, R=6))
    return out_path


def generate_all(outdir: str | pathlib.Path) -> dict[str, pathlib.Path]:
    """生成全部 fixture，返回 {名称: 路径}（键名即测试引用名）。"""
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    return {
        "with_oi": make_rgb_pdf(out / "with_oi.pdf", with_output_intent=True),
        "no_oi": make_rgb_pdf(out / "no_oi.pdf", with_output_intent=False),
        "cmyk_spots": make_cmyk_spots_pdf(out / "cmyk_spots.pdf"),
        "edge_cases": make_edge_cases_pdf(out / "edge_cases.pdf"),
        "encrypted": make_encrypted_pdf(out / "encrypted.pdf"),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：python -X utf8 tests/fixtures/make_pdf_fixtures.py <outdir>。"""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -X utf8 make_pdf_fixtures.py <outdir>", file=sys.stderr)
        return 2
    outdir = pathlib.Path(args[0])
    for name, path in generate_all(outdir).items():
        print(f"{name:12s} -> {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
