/** 主画布注册表与 Canvas 编码辅助。
 *  注：打样快照与展平导出已迁移为按需世界坐标离屏渲染（renderer.ts::renderOffscreen），
 *  不再维护每帧同步重绘的全局双缓冲 cleanCanvas。 */

let mainCanvas: HTMLCanvasElement | null = null;

export function registerMainCanvas(el: HTMLCanvasElement | null): void {
  mainCanvas = el;
}

export function getMainCanvas(): HTMLCanvasElement | null {
  return mainCanvas;
}

/** 把 Canvas 异步编码为 PNG Blob。 */
export function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/png");
  });
}
