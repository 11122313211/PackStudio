/** 中央画布区：场景渲染、标尺、适配窗口、拖放导入与空状态提示。 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useEditor } from "../store/editor";
import { contentBox, renderScene } from "../editor/renderer";
import { getPalette } from "../editor/theme";
import { fitCamera } from "../editor/viewport";
import { useStageInteraction } from "../editor/stageInteraction";
import { registerMainCanvas } from "../editor/canvasRegistry";
import { importFiles } from "../editor/importFiles";
import { PT_PER_MM } from "../editor/types";

/** 玻璃框架遮挡的画布内缩（px），与 tokens.css 的 --toolbar-w / --rightbar-w /
 *  --topbar-h / --statusbar-h / --ruler-h 保持一致；fit 时按真实可视区居中。 */
const SHELL_INSET = { left: 48, right: 312, top: 48 + 24, bottom: 26 };

export function CanvasStage() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rulerXRef = useRef<HTMLCanvasElement>(null);
  const rulerYRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [dragOver, setDragOver] = useState(false);

  const camera = useEditor((s) => s.camera);
  const dieline = useEditor((s) => s.dieline);
  const layers = useEditor((s) => s.layers);
  const selectionId = useEditor((s) => s.selectionId);
  const guides = useEditor((s) => s.guides);
  const measure = useEditor((s) => s.measure);
  const showGrid = useEditor((s) => s.showGrid);
  const units = useEditor((s) => s.units);
  const tool = useEditor((s) => s.tool);
  const fitRequest = useEditor((s) => s.fitRequest);
  const proofImage = useEditor((s) => s.proofImage);
  const oogImage = useEditor((s) => s.oogImage);
  const proofVisible = useEditor((s) => s.proofVisible);
  const oogVisible = useEditor((s) => s.oogVisible);
  const theme = useEditor((s) => s.theme);
  const setCamera = useEditor((s) => s.setCamera);
  const { guidePreview, panCursor, onRulerDown, onRulerMove, onRulerUp } =
    useStageInteraction(canvasRef);

  /* 尺寸自适应 */
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) {
      return;
    }
    const ro = new ResizeObserver(() => {
      const rect = wrap.getBoundingClientRect();
      setSize({ w: Math.floor(rect.width), h: Math.floor(rect.height) });
    });
    ro.observe(wrap);
    const rect = wrap.getBoundingClientRect();
    setSize({ w: Math.floor(rect.width), h: Math.floor(rect.height) });
    return () => ro.disconnect();
  }, []);

  const drawRulers = useCallback(() => {
    const dpr = window.devicePixelRatio || 1;
    const pt = units === "pt";
    const unit = (mm: number) => (pt ? mm * PT_PER_MM : mm);
    const palette = getPalette(theme);
    const RULER = 24;
    // 顶部标尺
    const rx = rulerXRef.current;
    if (rx && size.w > 0) {
      const targetW = Math.floor(size.w * dpr);
      const targetH = Math.floor(RULER * dpr);
      if (rx.width !== targetW || rx.height !== targetH) {
        rx.width = targetW;
        rx.height = targetH;
        rx.style.width = `${size.w}px`;
        rx.style.height = `${RULER}px`;
      }
      const ctx = rx.getContext("2d");
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, size.w, RULER);
        ctx.font = "9px Consolas, monospace";
        const step = camera.scale * 10 < 10 ? 100 : camera.scale * 10 < 28 ? 50 : 10;
        const start = Math.floor(camera.x / step) * step;
        for (let pos = start; (pos - camera.x) * camera.scale < size.w; pos += step) {
          const sx = Math.round((pos - camera.x) * camera.scale) + 0.5;
          ctx.strokeStyle = palette.rulerTick;
          ctx.beginPath();
          ctx.moveTo(sx, RULER - 8);
          ctx.lineTo(sx, RULER);
          ctx.stroke();
          ctx.fillStyle = palette.rulerText;
          ctx.fillText(String(Math.round(unit(pos))), sx + 2, 11);
        }
      }
    }
    // 左侧标尺
    const ry = rulerYRef.current;
    if (ry && size.h > 0) {
      const targetW = Math.floor(RULER * dpr);
      const targetH = Math.floor(size.h * dpr);
      if (ry.width !== targetW || ry.height !== targetH) {
        ry.width = targetW;
        ry.height = targetH;
        ry.style.width = `${RULER}px`;
        ry.style.height = `${size.h}px`;
      }
      const ctx = ry.getContext("2d");
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, RULER, size.h);
        ctx.font = "9px Consolas, monospace";
        const step = camera.scale * 10 < 10 ? 100 : camera.scale * 10 < 28 ? 50 : 10;
        const start = Math.floor(camera.y / step) * step;
        for (let pos = start; (pos - camera.y) * camera.scale < size.h; pos += step) {
          const sy = Math.round((pos - camera.y) * camera.scale) + 0.5;
          ctx.strokeStyle = palette.rulerTick;
          ctx.beginPath();
          ctx.moveTo(RULER - 8, sy);
          ctx.lineTo(RULER, sy);
          ctx.stroke();
          ctx.save();
          ctx.translate(11, sy + 2);
          ctx.rotate(-Math.PI / 2);
          ctx.fillStyle = palette.rulerText;
          ctx.fillText(String(Math.round(unit(pos))), 2, 0);
          ctx.restore();
        }
      }
    }
  }, [camera, size, units, theme]);

  /* 精确依赖的渲染循环：仅当画布尺寸/dpr 变化时重设 backing store，不再双绘 cleanCanvas */
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.w === 0) {
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    const targetW = Math.floor(size.w * dpr);
    const targetH = Math.floor(size.h * dpr);
    if (canvas.width !== targetW || canvas.height !== targetH) {
      canvas.width = targetW;
      canvas.height = targetH;
      canvas.style.width = `${size.w}px`;
      canvas.style.height = `${size.h}px`;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    const allGuides = guidePreview ? [...guides, guidePreview] : guides;
    renderScene({
      ctx,
      camera,
      dpr,
      viewW: size.w,
      viewH: size.h,
      dieline,
      layers,
      selectionId,
      guides: allGuides,
      measure,
      showGrid,
      proofImage,
      oogImage,
      proofVisible,
      oogVisible,
      theme,
    });
    drawRulers();
  }, [
    size,
    camera,
    dieline,
    layers,
    selectionId,
    guides,
    guidePreview,
    measure,
    showGrid,
    proofImage,
    oogImage,
    proofVisible,
    oogVisible,
    theme,
    drawRulers,
  ]);

  /* 适配窗口：fitRequest 变化或刀版内容尺寸变化（首次生成/换盒型/改尺寸） */
  const lastFitKey = useRef("");
  useEffect(() => {
    if (size.w === 0) {
      return;
    }
    const contentKey = dieline
      ? `${Math.round(dieline.viewBox.w)}x${Math.round(dieline.viewBox.h)}`
      : "none";
    const key = `${fitRequest}:${contentKey}`;
    if (key !== lastFitKey.current && (dieline || layers.length > 0)) {
      const box = contentBox(dieline, layers);
      setCamera(fitCamera(box, size.w, size.h, SHELL_INSET));
      lastFitKey.current = key;
    }
  }, [fitRequest, dieline, layers, size, setCamera]);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) {
      return;
    }
    const st = useEditor.getState();
    st.setStage("content");
    void importFiles(files, {
      addLayers: st.addLayers,
      dieline: st.dieline,
      setBusy: st.setBusy,
      setAudit: st.setAudit,
      toast: st.toast,
    });
  };

  const cursorCss =
    panCursor || tool === "pan"
      ? "grab"
      : tool === "measure" || tool === "eyedropper"
        ? "crosshair"
        : "default";

  return (
    <section className="stage">
      <div className="ruler-corner">{units}</div>
      <canvas
        className="ruler-x"
        ref={rulerXRef}
        onPointerDown={(e) => {
          onRulerDown("x");
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const rect = (e.target as HTMLElement).getBoundingClientRect();
          onRulerMove("x", e.clientX - rect.left);
        }}
        onPointerUp={() => onRulerUp()}
      />
      <canvas
        className="ruler-y"
        ref={rulerYRef}
        onPointerDown={(e) => {
          onRulerDown("y");
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const rect = (e.target as HTMLElement).getBoundingClientRect();
          onRulerMove("y", e.clientY - rect.top);
        }}
        onPointerUp={() => onRulerUp()}
      />
      <div
        className={"canvas-wrap" + (dragOver ? " drag-over" : "")}
        ref={wrapRef}
        onDragOver={(e) => {
          e.preventDefault();
          if (!dragOver) {
            setDragOver(true);
          }
        }}
        onDragLeave={(e) => {
          if (e.currentTarget === e.target) {
            setDragOver(false);
          }
        }}
        onDrop={handleDrop}
      >
        <canvas
          ref={(el) => {
            (canvasRef as { current: HTMLCanvasElement | null }).current = el;
            registerMainCanvas(el);
          }}
          style={{ cursor: cursorCss }}
        />
        {!dieline && layers.length === 0 ? (
          <div className="stage-hint">
            <svg className="sh-glyph" width="46" height="36" viewBox="0 0 46 36" aria-hidden="true">
              <path
                d="M15.5 6.5 H2 Q0.5 6.5 0.5 8 V32 Q0.5 33.5 2 33.5 H44 Q45.5 33.5 45.5 32 V8 Q45.5 6.5 44 6.5 H30.5"
                fill="none"
                stroke="var(--cut)"
                strokeWidth="1"
              />
              <path d="M15.5 6.5 V1.5 H30.5 V6.5" fill="none" stroke="var(--cut)" strokeWidth="1" />
              <g fill="none" stroke="var(--crease)" strokeWidth="1" strokeDasharray="3 3">
                <line x1="15.5" y1="6.5" x2="30.5" y2="6.5" />
                <line x1="15.5" y1="7" x2="15.5" y2="33" />
                <line x1="30.5" y1="7" x2="30.5" y2="33" />
              </g>
            </svg>
            <b>PackStudio 包装印前工作台</b>
            <div className="sh-steps">
              <span>生成刀版</span>
              <i>→</i>
              <span>导入素材</span>
              <i>→</i>
              <span>软打样 · 导出</span>
            </div>
            <span className="sh-sub">在右侧「盒型结构」开始，或直接将 PDF / 图片拖入画布</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}
