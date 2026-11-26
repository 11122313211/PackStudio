/** 编辑器全局状态（Zustand）：文档/视图/交互/历史/工作流。 */

import { create } from "zustand";
import { api } from "../lib/api";
import type { ProfileInfo, PdfAuditReport, VerifyResponse, ProofResponse, ValidationInfo } from "../lib/api";
import { buildStructurePayload } from "../lib/boxPayload";
import type { StageId } from "../flow/stages";
import type { ThemeName } from "../editor/theme";
import type {
  BoxSpec,
  Camera,
  Guide,
  LayerBase,
  MeasureState,
  ParsedDieline,
  PickedColor,
  ToolKind,
} from "../editor/types";
import { uid } from "../editor/types";
import { parseDielineSvg } from "../editor/svgparse";

export interface Toast {
  id: string;
  kind: "info" | "ok" | "error";
  text: string;
}

interface Snapshot {
  layers: LayerBase[];
  guides: Guide[];
}

export type ProofMetric = "de00" | "de76";

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
  proofMetric: ProofMetric;
  proofStats: ProofResponse["stats"] | null;
  /** 素材/刀版/打样配置改动后，旧打样覆盖层是否已过期 */
  proofStale: boolean;
  /** 打样配置或内容改动后，旧 CGATS 测量报告是否已过期 */
  verifyStale: boolean;

  /* 取色（吸管工具联动，含设备通道值/CMYK/TAC 与 Lab） */
  picked: PickedColor | null;
  setPicked: (p: PickedColor | null) => void;

  /* 打样覆盖图（位图不进历史，替换时自动 close 释放显存） */
  proofImage: ImageBitmap | null;
  oogImage: ImageBitmap | null;

  /* UI（工作流五阶段 + 闭环数据 + 主题；不进 undo 快照） */
  stage: StageId;
  audit: PdfAuditReport | null;
  verifyReport: VerifyResponse | null;
  profiles: ProfileInfo[];
  exportCount: number;
  theme: ThemeName;
  toasts: Toast[];
  busy: boolean;
  busyTask: string | null;
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
  addLayers: (layers: LayerBase[]) => void;
  updateLayer: (id: string, patch: Partial<LayerBase>, recordHistory?: boolean) => void;
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
    proofMetric?: ProofMetric;
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
  loadProfiles: (force?: boolean) => Promise<void>;
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

/** 构造让旧打样与旧验证失效的局部状态补丁。 */
function stalePatch(s: EditorState): Partial<EditorState> {
  return {
    proofStale: s.proofStats !== null ? true : s.proofStale,
    verifyStale: s.verifyReport !== null ? true : s.verifyStale,
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
  proofMetric: "de00",
  proofStats: null,
  proofStale: false,
  verifyStale: false,
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
      const payload = buildStructurePayload(boxSpec);
      const data = await api.dieline(payload);
      const parsed = parseDielineSvg(data.svg);
      set((s) => ({
        dieline: parsed,
        dielineName: String(data.metadata.box_name_zh ?? data.metadata.box_type ?? ""),
        dielineEcma: String(data.metadata.ecma ?? ""),
        dielineFefco: String(data.metadata.fefco ?? ""),
        netSize: data.net_size_mm,
        validation: data.validation,
        ...stalePatch(s),
      }));
      toast(`已生成刀版：${get().dielineName}`, "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  },

  addLayer: (layer) => {
    get().snapshot();
    set((s) => ({ layers: [...s.layers, layer], ...stalePatch(s) }));
  },

  addLayers: (newLayers) => {
    if (newLayers.length === 0) {
      return;
    }
    get().snapshot();
    set((s) => ({ layers: [...s.layers, ...newLayers], ...stalePatch(s) }));
  },

  updateLayer: (id, patch, recordHistory = false) => {
    if (recordHistory) {
      get().snapshot();
    }
    set((s) => ({
      layers: s.layers.map((l) => (l.id === id ? { ...l, ...patch } : l)),
      ...stalePatch(s),
    }));
  },

  removeLayer: (id) => {
    get().snapshot();
    set((s) => ({
      layers: s.layers.filter((l) => l.id !== id),
      selectionId: s.selectionId === id ? null : s.selectionId,
      ...stalePatch(s),
    }));
  },

  reorderLayer: (id, dir) => {
    const cur = get().layers;
    const idx = cur.findIndex((l) => l.id === id);
    const target = idx + dir;
    if (idx < 0 || target < 0 || target >= cur.length) {
      return;
    }
    get().snapshot();
    set((s) => {
      const layers = [...s.layers];
      const [item] = layers.splice(idx, 1);
      layers.splice(target, 0, item);
      return { layers, ...stalePatch(s) };
    });
  },

  select: (id) => set({ selectionId: id }),

  addGuide: (axis, pos) => {
    get().snapshot();
    set((s) => ({ guides: [...s.guides, { id: uid(), axis, pos }] }));
  },
  removeGuide: (id) => {
    get().snapshot();
    set((s) => ({ guides: s.guides.filter((g) => g.id !== id) }));
  },
  clearGuides: () => {
    get().snapshot();
    set({ guides: [] });
  },
  setMeasure: (patch) => set((s) => ({ measure: { ...s.measure, ...patch } })),

  setProof: (patch) =>
    set((s) => {
      // 显存回收：替换旧 ImageBitmap 时安全释放 GPU 句柄
      if (patch.proofImage !== undefined && s.proofImage && s.proofImage !== patch.proofImage) {
        try {
          s.proofImage.close();
        } catch {
          /* ignore */
        }
      }
      if (patch.oogImage !== undefined && s.oogImage && s.oogImage !== patch.oogImage) {
        try {
          s.oogImage.close();
        } catch {
          /* ignore */
        }
      }

      const configChanged =
        (patch.deviceProfilePath !== undefined && patch.deviceProfilePath !== s.deviceProfilePath) ||
        (patch.proofIntent !== undefined && patch.proofIntent !== s.proofIntent) ||
        (patch.proofBpc !== undefined && patch.proofBpc !== s.proofBpc) ||
        (patch.proofThreshold !== undefined && patch.proofThreshold !== s.proofThreshold) ||
        (patch.proofMetric !== undefined && patch.proofMetric !== s.proofMetric);

      const next: Partial<EditorState> = { ...patch };
      if (patch.proofStats !== undefined) {
        // 新一轮软打样完成：打样自身刷新为最新，但若配置变了仍保留 verifyStale
        next.proofStale = false;
      } else if (configChanged) {
        Object.assign(next, stalePatch(s));
      }
      return next;
    }),

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
  setVerifyReport: (verifyReport) => set({ verifyReport, verifyStale: false }),
  loadProfiles: async (force = false) => {
    if (!force && get().profiles.length > 0) {
      return;
    }
    try {
      const d = await api.profiles(force);
      set({ profiles: d.profiles });
      if (force) {
        get().toast(`已重新扫描系统 ICC：共 ${d.profiles.length} 个`, "ok");
      }
    } catch {
      if (force) {
        get().toast("扫描系统 ICC 失败，请确认后端已连接", "error");
      }
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
      ...stalePatch(s),
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
      ...stalePatch(s),
    }));
  },

  requestFit: () => set((s) => ({ fitRequest: s.fitRequest + 1 })),
}));

/** 便捷选择器：可见图层（渲染用）。 */
export const selectVisibleLayers = (s: EditorState): LayerBase[] => s.layers;
