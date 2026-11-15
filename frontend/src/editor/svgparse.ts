/** 解析后端导出的刀版 SVG（格式由本引擎确定：一行一线段 + data-layer 分层）。 */

import type { DielineSegment, PanelGuide, ParsedDieline } from "./types";

export function parseDielineSvg(svg: string): ParsedDieline {
  // 防御性校验：拒绝意外包含 DOCTYPE/实体的内容（XXE 加固）。
  if (svg.includes("<!DOCTYPE") || svg.includes("<!ENTITY")) {
    throw new Error("非法 SVG 内容（包含 DOCTYPE/ENTITY）");
  }

  const segments: DielineSegment[] = [];
  const lineTags = svg.match(/<line\b[^>]*\/?>/g) ?? [];
  for (const tag of lineTags) {
    const layer = tag.match(/ data-layer="([^"]*)"/)?.[1] ?? "";
    if (!layer) {
      continue;
    }
    const x1 = tag.match(/ x1="([^"]*)"/)?.[1] ?? "";
    const y1 = tag.match(/ y1="([^"]*)"/)?.[1] ?? "";
    const x2 = tag.match(/ x2="([^"]*)"/)?.[1] ?? "";
    const y2 = tag.match(/ y2="([^"]*)"/)?.[1] ?? "";
    const label = tag.match(/ data-label="([^"]*)"/)?.[1] ?? "";
    const seg: DielineSegment = {
      layer,
      x1: Number(x1),
      y1: Number(y1),
      x2: Number(x2),
      y2: Number(y2),
      label: label || undefined,
    };
    segments.push(seg);
  }

  const panels: PanelGuide[] = [];
  const rectTags = svg.match(/<rect class="panel-guide"[^>]*\/?>/g) ?? [];
  let panelIndex = 0;
  for (const tag of rectTags) {
    const nameRaw = tag.match(/ data-panel-name="([^"]*)"/)?.[1] ?? "";
    const name = nameRaw
      .replace(/&quot;/g, '"')
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&");
    const px = tag.match(/ x="([^"]*)"/)?.[1] ?? "0";
    const py = tag.match(/ y="([^"]*)"/)?.[1] ?? "0";
    const pw = tag.match(/ width="([^"]*)"/)?.[1] ?? "0";
    const ph = tag.match(/ height="([^"]*)"/)?.[1] ?? "0";
    const panel: PanelGuide = {
      id: "panel-" + panelIndex,
      name,
      x: Number(px),
      y: Number(py),
      w: Number(pw),
      h: Number(ph),
    };
    panelIndex += 1;
    panels.push(panel);
  }

  let viewBox = { x: 0, y: 0, w: 0, h: 0 };
  const vbMatch = svg.match(/ viewBox="([^"]+)"/);
  if (vbMatch) {
    const parts = vbMatch[1].split(/\s+/);
    if (parts.length === 4) {
      const nums = parts.map(Number);
      const finite = nums.every((v) => Number.isFinite(v));
      if (finite) {
        viewBox = { x: nums[0], y: nums[1], w: nums[2], h: nums[3] };
      }
    }
  }
  return { segments, panels, viewBox };
}
