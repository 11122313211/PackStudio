/** 右侧工作流面板：五阶段步进器 + 当前阶段内容（一次只披露一个阶段）。
 *  软门控：locked 阶段可点入查看，但显示前置原因、禁用主体操作。 */

import { useEditor } from "../store/editor";
import { FlowTracker } from "../flow/FlowTracker";
import { useStageViews } from "../flow/stages";
import { StructureStage } from "../stages/StructureStage";
import { ContentStage } from "../stages/ContentStage";
import { ColorStage } from "../stages/ColorStage";
import { ProofStage } from "../stages/ProofStage";
import { DeliverStage } from "../stages/DeliverStage";

const STAGE_COMPONENTS = {
  structure: StructureStage,
  content: ContentStage,
  color: ColorStage,
  proof: ProofStage,
  deliver: DeliverStage,
} as const;

export function RightPanel() {
  const stage = useEditor((s) => s.stage);
  const views = useStageViews();
  const view = views[stage];
  const Body = STAGE_COMPONENTS[stage];

  return (
    <aside className="side">
      <FlowTracker />
      <div className="stage-head">
        <span className="stage-index">{view.index}</span>
        <span className="stage-title">{view.title}</span>
        {view.summary ? <span className="stage-summary">{view.summary}</span> : null}
      </div>
      {view.status === "locked" && view.lockedReason ? (
        <div className="notice">
          <b>前置未满足</b> — {view.lockedReason}
        </div>
      ) : null}
      <div className="stage-body">
        <Body />
      </div>
    </aside>
  );
}
