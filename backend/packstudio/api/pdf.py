"""PDF API：印前色彩审计（POST /api/pdf/audit）。"""

from __future__ import annotations

import dataclasses

from flask import Blueprint, request

from packstudio.api.common import ApiError, ok
from packstudio.engines.color_engine.pdfaudit.audit import PdfAuditError, audit_pdf

bp = Blueprint("pdf", __name__)


@bp.post("/api/pdf/audit")
def audit():
    """上传 PDF → OutputIntent / 逐页色空间 / 专色 / 嵌入 profile 审计。"""
    fs = request.files.get("file")
    if fs is None:
        raise ApiError("MISSING_FILE", "缺少上传的 PDF。")
    data = fs.read()
    if not data:
        raise ApiError("EMPTY_FILE", "上传的 PDF 是空的。")
    try:
        result = audit_pdf(data)
    except PdfAuditError as exc:
        raise ApiError(exc.code, str(exc), status=422, detail=getattr(exc, "detail", None)) from exc
    return ok({"report": dataclasses.asdict(result)})
