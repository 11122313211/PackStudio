import { describe, expect, it } from "vitest";
import { fitCamera, pointInRotatedLayer, screenToWorld, worldToScreen, zoomAt } from "./viewport";

describe("viewport 坐标与旋转命中", () => {
  it("worldToScreen 与 screenToWorld 互逆", () => {
    const cam = { x: -20, y: 15, scale: 3.5 };
    const w = { x: 42.5, y: 88.2 };
    const back = screenToWorld(worldToScreen(w, cam), cam);
    expect(back.x).toBeCloseTo(w.x, 6);
    expect(back.y).toBeCloseTo(w.y, 6);
  });

  it("zoomAt 保持光标指向的世界点不动", () => {
    const cam = { x: 10, y: 20, scale: 2 };
    const screen = { x: 300, y: 240 };
    const before = screenToWorld(screen, cam);
    const next = zoomAt(cam, 1.5, screen);
    const after = screenToWorld(screen, next);
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  it("fitCamera 在扣除玻璃框架遮挡后将内容盒居中", () => {
    const cam = fitCamera({ x: 0, y: 0, w: 200, h: 100 }, 1200, 800, {
      left: 48,
      right: 312,
      top: 72,
      bottom: 26,
    });
    const center = worldToScreen({ x: 100, y: 50 }, cam);
    expect(center.x).toBeCloseTo(48 + (1200 - 48 - 312) / 2, 3);
    expect(center.y).toBeCloseTo(72 + (800 - 72 - 26) / 2, 3);
  });

  it("pointInRotatedLayer 精准处理未旋转与 90° 旋转图层", () => {
    const unrotated = { x: 10, y: 20, w: 40, h: 10, rotation: 0 };
    expect(pointInRotatedLayer({ x: 25, y: 25 }, unrotated)).toBe(true);
    expect(pointInRotatedLayer({ x: 5, y: 25 }, unrotated)).toBe(false);

    // 绕左上角 (10, 20) 顺时针旋转 90°：原宽 40 朝 +y 延伸到 (10, 60)，原高 10 朝 -x 延伸到 (0, 20)
    const rotated90 = { x: 10, y: 20, w: 40, h: 10, rotation: 90 };
    expect(pointInRotatedLayer({ x: 5, y: 40 }, rotated90)).toBe(true);
    // 未旋转时在框内但旋转后已经不在框内
    expect(pointInRotatedLayer({ x: 35, y: 25 }, rotated90)).toBe(false);
  });
});
