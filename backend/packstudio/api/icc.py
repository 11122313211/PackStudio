"""ICC 色彩 API：profile 扫描 / 图像转换 / 软打样 / 越界掩图 / 取色 / 校验 / 测量验证。"""

from __future__ import annotations

import dataclasses
import io
import math
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, request
from PIL import Image

from packstudio.api.common import (
    ApiError,
    _UPLOAD_DIR,
    ok,
    save_upload,
    store_output,
    validate_out_format,
)
from packstudio.engines.color_engine.cmm import discovery, engine as cmm_engine
from packstudio.engines.color_engine.errors import IccToolkitError
from packstudio.engines.color_engine.imaging.pipeline import ProfileOp, convert_image, process_file

bp = Blueprint("icc", __name__)

_INTENTS = {"perceptual": 0, "relative": 1, "saturation": 2, "absolute": 3}
#: 软打样安全像素上限（防止超大快照 OOM 或长阻塞）
_MAX_PROOF_PIXELS = 4_000_000
#: 允许通过路径访问的 ICC 档案后缀白名单
_ALLOWED_PROFILE_SUFFIXES: frozenset[str] = discovery._PROFILE_SUFFIXES | {".pf", ".prof"}


def _allowed_profile_roots() -> list[Path]:
    """返回允许通过路径参数访问的 ICC 根目录列表（系统目录 + 临时目录 + 可选测试扩展目录）。"""
    roots: list[Path] = []
    for d in discovery.system_profile_dirs():
        try:
            roots.append(Path(d).resolve())
        except OSError:
            continue
    for d in (_UPLOAD_DIR, Path(tempfile.gettempdir())):
        try:
            roots.append(d.resolve())
        except OSError:
            continue
    extra = current_app.config.get("EXTRA_PROFILE_DIRS") if current_app else None
    if extra:
        for d in extra:
            try:
                roots.append(Path(d).resolve())
            except OSError:
                continue
    return roots


def _resolve_profile_path(raw: str) -> str:
    """校验并返回白名单内的规范化 ICC 路径，阻断任意本机文件探测。"""
    cleaned = (raw or "").strip()
    if not cleaned or "\x00" in cleaned:
        raise ApiError("INVALID_PROFILE_PATH", "非法的 profile 路径。", status=403)
    p = Path(cleaned)
    if p.suffix.lower() not in _ALLOWED_PROFILE_SUFFIXES:
        raise ApiError(
            "INVALID_PROFILE_PATH",
            f"仅允许访问 ICC 档案文件（不支持后缀：{p.suffix or '<无>'}）。",
            status=403,
        )
    try:
        resolved = p.resolve()
    except OSError as exc:
        raise ApiError("INVALID_PROFILE_PATH", f"无法解析 profile 路径：{exc}", status=403) from exc

    if not any(resolved.is_relative_to(root) for root in _allowed_profile_roots()):
        raise ApiError(
            "INVALID_PROFILE_PATH",
            "仅允许访问系统 ICC 目录或临时上传目录中的档案。",
            status=403,
        )
    return str(resolved)


def _peek_profile_space(profile: str | bytes) -> str:
    """快速读取 ICC 头部 16..20 字节数据色空间（如 RGB / CMYK / GRAY），失败返回空串。"""
    try:
        if isinstance(profile, bytes):
            header = profile[:20]
        else:
            with Path(profile).open("rb") as fh:
                header = fh.read(20)
        if len(header) >= 20:
            return header[16:20].decode("ascii", errors="ignore").strip().upper()
    except OSError:
        pass
    return ""


@bp.get("/api/icc/profiles")
def list_profiles():
    """扫描系统 ICC profile（带缓存；传入 ?refresh=1 强制重新扫描）。"""
    refresh = (request.args.get("refresh") or "").strip().lower() in ("1", "true", "yes")
    if refresh:
        discovery.scan_cache_clear()
    found = discovery.scan_profiles()
    return ok({"profiles": [dataclasses.asdict(p) for p in found]})


def _intent_from(value: str | None) -> cmm_engine.RenderingIntent:
    key = (value or "relative").lower()
    if key not in _INTENTS:
        raise ApiError("INVALID_INTENT", f"不支持的渲染意图：{value}")
    return cmm_engine.RenderingIntent(_INTENTS[key])


