import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { applyTheme, useEditor } from "./store/editor";
import "./styles/tokens.css";

// 首帧前应用主题（深色默认，localStorage 持久化），避免闪白/闪黑
applyTheme(useEditor.getState().theme);

const rootEl = document.getElementById("root");
if (rootEl) {
  createRoot(rootEl).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
