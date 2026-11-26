/** ② 素材与审计：导入（PDF 自动色彩审计）、图层管理、逐图层 ICC 赋档/转换。 */

import { useRef, useState } from "react";
import { useEditor } from "../store/editor";
import { api, triggerDownload, type PdfAuditReport } from "../lib/api";
import { importFiles } from "../editor/importFiles";
import { toLayer } from "../editor/importers";
import type { LayerBase } from "../editor/types";
import { Icon } from "../components/icons";
import { useStageViews } from "../flow/stages";

const KIND_LABEL: Record<string, string> = {
  image: "图片",
  pdf: "PDF",
  proof: "打样",
  oog: "色域",
};

export function ContentStage() {
  const views = useStageViews();
  const locked = views.content.status === "locked";
  const fileRef = useRef<HTMLInputElement>(null);
  const busy = useEditor((s) => s.busy);
  const audit = useEditor((s) => s.audit);
  const dieline = useEditor((s) => s.dieline);
  const addLayers = useEditor((s) => s.addLayers);
  const setAudit = useEditor((s) => s.setAudit);
  const setBusy = useEditor((s) => s.setBusy);
  const toast = useEditor((s) => s.toast);

  const onFiles = (files: FileList | null) => {
    void importFiles(files, { addLayers, dieline, setBusy, setAudit, toast });
    if (fileRef.current) {
      fileRef.current.value = "";
    }
  };

  return (
    <>
      <div className="row">
        <button
          className="primary"
          style={{ flex: 1 }}
          disabled={locked || busy}
          title={locked ? views.content.lockedReason : "导入客户 PDF / 图片素材"}
          onClick={() => fileRef.current?.click()}
        >
          <Icon name="import" size={13} /> 导入素材（PDF/图片）
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,image/*"
          multiple
          hidden
          onChange={(e) => onFiles(e.target.files)}
        />
      </div>
      <p className="hint">PDF 导入时自动执行色彩审计（OutputIntent / 内嵌档案 / 专色）。</p>

      {audit ? <AuditCard report={audit} /> : null}

      <div className="divider" />
      <LayerList />
    </>
  );
}

/* ---------------- 色彩审计卡 ---------------- */

function AuditCard({ report }: { report: PdfAuditReport }) {
  const num = (key: string): number | null =>
    typeof report[key] === "number" ? (report[key] as number) : null;
  const summary = report["summary"] as Record<string, unknown> | undefined;
  const sumNum = (key: string): number | null =>
    summary && typeof summary[key] === "number" ? (summary[key] as number) : null;
  const intents = Array.isArray(report["output_intents"]) ? (report["output_intents"] as unknown[]) : [];
  const spots = Array.isArray(report["spot_colors"]) ? (report["spot_colors"] as unknown[]) : [];
  const warnings = Array.isArray(report["warnings"]) ? (report["warnings"] as unknown[]) : [];
  const pageCount = num("page_count") ?? (Array.isArray(report["pages"]) ? report["pages"].length : null);
  const deviceRgb = sumNum("device_rgb_pages");
  const deviceCmyk = sumNum("device_cmyk_pages");
  const iccPages = sumNum("icc_pages");

  return (
    <>
      <div className="divider" />
      <div className="row">
        <span className="section-title" style={{ margin: 0, flex: 1 }}>
          色彩审计
        </span>
        {warnings.length > 0 ? (
          <span className="badge warn">{warnings.length} 警告</span>
        ) : (
          <span className="badge ok">无警告</span>
        )}
      </div>
      <div className="check-list">
        {pageCount != null ? (
          <div className="check-item pass">
            <span className="dot" />
            <span className="cid">页数</span>
            <span className="cdetail">{pageCount} 页</span>
          </div>
        ) : null}
        <div className={"check-item " + (intents.length > 0 ? "pass" : "warn")}>
          <span className="dot" />
          <span className="cid">OutputIntent</span>
          <span className="cdetail">
            {intents.length > 0
              ? `${intents.length} 个${intents.some((i) => (i as Record<string, unknown>)["dest_profile"]) ? "（含目标档案）" : "（缺目标档案，建议补）"}`
              : "未声明（未管理的 PDF）"}
          </span>
        </div>
        <div className={"check-item " + (spots.length > 0 ? "pass" : "")}>
          <span className="dot" />
          <span className="cid">专色</span>
          <span className="cdetail">
            {spots.length > 0
              ? spots.map((s) => String((s as Record<string, unknown>)["name"] ?? "?")).join("、")
              : "无"}
          </span>
        </div>
        {deviceRgb != null || deviceCmyk != null ? (
          <div className={"check-item " + (deviceRgb ? "warn" : "pass")}>
            <span className="dot" />
            <span className="cid">设备空间</span>
            <span className="cdetail">
              {deviceRgb ? `${deviceRgb} 页未管理 RGB` : ""}
              {deviceRgb && deviceCmyk ? " · " : ""}
              {deviceCmyk ? `${deviceCmyk} 页未管理 CMYK` : ""}
              {!deviceRgb && !deviceCmyk ? "全部色彩已管理" : ""}
            </span>
          </div>
        ) : null}
        {iccPages != null ? (
          <div className="check-item pass">
            <span className="dot" />
            <span className="cid">内嵌档案</span>
            <span className="cdetail">{iccPages} 页含 ICC 内嵌 profile</span>
          </div>
        ) : null}
      </div>
      <details>
        <summary className="hint" style={{ cursor: "pointer", margin: "6px 0" }}>
          查看完整审计 JSON
        </summary>
        <pre className="report-pre">{JSON.stringify(report, null, 1)}</pre>
      </details>
    </>
  );
}

/* ---------------- 图层列表 ---------------- */

function LayerList() {
  const layers = useEditor((s) => s.layers);
  const selectionId = useEditor((s) => s.selectionId);
  const select = useEditor((s) => s.select);
  const updateLayer = useEditor((s) => s.updateLayer);
  const removeLayer = useEditor((s) => s.removeLayer);
  const reorderLayer = useEditor((s) => s.reorderLayer);

  const selected = layers.find((l) => l.id === selectionId) ?? null;
  const display = [...layers].reverse(); // 顶层在上

  return (
    <>
      <div className="row">
        <span className="section-title" style={{ margin: 0, flex: 1 }}>
          图层（{layers.filter((l) => l.kind !== "proof" && l.kind !== "oog").length}）
        </span>
      </div>
      {layers.length === 0 ? (
        <p className="hint">尚无图层。导入 PDF/图片素材后会出现在这里。</p>
      ) : null}
      <div className="layer-list">
        {display.map((l) => (
          <div
            key={l.id}
            className={"layer-item" + (l.id === selectionId ? " selected" : "")}
            onClick={() => select(l.id)}
          >
            <button
              className={"icon-btn" + (l.visible ? "" : " off")}
              title={l.visible ? "隐藏" : "显示"}
              onClick={(e) => {
                e.stopPropagation();
                updateLayer(l.id, { visible: !l.visible }, true);
              }}
            >
              <Icon name={l.visible ? "eye" : "eye-off"} size={13} />
            </button>
            <span className="lname" title={l.name}>
              {l.name}
            </span>
            <span className="lkind">{l.convertedOutput?.colorSpace || KIND_LABEL[l.kind] || l.kind}</span>
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round(l.opacity * 100)}
              title={`不透明度 ${Math.round(l.opacity * 100)}%`}
              onClick={(e) => e.stopPropagation()}
              onPointerDown={() => useEditor.getState().snapshot()}
              onChange={(e) => updateLayer(l.id, { opacity: Number(e.target.value) / 100 })}
            />
            <button
              className={"icon-btn" + (l.locked ? "" : " off")}
              title={l.locked ? "解锁" : "锁定"}
              onClick={(e) => {
                e.stopPropagation();
                updateLayer(l.id, { locked: !l.locked }, true);
              }}
            >
              <Icon name={l.locked ? "lock" : "unlock"} size={13} />
            </button>
            <button
              className="icon-btn"
              title="上移一层"
              onClick={(e) => {
                e.stopPropagation();
                reorderLayer(l.id, 1);
              }}
            >
              <Icon name="arrow-up" size={13} />
            </button>
            <button
              className="icon-btn"
              title="下移一层"
              onClick={(e) => {
                e.stopPropagation();
                reorderLayer(l.id, -1);
              }}
            >
              <Icon name="arrow-down" size={13} />
            </button>
            <button
              className="icon-btn danger"
              title="删除图层"
              onClick={(e) => {
                e.stopPropagation();
                removeLayer(l.id);
              }}
            >
              <Icon name="close" size={13} />
            </button>
          </div>
        ))}
      </div>
      {selected ? (
        <>
          <LayerProps layer={selected} onChange={updateLayer} />
          {selected.kind !== "proof" && selected.kind !== "oog" ? <LayerColorOps layer={selected} /> : null}
        </>
      ) : null}
    </>
  );
}

function LayerProps({
  layer,
  onChange,
}: {
  layer: LayerBase;
  onChange: (id: string, patch: Partial<LayerBase>, recordHistory?: boolean) => void;
}) {
  const num = (v: string) => Number(v) || 0;
  const snap = () => useEditor.getState().snapshot();
  return (
    <>
      <div className="divider" />
      <p className="hint">选中图层属性（mm）</p>
      <div className="grid2">
        <div className="field">
          <span>X</span>
          <input type="number" step={0.5} value={Math.round(layer.x * 100) / 100} onFocus={snap} onChange={(e) => onChange(layer.id, { x: num(e.target.value) })} />
        </div>
        <div className="field">
          <span>Y</span>
          <input type="number" step={0.5} value={Math.round(layer.y * 100) / 100} onFocus={snap} onChange={(e) => onChange(layer.id, { y: num(e.target.value) })} />
        </div>
        <div className="field">
          <span>宽 W</span>
          <input type="number" step={0.5} min={0.1} value={Math.round(layer.w * 100) / 100} onFocus={snap} onChange={(e) => onChange(layer.id, { w: Math.max(0.1, num(e.target.value)) })} />
        </div>
        <div className="field">
          <span>高 H</span>
          <input type="number" step={0.5} min={0.1} value={Math.round(layer.h * 100) / 100} onFocus={snap} onChange={(e) => onChange(layer.id, { h: Math.max(0.1, num(e.target.value)) })} />
        </div>
        <div className="field">
          <span>旋转 (°)</span>
          <input type="number" step={1} value={layer.rotation} onFocus={snap} onChange={(e) => onChange(layer.id, { rotation: num(e.target.value) })} />
        </div>
        <div className="field">
          <span>不透明度 (0–1)</span>
          <input type="number" step={0.05} min={0} max={1} value={layer.opacity} onFocus={snap} onChange={(e) => onChange(layer.id, { opacity: Math.min(1, Math.max(0, num(e.target.value))) })} />
        </div>
      </div>
      {layer.convertedOutput ? (
        <div className="row" style={{ marginTop: 6 }}>
          <button
            className="small ghost"
            style={{ flex: 1 }}
            onClick={() =>
              triggerDownload(
                api.downloadUrl(layer.convertedOutput!.id, layer.convertedOutput!.filename),
                layer.convertedOutput!.filename,
              )
            }
          >
            <Icon name="share" size={12} /> 下载真实 {layer.convertedOutput.colorSpace || "ICC"} 文件（
            {layer.convertedOutput.filename}）
          </button>
        </div>
      ) : null}
    </>
  );
}

/* ---------------- 逐图层 ICC 赋档/转换（闭环②） ---------------- */

function LayerColorOps({ layer }: { layer: LayerBase }) {
  const profiles = useEditor((s) => s.profiles);
  const loadProfiles = useEditor((s) => s.loadProfiles);
  const addLayer = useEditor((s) => s.addLayer);
  const proofBpc = useEditor((s) => s.proofBpc);
  const setBusy = useEditor((s) => s.setBusy);
  const busy = useEditor((s) => s.busy);
  const busyTask = useEditor((s) => s.busyTask);
  const toast = useEditor((s) => s.toast);

  const [op, setOp] = useState<"convert" | "assign" | "embed">("convert");
  const [profilePath, setProfilePath] = useState("");
  const [intent, setIntent] = useState("relative");

  const usable = profiles.filter((p) => p.color_space.trim() !== "");
  const chosen = usable.find((p) => p.path === profilePath);
  const targetIsCmyk = (chosen?.color_space.trim().toUpperCase() ?? "") === "CMYK";

  const run = async () => {
    if (!layer.bitmap || !profilePath) {
      return;
    }
    setBusy(true, "convert");
    try {
      // ImageBitmap → PNG 上传（无嵌入 ICC 的 RGB 图后端自动按 sRGB 源处理）
      const c = document.createElement("canvas");
      c.width = layer.bitmap.width;
      c.height = layer.bitmap.height;
      const ctx = c.getContext("2d");
      if (!ctx) {
        throw new Error("无法创建画布上下文");
      }
      ctx.drawImage(layer.bitmap as CanvasImageSource, 0, 0);
      const blob = await new Promise<Blob | null>((resolve) => c.toBlob(resolve, "image/png"));
      if (!blob) {
        throw new Error("图层位图编码失败");
      }
      const form = new FormData();
      form.append("file", new File([blob], `${layer.name}.png`, { type: "image/png" }));
      form.append("op", op);
      form.append("profile_path", profilePath);
      form.append("intent", intent);
      form.append("bpc", String(proofBpc));
      // CMYK 转换自动写出无损 TIFF（由后端同时返回 sRGB 预览 PNG 回填画布）
      form.append("out_format", op === "convert" && targetIsCmyk ? "tif" : "png");
      const r = await api.convert(form);
      if (!r.report.ok) {
        throw new Error(r.report.message || "转换失败");
      }
      const previewRef = r.preview ?? { id: r.id, filename: r.filename };
      const dl = await fetch(api.downloadUrl(previewRef.id, previewRef.filename));
      if (!dl.ok) {
        throw new Error(`加载转换预览失败（HTTP ${dl.status}）`);
      }
      const outBitmap = await createImageBitmap(await dl.blob());
      const newLayer = toLayer({
        bitmap: outBitmap,
        wmm: layer.w,
        hmm: layer.h,
        name: `${layer.name} · ${op === "assign" ? "赋档" : op === "embed" ? "嵌档" : r.color_space ? `转 ${r.color_space}` : "转换"}`,
      });
      newLayer.x = layer.x;
      newLayer.y = layer.y;
      newLayer.rotation = layer.rotation;
      newLayer.convertedOutput = {
        id: r.id,
        filename: r.filename,
        colorSpace: r.color_space || chosen?.color_space.trim() || "",
      };
      addLayer(newLayer);
      const extraHint =
        r.color_space === "CMYK"
          ? "（已生成真 CMYK TIFF 供下载 + sRGB 预览图层）"
          : r.assumed_src_srgb
            ? "（源图无嵌入档案，已按 sRGB 转换）"
            : "";
      toast(`ICC ${op === "assign" ? "赋档" : op === "embed" ? "嵌档" : "转换"}完成${extraHint}`, "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="divider" />
      <p className="hint">图层 ICC 操作（结果作为新图层加入，原图层保留；CMYK 转换自动保存 TIFF 并附画布预览）</p>
      <div className="row">
        <select value={op} onChange={(e) => setOp(e.target.value as typeof op)} aria-label="ICC 操作">
          <option value="convert">转换（映射色彩）</option>
          <option value="assign">赋档（仅重标注）</option>
          <option value="embed">嵌档（写入档案）</option>
        </select>
        <select value={intent} onChange={(e) => setIntent(e.target.value)} disabled={op !== "convert"} aria-label="渲染意图">
          <option value="perceptual">感知</option>
          <option value="relative">相对比色</option>
          <option value="saturation">饱和度</option>
          <option value="absolute">绝对比色</option>
        </select>
      </div>
      <div className="row">
        <select value={profilePath} onChange={(e) => setProfilePath(e.target.value)} aria-label="目标 profile">
          <option value="">— 选择目标 ICC profile —</option>
          {usable.map((p) => (
            <option key={p.path} value={p.path}>
              {(p.description || p.path).slice(0, 46)}（{p.color_space.trim()}）
            </option>
          ))}
        </select>
        <button className="small ghost" onClick={() => void loadProfiles(true)} title="强制重新扫描系统 profile">
          <Icon name="refresh" size={12} />
        </button>
      </div>
      <div className="row">
        <button
          className="primary"
          style={{ flex: 1 }}
          disabled={busy || !profilePath || busyTask === "convert"}
          onClick={() => void run()}
        >
          {busy && busyTask === "convert" ? "处理中…" : "执行 ICC 操作"}
        </button>
      </div>
    </>
  );
}
