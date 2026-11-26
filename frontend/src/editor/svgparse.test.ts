import { describe, expect, it } from "vitest";
import { parseDielineSvg } from "./svgparse";

describe("parseDielineSvg", () => {
  it("解析线段、面板与 viewBox 并还原 XML 实体", () => {
    const svg = `<?xml version="1.0"?>
<svg viewBox="0 0 320 240">
  <rect class="panel-guide" data-panel-name="主面 &amp; &quot;A&quot;" x="10" y="20" width="90" height="130" />
  <line data-layer="CUT" x1="0" y1="0" x2="100" y2="0" data-label="top" />
  <line data-layer="CREASE" x1="0" y1="50" x2="100" y2="50" />
</svg>`;
    const parsed = parseDielineSvg(svg);
    expect(parsed.viewBox).toEqual({ x: 0, y: 0, w: 320, h: 240 });
    expect(parsed.segments).toHaveLength(2);
    expect(parsed.segments[0]).toMatchObject({ layer: "CUT", x2: 100, label: "top" });
    expect(parsed.panels).toHaveLength(1);
    expect(parsed.panels[0].name).toBe('主面 & "A"');
  });

  it("拒绝含 DOCTYPE 或 ENTITY 的 SVG 以防 XXE", () => {
    expect(() => parseDielineSvg("<!DOCTYPE svg><svg/>")).toThrow(/DOCTYPE|ENTITY/);
    expect(() => parseDielineSvg("<!ENTITY x '1'><svg/>")).toThrow(/DOCTYPE|ENTITY/);
  });
});
