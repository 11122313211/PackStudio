"""Contact sheet generator: renders all implemented box types into one HTML page.

Each cell shows the 2D dieline SVG, box name, dimensions, and validation
status. Useful for a quick visual overview and for verifying that every
box type produces valid geometry.
"""

from __future__ import annotations

import html
from pathlib import Path

from packstudio.engines.packaging_engine.engine.render import render
from packstudio.engines.packaging_engine.exporters.svg import dieline_to_svg
from packstudio.engines.packaging_engine.models.ir import StructureSpec
from packstudio.engines.packaging_engine.structure.registry import BOX_TYPES
from packstudio.engines.packaging_engine.validator.service import validate_dieline

DEFAULT_DIMS = {"length": 80.0, "width": 40.0, "height": 120.0, "unit": "mm"}


def _thumbnail_svg(dieline) -> str:
    """Scale the dieline SVG to fit a thumbnail cell (max 200x200mm)."""
    svg = dieline_to_svg(dieline)
    return svg


def _cell(box_type, index: int) -> str:
    code = box_type.code
    name = box_type.name_zh or box_type.name_en
    try:
        spec = StructureSpec.from_dict({"model_code": code, "dimensions": dict(DEFAULT_DIMS)})
        dieline = render(spec)
        report = validate_dieline(dieline, spec)
        passed = report.passed
        status_text = "✅ 通过" if passed else "❌ 未通过"
        svg = _thumbnail_svg(dieline)
    except Exception as exc:  # noqa: BLE001
        passed = False
        status_text = f"⚠️ 错误: {html.escape(str(exc))}"
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" fill="#f0f0f0"/><text x="50" y="50" text-anchor="middle" font-size="8">渲染失败</text></svg>'
    safe_name = html.escape(name, quote=True)
    safe_code = html.escape(code, quote=True)
    return f'''<div class="cell">
  <div class="cell-header"><span class="cell-name">{safe_name}</span><span class="cell-status {"ok" if passed else "fail"}">{status_text}</span></div>
  <div class="cell-svg">{svg}</div>
  <div class="cell-code">{safe_code}</div>
</div>'''


_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>盒型刀模一览 — Contact Sheet</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }
h1 { text-align: center; margin-bottom: 20px; font-weight: 300; }
.summary { text-align: center; color: #888; margin-bottom: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; max-width: 1400px; margin: 0 auto; }
.cell { background: #16213e; border-radius: 10px; padding: 12px; border: 1px solid #0f3460; }
.cell-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.cell-name { font-size: 14px; font-weight: 600; }
.cell-status { font-size: 12px; padding: 2px 8px; border-radius: 10px; }
.cell-status.ok { background: #0f3460; color: #00ff88; }
.cell-status.fail { background: #3d1a1a; color: #ff6b6b; }
.cell-svg { background: #fff; border-radius: 6px; padding: 4px; min-height: 180px; display: flex; align-items: center; justify-content: center; }
.cell-svg svg { max-width: 100%; max-height: 200px; }
.cell-code { font-size: 10px; color: #666; margin-top: 6px; font-family: monospace; word-break: break-all; }
</style>
</head>
<body>
<h1>包装盒型刀模一览</h1>
<p class="summary">共 {total} 类盒型 | 默认尺寸 80 × 40 × 120 mm | 生成时间 {timestamp}</p>
<div class="grid">
{cells}
</div>
</body>
</html>"""


def generate_contact_sheet(output_dir: str | Path = "output/contact-sheet", count: int | None = None) -> str:
    """Render all implemented box types into a single HTML contact sheet.

    Returns the HTML string and writes it to ``output_dir/contact-sheet.html``.
    """
    implemented = [b for b in BOX_TYPES if b.implemented]
    if count is not None:
        implemented = implemented[:count]

    cells = "\n".join(_cell(b, i) for i, b in enumerate(implemented))

    from datetime import datetime
    page = (_PAGE
            .replace("{total}", str(len(implemented)))
            .replace("{timestamp}", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            .replace("{cells}", cells))

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "contact-sheet.html").write_text(page, encoding="utf-8")
    return page
