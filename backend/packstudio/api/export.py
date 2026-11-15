"""导出 API：POST /api/export（刀版六格式）+ 下载（GET /api/download）。"""

from __future__ import annotations

from flask import Blueprint, request, send_file

from packstudio.api.common import ApiError, ok, output_path, store_output
from packstudio.engines.packaging_engine.engine.render import render
from packstudio.engines.packaging_engine.exporters import (
    dieline_to_cf2,
    dieline_to_dxf,
    dieline_to_pdf,
    dieline_to_spot_pdf,
    dieline_to_svg,
)
from packstudio.engines.packaging_engine.models.ir import StructureSpec

bp = Blueprint("export", __name__)

#: format → (导出函数, 扩展名, MIME, 是否字节输出)
_FORMATS: dict[str, tuple] = {
    "svg": (dieline_to_svg, "svg", "image/svg+xml", False),
    "dxf": (dieline_to_dxf, "dxf", "application/dxf", False),
    "pdf": (dieline_to_pdf, "pdf", "application/pdf", True),
    "pdf_spot": (dieline_to_spot_pdf, "pdf", "application/pdf", True),
    "cf2": (dieline_to_cf2, "cf2", "application/xml", True),
}


@bp.post("/api/export")
def export_dieline():
    """按盒型参数渲染刀版并导出为指定格式。

    请求体：``{...StructureSpec 字段..., "format": "svg|dxf|pdf|pdf_spot|cf2"}``。
    返回下载 id 与文件名，前端用 GET /api/download/<id>/<name> 取文件。
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("INVALID_REQUEST", "请求体必须是 JSON 对象。")
    fmt = str(payload.pop("format", "")).lower()
    if fmt not in _FORMATS:
        raise ApiError("INVALID_FORMAT", f"不支持的导出格式：{fmt}", detail={"supported": list(_FORMATS)})
    exporter, ext, mime, is_bytes = _FORMATS[fmt]

    spec = StructureSpec.from_dict(payload)
    dieline = render(spec)
    data = exporter(dieline, spec)
    raw: bytes = data if is_bytes else str(data).encode("utf-8")
    filename = f"dieline_{spec.model_code.replace('.', '_')}.{ext}"
    output_id = store_output(filename, raw)
    return ok({"id": output_id, "filename": filename, "mime": mime, "size": len(raw)})


@bp.get("/api/download/<output_id>/<name>")
def download(output_id: str, name: str):
    """按白名单 id 下载生成物。"""
    path = output_path(output_id, name)
    if not path.is_file():
        raise ApiError("NOT_FOUND", "生成物不存在或已清理。", status=404)
    return send_file(path, as_attachment=True, download_name=name)
