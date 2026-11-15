/** App 骨架：三区布局 + Toast。 */

import { useEditor } from "./store/editor";
import { TopBar } from "./layout/TopBar";
import { Toolbar } from "./layout/Toolbar";
import { CanvasStage } from "./layout/CanvasStage";
import { RightPanel } from "./panels/RightPanel";
import { StatusBar } from "./layout/StatusBar";

export default function App() {
  const toasts = useEditor((s) => s.toasts);
  return (
    <div className="app">
      <TopBar />
      <Toolbar />
      <CanvasStage />
      <RightPanel />
      <StatusBar />
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={"toast " + t.kind}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}
