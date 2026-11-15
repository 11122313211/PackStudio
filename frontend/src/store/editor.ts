/** 编辑器全局状态（Zustand）：文档/视图/交互/历史/工作流。 */

import { create } from "zustand";
import { api } from "../lib/api";
import type { ProfileInfo, PdfAuditReport, VerifyResponse } from "../lib/api";
import type { StageId } from "../flow/stages";
import type { ThemeName } from "../editor/theme";
import type {
  BoxSpec,
  Camera,
  Guide,
  LayerBase,
  MeasureState,
  ParsedDieline,
  ToolKind,
} from "../editor/types";
import { uid } from "../editor/types";
import { parseDielineSvg } from "../editor/svgparse";
import type { ProofResponse, ValidationInfo } from "../lib/api";

export interface Toast {
  id: string;
  kind: "info" | "ok" | "error";
  text: string;
}

interface Snapshot {
  layers: LayerBase[];
  guides: Guide[];
}

export interface EditorState {
  /* 文档 */
  boxSpec: BoxSpec;
  dieline: ParsedDieline | null;
  dielineName: string;
  dielineEcma: string;
  dielineFefco: string;
  netSize: { width: number; height: number } | null;
  validation: ValidationInfo | null;
  layers: LayerBase[];
  guides: Guide[];

  /* 视图 */
  camera: Camera;
  tool: ToolKind;
  showGrid: boolean;
  units: "mm" | "pt";

  /* 交互 */
  selectionId: string | null;
  measure: MeasureState;
  spacePanning: boolean;
  cursor: { x: number; y: number } | null;

  /* 打样 */
  proofVisible: boolean;
  oogVisible: boolean;
  deviceProfilePath: string;
  proofIntent: string;
  proofBpc: boolean;
  proofThreshold: number;
  proofStats: ProofResponse["stats"] | null;

  /* 取色（度量工具联动） */
  picked: { rgb: [number, number, number]; lab: [number, number, number] | null } | null;
  setPicked: (p: { rgb: [number, number, number]; lab: [number, number, number] | null } | null) => void;

  /* 打样覆盖图（位图不进历史） */
  proofImage: ImageBitmap | null;
  oogImage: ImageBitmap | null;

  /* UI（工作流五阶段 + 闭环数据 + 主题；不进 undo 快照） */
  /** 当前激活阶段（五阶段软门控工作流，见 flow/stages.ts） */
  stage: StageId;
  /** ② 自动 PDF 色彩审计报告（最新一次 PDF 导入） */
  audit: PdfAuditReport | null;
  /** ④ CGATS 测量验证结果 */
  verifyReport: VerifyResponse | null;
  /** ③ 系统扫描到的 ICC profile 库（ColorStage 首次进入时加载） */
  profiles: ProfileInfo[];
  /** ⑤ 已导出次数（交付阶段完成度） */
  exportCount: number;
  /** 界面主题（深色默认，localStorage 持久化，驱动 data-theme 与画布调色板） */
  theme: ThemeName;
  toasts: Toast[];
  busy: boolean;
  /** 当前忙碌任务域（dieline/proof/export/import/audit/verify），供各阶段只显示自己的"…中"文案 */
  busyTask: string | null;
  /** 适配窗口请求计数（CanvasStage 监听自增执行 fit） */
  fitRequest: number;

  /* 历史 */
  past: Snapshot[];
  future: Snapshot[];

  /* ---- actions ---- */
  setTool: (tool: ToolKind) => void;
  setCamera: (camera: Camera) => void;
  setUnits: (units: "mm" | "pt") => void;
  toggleGrid: () => void;
  setCursor: (p: { x: number; y: number } | null) => void;
  setSpacePanning: (on: boolean) => void;

  setBoxSpec: (patch: Partial<BoxSpec>) => void;
  generateDieline: () => Promise<void>;

  addLayer: (layer: LayerBase) => void;
  updateLayer: (id: string, patch: Partial<LayerBase>) => void;
  removeLayer: (id: string) => void;
  reorderLayer: (id: string, dir: -1 | 1) => void;
  select: (id: string | null) => void;

