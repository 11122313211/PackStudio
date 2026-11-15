/** 主画布注册表：让面板能取到画布快照（打样/展平导出用）。 */

let mainCanvas: HTMLCanvasElement | null = null;
/** 干净合成画布：只含 纸张+网格+面板+图层+刀版，不含打样/掩图/参考线/测量覆盖。
 *  打样快照与展平导出都取自它，避免把上一次的覆盖层烤进素材（自污染）。 */
let cleanCanvas: HTMLCanvasElement | null = null;

export function registerMainCanvas(el: HTMLCanvasElement | null): void {
  mainCanvas = el;
}

export function getMainCanvas(): HTMLCanvasElement | null {
  return mainCanvas;
}

/** 干净合成画布（惰性创建，尺寸由渲染循环与主画布同步）。 */
export function getCleanCanvas(): HTMLCanvasElement {
  if (!cleanCanvas) {
    cleanCanvas = document.createElement("canvas");
  }
  return cleanCanvas;
}

/** 设备像素矩形（主/干净画布坐标系）。 */
export interface SnapRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 取画布快照 PNG Blob。给 rect 时裁剪出内容盒区域（越界部分透明）。 */
export async function snapshotMainCanvas(rect?: SnapRect): Promise<Blob | null> {
  const hasClean = cleanCanvas !== null && cleanCanvas.width > 0 && cleanCanvas.height > 0;
  const src = hasClean ? cleanCanvas : mainCanvas;
  if (!src) {
    return null;
  }
  if (!rect || rect.w < 1 || rect.h < 1) {
    return new Promise((resolve) => {
      src.toBlob((blob) => resolve(blob), "image/png");
    });
  }
  const out = document.createElement("canvas");
  out.width = Math.max(1, Math.round(rect.w));
  out.height = Math.max(1, Math.round(rect.h));
  const ctx = out.getContext("2d");
  if (!ctx) {
    return null;
  }
  ctx.drawImage(src, rect.x, rect.y, rect.w, rect.h, 0, 0, out.width, out.height);
  return new Promise((resolve) => {
    out.toBlob((blob) => resolve(blob), "image/png");
  });
}