def _load_image_from_request(field: str = "file") -> Image.Image:
    fs = request.files.get(field)
    if fs is None:
        raise ApiError("MISSING_FILE", "缺少上传图像。")
    try:
        return Image.open(io.BytesIO(fs.read()))
    except Exception as exc:  # noqa: BLE001 - 统一转稳定码
        raise ApiError("IMAGE_DECODE", "无法解码上传图像。", detail={"reason": str(exc)}) from exc


def _srgb_profile_bytes() -> bytes:
    """内置 sRGB 显示 profile（lcms 合成，避免依赖系统文件存在）。"""
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _profile_arg(value: str | None, *, allow_builtin: bool = False):
    """把请求里的 profile 参数解析为经白名单校验的路径或内置 sRGB bytes。"""
    if value and value.strip():
        return _resolve_profile_path(value)
    if allow_builtin:
        return _srgb_profile_bytes()
    raise ApiError("MISSING_PROFILE", "缺少 profile。")


@bp.post("/api/icc/convert")
def convert():
    """图像 profile 操作（assign/embed/strip/convert），结果落为可下载文件。

    - 若 op=convert 且源 RGB 图未嵌入 ICC、调用方未指定 src_profile，自动以内置 sRGB 作为源档案；
    - 若目标 profile 为 CMYK 且请求 png/未指定格式，自动输出真 CMYK TIFF（避免 Pillow CMYK PNG 失败），
      同时返回回转 sRGB 的 ``preview`` PNG 供浏览器画布展示。
    """
    fs = request.files.get("file")
    if fs is None:
        raise ApiError("MISSING_FILE", "缺少上传图像。")
    op = (request.form.get("op") or "").strip().lower()
    try:
        profile_op = ProfileOp(op)
    except ValueError as exc:
        raise ApiError("INVALID_OP", f"不支持的操作：{op}（可选 assign/embed/strip/convert）") from exc
    raw_profile_path = (request.form.get("profile_path") or "").strip()
    if op in ("assign", "embed", "convert") and not raw_profile_path:
        raise ApiError("MISSING_PROFILE", "该操作需要提供 profile_path。")
    profile_path = _resolve_profile_path(raw_profile_path) if raw_profile_path else ""

    raw_src_profile = (request.form.get("src_profile") or "").strip()
    src_profile_arg: str | bytes | None = _resolve_profile_path(raw_src_profile) if raw_src_profile else None

    target_space = _peek_profile_space(profile_path) if profile_path else ""
    req_fmt = (request.form.get("out_format") or "").strip()
    out_fmt = validate_out_format(req_fmt, default="tif") if req_fmt else "tif"
    if op == "convert" and target_space == "CMYK" and out_fmt == "png":
        # Pillow 不支持把 CMYK 写出为 PNG，自动切到无损 CMYK TIFF
        out_fmt = "tif"

    intent = _intent_from(request.form.get("intent"))
    bpc = (request.form.get("bpc") or "false").lower() == "true"

    orig_ext = "." + (fs.filename or "img.png").rsplit(".", 1)[-1]
    src = save_upload(fs, suffix=orig_ext)
    tmp_out = src.parent / ("out_" + src.stem + "." + out_fmt)
    assumed_src_srgb = False

    try:
        # 浏览器画布导出的 PNG 通常不带内嵌 ICC；对无嵌入 RGB 图默认视作 sRGB，避免报 NO_SOURCE_PROFILE
        if op == "convert" and src_profile_arg is None:
            try:
                with Image.open(src) as probe_im:
                    if not probe_im.info.get("icc_profile") and probe_im.mode in ("RGB", "RGBA", "RGBX", "P"):
                        src_profile_arg = _srgb_profile_bytes()
                        assumed_src_srgb = True
            except OSError:
                pass

        result = process_file(
            src,
            tmp_out,
            profile_op,
            profile=profile_path or None,
            src_profile=src_profile_arg,
            intent=intent,
            bpc=bpc,
            out_format=out_fmt,
        )
        if not result.ok:
            raise ApiError(result.error_code or "CONVERT_FAILED", result.message or "转换失败", status=422)

        out_name = f"converted.{out_fmt}"
        data = tmp_out.read_bytes()
        output_id = store_output(out_name, data)

        # 浏览器无法直接用 createImageBitmap 解码 CMYK 或 TIFF：直接从内存字节解码生成 sRGB PNG 预览（避免 Windows 文件句柄占用）
        preview_info: dict[str, str] | None = None
        if out_fmt in ("tif", "tiff") or target_space == "CMYK":
            try:
                with Image.open(io.BytesIO(data)) as out_im:
                    out_im.load()
                    if out_im.mode == "CMYK" and profile_path:
                        prev_im = convert_image(
                            out_im,
                            _srgb_profile_bytes(),
                            profile_path,
                            intent=cmm_engine.RenderingIntent.RELATIVE_COLORIMETRIC,
                            bpc=bpc,
                        ).convert("RGB")
                    else:
                        prev_im = out_im.convert("RGBA" if "A" in out_im.mode else "RGB")
                    pbuf = io.BytesIO()
                    prev_im.save(pbuf, format="PNG")
                    prev_id = store_output("converted_preview.png", pbuf.getvalue())
                    preview_info = {"id": prev_id, "filename": "converted_preview.png"}
            except Exception:  # noqa: BLE001 - 预览降级不影响主产物交付
                preview_info = None

        return ok(
            {
                "id": output_id,
                "filename": out_name,
                "color_space": target_space or "",
                "assumed_src_srgb": assumed_src_srgb,
                "preview": preview_info,
                "report": dataclasses.asdict(result),
            }
        )
    finally:
        for tmp_path in (src, tmp_out):
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


