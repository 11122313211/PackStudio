/** 画布渲染器：把图层/刀版/参考线/测量绘制到 2D 上下文，并提供按需离屏高清渲染。 */

import type { Camera, Guide, LayerBase, MeasureState, ParsedDieline, Vec2 } from "./types";
import type { ThemeName } from "./theme";
import { getPalette } from "./theme";
import { worldToScreen } from "./viewport";

interface StrokeSpec {
  stroke: string;
  dash: number[];
  width: number;
}

/** 刀线行业色（与设计 token 一致；纸面恒白，行业色不随主题变化）。 */
const LAYER_COLORS: Record<string, StrokeSpec> = {
  CUT: { stroke: "#e5484d", dash: [], width: 1.4 },
  CREASE: { stroke: "#12a594", dash: [6, 4], width: 1.0 },
  GLUE: { stroke: "#0a7cff", dash: [2, 3], width: 1.0 },
  BLEED: { stroke: "#c9c9c4", dash: [], width: 0.8 },
  SAFETY: { stroke: "#8ab4ff", dash: [2, 3], width: 0.8 },
  GUIDE: { stroke: "#b3b3ad", dash: [1, 2], width: 0.6 },
  TEXT: { stroke: "#a8a8a2", dash: [], width: 0.8 },
};

const SPEC_BY_LAYER = (layer: string): StrokeSpec => LAYER_COLORS[layer] ?? LAYER_COLORS.GUIDE;

export interface RenderInput {
  ctx: CanvasRenderingContext2D;
  camera: Camera;
  dpr: number;
  viewW: number;
  viewH: number;
  dieline: ParsedDieline | null;
  layers: LayerBase[];
  selectionId: string | null;
  guides: Guide[];
  measure: MeasureState;
  /** 参考网格开关（10mm 细网格） */
  showGrid: boolean;
  /** 打样/越界覆盖图（内容盒离屏位图，画在刀版之下、参考线之上） */
  proofImage: ImageBitmap | null;
  oogImage: ImageBitmap | null;
  proofVisible: boolean;
  oogVisible: boolean;
  /** 主题（决定井底/纸张描边/网格/度量气泡/参考线颜色） */
  theme: ThemeName;
}

/** 离屏渲染配置（软打样快照 / 高清展平导出共用，完全脱离屏幕视口裁切）。 */
export interface OffscreenRenderOptions {
  dieline: ParsedDieline | null;
  layers: LayerBase[];
  /** 目标导出 DPI；若仅指定 maxEdgePx，按最大边自动计算分辨率 */
  dpi?: number;
  /** 长边最大像素限制（默认 12000，软打样建议 2048） */
  maxEdgePx?: number;
  /** 是否把矢量刀版绘制到离屏输出（软打样应为 false，避免刀线污染 ΔE 越界统计） */
  includeDieline: boolean;
}

export interface OffscreenRenderResult {
  canvas: HTMLCanvasElement;
  width: number;
  height: number;
  effectiveDpi: number;
  clamped: boolean;
}

/** 内容包围盒：刀版优先，其次图层联合。 */
export function contentBox(dieline: ParsedDieline | null, layers: LayerBase[]): { x: number; y: number; w: number; h: number } {
  if (dieline && dieline.viewBox.w > 0) {
    return dieline.viewBox;
  }
  const visible = layers.filter((l) => l.visible && l.bitmap && l.kind !== "proof" && l.kind !== "oog");
  if (visible.length === 0) {
    return { x: 0, y: 0, w: 210, h: 297 };
  }
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const l of visible) {
    minX = Math.min(minX, l.x);
    minY = Math.min(minY, l.y);
    maxX = Math.max(maxX, l.x + l.w);
    maxY = Math.max(maxY, l.y + l.h);
  }
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

