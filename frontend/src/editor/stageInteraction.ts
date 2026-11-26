/** 画布交互：工具行为、拖拽、吸附、快捷键。作为 hook 供 CanvasStage 使用。 */

import { useEffect, useRef, useState } from "react";
import { useEditor } from "../store/editor";
import { api } from "../lib/api";
import { sampleArtworkPixel } from "./renderer";
import type { Camera, Guide, Vec2 } from "./types";
import { pointInRotatedLayer, screenToWorld, zoomAt } from "./viewport";

type Drag =
  | { mode: "pan"; startScreen: Vec2; startCam: Camera }
  | { mode: "move"; id: string; startWorld: Vec2; origin: Vec2; snapshotted: boolean }
  | { mode: "measure"; startWorld: Vec2 }
  | { mode: "guide"; axis: "x" | "y" }
  | null;

export interface StageSize {
  w: number;
  h: number;
}

export function useStageInteraction(canvasRef: React.RefObject<HTMLCanvasElement | null>) {
  const dragRef = useRef<Drag>(null);
  const [guidePreview, setGuidePreview] = useState<Guide | null>(null);
  const [panCursor, setPanCursor] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }

    const toScreen = (e: PointerEvent | WheelEvent): Vec2 => {
      const rect = canvas.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    const hitLayer = (world: Vec2): string | null => {
      const { layers } = useEditor.getState();
      for (let i = layers.length - 1; i >= 0; i -= 1) {
        const l = layers[i];
        if (!l.visible || l.locked || !l.bitmap) {
          continue;
        }
        if (l.kind === "proof" || l.kind === "oog") {
          continue;
        }
        if (pointInRotatedLayer(world, l)) {
          return l.id;
        }
      }
      return null;
    };

    /** 吸附：参考线 + 其他图层边缘（6px 阈值）。返回吸附后的坐标。 */
    const snap = (world: Vec2, ignoreId: string): Vec2 => {
      const state = useEditor.getState();
      const threshold = 6 / state.camera.scale;
      let bestX: number | null = null;
      let bestY: number | null = null;
      let bestDx = Infinity;
      let bestDy = Infinity;
      const consider = (x: number | null, y: number | null) => {
        if (x != null) {
          const d = Math.abs(x - world.x);
          if (d < bestDx) {
            bestDx = d;
            bestX = x;
          }
        }
        if (y != null) {
          const d = Math.abs(y - world.y);
          if (d < bestDy) {
            bestDy = d;
            bestY = y;
          }
        }
      };
      for (const g of state.guides) {
        consider(g.axis === "x" ? g.pos : null, g.axis === "y" ? g.pos : null);
      }
      for (const l of state.layers) {
        if (l.id === ignoreId || !l.visible) {
          continue;
        }
        consider(l.x, l.y);
        consider(l.x + l.w, l.y + l.h);
        consider(l.x, l.y + l.h);
        consider(l.x + l.w, l.y);
      }
      const snapped = { x: world.x, y: world.y };
      if (bestX != null && bestDx <= threshold) {
        snapped.x = bestX;
      }
      if (bestY != null && bestDy <= threshold) {
        snapped.y = bestY;
      }
      return snapped;
    };

    const onPointerDown = (e: PointerEvent) => {
      const state = useEditor.getState();
      const screen = toScreen(e);
      const world = screenToWorld(screen, state.camera);
      canvas.setPointerCapture(e.pointerId);

      if (state.spacePanning || state.tool === "pan" || e.button === 1) {
        dragRef.current = { mode: "pan", startScreen: screen, startCam: state.camera };
        setPanCursor(true);
        return;
      }
      if (e.button !== 0) {
        return;
      }
      if (state.tool === "select") {
        const id = hitLayer(world);
        state.select(id);
        if (id) {
          const layer = state.layers.find((l) => l.id === id);
          if (layer) {
            dragRef.current = {
              mode: "move",
              id,
              startWorld: world,
              origin: { x: layer.x, y: layer.y },
              snapshotted: false,
            };
          }
        }
        return;
      }
      if (state.tool === "measure") {
        state.setMeasure({ active: true, start: world, end: world });
        dragRef.current = { mode: "measure", startWorld: world };
        return;
      }
      if (state.tool === "eyedropper") {
        // 从离屏纯素材层取色，避免受打样/越界掩图/刀线/网格干扰
        const rgb = sampleArtworkPixel(world, state.layers);
        state.setPicked({ rgb, lab: null });
        api
          .lab(rgb, {
            profile: state.deviceProfilePath || undefined,
            intent: state.proofIntent,
            bpc: state.proofBpc,
          })
          .then((r) =>
            state.setPicked({
              rgb,
              lab: r.lab,
              colorSpace: r.color_space,
              deviceValues: r.device_values,
              tac: r.tac,
            }),
          )
          .catch((err) => {
            state.setPicked({ rgb, lab: null });
            state.toast(err instanceof Error ? err.message : "取色换算失败", "error");
          });
      }
    };

    const onPointerMove = (e: PointerEvent) => {
      const state = useEditor.getState();
      const screen = toScreen(e);
      const world = screenToWorld(screen, state.camera);
      state.setCursor(world);
      const drag = dragRef.current;
      if (!drag) {
        return;
      }
      if (drag.mode === "pan") {
        const dx = (screen.x - drag.startScreen.x) / state.camera.scale;
        const dy = (screen.y - drag.startScreen.y) / state.camera.scale;
        state.setCamera({
          x: drag.startCam.x - dx,
          y: drag.startCam.y - dy,
          scale: drag.startCam.scale,
        });
        return;
      }
      if (drag.mode === "move") {
        if (!drag.snapshotted) {
          state.snapshot();
          drag.snapshotted = true;
        }
        const target = { x: drag.origin.x + (world.x - drag.startWorld.x), y: drag.origin.y + (world.y - drag.startWorld.y) };
        const snapped = snap(target, drag.id);
        state.updateLayer(drag.id, { x: snapped.x, y: snapped.y });
        return;
      }
      if (drag.mode === "measure") {
        state.setMeasure({ end: world });
        return;
      }
      void drag;
    };

    const finishDrag = () => {
      dragRef.current = null;
      setPanCursor(false);
    };

    const onPointerUp = (e: PointerEvent) => {
      if (canvas.hasPointerCapture(e.pointerId)) {
        canvas.releasePointerCapture(e.pointerId);
      }
      finishDrag();
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const state = useEditor.getState();
      const factor = Math.pow(1.0018, -e.deltaY);
      state.setCamera(zoomAt(state.camera, factor, toScreen(e)));
    };

    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault();
      const state = useEditor.getState();
      const world = screenToWorld(toScreen(e as unknown as PointerEvent), state.camera);
      // 右键：就近删除参考线（10px 内）
      const threshold = 10 / state.camera.scale;
      const near = state.guides.find(
        (g) => (g.axis === "x" && Math.abs(g.pos - world.x) <= threshold) ||
          (g.axis === "y" && Math.abs(g.pos - world.y) <= threshold),
      );
      if (near) {
        state.removeGuide(near.id);
      }
    };

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("contextmenu", onContextMenu);
    return () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("contextmenu", onContextMenu);
    };
  }, [canvasRef]);

  /** 标尺拖拽出参考线（CanvasStage 把标尺事件转发进来）。 */
  const rulerDrag = useRef<{ axis: "x" | "y" } | null>(null);

  const onRulerDown = (axis: "x" | "y") => {
    rulerDrag.current = { axis };
    setGuidePreview({ id: "preview", axis, pos: 0 });
  };

  const onRulerMove = (axis: "x" | "y", screenPos: number) => {
    const state = useEditor.getState();
    const pos = axis === "x" ? screenPos / state.camera.scale + state.camera.x : screenPos / state.camera.scale + state.camera.y;
    if (rulerDrag.current?.axis === axis) {
      setGuidePreview({ id: "preview", axis, pos });
    }
    return pos;
  };

  const onRulerUp = () => {
    const state = useEditor.getState();
    if (rulerDrag.current && guidePreview) {
      state.addGuide(guidePreview.axis, guidePreview.pos);
    }
    rulerDrag.current = null;
    setGuidePreview(null);
  };

  /** 全局快捷键。 */
  useEffect(() => {
    const isTyping = (): boolean => {
      const el = document.activeElement;
      return el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement;
    };
    const onKeyDown = (e: KeyboardEvent) => {
      const state = useEditor.getState();
      if (isTyping()) {
        return;
      }
      if (e.code === "Space") {
        state.setSpacePanning(true);
        e.preventDefault();
        return;
      }
      const ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && e.key === "z") {
        e.preventDefault();
        if (e.shiftKey) {
          state.redo();
        } else {
          state.undo();
        }
        return;
      }
      if (ctrl && e.key === "y") {
        e.preventDefault();
        state.redo();
        return;
      }
      if (ctrl && e.key === "0") {
        e.preventDefault();
        state.requestFit();
        return;
      }
      if (e.key === "v" || e.key === "V") state.setTool("select");
      else if (e.key === "h" || e.key === "H") state.setTool("pan");
      else if (e.key === "m" || e.key === "M") state.setTool("measure");
      else if (e.key === "i" || e.key === "I") state.setTool("eyedropper");
      else if (e.key === "g" || e.key === "G") state.toggleGrid();
      else if (e.key === "Escape") state.select(null);
      else if ((e.key === "Delete" || e.key === "Backspace") && state.selectionId) {
        state.removeLayer(state.selectionId);
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        useEditor.getState().setSpacePanning(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, []);

  return { guidePreview, panCursor, onRulerDown, onRulerMove, onRulerUp };
}
