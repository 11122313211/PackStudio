"""刀版 API：POST /api/dieline（渲染+校验+尺寸+SVG）、GET /api/dieline/fold（3D 预览）。"""

from __future__ import annotations

from flask import Blueprint, request, Response

from packstudio.api.common import ApiError, ok
from packstudio.engines.packaging_engine.engine.render import render
from packstudio.engines.packaging_engine.exporters.svg import dieline_to_svg
from packstudio.engines.packaging_engine.fold.viewer import dieline_to_preview_html
from packstudio.engines.packaging_engine.models.ir import StructureSpec
from packstudio.engines.packaging_engine.validator.service import validate_dieline

bp = Blueprint("dieline", __name__)


@bp.post("/api/dieline")
def render_one():
    """按盒型与尺寸渲染刀版。

    请求体即 StructureSpec 字典（model_code + dimensions + 可选参数），
    校验由引擎负责（稳定机器码原样透出）。响应含 SVG（y-down，可直接
    进画布）、面板元数据、外接矩形与 14 项机械校验报告。
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("INVALID_REQUEST", "请求体必须是 JSON 对象。")
    try:
        spec = StructureSpec.from_dict(payload)
        dieline = render(spec)
    except ApiError:
        raise
    report = validate_dieline(dieline, spec)
    min_x, min_y, max_x, max_y = dieline.bounds
    return ok(
        {
            "svg": dieline_to_svg(dieline, spec),
            "metadata": dieline.metadata,
            "bounds": {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
            "net_size_mm": {"width": max_x - min_x, "height": max_y - min_y},
            "validation": report.to_dict(),
        }
    )


#: 3D 折叠预览允许通过 query 透传的可选工艺参数（mm）
_OPTIONAL_MM_FIELDS = (
    "tuck_height",
    "glue_width",
    "shrink",
    "bleed",
    "board_thickness",
    "safe_margin",
)


@bp.get("/api/dieline/fold")
def fold_preview():
    """3D 折叠预览（自包含 HTML，Three.js 走前端本地 /vendor/three.min.js）。"""
    payload = request.args
    try:
        spec_dict: dict[str, object] = {
            "model_code": payload.get("model_code", ""),
            "dimensions": {
                "length": float(payload.get("length", 0)),
                "width": float(payload.get("width", 0)),
                "height": float(payload.get("height", 0)),
                "unit": "mm",
            },
        }
        for key in _OPTIONAL_MM_FIELDS:
            raw = payload.get(key)
            if raw is not None and raw.strip() != "":
                spec_dict[key] = float(raw)
    except ValueError as exc:
        raise ApiError("INVALID_REQUEST", f"尺寸或工艺参数格式非法：{exc}", status=400) from exc

    # 引擎稳定错误码（PackagingError）交由 app.py 统一 422 透出，不再吞掉
    spec = StructureSpec.from_dict(spec_dict)
    dieline = render(spec)
    html = dieline_to_preview_html(dieline, spec)
    # 离线化：three.js 改走前端打包的本地副本
    html = html.replace(
        "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js",
        "/vendor/three.min.js",
    )
    return Response(html, mimetype="text/html")
