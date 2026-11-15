"""packaging-ai CLI — deterministic entry point for the packaging skill.

Subcommands map 1:1 to the routing actions. Heavy module imports are lazy so
`health-check` / `list-boxes` work even before optional modules exist.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _write(path: Path, data: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return str(path)


def _write_json(path: Path, data: dict) -> str:
    return _write(path, json.dumps(data, ensure_ascii=False, indent=2))


def cmd_health_check(args) -> int:
    from packstudio.engines.packaging_engine.routing.router import capability_manifest

    manifest = capability_manifest()
    payload = {
        "status": "completed",
        "success": True,
        "python": sys.version.split()[0],
        "capability_manifest": manifest,
        "note": "几何由确定性代码生成；输出默认 DESIGN_TEMPLATE，需印厂复核。",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_list_boxes(args) -> int:
    from packstudio.engines.packaging_engine.structure.registry import model_report

    print(json.dumps({"box_types": model_report()}, ensure_ascii=False, indent=2))
    return 0


def cmd_structure(args) -> int:
    from packstudio.engines.packaging_engine.engine.render import render
    from packstudio.engines.packaging_engine.exceptions import PackagingError
    from packstudio.engines.packaging_engine.exporters.svg import dieline_to_svg
    from packstudio.engines.packaging_engine.models.ir import StructureSpec

    data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "parameters" in data:
        data = data["parameters"]
    spec = StructureSpec.from_dict(data)
    out_dir = Path(args.output_dir or "output")
    dieline = render(spec)
    svg = dieline_to_svg(dieline, spec)
    svg_path = _write(out_dir / "template.svg", svg)
    _write_json(out_dir / "structure-spec.json", {"model_code": spec.model_code, "bounds": list(dieline.bounds), "segments": len(dieline.segments), "segment_list": [s.to_dict() for s in dieline.segments], "metadata": dieline.metadata})
    print(json.dumps({"status": "completed", "success": True, "outputs": {"template.svg": svg_path}, "bounds": dieline.bounds, "segments": len(dieline.segments), "review_required": "REQUIRES_MANUFACTURER_REVIEW"}, ensure_ascii=False, indent=2))
    return 0


def cmd_preview(args) -> int:
    from packstudio.engines.packaging_engine.engine.render import render
    from packstudio.engines.packaging_engine.fold.model import build_fold_model
    from packstudio.engines.packaging_engine.fold.viewer import dieline_to_preview_html
    from packstudio.engines.packaging_engine.models.ir import StructureSpec

    data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "parameters" in data:
        data = data["parameters"]
    spec = StructureSpec.from_dict(data)
    out_dir = Path(args.output_dir or "output")
    dieline = render(spec)
    model = build_fold_model(dieline)
    html = dieline_to_preview_html(dieline, spec)
    html_path = _write(out_dir / "preview.html", html)
    model_path = _write_json(out_dir / "fold-model.json", model.to_dict())
    print(json.dumps({
        "status": "completed",
        "success": True,
        "outputs": {"preview.html": html_path, "fold-model.json": model_path},
        "available": model.available,
        "status_message": model.status,
        "faces": len(model.faces),
        "hinges": len(model.hinges),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args) -> int:
    from packstudio.engines.packaging_engine.validator.service import validate_dieline

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    dieline_dict = data.get("dieline", data)
    from packstudio.engines.packaging_engine.models.ir import Dieline, CreaseSegment

    seg_source = dieline_dict.get("segment_list") or dieline_dict.get("segments") or []
    if not isinstance(seg_source, list):
        seg_source = []
    segs = [CreaseSegment(**s) for s in seg_source]
    bounds = tuple(dieline_dict.get("bounds", [0, 0, 0, 0]))
    dieline = Dieline(model_code=dieline_dict.get("model_code", ""), segments=segs, bounds=bounds, metadata=dieline_dict.get("metadata", {}))
    report = validate_dieline(dieline, None)
    payload = report.to_dict()
    if args.output_dir:
        _write_json(Path(args.output_dir) / "validation-report.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


def cmd_content(args) -> int:
    from packstudio.engines.packaging_engine.content.models import ContentBrief
    from packstudio.engines.packaging_engine.content.layout import layout_content
    from packstudio.engines.packaging_engine.engine.render import render
    from packstudio.engines.packaging_engine.models.ir import StructureSpec

    data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "parameters" in data:
        data = data["parameters"]
    spec = StructureSpec.from_dict(data)
    brief = ContentBrief.from_dict(json.loads(Path(args.brief).read_text(encoding="utf-8")))
    dieline = render(spec)
    result = layout_content(dieline, brief, spec, args.output_dir or "output")
    print(json.dumps({"status": "completed", "success": True, "outputs": result.outputs if hasattr(result, "outputs") else str(result), "manual_review": True}, ensure_ascii=False, indent=2))
    return 0


def cmd_mockup(args) -> int:
    from packstudio.engines.packaging_engine.cmf.service import plan_cmf

    payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
    plan = plan_cmf(
        artwork_path=args.artwork,
        dimensions=payload.get("dimensions"),
        material=payload.get("material", ""),
        finishes=payload.get("finishes", []),
        output_dir=args.output_dir or "output",
    )
    print(json.dumps({"status": "completed", "success": True, "cmf_plan": plan}, ensure_ascii=False, indent=2))
    return 0


def cmd_knowledge(args) -> int:
    from packstudio.engines.packaging_engine.knowledge.loader import load_knowledge, list_knowledge

    if args.name == "list":
        print(json.dumps({"domain": args.domain, "entries": list_knowledge(args.domain)}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(load_knowledge(args.domain, args.name), ensure_ascii=False, indent=2))
    return 0


def cmd_route(args) -> int:
    from packstudio.engines.packaging_engine.models.ir import Request
    from packstudio.engines.packaging_engine.routing.router import route

    request = Request.from_dict(json.loads(Path(args.request).read_text(encoding="utf-8")))
    print(json.dumps(route(request).to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    from packstudio.engines.packaging_engine.models.ir import Request
    from packstudio.engines.packaging_engine.routing.router import route

    request = Request.from_dict(json.loads(Path(args.job).read_text(encoding="utf-8")))
    result = route(request)
    payload = {"request": request.to_dict(), "route": result.to_dict()}
    if args.dry_run:
        payload["dry_run"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_contact_sheet(args) -> int:
    from packstudio.engines.packaging_engine.contact_sheet import generate_contact_sheet

    out_dir = Path(args.output_dir or "output")
    path = generate_contact_sheet(str(out_dir), count=args.count)
    print(json.dumps({"status": "completed", "success": True, "outputs": {"contact-sheet.html": path}}, ensure_ascii=False, indent=2))
    return 0


def cmd_export_cf2(args) -> int:
    from packstudio.engines.packaging_engine.engine.render import render
    from packstudio.engines.packaging_engine.exporters.cf2 import dieline_to_cf2
    from packstudio.engines.packaging_engine.models.ir import StructureSpec

    data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "parameters" in data:
        data = data["parameters"]
    spec = StructureSpec.from_dict(data)
    dieline = render(spec)
    cf2 = dieline_to_cf2(dieline, spec)
    out_dir = Path(args.output_dir or "output")
    path = _write(out_dir / "dieline.cf2", cf2)
    print(json.dumps({"status": "completed", "success": True, "outputs": {"dieline.cf2": path}}, ensure_ascii=False, indent=2))
    return 0


def cmd_export_spot_pdf(args) -> int:
    from packstudio.engines.packaging_engine.engine.render import render
    from packstudio.engines.packaging_engine.exporters.pdf_spot import dieline_to_spot_pdf
    from packstudio.engines.packaging_engine.models.ir import StructureSpec

    data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "parameters" in data:
        data = data["parameters"]
    spec = StructureSpec.from_dict(data)
    dieline = render(spec)
    pdf = dieline_to_spot_pdf(dieline, spec)
    out_dir = Path(args.output_dir or "output")
    path = _write(out_dir / "dieline-spot.pdf", pdf)
    print(json.dumps({"status": "completed", "success": True, "outputs": {"dieline-spot.pdf": path}}, ensure_ascii=False, indent=2))
    return 0


def cmd_dimensions(args) -> int:
    from packstudio.engines.packaging_engine.dimensions import convert_dimensions

    dims = {"length": args.length, "width": args.width, "height": args.height, "unit": args.unit}
    result = convert_dimensions(dims, args.from_type, args.to_type, board_thickness=args.thickness)
    print(json.dumps({"status": "completed", "success": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="packaging-ai", description="AI Skill: packaging design dielines, validation, content, BOM/SOP, CMF.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health-check", help="capability manifest").set_defaults(func=cmd_health_check)
    sub.add_parser("list-boxes", help="list implemented box types").set_defaults(func=cmd_list_boxes)

    p = sub.add_parser("structure", help="render a deterministic dieline SVG")
    p.add_argument("--spec", required=True)
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_structure)

    p = sub.add_parser("preview", help="generate a 3D fold preview HTML")
    p.add_argument("--spec", required=True)
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("validate", help="run the mechanical validation gate")
    p.add_argument("--input", required=True)
    p.add_argument("--output-dir")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("content", help="content layout + back-label fields")
    p.add_argument("--spec", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_content)

    p = sub.add_parser("mockup", help="CMF mockup plan (needs image provider)")
    p.add_argument("--artwork", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_mockup)

    p = sub.add_parser("knowledge", help="load knowledge base entry")
    p.add_argument("--domain", required=True)
    p.add_argument("--name", default="list")
    p.set_defaults(func=cmd_knowledge)

    p = sub.add_parser("route", help="route a request JSON")
    p.add_argument("--request", required=True)
    p.set_defaults(func=cmd_route)

    p = sub.add_parser("run", help="route + (optionally dry-run) a job")
    p.add_argument("--job", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("contact-sheet", help="generate a 9-cell dieline contact sheet HTML")
    p.add_argument("--output-dir", default="output")
    p.add_argument("--count", type=int, default=0)
    p.set_defaults(func=cmd_contact_sheet)

    p = sub.add_parser("export-cf2", help="export dieline as CF2 laser-cutting XML")
    p.add_argument("--spec", required=True)
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_export_cf2)

    p = sub.add_parser("export-spot-pdf", help="export dieline as spot-colour separation PDF")
    p.add_argument("--spec", required=True)
    p.add_argument("--output-dir", default="output")
    p.set_defaults(func=cmd_export_spot_pdf)

    p = sub.add_parser("dimensions", help="convert between inner/outer/blank dimensions")
    p.add_argument("--length", type=float, required=True)
    p.add_argument("--width", type=float, required=True)
    p.add_argument("--height", type=float, required=True)
    p.add_argument("--unit", default="mm")
    p.add_argument("--from-type", required=True, choices=["finished_inner", "finished_outer", "blank"])
    p.add_argument("--to-type", required=True, choices=["finished_inner", "finished_outer", "blank"])
    p.add_argument("--thickness", type=float, default=0.3)
    p.set_defaults(func=cmd_dimensions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - stable error envelope
        code = getattr(exc, "code", "ERROR")
        print(json.dumps({"status": "failed", "error": {"code": code, "message": str(exc), "details": getattr(exc, "details", {})}}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
