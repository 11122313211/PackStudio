/** 素材导入编排：顶栏、画布拖放与「② 素材与审计」阶段共用。
 *  PDF 导入后自动触发色彩审计（闭环②：素材进来即审计）；
 *  默认居中落位到当前刀版内容盒，且批量导入合并为单次撤销点。 */

import { api } from "../lib/api";
import { importImage, importPdfPages, toLayer } from "./importers";
import type { LayerBase, ParsedDieline } from "./types";

export type ToastFn = (text: string, kind?: "info" | "ok" | "error") => void;

interface ImportDeps {
  addLayers: (layers: LayerBase[]) => void;
  dieline?: ParsedDieline | null;
  setBusy: (busy: boolean, task?: string) => void;
  setAudit: (report: Record<string, unknown> | null) => void;
  toast: ToastFn;
}

export async function importFiles(files: FileList | null, deps: ImportDeps): Promise<void> {
  if (!files || files.length === 0) {
    return;
  }
  const { addLayers, dieline, setBusy, setAudit, toast } = deps;
  setBusy(true, "import");
  try {
    const collected: LayerBase[] = [];
    const anchor = dieline && dieline.viewBox.w > 0 ? dieline.viewBox : null;
    let stackOffset = 0;

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
          if (anchor) {
            layer.x = anchor.x + (anchor.w - page.wmm) / 2 + stackOffset;
            layer.y = anchor.y + (anchor.h - page.hmm) / 2 + stackOffset;
            stackOffset += 6;
          } else {
            layer.y = stackOffset;
            stackOffset += page.hmm + 5;
          }
          collected.push(layer);
        }
      } else if (/\.(png|jpe?g|webp|bmp|gif)$/.test(lower)) {
        const page = await importImage(file);
        const layer = toLayer(page, "image");
        if (anchor) {
          layer.x = anchor.x + (anchor.w - page.wmm) / 2 + stackOffset;
          layer.y = anchor.y + (anchor.h - page.hmm) / 2 + stackOffset;
          stackOffset += 6;
        } else {
          layer.y = stackOffset;
          stackOffset += page.hmm + 5;
        }
        collected.push(layer);
      } else {
        toast(`暂不支持的格式：${file.name}`, "error");
      }
    }

    if (collected.length > 0) {
      // 合并为单次历史快照：撤回多页 PDF 只需按一次 Ctrl+Z
      addLayers(collected);
      toast(`已导入 ${collected.length} 个图层`, "ok");
    }
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), "error");
  } finally {
    setBusy(false);
  }
}
