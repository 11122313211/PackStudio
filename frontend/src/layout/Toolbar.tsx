/** 左侧工具列：选择 / 平移 / 度量 / 取色 / 网格（SF 风格线性图标）。 */

import { useEditor } from "../store/editor";
import type { ToolKind } from "../editor/types";
import { Icon, type IconName } from "../components/icons";

const TOOLS: { kind: ToolKind; icon: IconName; label: string; key: string }[] = [
  { kind: "select", icon: "cursor", label: "选择 / 移动", key: "V" },
  { kind: "pan", icon: "hand", label: "平移画布", key: "H" },
  { kind: "measure", icon: "ruler", label: "度量距离", key: "M" },
  { kind: "eyedropper", icon: "eyedropper", label: "取色（Lab）", key: "I" },
];

export function Toolbar() {
  const tool = useEditor((s) => s.tool);
  const setTool = useEditor((s) => s.setTool);
  const showGrid = useEditor((s) => s.showGrid);
  const toggleGrid = useEditor((s) => s.toggleGrid);

  return (
    <nav className="toolbar">
      {TOOLS.map((t) => (
        <button
          key={t.kind}
          className={"tool-btn" + (tool === t.kind ? " active" : "")}
          title={`${t.label} (${t.key})`}
          onClick={() => setTool(t.kind)}
        >
          <Icon name={t.icon} size={17} />
        </button>
      ))}
      <div className="tool-sep" />
      <button
        className={"tool-btn" + (showGrid ? " active" : "")}
        title="参考网格 (G)"
        onClick={toggleGrid}
      >
        <Icon name="grid" size={17} />
      </button>
    </nav>
  );
}