@bp.post("/api/icc/proof")
def proof():
    """画布打样模拟 + 越界掩图。

    画布快照是 sRGB 显示数据，因此采用往返语义：
    1. 打样预览：sRGB → 设备 profile（用户意图，色域压缩）→ sRGB 展示；
    2. 越界掩图：sRGB → 设备（相对比色）后量 Lab 色差，默认使用 ISO 标准
       ``ΔE00``（CIEDE2000，与 ④ 测量验证口径一致；传 ``metric=de76`` 可兼容旧欧氏口径），
       超阈值的像素标记为透明底红色告警掩图。
    """
    im = _load_image_from_request().convert("RGB")
    device = _profile_arg(request.form.get("device_profile"), allow_builtin=True)
    intent = _intent_from(request.form.get("intent"))
    bpc = (request.form.get("bpc") or "true").lower() == "true"
    metric = (request.form.get("metric") or "de00").strip().lower()
    if metric not in ("de00", "de76"):
        raise ApiError("INVALID_METRIC", f"不支持的色差指标：{metric}（可选 de00 / de76）")
    try:
        threshold = float(request.form.get("oog_threshold") or 4.0)
    except ValueError as exc:
        raise ApiError("INVALID_THRESHOLD", "oog_threshold 必须是数字。") from exc
    threshold = min(max(threshold, 0.5), 20.0)

    # 安全像素上限保护：避免超高分辨率快照耗尽内存或阻塞请求
    w, h = im.size
    scaled = False
    if w * h > _MAX_PROOF_PIXELS:
        ratio = math.sqrt(_MAX_PROOF_PIXELS / float(w * h))
        im = im.resize((max(1, int(round(w * ratio))), max(1, int(round(h * ratio)))), Image.Resampling.LANCZOS)
        w, h = im.size
        scaled = True

    srgb = _srgb_profile_bytes()
    try:
        # 1) 打样预览（往返，按用户意图做色域压缩）
        device_im = convert_image(im, device, srgb, intent=intent, bpc=bpc)
        preview = convert_image(
            device_im,
            srgb,
            device,
            intent=cmm_engine.RenderingIntent.RELATIVE_COLORIMETRIC,
            bpc=bpc,
        )
        # 2) 越界掩图：固定相对比色（当预览意图本身为相对比色时直接复用，省去 1/3 变换耗时）
        if intent == cmm_engine.RenderingIntent.RELATIVE_COLORIMETRIC:
            dev_rel = device_im
        else:
            dev_rel = convert_image(
                im,
                device,
                srgb,
                intent=cmm_engine.RenderingIntent.RELATIVE_COLORIMETRIC,
                bpc=bpc,
            )
        lab_src = cmm_engine.transform_lab(srgb, im)
        lab_dev = cmm_engine.transform_lab(device, dev_rel)
    except IccToolkitError as exc:
        raise ApiError(exc.code, str(exc), status=422, detail=getattr(exc, "detail", None)) from exc

    import numpy

    if metric == "de00":
        from packstudio.engines.color_engine.measurement.verify import delta_e_00

        delta_e = delta_e_00(lab_src, lab_dev)
    else:
        delta_e = numpy.sqrt(((lab_src - lab_dev) ** 2).sum(axis=1))

    oog = delta_e > threshold
    # 透明底 + 红色告警像素：叠加时不再刷白底层内容
    mask = numpy.zeros((h, w, 4), dtype=numpy.uint8)
    mask[oog.reshape(h, w)] = (230, 51, 51, 255)
    oog_im = Image.fromarray(mask, mode="RGBA")

    buf = io.BytesIO()
    preview.convert("RGB").save(buf, format="PNG")
    proof_id = store_output("proof.png", buf.getvalue())
    buf = io.BytesIO()
    oog_im.save(buf, format="PNG")
    oog_id = store_output("out_of_gamut.png", buf.getvalue())
    return ok(
        {
            "proof": {"id": proof_id, "filename": "proof.png"},
            "out_of_gamut": {"id": oog_id, "filename": "out_of_gamut.png"},
            "stats": {
                "oog_count": int(oog.sum()),
                "oog_ratio": float(oog.mean()),
                "delta_e_max": round(float(delta_e.max()), 2),
                "delta_e_mean": round(float(delta_e.mean()), 3),
                "threshold": threshold,
                "metric": metric,
                "scaled": scaled,
            },
        }
    )


