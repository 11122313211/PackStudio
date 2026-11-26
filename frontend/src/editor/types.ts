/** 编辑器核心类型：世界坐标为毫米；刀版 SVG 已由后端转为 y-down。 */

export type ToolKind = "select" | "pan" | "measure" | "eyedropper";

export interface Camera {
  /** 视口左上角对应的世界坐标（mm） */
  x: number;
  y: number;
  /** 缩放：屏幕像素 / 毫米 */
  scale: number;
}

export interface Vec2 {
  x: number;
  y: number;
}

export type LayerKind = "image" | "pdf" | "proof" | "oog";

export interface ConvertedOutput {
  id: string;
  filename: string;
  colorSpace: string;
}

export interface PickedColor {
  rgb: [number, number, number];
  lab: [number, number, number] | null;
  colorSpace?: string;
  deviceValues?: number[];
  tac?: number | null;
}

export interface LayerBase {
  id: string;
  name: string;
  kind: LayerKind;
  /** 世界坐标（mm），左上角 */
  x: number;
  y: number;
  w: number;
  h: number;
  rotation: number; // deg
  opacity: number; // 0..1
  visible: boolean;
  locked: boolean;
  /** 位图句柄不参与历史快照 */
  bitmap: ImageBitmap | HTMLImageElement | null;
  /** 若该图层由 ICC 转换生成，关联后端真实色彩文件（如 CMYK TIFF）下载信息 */
  convertedOutput?: ConvertedOutput;
}

export interface DielineSegment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  layer: string; // CUT / CREASE / GLUE / BLEED / SAFETY / GUIDE / TEXT
  style?: string; // solid / dashed / dotted / glue
  label?: string;
}

export interface PanelGuide {
  id: string;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ParsedDieline {
  segments: DielineSegment[];
  panels: PanelGuide[];
  viewBox: { x: number; y: number; w: number; h: number };
}

export interface Guide {
  id: string;
  axis: "x" | "y";
  pos: number; // mm
}

export interface MeasureState {
  active: boolean;
  start: Vec2 | null;
  end: Vec2 | null;
}

export interface BoxSpec {
  model_code: string;
  length: number;
  width: number;
  height: number;
  tuck_height?: number;
  glue_width?: number;
  shrink?: number;
  bleed?: number;
}

export const MM_PER_PT = 25.4 / 72;
export const PT_PER_MM = 72 / 25.4;

export function uid(): string {
  const buf = new Uint32Array(2);
  crypto.getRandomValues(buf);
  return Array.from(buf, (n) => n.toString(36)).join("");
}
