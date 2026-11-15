/** 素材导入编排：顶栏与「② 素材与审计」阶段共用。
 *  PDF 导入后自动触发色彩审计（闭环②：素材进来即审计）。 */

import { api } from "../lib/api";
import { importImage, importPdfPages, toLayer } from "./importers";
import type { LayerBase } from "./types";

export type ToastFn = (text: string, kind?: "info" | "ok" | "error") => void;

interface ImportDeps {
  addLayer: (layer: LayerBase) => void;
  setBusy: (busy: boolean, task?: string) => void;
  setAudit: (report: Record<string, unknown> | null) => void;
  toast: ToastFn;
}

export async function importFiles(files: FileList | null, deps: ImportDeps): Promise<void> {
  if (!files || files.length === 0) {
    return;
  }
  const { addLayer, setBusy, setAudit, toast } = deps;
  setBusy(true, "import");
  try {
    let y = 0;
    for (const file of Array.from(files)) {
      const lower = file.name.toLowerCase();
      if (lower.endsWith(".pdf")) {
        // 闭环②：客户 PDF 进来即审计（OutputIntent / 内嵌档案 / 专色）
        try {
          const r = await api.pdfAudit(file);
          setAudit(r.report);
          toast(`已导入并审计：${file.name}`, "ok");
        } catch (err) {
          // 审计失败不阻断导入（印前工具要有容错）
          setAudit(null);
          toast(`审计失败：${err instanceof Error ? err.message : String(err)}`, "error");
        }
        const pages = await importPdfPages(file, 150);
        for (const page of pages) {
          const layer = toLayer(page, "pdf");
          layer.y = y;
          addLayer(layer);
          y += page.hmm + 5;
        }
      } else if (/\.(png|jpe?g|webp|bmp|gif)$/.test(lower)) {
        const page = await importImage(file);
        const layer = toLayer(page, "image");
        layer.y = y;
        addLayer(layer);
        y += page.hmm + 5;
      } else {
        toast(`暂不支持的格式：${file.name}`, "error");
      }
    }
    toast("素材已导入为图层", "ok");
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), "error");
  } finally {
    setBusy(false);
  }
}
