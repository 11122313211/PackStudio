/** 底部状态栏：后端健康、光标坐标、单位、缩放、选中信息、机械校验与色彩闭环胶囊。 */

import { useEffect, useState } from "react";
import { useEditor } from "../store/editor";
import { api } from "../lib/api";
import { PT_PER_MM } from "../editor/types";
import { loopVerdict } from "../flow/stages";

export function StatusBar() {
  const cursor = useEditor((s) => s.cursor);
  const units = useEditor((s) => s.units);
  const setUnits = useEditor((s) => s.setUnits);
  const zoom = useEditor((s) => s.camera.scale);
  const selected = useEditor((s) => s.layers.find((l) => l.id === s.selectionId) ?? null);
  const validation = useEditor((s) => s.validation);
  const verdict = useEditor(loopVerdict);
  const tool = useEditor((s) => s.tool);

  const [health, setHealth] = useState<"ok" | "down" | "checking">("checking");
  useEffect(() => {
    api
      .health()
      .then(() => setHealth("ok"))
      .catch(() => setHealth("down"));
  }, []);

  const fmtPos = (mm: number) =>
    units === "pt" ? `${(mm * PT_PER_MM).toFixed(1)}pt` : `${mm.toFixed(1)}mm`;

  return (
    <footer className="statusbar">
      <span>
        后端：
        <span style={{ color: health === "ok" ? "var(--ok)" : "var(--bad)" }}>
          {health === "ok" ? "已连接" : health === "down" ? "未连接（先启动 Flask :8001）" : "…"}
        </span>
      </span>
      <span>
        光标：
        <span className="mono">
          {cursor ? `${fmtPos(cursor.x)}, ${fmtPos(cursor.y)}` : "—"}
        </span>
      </span>
      <button className="ghost" style={{ padding: "0 6px" }} onClick={() => setUnits(units === "mm" ? "pt" : "mm")}>
        单位：{units}
      </button>
      <span className="mono">{Math.round((zoom / 2.834645669) * 100)}%</span>
      <span className="spacer" />
      {selected ? (
        <span>
          选中：{selected.name}（{selected.w.toFixed(1)}×{selected.h.toFixed(1)}mm）
        </span>
      ) : (
        <span>{TOOL_HINT[tool]}</span>
      )}
      {validation ? (
        <span className={"status-pill " + (validation.passed ? "pass" : "fail")}>
          校验{validation.passed ? "通过" : "未通过"}
        </span>
      ) : null}
      <span
        className={
          "status-pill " +
          (verdict === "pass" ? "pass" : verdict === "fail" ? "fail" : verdict === "run" ? "warn" : "")
        }
        title="色彩闭环状态（④ 打样验证）"
      >
        {verdict === "idle" ? "闭环未开始" : verdict === "run" ? "已模拟未测量" : verdict === "pass" ? "闭环通过" : "需修正"}
      </span>
    </footer>
  );
}

const TOOL_HINT: Record<string, string> = {
  select: "选择工具：点击图层选中并拖动；空格拖拽平移；右键删除附近参考线",
  pan: "平移工具：拖拽移动画布",
  measure: "度量工具：拖拽测量距离（mm）",
  eyedropper: "取色工具：点击画布读取 RGB/Lab",
};
