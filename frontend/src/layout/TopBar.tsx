/** 顶栏：品牌、文档名、导入（跳转②）、撤销重做、缩放、适配、主题切换。 */

import { useRef } from "react";
import { useEditor } from "../store/editor";
import { importFiles } from "../editor/importFiles";
import { Icon } from "../components/icons";

export function TopBar() {
  const docName = useEditor((s) => (s.dielineName ? `未命名包装 · ${s.dielineName}` : "未命名包装"));
  const zoom = useEditor((s) => s.camera.scale);
  const canUndo = useEditor((s) => s.past.length > 0);
  const canRedo = useEditor((s) => s.future.length > 0);
  const dieline = useEditor((s) => s.dieline);
  const undo = useEditor((s) => s.undo);
  const redo = useEditor((s) => s.redo);
  const toast = useEditor((s) => s.toast);
  const addLayers = useEditor((s) => s.addLayers);
  const setAudit = useEditor((s) => s.setAudit);
  const setBusy = useEditor((s) => s.setBusy);
  const setStage = useEditor((s) => s.setStage);
  const theme = useEditor((s) => s.theme);
  const setTheme = useEditor((s) => s.setTheme);
  const busy = useEditor((s) => s.busy);
  const fileRef = useRef<HTMLInputElement>(null);

  const zoomPct = Math.round((zoom / 2.834645669) * 100);

  const onFiles = (files: FileList | null) => {
    // 导入动作归属闭环②：跳转到素材与审计阶段，审计结果就地呈现
    setStage("content");
    void importFiles(files, { addLayers, dieline, setBusy, setAudit, toast });
    if (fileRef.current) {
      fileRef.current.value = "";
    }
  };

  return (
    <header className="topbar">
      <span className="brand">
        Pack<em>Studio</em>
      </span>
      <span className="doc-name">{docName}</span>
      <div className="spacer" />
      <button className="ghost" onClick={() => fileRef.current?.click()} disabled={busy}>
        <Icon name="import" size={13} /> 导入素材（PDF/图片）
      </button>
      <input
        ref={fileRef}
        type="file"
        accept=".pdf,image/*"
        multiple
        hidden
        onChange={(e) => onFiles(e.target.files)}
      />
      <button className="icon-btn-top" onClick={undo} disabled={!canUndo} title="撤销 (Ctrl+Z)" aria-label="撤销">
        <Icon name="undo" size={15} />
      </button>
      <button className="icon-btn-top" onClick={redo} disabled={!canRedo} title="重做 (Ctrl+Shift+Z)" aria-label="重做">
        <Icon name="redo" size={15} />
      </button>
      <div className="zoom-box">
        <b>{zoomPct}%</b>
      </div>
      <button className="icon-btn-top" onClick={() => useEditor.getState().requestFit()} title="适配窗口 (Ctrl+0)" aria-label="适配窗口">
        <Icon name="fit" size={15} />
      </button>
      <button
        className="icon-btn-top"
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        title={theme === "dark" ? "切换到浅色外观" : "切换到深色外观"}
        aria-label="切换主题"
      >
        <Icon name={theme === "dark" ? "sun" : "moon"} size={15} />
      </button>
    </header>
  );
}
