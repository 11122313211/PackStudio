/** ⑤ 输出交付：刀版五格式 + 展平位图 + 色彩闭环报告汇总。 */

import { useState } from "react";
import { useEditor } from "../store/editor";
import { api, triggerDownload } from "../lib/api";
import { contentBox } from "../editor/renderer";
import { getCleanCanvas } from "../editor/canvasRegistry";
import { Icon } from "../components/icons";
import { loopVerdict, useStageViews } from "../flow/stages";

const FORMATS: { key: string; label: string; desc: string }[] = [
  { key: "svg", label: "SVG", desc: "矢量刀版（切/压/糊分层）" },
  { key: "dxf", label: "DXF", desc: "CAD 交换（R12）" },
  { key: "pdf", label: "PDF", desc: "PDF 1.4 矢量刀版" },
  { key: "pdf_spot", label: "专色 PDF", desc: "四色版分色（Cut/Valley/Mountain/Bleed）" },
  { key: "cf2", label: "CF2", desc: "激光切割 XML" },
];

export function DeliverStage() {
  const views = useStageViews();
  const boxSpec = useEditor((s) => s.boxSpec);
  const busy = useEditor((s) => s.busy);
  const setBusy = useEditor((s) => s.setBusy);
  const toast = useEditor((s) => s.toast);
  const dieline = useEditor((s) => s.dieline);
  const dielineName = useEditor((s) => s.dielineName);
  const layers = useEditor((s) => s.layers);
  const profiles = useEditor((s) => s.profiles);
  const deviceProfilePath = useEditor((s) => s.deviceProfilePath);
  const proofStats = useEditor((s) => s.proofStats);
  const verifyReport = useEditor((s) => s.verifyReport);
  const audit = useEditor((s) => s.audit);
  const validation = useEditor((s) => s.validation);
  const markExported = useEditor((s) => s.markExported);
  const setStage = useEditor((s) => s.setStage);
  const verdict = useEditor(loopVerdict);
  const [dpi, setDpi] = useState(150);

  const specPayload = () => {
    const params: Record<string, number> = {};
    if (boxSpec.tuck_height != null) params.tuck_height = boxSpec.tuck_height;
    if (boxSpec.glue_width != null) params.glue_width = boxSpec.glue_width;
    if (boxSpec.shrink != null) params.shrink = boxSpec.shrink;
    if (boxSpec.bleed != null) params.bleed = boxSpec.bleed;
    const payload: Record<string, unknown> = {
      model_code: boxSpec.model_code,
      dimensions: { length: boxSpec.length, width: boxSpec.width, height: boxSpec.height, unit: "mm" },
    };
    if (Object.keys(params).length > 0) {
      payload.parameters_mm = params;
    }
    return payload;
  };

  const exportFormat = async (format: string) => {
    setBusy(true, "export");
    try {
      const r = await api.exportDieline(specPayload(), format);
      triggerDownload(api.downloadUrl(r.id, r.filename), r.filename);
      markExported();
      toast(`已导出 ${r.filename}（${(r.size / 1024).toFixed(1)} KB）`, "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const flatten = () => {
    // 取干净合成（不含打样/掩图/参考线覆盖），展平成品只含 素材+刀版
    const main = getCleanCanvas();
    if (!main || main.width === 0 || main.height === 0) {
      toast("画布尚未就绪", "error");
      return;
    }
    // 与打样快照的内容盒口径一致：排除打样/掩图覆盖层
    const box = contentBox(
      dieline,
      layers.filter((l) => l.kind !== "proof" && l.kind !== "oog"),
    );
    const pxPerMm = dpi / 25.4;
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(box.w * pxPerMm));
    out.height = Math.max(1, Math.round(box.h * pxPerMm));
    const ctx = out.getContext("2d");
    if (!ctx) {
      return;
    }
    // 白底（印前展平不透出画布井）
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, out.width, out.height);
    // 以内容盒为基准，把主画布的对应区域等比重绘
    const cam = useEditor.getState().camera;
    const sx = (box.x - cam.x) * cam.scale * (window.devicePixelRatio || 1);
    const sy = (box.y - cam.y) * cam.scale * (window.devicePixelRatio || 1);
    const sw = box.w * cam.scale * (window.devicePixelRatio || 1);
    const sh = box.h * cam.scale * (window.devicePixelRatio || 1);
    ctx.drawImage(main, sx, sy, sw, sh, 0, 0, out.width, out.height);
    out.toBlob((blob) => {
      if (!blob) {
        return;
      }
      const url = URL.createObjectURL(blob);
      triggerDownload(url, `flat_${dpi}dpi.png`);
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
      markExported();
      toast(`已展平导出 ${out.width}×${out.height}px`, "ok");
    }, "image/png");
  };

  const profile = profiles.find((p) => p.path === deviceProfilePath);
  const artworkCount = layers.filter((l) => l.kind !== "proof" && l.kind !== "oog").length;

  return (
    <>
      <p className="hint">刀版矢量格式（由引擎确定性生成）</p>
      {FORMATS.map((f) => (
        <div className="row" key={f.key}>
          <button style={{ flex: "0 0 96px" }} disabled={busy || views.deliver.status === "locked"} onClick={() => void exportFormat(f.key)}>
            {f.label}
          </button>
          <span className="hint" style={{ margin: 0, flex: 1 }}>
            {f.desc}
          </span>
        </div>
      ))}
      <div className="divider" />
      <p className="hint">画布展平（素材+刀版合成位图）</p>
      <div className="row">
        <label style={{ flex: "0 0 auto" }}>DPI</label>
        <select value={dpi} onChange={(e) => setDpi(Number(e.target.value))} style={{ width: 90 }}>
          <option value={96}>96</option>
          <option value={150}>150</option>
          <option value={300}>300</option>
        </select>
        <button style={{ flex: 1 }} disabled={views.deliver.status === "locked"} onClick={flatten}>
          展平导出 PNG
        </button>
      </div>
      <p className="hint">展平取干净画布（素材+刀版，不含打样/掩图/参考线覆盖）重采样；矢量素材建议走 SVG/PDF 交印厂。</p>

      <div className="divider" />
      <div className="row">
        <span className="section-title" style={{ margin: 0, flex: 1 }}>
          色彩闭环报告
        </span>
        <span
          className={
            "badge " +
            (verdict === "pass" ? "ok" : verdict === "fail" ? "bad" : verdict === "run" ? "warn" : "")
          }
        >
          {verdict === "pass" ? "闭环通过" : verdict === "fail" ? "需修正" : verdict === "run" ? "未测量" : "未开始"}
        </span>
      </div>
      <div className="check-list">
        <div className={"check-item " + (dieline ? (validation?.passed ? "pass" : "fail") : "")}>
          <span className="dot" />
          <span className="cid">结构</span>
          <span className="cdetail">
            {dieline
              ? `${dielineName} · 机械校验${validation?.passed ? "通过" : "未通过"}`
              : "未生成刀版"}
          </span>
        </div>
        <div className={"check-item " + (artworkCount > 0 ? "pass" : "")}>
          <span className="dot" />
          <span className="cid">素材</span>
          <span className="cdetail">
            {artworkCount > 0 ? `${artworkCount} 图层${audit ? " · 已审计" : " · 未审计"}` : "未导入素材"}
          </span>
        </div>
        <div className={"check-item " + (deviceProfilePath ? "pass" : "warn")}>
          <span className="dot" />
          <span className="cid">输出档案</span>
          <span className="cdetail">
            {deviceProfilePath
              ? profile?.description || deviceProfilePath
              : "未指定（打样以内置 sRGB 演示）"}
          </span>
        </div>
        <div className={"check-item " + (proofStats ? (verdict === "fail" ? "fail" : "pass") : "")}>
          <span className="dot" />
          <span className="cid">软打样</span>
          <span className="cdetail">
            {proofStats
              ? `越界 ${(proofStats.oog_ratio * 100).toFixed(2)}% · ΔE均值 ${proofStats.delta_e_mean}`
              : "未打样"}
          </span>
        </div>
        <div className={"check-item " + (verifyReport ? (verifyReport.passed ? "pass" : "fail") : "warn")}>
          <span className="dot" />
          <span className="cid">测量验证</span>
          <span className="cdetail">
            {verifyReport
              ? `ΔE00 均值 ${verifyReport.report.stats.mean.toFixed(2)} · ${verifyReport.report.reference_name ?? "档案推导"}`
              : "未上传测量文件"}
          </span>
        </div>
      </div>
      {verdict === "fail" || verdict === "run" ? (
        <div className="loop-actions">
          <button className="small" onClick={() => setStage("proof")}>
            回 ④ 完成验证
          </button>
        </div>
      ) : null}
      <p className="hint" style={{ marginTop: 8 }}>
        <Icon name="warn" size={11} /> 测量未通过或未测量时交付，下游印刷色彩风险自担；建议完成闭环后再出片。
      </p>
    </>
  );
}
