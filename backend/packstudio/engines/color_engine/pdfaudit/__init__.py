"""PDF 色彩审计子模块（TECH-DESIGN §2.5 导出面）。"""

from __future__ import annotations

from packstudio.engines.color_engine.pdfaudit.audit import (ImageColorEntry, OutputIntentInfo,
                                        PageColorReport, PdfColorAuditResult,
                                        SpotColorEntry, audit_pdf)

__all__ = [
    "OutputIntentInfo",
    "PageColorReport",
    "ImageColorEntry",
    "SpotColorEntry",
    "PdfColorAuditResult",
    "audit_pdf",
]
