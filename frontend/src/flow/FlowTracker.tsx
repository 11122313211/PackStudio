/** 工作流步进器（iOS 分段控件风格）：五阶段 + 状态点。
 *  软门控：locked 阶段仍可点入（渐进式披露），由面板内提示原因。 */

import { useEditor } from "../store/editor";
import { STAGE_ORDER, useStageViews, type StageView } from "./stages";

const DOT_CLASS: Record<StageView["status"], string> = {
  locked: "",
  ready: "ready",
  done: "done",
  issue: "issue",
};

export function FlowTracker() {
  const stage = useEditor((s) => s.stage);
  const setStage = useEditor((s) => s.setStage);
  const views = useStageViews();

  return (
    <nav className="flow-tracker" aria-label="色彩闭环工作流">
      {STAGE_ORDER.map((id) => {
        const v = views[id];
        const active = stage === id;
        return (
          <button
            key={id}
            className={
              "flow-seg" + (active ? " active" : "") + (v.status === "locked" && !active ? " locked" : "")
            }
            title={v.status === "locked" ? v.lockedReason : `${v.index}. ${v.title}${v.summary ? " — " + v.summary : ""}`}
            onClick={() => setStage(id)}
          >
            <span className={"flow-dot " + (active ? "active" : DOT_CLASS[v.status])} />
            <span className="flow-label">{v.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
