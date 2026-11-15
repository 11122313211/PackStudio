/** 素材导入：PDF（pdf.js 逐页渲染为位图图层）与位图图片。 */

import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { MM_PER_PT, uid } from "./types";
import type { LayerBase } from "./types";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export interface ImportedPage {
  bitmap: ImageBitmap;
  wmm: number;
  hmm: number;
  name: string;
}

/** 把 PDF 每页渲染为高分辨率位图（scale = dpi/72，1pt=1/72inch）。 */
export async function importPdfPages(file: File, dpi: number): Promise<ImportedPage[]> {
  const data = await file.arrayBuffer();
  const doc = await pdfjs.getDocument({ data: new Uint8Array(data) }).promise;
  const pages: ImportedPage[] = [];
  for (let i = 1; i <= doc.numPages; i += 1) {
    const page = await doc.getPage(i);
    const viewport = page.getViewport({ scale: dpi / 72 });
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      throw new Error("无法创建画布上下文");
    }
    await page.render({ canvasContext: ctx, viewport }).promise;
    const bitmap = await createImageBitmap(canvas);
    const pxPerMm = dpi / 25.4;
    pages.push({
      bitmap,
      wmm: viewport.width / pxPerMm,
      hmm: viewport.height / pxPerMm,
      name: `Page ${i}`,
    });
  }
  return pages;
}

/** 位图导入：按 96 DPI 假定物理尺寸（用户可在属性面板再缩放）。 */
export async function importImage(file: File): Promise<ImportedPage> {
  const bitmap = await createImageBitmap(file);
  const mmPerPx = 25.4 / 96;
  const layer: ImportedPage = {
    bitmap,
    wmm: bitmap.width * mmPerPx,
    hmm: bitmap.height * mmPerPx,
    name: file.name,
  };
  return layer;
}

/** 组装为画布图层。 */
export function toLayer(page: ImportedPage, kind: LayerBase["kind"] = "image"): LayerBase {
  const layer: LayerBase = {
    id: uid(),
    name: page.name,
    kind,
    x: 0,
    y: 0,
    w: page.wmm,
    h: page.hmm,
    rotation: 0,
    opacity: 1,
    visible: true,
    locked: false,
    bitmap: page.bitmap,
  };
  return layer;
}

export { MM_PER_PT };