function drawSheet(
  ctx: CanvasRenderingContext2D,
  box: { x: number; y: number; w: number; h: number },
  camera: Camera,
  palette: ReturnType<typeof getPalette>,
): void {
  const a = worldToScreen({ x: box.x, y: box.y }, camera);
  const b = worldToScreen({ x: box.x + box.w, y: box.y + box.h }, camera);
  ctx.fillStyle = palette.sheetFill;
  ctx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y);
  ctx.strokeStyle = palette.sheetBorder;
  ctx.lineWidth = 1;
  ctx.strokeRect(a.x + 0.5, a.y + 0.5, b.x - a.x - 1, b.y - a.y - 1);
}

function drawDieline(
  ctx: CanvasRenderingContext2D,
  dieline: ParsedDieline,
  camera: Camera,
  lineWidthScale = 1,
): void {
  ctx.save();
  ctx.lineCap = "round";
  for (const seg of dieline.segments) {
    const spec = SPEC_BY_LAYER(seg.layer);
    const p1 = worldToScreen({ x: seg.x1, y: seg.y1 }, camera);
    const p2 = worldToScreen({ x: seg.x2, y: seg.y2 }, camera);
    ctx.strokeStyle = spec.stroke;
    ctx.lineWidth = spec.width * lineWidthScale;
    ctx.setLineDash(spec.dash.map((d) => d * Math.max(1, camera.scale / 3)));
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawPanelRects(ctx: CanvasRenderingContext2D, dieline: ParsedDieline, camera: Camera): void {
  ctx.save();
  ctx.strokeStyle = "#a5c9f5";
  ctx.lineWidth = 0.7;
  ctx.setLineDash([3, 3]);
  for (const p of dieline.panels) {
    const a = worldToScreen({ x: p.x, y: p.y }, camera);
    const b = worldToScreen({ x: p.x + p.w, y: p.y + p.h }, camera);
    ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
  }
  ctx.restore();
}

/** 打样/越界覆盖图：按世界内容盒精确贴回画布（与离屏打样完全对齐）。 */
function drawOverlays(
  ctx: CanvasRenderingContext2D,
  camera: Camera,
  dieline: ParsedDieline | null,
  layers: LayerBase[],
  proofImage: ImageBitmap | null,
  oogImage: ImageBitmap | null,
  proofVisible: boolean,
  oogVisible: boolean,
): void {
  const box = contentBox(dieline, layers.filter((l) => l.kind !== "proof" && l.kind !== "oog"));
  if (proofVisible && proofImage) {
    const a = worldToScreen({ x: box.x, y: box.y }, camera);
    ctx.save();
    ctx.globalAlpha = 0.92;
    ctx.drawImage(proofImage, a.x, a.y, box.w * camera.scale, box.h * camera.scale);
    ctx.restore();
  }
  if (oogVisible && oogImage) {
    const a = worldToScreen({ x: box.x, y: box.y }, camera);
    ctx.save();
    ctx.globalAlpha = 0.85;
    ctx.drawImage(oogImage, a.x, a.y, box.w * camera.scale, box.h * camera.scale);
    ctx.restore();
  }
}

function drawArtworkLayers(
  ctx: CanvasRenderingContext2D,
  layers: LayerBase[],
  camera: Camera,
): void {
  for (const layer of layers) {
    if (!layer.visible || !layer.bitmap || layer.kind === "proof" || layer.kind === "oog") {
      continue;
    }
    const origin = worldToScreen({ x: layer.x, y: layer.y }, camera);
    ctx.save();
    ctx.globalAlpha = layer.opacity;
    ctx.translate(origin.x, origin.y);
    if (layer.rotation) {
      ctx.rotate((layer.rotation * Math.PI) / 180);
    }
    ctx.drawImage(layer.bitmap as CanvasImageSource, 0, 0, layer.w * camera.scale, layer.h * camera.scale);
    ctx.restore();
  }
}

function drawSelectionBox(
  ctx: CanvasRenderingContext2D,
  layers: LayerBase[],
  camera: Camera,
  selectionId: string | null,
  palette: ReturnType<typeof getPalette>,
): void {
  if (!selectionId) {
    return;
  }
  const sel = layers.find((l) => l.id === selectionId);
  if (!sel) {
    return;
  }
  const origin = worldToScreen({ x: sel.x, y: sel.y }, camera);
  ctx.save();
  ctx.translate(origin.x, origin.y);
  if (sel.rotation) {
    ctx.rotate((sel.rotation * Math.PI) / 180);
  }
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.strokeRect(-2, -2, sel.w * camera.scale + 4, sel.h * camera.scale + 4);
  ctx.restore();
}

function drawGuides(
  ctx: CanvasRenderingContext2D,
  guides: Guide[],
  camera: Camera,
  viewW: number,
  viewH: number,
  palette: ReturnType<typeof getPalette>,
): void {
  ctx.save();
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 1;
  for (const g of guides) {
    ctx.beginPath();
    if (g.axis === "x") {
      const sx = (g.pos - camera.x) * camera.scale;
      ctx.moveTo(sx + 0.5, 0);
      ctx.lineTo(sx + 0.5, viewH);
    } else {
      const sy = (g.pos - camera.y) * camera.scale;
      ctx.moveTo(0, sy + 0.5);
      ctx.lineTo(viewW, sy + 0.5);
    }
    ctx.stroke();
  }
  ctx.restore();
}

function drawMeasure(
  ctx: CanvasRenderingContext2D,
  measure: MeasureState,
  camera: Camera,
  palette: ReturnType<typeof getPalette>,
): void {
  if (!measure.active || !measure.start || !measure.end) {
    return;
  }
  const a = worldToScreen(measure.start, camera);
  const b = worldToScreen(measure.end, camera);
  ctx.save();
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
  for (const p of [a, b]) {
    ctx.beginPath();
    ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = palette.accent;
    ctx.fill();
  }
  const dx = Math.abs(measure.end.x - measure.start.x);
  const dy = Math.abs(measure.end.y - measure.start.y);
  const dist = Math.hypot(dx, dy);
  const label = `${dist.toFixed(1)} mm   Δx ${dx.toFixed(1)}  Δy ${dy.toFixed(1)}`;
  ctx.font = "11px 'Segoe UI', sans-serif";
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = palette.measureBg;
  ctx.fillRect(b.x + 8, b.y - 9, tw + 10, 18);
  ctx.fillStyle = palette.measureText;
  ctx.fillText(label, b.x + 13, b.y + 4);
  ctx.restore();
}

export function renderScene(input: RenderInput): void {
  const { ctx, dpr, camera, viewW, viewH, dieline, layers } = input;
  const palette = getPalette(input.theme);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = palette.well;
  ctx.fillRect(0, 0, viewW, viewH);

  const box = contentBox(dieline, layers);
  drawSheet(ctx, box, camera, palette);
  if (input.showGrid) {
    drawGrid(ctx, camera, viewW, viewH, box, palette);
  }
  if (dieline) {
    drawPanelRects(ctx, dieline, camera);
  }
  drawArtworkLayers(ctx, layers, camera);

  // 覆盖层在刀版之下：切/压/糊线始终可见
  drawOverlays(
    ctx,
    camera,
    dieline,
    layers,
    input.proofImage,
    input.oogImage,
    input.proofVisible,
    input.oogVisible,
  );
  if (dieline) {
    drawDieline(ctx, dieline, camera);
  }
  drawSelectionBox(ctx, layers, camera, input.selectionId, palette);
  drawGuides(ctx, input.guides, camera, viewW, viewH, palette);
  drawMeasure(ctx, input.measure, camera, palette);
}

/** 按世界坐标内容盒离屏高清渲染（软打样快照 / 展平导出专用）。
 *
 *  特点：
 *  - 永远不依赖当前屏幕视口缩放/平移，绝无裁切错位；
 *  - 绝不混入网格、面板虚线框、图层选中框或井底背景；
 *  - 按目标 DPI 矢量重绘刀版 + 原分辨率重绘位图素材。
 */
export function renderOffscreen(opts: OffscreenRenderOptions): OffscreenRenderResult | null {
  const artworkLayers = opts.layers.filter((l) => l.kind !== "proof" && l.kind !== "oog");
  const box = contentBox(opts.dieline, artworkLayers);
  if (box.w <= 0 || box.h <= 0) {
    return null;
  }

  const maxEdge = opts.maxEdgePx ?? 12000;
  let pxPerMm = opts.dpi ? opts.dpi / 25.4 : maxEdge / Math.max(box.w, box.h);
  let clamped = false;
  if (box.w * pxPerMm > maxEdge || box.h * pxPerMm > maxEdge) {
    pxPerMm = maxEdge / Math.max(box.w, box.h);
    clamped = true;
  }

  const width = Math.max(1, Math.round(box.w * pxPerMm));
  const height = Math.max(1, Math.round(box.h * pxPerMm));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return null;
  }

  // 纯白纸面底色（印刷底材）
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  const offCam: Camera = { x: box.x, y: box.y, scale: pxPerMm };
  drawArtworkLayers(ctx, artworkLayers, offCam);

  if (opts.includeDieline && opts.dieline) {
    // 以 96 DPI (~3.78 px/mm) 为参考基准按比例缩放线宽，高 DPI 下刀线保持清晰可读
    const lwScale = Math.max(1, pxPerMm / (96 / 25.4));
    drawDieline(ctx, opts.dieline, offCam, lwScale);
  }

  return {
    canvas,
    width,
    height,
    effectiveDpi: Math.round(pxPerMm * 25.4),
    clamped,
  };
}

/** 离屏采样单个世界坐标处的干净素材 RGB（排除越界掩图/打样层/刀线/网格/选框干扰）。 */
export function sampleArtworkPixel(world: Vec2, layers: LayerBase[]): [number, number, number] {
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) {
    return [255, 255, 255];
  }
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, 1, 1);
  // scale=4 采样 0.25mm 精度，将目标 world 点对齐到像素中心 (0.5, 0.5)
  const scale = 4;
  const cam: Camera = {
    x: world.x - 0.5 / scale,
    y: world.y - 0.5 / scale,
    scale,
  };
  drawArtworkLayers(ctx, layers, cam);
  const px = ctx.getImageData(0, 0, 1, 1).data;
  return [px[0], px[1], px[2]];
}