  addGuide: (axis: "x" | "y", pos: number) => void;
  removeGuide: (id: string) => void;
  clearGuides: () => void;
  setMeasure: (patch: Partial<MeasureState>) => void;

  setProof: (patch: {
    proofVisible?: boolean;
    oogVisible?: boolean;
    deviceProfilePath?: string;
    proofIntent?: string;
    proofBpc?: boolean;
    proofThreshold?: number;
    proofStats?: ProofResponse["stats"] | null;
    proofImage?: ImageBitmap | null;
    oogImage?: ImageBitmap | null;
  }) => void;

  toast: (text: string, kind?: Toast["kind"]) => void;
  dismissToast: (id: string) => void;
  setBusy: (busy: boolean, task?: string) => void;

  /* 工作流与主题 */
  setStage: (stage: StageId) => void;
  setAudit: (audit: PdfAuditReport | null) => void;
  setVerifyReport: (report: VerifyResponse | null) => void;
  loadProfiles: () => Promise<void>;
  markExported: () => void;
  setTheme: (theme: ThemeName) => void;

  snapshot: () => void;
  undo: () => void;
  redo: () => void;

  requestFit: () => void;
}

const DEFAULT_SPEC: BoxSpec = {
  model_code: "carton.box_v2.straight",
  length: 90,
  width: 50,
  height: 130,
};

function takeSnapshot(state: EditorState): Snapshot {
  return {
    layers: state.layers.map((l) => ({ ...l })),
    guides: state.guides.map((g) => ({ ...g })),
  };
}

/* ---- 主题 ---- */

const THEME_KEY = "packstudio.theme";

function initialTheme(): ThemeName {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light") {
      return saved;
    }
  } catch {
    /* localStorage 不可用时用默认 */
  }
  return "dark"; // 产品决策：深色为默认主题
}

/** 把主题写到 <html data-theme> 与 localStorage（main.tsx 启动与切换时调用）。 */
export function applyTheme(theme: ThemeName): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* 忽略持久化失败 */
  }
}

