"""ICC 色彩 API：profile 扫描 / 图像转换 / 软打样 / 越界掩图。"""

from __future__ import annotations

import dataclasses
import io

from flask import Blueprint, request
from PIL import Image

from packstudio.api.common import ApiError, ok, save_upload, store_output
from packstudio.engines.color_engine.cmm import discovery, engine as cmm_engine
from packstudio.engines.color_engine.imaging.pipeline import ProfileOp, process_file
from packstudio.engines.color_engine.errors import IccToolkitError

bp = Blueprint("icc", __name__)

_INTENTS = {"perceptual": 0, "relative": 1, "saturation": 2, "absolute": 3}


@bp.get("/api/icc/profiles")
def list_profiles():
    """扫描系统 ICC profile（带缓存）。"""
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


@bp.post("/api/icc/convert")
def convert():
    """图像 profile 操作（assign/embed/strip/convert），结果落为可下载文件。"""
    fs = request.files.get("file")
    if fs is None:
        raise ApiError("MISSING_FILE", "缺少上传图像。")
    op = (request.form.get("op") or "").lower()
    if op not in ProfileOp.__members__:
        raise ApiError("INVALID_OP", f"不支持的操作：{op}（可选 assign/embed/strip/convert）")
    profile_path = request.form.get("profile_path") or ""
    if op in ("assign", "embed", "convert") and not profile_path:
        raise ApiError("MISSING_PROFILE", "该操作需要提供 profile_path。")

    src = save_upload(fs, suffix="." + (fs.filename or "img").rsplit(".", 1)[-1])
    out_name = f"converted.{request.form.get('out_format') or 'tif'}"
    dst_dir = src.parent
    result = process_file(
        src,
        dst_dir / ("out_" + src.name),
        ProfileOp(op),
        profile=profile_path or None,
        intent=_intent_from(request.form.get("intent")),
        bpc=(request.form.get("bpc") or "false").lower() == "true",
        out_format=request.form.get("out_format") or None,
    )
    if not result.ok:
        raise ApiError(result.error_code or "CONVERT_FAILED", result.message or "转换失败", status=422)
    data = (dst_dir / ("out_" + src.name)).read_bytes()
    output_id = store_output(out_name, data)
    return ok({"id": output_id, "filename": out_name, "report": dataclasses.asdict(result)})


def _srgb_profile_bytes() -> bytes:
    """内置 sRGB 显示 profile（lcms 合成，避免依赖系统文件存在）。"""
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _profile_arg(value: str | None, *, allow_builtin: bool = False):
    """把请求里的 profile 参数解析为引擎可接受的 bytes。"""
    if value:
        return value  # 路径字符串（来自 /api/icc/profiles 的扫描结果）
    if allow_builtin:
        return _srgb_profile_bytes()
    raise ApiError("MISSING_PROFILE", "缺少 profile。")


@bp.post("/api/icc/proof")
def proof():
    """画布打样模拟 + 越界掩图。

    画布快照是 sRGB 显示数据，因此采用往返语义：

    1. 打样预览：sRGB → 设备 profile（用户意图，色域压缩）→ sRGB 展示；
    2. 越界掩图：sRGB → 设备（相对比色）后量 Lab 色差，ΔE76 超阈值
       的像素即"印刷无法复现"的告警区（红色掩图）。
    """
    im = _load_image_from_request().convert("RGB")
    device = _profile_arg(request.form.get("device_profile"), allow_builtin=True)
    intent = _intent_from(request.form.get("intent"))
    bpc = (request.form.get("bpc") or "true").lower() == "true"
    try:
        threshold = float(request.form.get("oog_threshold") or 4.0)
    except ValueError as exc:
        raise ApiError("INVALID_THRESHOLD", "oog_threshold 必须是数字。") from exc
    threshold = min(max(threshold, 0.5), 20.0)
    srgb = _srgb_profile_bytes()
    try:
        from packstudio.engines.color_engine.imaging.pipeline import convert_image

        # 1) 打样预览（往返，按用户意图做色域压缩）
        device_im = convert_image(im, device, srgb, intent=intent, bpc=bpc)
        preview = convert_image(
            device_im, srgb, device,
            intent=cmm_engine.RenderingIntent.RELATIVE_COLORIMETRIC, bpc=bpc,
        )
        # 2) 越界掩图：固定相对比色（掩图语义不随预览意图漂移，见 docstring）
        dev_rel = convert_image(
            im, device, srgb,
            intent=cmm_engine.RenderingIntent.RELATIVE_COLORIMETRIC, bpc=bpc,
        )
        lab_src = cmm_engine.transform_lab(srgb, im)
        lab_dev = cmm_engine.transform_lab(device, dev_rel)
    except IccToolkitError as exc:
        raise ApiError(exc.code, str(exc), status=422, detail=getattr(exc, "detail", None)) from exc

    import numpy

    delta_e = numpy.sqrt(((lab_src - lab_dev) ** 2).sum(axis=1))
    oog = delta_e > threshold
    w, h = im.size
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
            },
        }
    )


@bp.post("/api/icc/lab")
def pixel_lab():
    """取色换算：sRGB 像素值 → 指定输出 profile 下的 Lab（度量/取色工具）。"""
    payload = request.get_json(silent=True) or {}
    rgb = payload.get("rgb")
    if not isinstance(rgb, list) or len(rgb) != 3:
        raise ApiError("INVALID_RGB", "rgb 必须是 [r, g, b]。")
    try:
        values = [max(0, min(255, int(v))) for v in rgb]
    except (TypeError, ValueError) as exc:
        raise ApiError("INVALID_RGB", "rgb 必须是整数。") from exc
    profile = _profile_arg(payload.get("profile"), allow_builtin=True)
    im = Image.new("RGB", (1, 1), tuple(values))
    try:
        lab = cmm_engine.transform_lab(profile, im, intent=cmm_engine.RenderingIntent.RELATIVE_COLORIMETRIC)
    except IccToolkitError as exc:
        raise ApiError(exc.code, str(exc), status=422) from exc
    row = lab[0]
    return ok({"lab": [round(float(v), 3) for v in (row[0], row[1], row[2])]})


# ---------------------------------------------------------------------------
# 档案质量校验（色彩闭环 ③：档案入库前的结构体检）
# ---------------------------------------------------------------------------


@bp.get("/api/icc/validate")
def validate_by_path():
    """按扫描路径校验 profile 结构（19 条规则 + 必需 tag 表）。

    Query: ``path`` — /api/icc/profiles 返回的 profile 路径。
    校验失败（结构问题）不报错，以 report.issues 形式返回；
    只有文件不可读/不可解析才以稳定机器码 422 透出。
    """
    path_str = (request.args.get("path") or "").strip()
    if not path_str:
        raise ApiError("MISSING_PROFILE", "缺少 profile 路径参数 path。")
    from packstudio.engines.color_engine.icc.validate import validate_profile

    report = validate_profile(path_str)  # IO 失败由引擎错误统一映射 422
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
        from packstudio.engines.color_engine.icc.validate import validate_profile

        return ok({"report": validate_profile(path_str).as_json()})
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

    profile_path = (request.form.get("profile_path") or "").strip() or None
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