@bp.post("/api/icc/lab")
def pixel_lab():
    """取色换算：sRGB 像素值 → 输出设备空间（RGB/CMYK/Gray）→ Lab。

    先经 CMM 将 sRGB 转换到目标输出 profile 的设备色空间，再计算设备空间下的
    CIELAB，彻底解决 CMYK/Gray 输出档案下直接传 RGB 像素报 422 的缺陷，
    并同步返回目标设备通道值（CMYK 含总墨量 TAC%）。
    """
    payload = request.get_json(silent=True) or {}
    rgb = payload.get("rgb")
    if not isinstance(rgb, list) or len(rgb) != 3:
        raise ApiError("INVALID_RGB", "rgb 必须是 [r, g, b]。")
    try:
        values = [max(0, min(255, int(v))) for v in rgb]
    except (TypeError, ValueError) as exc:
        raise ApiError("INVALID_RGB", "rgb 必须是整数。") from exc

    raw_profile = payload.get("profile")
    profile = _profile_arg(raw_profile, allow_builtin=True)
    intent = _intent_from(payload.get("intent"))
    bpc = bool(payload.get("bpc", True))
    srgb = _srgb_profile_bytes()
    srgb_im = Image.new("RGB", (1, 1), tuple(values))

    try:
        if raw_profile and str(raw_profile).strip():
            dev_im = convert_image(srgb_im, profile, srgb, intent=intent, bpc=bpc)
            lab = cmm_engine.transform_lab(profile, dev_im, intent=intent, bpc=bpc)
        else:
            dev_im = srgb_im
            lab = cmm_engine.transform_lab(srgb, srgb_im, intent=intent, bpc=bpc)
    except IccToolkitError as exc:
        raise ApiError(exc.code, str(exc), status=422) from exc

    row = lab[0]
    px = dev_im.getpixel((0, 0))
    color_space = dev_im.mode
    device_values: list[float]
    tac: float | None = None
    if color_space == "CMYK" and isinstance(px, tuple) and len(px) == 4:
        device_values = [round(v / 255.0 * 100.0, 1) for v in px]
        tac = round(sum(device_values), 1)
    elif color_space == "L":
        val = px[0] if isinstance(px, tuple) else int(px)
        device_values = [round(val / 255.0 * 100.0, 1)]
        color_space = "GRAY"
    else:
        device_values = [int(v) for v in (px[:3] if isinstance(px, tuple) else values)]

    return ok(
        {
            "lab": [round(float(v), 3) for v in (row[0], row[1], row[2])],
            "color_space": color_space,
            "device_values": device_values,
            "tac": tac,
        }
    )