export const useEditor = create<EditorState>((set, get) => ({
  boxSpec: { ...DEFAULT_SPEC },
  dieline: null,
  dielineName: "",
  dielineEcma: "",
  dielineFefco: "",
  netSize: null,
  validation: null,
  layers: [],
  guides: [],

  camera: { x: 0, y: 0, scale: 3 },
  tool: "select",
  showGrid: true,
  units: "mm",

  selectionId: null,
  measure: { active: false, start: null, end: null },
  spacePanning: false,
  cursor: null,

  proofVisible: false,
  oogVisible: false,
  deviceProfilePath: "",
  proofIntent: "relative",
  proofBpc: true,
  proofThreshold: 4,
  proofStats: null,
  picked: null,
  proofImage: null,
  oogImage: null,

  stage: "structure",
  audit: null,
  verifyReport: null,
  profiles: [],
  exportCount: 0,
  theme: initialTheme(),
  toasts: [],
  busy: false,
  busyTask: null,
  fitRequest: 0,

  past: [],
  future: [],

  setTool: (tool) => set({ tool, measure: { active: tool === "measure", start: null, end: null } }),
  setCamera: (camera) => set({ camera }),
  setUnits: (units) => set({ units }),
  toggleGrid: () => set((s) => ({ showGrid: !s.showGrid })),
  setCursor: (cursor) => set({ cursor }),
  setSpacePanning: (spacePanning) => set({ spacePanning }),

  setBoxSpec: (patch) => set((s) => ({ boxSpec: { ...s.boxSpec, ...patch } })),

  generateDieline: async () => {
    const { boxSpec, toast, setBusy } = get();
    setBusy(true, "dieline");
    try {
      const payload: Record<string, unknown> = {
        model_code: boxSpec.model_code,
        dimensions: {
          length: boxSpec.length,
          width: boxSpec.width,
          height: boxSpec.height,
          unit: "mm",
        },
      };
      const params: Record<string, number> = {};
      if (boxSpec.tuck_height != null) params.tuck_height = boxSpec.tuck_height;
      if (boxSpec.glue_width != null) params.glue_width = boxSpec.glue_width;
      if (boxSpec.shrink != null) params.shrink = boxSpec.shrink;
      if (boxSpec.bleed != null) params.bleed = boxSpec.bleed;
      if (Object.keys(params).length > 0) {
        payload.parameters_mm = params;
      }
      const data = await api.dieline(payload);
      const parsed = parseDielineSvg(data.svg);
      set({
        dieline: parsed,
        dielineName: String(data.metadata.box_name_zh ?? data.metadata.box_type ?? ""),
        dielineEcma: String(data.metadata.ecma ?? ""),
        dielineFefco: String(data.metadata.fefco ?? ""),
        netSize: data.net_size_mm,
        validation: data.validation,
      });
      get().snapshot();
      toast(`已生成刀版：${get().dielineName}`, "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  },

  addLayer: (layer) => {
    set((s) => ({ layers: [...s.layers, layer] }));
    get().snapshot();
  },

  updateLayer: (id, patch) => {
    set((s) => ({
      layers: s.layers.map((l) => (l.id === id ? { ...l, ...patch } : l)),
    }));
  },

  removeLayer: (id) => {
    set((s) => ({
      layers: s.layers.filter((l) => l.id !== id),
      selectionId: s.selectionId === id ? null : s.selectionId,
    }));
    get().snapshot();
  },

  reorderLayer: (id, dir) => {
    set((s) => {
      const idx = s.layers.findIndex((l) => l.id === id);
      const target = idx + dir;
      if (idx < 0 || target < 0 || target >= s.layers.length) {
        return s;
      }
      const layers = [...s.layers];
      const [item] = layers.splice(idx, 1);
      layers.splice(target, 0, item);
      return { layers };
    });
    get().snapshot();
  },

  select: (id) => set({ selectionId: id }),

  addGuide: (axis, pos) => {
    set((s) => ({ guides: [...s.guides, { id: uid(), axis, pos }] }));
    get().snapshot();
  },
  removeGuide: (id) => {
    set((s) => ({ guides: s.guides.filter((g) => g.id !== id) }));
    get().snapshot();
  },
  clearGuides: () => {
    set({ guides: [] });
    get().snapshot();
  },
  setMeasure: (patch) => set((s) => ({ measure: { ...s.measure, ...patch } })),

  setProof: (patch) => set(patch),
  setPicked: (picked) => set({ picked }),

  toast: (text, kind = "info") => {
    const id = uid();
    set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
    window.setTimeout(() => get().dismissToast(id), 4200);
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setBusy: (busy, task) => set({ busy, busyTask: busy ? (task ?? null) : null }),

  /* 工作流与主题 */
  setStage: (stage) => set({ stage }),
  setAudit: (audit) => set({ audit }),
  setVerifyReport: (verifyReport) => set({ verifyReport }),
  loadProfiles: async () => {
    if (get().profiles.length > 0) {
      return;
    }
    try {
      const d = await api.profiles();
      set({ profiles: d.profiles });
    } catch {
      /* 扫描失败保持空列表（后端未启动等），UI 显示空态 */
    }
  },
  markExported: () => set((s) => ({ exportCount: s.exportCount + 1 })),
  setTheme: (theme) => {
    applyTheme(theme);
    set({ theme });
  },

  snapshot: () => {
    set((s) => ({
      past: [...s.past.slice(-49), takeSnapshot(s)],
      future: [],
    }));
  },

  undo: () => {
    const state = get();
    const prev = state.past[state.past.length - 1];
    if (!prev) {
      return;
    }
    set((s) => ({
      past: s.past.slice(0, -1),
      future: [...s.future, takeSnapshot(s)],
      layers: prev.layers,
      guides: prev.guides,
      selectionId: null,
    }));
  },

  redo: () => {
    const state = get();
    const next = state.future[state.future.length - 1];
    if (!next) {
      return;
    }
    set((s) => ({
      future: s.future.slice(0, -1),
      past: [...s.past, takeSnapshot(s)],
      layers: next.layers,
      guides: next.guides,
      selectionId: null,
    }));
  },

  requestFit: () => set((s) => ({ fitRequest: s.fitRequest + 1 })),
}));

/** 便捷选择器：可见图层（渲染用）。 */
export const selectVisibleLayers = (s: EditorState): LayerBase[] => s.layers;
