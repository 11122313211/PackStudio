/** 视口/相机数学：世界坐标（mm，y-down）↔ 屏幕像素。 */

import type { Camera, Vec2 } from "./types";

export function worldToScreen(p: Vec2, cam: Camera): Vec2 {
  return { x: (p.x - cam.x) * cam.scale, y: (p.y - cam.y) * cam.scale };
}

export function screenToWorld(p: Vec2, cam: Camera): Vec2 {
  return { x: p.x / cam.scale + cam.x, y: p.y / cam.scale + cam.y };
}

export function zoomAt(cam: Camera, factor: number, screenPoint: Vec2): Camera {
  const world = screenToWorld(screenPoint, cam);
  const nextScale = Math.min(80, Math.max(0.1, cam.scale * factor));
  // 保持光标下的世界点不动
  const x = world.x - screenPoint.x / nextScale;
  const y = world.y - screenPoint.y / nextScale;
  return { x, y, scale: nextScale };
}

/** 画布可视区内缩（屏幕像素）：玻璃框架遮住的四边宽度。 */
export interface ViewInset {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

const NO_INSET: ViewInset = { left: 0, right: 0, top: 0, bottom: 0 };

/** 视口尺寸（屏幕像素）→ 让内容盒居中于可视区（扣除玻璃框架遮挡）并留边。 */
export function fitCamera(
  box: { x: number; y: number; w: number; h: number },
  vw: number,
  vh: number,
  inset: ViewInset = NO_INSET,
): Camera {
  const availW = vw - inset.left - inset.right;
  const availH = vh - inset.top - inset.bottom;
  if (availW <= 0 || availH <= 0 || box.w <= 0 || box.h <= 0) {
    return { x: 0, y: 0, scale: 1 };
  }
  const margin = 40;
  const scale = Math.min((availW - margin * 2) / box.w, (availH - margin * 2) / box.h);
  const clamped = Math.min(80, Math.max(0.1, scale));
  // 可视区中心在画布屏幕坐标中的位置
  const cx = inset.left + availW / 2;
  const cy = inset.top + availH / 2;
  return {
    x: box.x - (cx / clamped - box.w / 2),
    y: box.y - (cy / clamped - box.h / 2),
    scale: clamped,
  };
}
