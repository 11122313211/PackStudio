/** 五阶段色彩闭环工作流：阶段定义 + 软门控状态推导（纯函数，输入 store 快照）。
 *
 *  软门控约定：locked 仅表示前置未满足 —— 阶段仍可点入查看（渐进式披露），
 *  但面板顶部显示原因提示，主体操作按钮禁用；已完成阶段随时可回跳修改。
 *
 *  闭环叙事：① 盒型 → ② 素材审计 → ③ 色彩管理 → ④ 打样验证（测量→反馈修正）
 *  → ⑤ 输出交付。
 */

import type { EditorState } from "../store/editor";
import { useEditor } from "../store/editor";
import { useShallow } from "zustand/react/shallow";

export type StageId = "structure" | "content" | "color" | "proof" | "deliver";
export type StageStatus = "locked" | "ready" | "done" | "issue";

export interface StageView {
  id: StageId;
  index: number;
  /** 步进器短标（2 字） */
  label: string;
  /** 面板标题 */
  title: string;
  status: StageStatus;
  /** 标题行右侧摘要（done/issue 时的状态一句话） */
  summary: string;
  /** locked 时的人话原因 */
  lockedReason: string;
}

export const STAGE_ORDER: StageId[] = ["structure", "content", "color", "proof", "deliver"];

const META: Record<StageId, { index: number; label: string; title: string }> = {
  structure: { index: 1, label: "盒型", title: "盒型结构" },
  content: { index: 2, label: "素材", title: "素材与审计" },
  color: { index: 3, label: "色彩", title: "色彩管理" },
  proof: { index: 4, label: "打样", title: "打样验证" },
  deliver: { index: 5, label: "交付", title: "输出交付" },
};

const hasArtwork = (s: EditorState): boolean =>
  s.layers.some((l) => l.kind !== "proof" && l.kind !== "oog");

/** PDF 审计是否带警告（宽松契约下安全取字段）。 */
function auditWarnings(s: EditorState): number {
  if (!s.audit) {
    return 0;
  }
  const warnings = s.audit["warnings"];
  return Array.isArray(warnings) ? warnings.length : 0;
}

/** 色彩闭环总体判定（状态栏胶囊 + ④ 横幅共用）。
 *  idle=未打样 · run=已模拟未测量 · pass=闭环通过 · fail=需修正 */
export function loopVerdict(s: EditorState): "idle" | "run" | "pass" | "fail" {
  if (!s.proofStats) {
    return "idle";
  }
  if (s.verifyReport) {
    return s.verifyReport.passed ? "pass" : "fail";
  }
  return "run";
}

function view(
  id: StageId,
  status: StageStatus,
  summary = "",
  lockedReason = "",
): StageView {
  return { id, ...META[id], status, summary, lockedReason };
}

/** 从 store 快照推导五阶段状态（RightPanel/FlowTracker/StatusBar 共用）。 */
export function deriveStageViews(s: EditorState): Record<StageId, StageView> {
  const artwork = hasArtwork(s);

  /* ① 盒型结构：退出条件 = 刀版生成且 14 项机械校验通过 */
  const structure = s.dieline
    ? s.validation?.passed
      ? view(
          "structure",
          "done",
          `${s.dielineName} · ${s.netSize?.width.toFixed(0) ?? "?"}×${s.netSize?.height.toFixed(0) ?? "?"}mm`,
        )
      : view("structure", "issue", "机械校验未通过")
    : view("structure", "ready");

  /* ② 素材与审计：前置 = 刀版；退出条件 = 已有素材图层；审计有警告 → issue */
  const content = !s.dieline
    ? view("content", "locked", "", "先生成刀版（① 盒型），素材才有落位基准")
    : !artwork
      ? view("content", "ready", "")
      : auditWarnings(s) > 0
        ? view("content", "issue", `${s.layers.filter((l) => l.kind !== "proof" && l.kind !== "oog").length} 图层 · 审计有警告`)
        : view(
            "content",
            "done",
            `${s.layers.filter((l) => l.kind !== "proof" && l.kind !== "oog").length} 图层${s.audit ? " · 已审计" : ""}`,
          );

  /* ③ 色彩管理：配置型阶段，永远可用；选定输出档案即视为完成 */
  const chosenProfile = s.profiles.find((p) => p.path === s.deviceProfilePath);
  const color = s.deviceProfilePath
    ? view("color", "done", chosenProfile?.description || "已选输出档案")
    : view("color", "ready", "未选输出档案");

  /* ④ 打样验证：前置 = 素材 + 输出档案；verify 通过 → done，未通过 → issue */
  let proof: StageView;
  if (!artwork || !s.deviceProfilePath) {
    const reasons: string[] = [];
    if (!artwork) {
      reasons.push("先导入素材（② 素材）");
    }
    if (!s.deviceProfilePath) {
      reasons.push("先选择输出设备档案（③ 色彩）");
    }
    proof = view("proof", "locked", "", reasons.join("，"));
  } else if (s.verifyReport) {
    proof = s.verifyReport.passed
      ? view("proof", "done", `ΔE00 均值 ${s.verifyReport.report.stats.mean.toFixed(2)} · 闭环通过`)
      : view("proof", "issue", `ΔE00 均值 ${s.verifyReport.report.stats.mean.toFixed(2)} · 需修正`);
  } else if (s.proofStats) {
    proof = view("proof", "done", `越界 ${(s.proofStats.oog_ratio * 100).toFixed(2)}% · 未测量`);
  } else {
    proof = view("proof", "ready", "");
  }

  /* ⑤ 输出交付：前置 = 刀版；导出过 → done */
  const deliver = !s.dieline
    ? view("deliver", "locked", "", "先生成刀版（① 盒型）才有可交付产物")
    : s.exportCount > 0
      ? view("deliver", "done", `已导出 ${s.exportCount} 次${loopVerdict(s) === "run" ? " · 建议先完成测量验证" : ""}`)
      : view("deliver", "ready", "", "");

  return { structure, content, color, proof, deliver };
}

/** 状态推导依赖的字段（浅比较订阅，任一变化才重渲染；光标移动等高频更新不触发）。 */
const FLOW_KEYS = [
  "dieline",
  "validation",
  "netSize",
  "dielineName",
  "layers",
  "audit",
  "deviceProfilePath",
  "proofStats",
  "verifyReport",
  "exportCount",
  "profiles",
] as const;

/** React 侧订阅钩子：按依赖字段浅比较订阅，返回五阶段视图。 */
export function useStageViews(): Record<StageId, StageView> {
  useEditor(useShallow((s) => FLOW_KEYS.map((k) => s[k])));
  return deriveStageViews(useEditor.getState());
}