# ---------------------------------------------------------------------------
# 档案质量校验（色彩闭环 ③：档案入库前的结构体检）
# ---------------------------------------------------------------------------


@bp.get("/api/icc/validate")
def validate_by_path():
    """按扫描路径校验 profile 结构（19 条规则 + 必需 tag 表）。

    Query: ``path`` — /api/icc/profiles 返回的 profile 路径（须在白名单内）。
    校验失败（结构问题）不报错，以 report.issues 形式返回；
    只有文件不可读/不可解析才以稳定机器码 422 透出。
    """
    path_str = (request.args.get("path") or "").strip()
    if not path_str:
        raise ApiError("MISSING_PROFILE", "缺少 profile 路径参数 path。")
    safe_path = _resolve_profile_path(path_str)
    from packstudio.engines.color_engine.icc.validate import validate_profile

    report = validate_profile(safe_path)  # IO 失败由引擎错误统一映射 422
    return ok({"report": report.as_json()})


@bp.post("/api/icc/validate")
def validate_by_upload():
    """校验上传的 profile 字节（multipart ``file``）或 form ``path`` 指向的文件。"""
    path_str = (request.form.get("path") or "").strip()
    fs = request.files.get("file")
    if fs is not None:
        from packstudio.engines.color_engine.icc.validate import validate_bytes

        return ok({"report": validate_bytes(fs.read()).as_json(), "filename": fs.filename})
    if path_str:
        safe_path = _resolve_profile_path(path_str)
        from packstudio.engines.color_engine.icc.validate import validate_profile

        return ok({"report": validate_profile(safe_path).as_json()})
    raise ApiError("MISSING_PROFILE", "需要上传 file 或提供 path。")


# ---------------------------------------------------------------------------
# 测量验证（色彩闭环 ④：实测 CGATS → ΔE00 对标 ISO 12647-2/Fogra39 判定）
# ---------------------------------------------------------------------------


@bp.post("/api/icc/verify")
def verify_measurement():
    """CGATS 测量文件 ΔE00 统计 + ISO 12647-2 判定（"测量验证 → 反馈修正"环节）。

    multipart:
        ``file``          CGATS.17 测量文件（文本，需 LAB_L/A/B 列）；
        ``profile_path``  可选，提供且文件含 device 列时由 profile 推导参考 Lab；
        ``reference``     内置参考表名，默认 Fogra39（None 则纯 profile 推导）；
        ``intent``/``bpc`` 参考 Lab 推导的意图与黑点补偿；
        ``tolerance``     可选，统一覆盖 ISO 判定容差（ΔE00）。

    返回 ``{verdict: PASS|WARNING|FAIL, passed, reference, tolerance, report}``；
    ``report`` 即引擎 VerifyResult.as_json()（patches/stats/iso/channel_diagnostics）。
    """
    from packstudio.engines.color_engine.measurement import cgats as cgats_mod
    from packstudio.engines.color_engine.measurement import verify as verify_mod

    fs = request.files.get("file")
    if fs is None:
        raise ApiError("MISSING_FILE", "缺少 CGATS 测量文件。")
    measured = cgats_mod.parse_cgats(fs.read())  # CgatsParseError → 422 (引擎错误映射)

    raw_profile_path = (request.form.get("profile_path") or "").strip()
    profile_path = _resolve_profile_path(raw_profile_path) if raw_profile_path else None
    reference = (request.form.get("reference") or "Fogra39").strip() or None
    intent = _intent_from(request.form.get("intent"))
    bpc = (request.form.get("bpc") or "true").lower() == "true"
    tolerance = None
    tolerance_raw = (request.form.get("tolerance") or "").strip()
    if tolerance_raw:
        try:
            tolerance = float(tolerance_raw)
        except ValueError as exc:
            raise ApiError("INVALID_TOLERANCE", "tolerance 必须是数字。") from exc

    result = verify_mod.verify_cgats(
        measured,
        profile_path,
        intent=intent,
        bpc=bpc,
        reference=reference,
        tolerance=tolerance,
    )
    return ok(
        {
            "verdict": verify_mod.overall_verdict(result),
            "passed": bool(result.passed),
            "reference": result.reference_name,
            "tolerance": result.tolerance,
            "report": result.as_json(),
        }
    )