function drawGrid(
  ctx: CanvasRenderingContext2D,
  camera: Camera,
  viewW: number,
  viewH: number,
  box: { x: number; y: number; w: number; h: number },
  palette: ReturnType<typeof getPalette>,
): void {
  ctx.save();
  const pa = worldToScreen({ x: box.x, y: box.y }, camera);
  const pb = worldToScreen({ x: box.x + box.w, y: box.y + box.h }, camera);
  ctx.beginPath();
  ctx.rect(pa.x, pa.y, pb.x - pa.x, pb.y - pa.y);
  ctx.clip();
  ctx.strokeStyle = palette.grid;
  ctx.lineWidth = 1;
  const stepPx = 10 * camera.scale; // 10mm 网格
  if (stepPx < 6) {
    ctx.restore();
    return;
  }
  const startX = Math.floor(camera.x / 10) * 10;
  const startY = Math.floor(camera.y / 10) * 10;
  for (let x = startX; (x - camera.x) * camera.scale < viewW; x += 10) {
    const sx = Math.round((x - camera.x) * camera.scale) + 0.5;
    ctx.beginPath();
    ctx.moveTo(sx, 0);
    ctx.lineTo(sx, viewH);
    ctx.stroke();
  }
  for (let y = startY; (y - camera.y) * camera.scale < viewH; y += 10) {
    const sy = Math.round((y - camera.y) * camera.scale) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, sy);
    ctx.lineTo(viewW, sy);
    ctx.stroke();
  }
  ctx.restore();
}
