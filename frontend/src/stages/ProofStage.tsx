/** ④ 打样验证：离屏软打样模拟 + 越界掩图 + CGATS 测量 ΔE00 验证（色彩闭环的收敛环节）。
 *  超标或内容变更过期时给出行动按钮回跳 ②/③ 修正或重跑，形成"模拟 → 测量 → 反馈修正"闭环。 */

import { useRef, useState } from "react";
import { useEditor } from "../store/editor";
import { api, triggerDownload, type VerifyResponse } from "../lib/api";
import { canvasToPngBlob } from "../editor/canvasRegistry";
import { renderOffscreen } from "../editor/renderer";
import { Icon } from "../components/icons";
import { loopVerdict, useStageViews } from "../flow/stages";

const VERDICT_LABEL: Record<VerifyResponse["verdict"], string> = {
  PASS: "闭环通过",
  WARNING: "临界（工程警示带）",
  FAIL: "未通过",
};

export function ProofStage() {
  const views = useStageViews();
  const locked = views.proof.status === "locked";
  const verdict = useEditor(loopVerdict);
  const deviceProfilePath = useEditor((s) => s.deviceProfilePath);
  const proofIntent = useEditor((s) => s.proofIntent);
  const proofBpc = useEditor((s) => s.proofBpc);
  const proofThreshold = useEditor((s) => s.proofThreshold);
  const proofMetric = useEditor((s) => s.proofMetric);
  const proofStats = useEditor((s) => s.proofStats);
  const proofStale = useEditor((s) => s.proofStale);
  const proofVisible = useEditor((s) => s.proofVisible);
  const oogVisible = useEditor((s) => s.oogVisible);
  const proofImage = useEditor((s) => s.proofImage);
  const setProof = useEditor((s) => s.setProof);
  const setStage = useEditor((s) => s.setStage);
  const toast = useEditor((s) => s.toast);
  const setBusy = useEditor((s) => s.setBusy);
  const busy = useEditor((s) => s.busy);
  const busyTask = useEditor((s) => s.busyTask);

  const run = async () => {
    // 独立世界坐标离屏渲染：仅含纯白底 + 素材图层（不含刀线/网格/选框，且不受视口缩放平移裁切）
    const state = useEditor.getState();
    const off = renderOffscreen({
      dieline: state.dieline,
      layers: state.layers,
      maxEdgePx: 2048,
      includeDieline: false,
    });
    if (!off) {
      toast("画布尚未就绪", "error");
      return;
    }
    const blob = await canvasToPngBlob(off.canvas);
    if (!blob) {
      toast("离屏快照编码失败", "error");
      return;
    }
    setBusy(true, "proof");
    try {
      const form = new FormData();
      form.append("file", new File([blob], "artwork.png", { type: "image/png" }));
      if (deviceProfilePath) {
        form.append("device_profile", deviceProfilePath);
      }
      form.append("intent", proofIntent);
      form.append("bpc", String(proofBpc));
      form.append("oog_threshold", String(proofThreshold));
      form.append("metric", proofMetric);
      const r = await api.proof(form);
      const [proofBmp, oogBmp] = await Promise.all([
        fetch(api.downloadUrl(r.proof.id, r.proof.filename))
          .then((resp) => resp.blob())
          .then((b) => createImageBitmap(b)),
        fetch(api.downloadUrl(r.out_of_gamut.id, r.out_of_gamut.filename))
          .then((resp) => resp.blob())
          .then((b) => createImageBitmap(b)),
      ]);
      setProof({
        proofImage: proofBmp,
        oogImage: oogBmp,
        proofStats: r.stats,
        proofVisible: true,
      });
      const metricTag = (r.stats.metric ?? proofMetric) === "de00" ? "ΔE00" : "ΔE76";
      toast(
        `打样完成：越界 ${(r.stats.oog_ratio * 100).toFixed(2)}%（${metricTag}>${r.stats.threshold}）`,
        "ok",
      );
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const metricLabel = (proofStats?.metric ?? proofMetric) === "de00" ? "ΔE00" : "ΔE76";

  return (
    <>
      <div className={"loop-banner " + verdict}>
        <span className="loop-dot" />
        <span className="loop-text">
          {verdict === "idle" && (
            <>
              <b>色彩闭环未开始</b> — 运行离屏软打样检查纯素材色域越界，再上传测量文件完成 ΔE00 验证。
            </>
          )}
          {verdict === "run" && (
            <>
              <b>已模拟，未测量</b> — 上传 CGATS 测量文件，对标 Fogra39 完成 ΔE00 判定。
            </>
          )}
          {verdict === "stale" && (
            <>
              <b>{proofStale ? "软打样已过期" : "测量报告已过期"}</b> — 素材、刀版或色彩配置已改动，请重新运行验证后再交付。
            </>
          )}
          {verdict === "pass" && (
            <>
              <b>色彩闭环通过</b> — ΔE00 达标，可进入 ⑤ 交付。
            </>
          )}
          {verdict === "fail" && (
            <>
              <b>闭环未通过</b> — 按下方诊断修正后重跑打样与测量。
            </>
          )}
        </span>
      </div>
      {verdict === "fail" ? (
        <div className="loop-actions">
          <button className="small" onClick={() => setStage("content")}>
            回 ② 修正素材
          </button>
          <button className="small" onClick={() => setStage("color")}>
            回 ③ 换档案 / 意图
          </button>
        </div>
      ) : null}

      <div className="row">
        <button className="primary" style={{ flex: 1 }} disabled={locked || busy} onClick={() => void run()}>
          {busy && busyTask === "proof"
            ? "打样计算中…"
            : proofStale
              ? "重新打样（已过期）"
              : proofStats
                ? "重新打样（离屏素材）"
                : "开始打样（离屏素材）"}
        </button>
      </div>
      <p className="hint">
        意图 {INTENT_LABEL[proofIntent] ?? proofIntent} · 黑点补偿{proofBpc ? "开" : "关"} · 阈值 {metricLabel}
        {proofThreshold}（在 ③ 调整）
        {!deviceProfilePath ? " · 未选输出档案，以内置 sRGB 演示" : ""}
      </p>

      {proofStats ? (
        <>
          <div className="divider" />
          <div className="check-list">
            <div className={"check-item" + (proofStale ? " warn" : "")}>
              <span className="dot" style={{ background: proofStale ? "var(--warn)" : "var(--accent)" }} />
              <span className="cid">打样预览</span>
              <span className="cdetail">
                {proofVisible ? "显示中" : "已隐藏"} · {metricLabel} 均值 {proofStats.delta_e_mean}
                {proofStale ? " · 已过期" : ""}
              </span>
            </div>
            <div className={"check-item " + (proofStats.oog_ratio > 0.02 ? "fail" : "pass")}>
              <span className="dot" />
              <span className="cid">越界像素</span>
              <span className="cdetail">
                {proofStats.oog_count} 个（{(proofStats.oog_ratio * 100).toFixed(2)}%），最大 {metricLabel}{" "}
                {proofStats.delta_e_max}
              </span>
            </div>
          </div>
          <div className="row">
            <label style={{ flex: "0 0 auto" }}>
              <input
                type="checkbox"
                checked={proofVisible}
                onChange={(e) => setProof({ proofVisible: e.target.checked })}
              />{" "}
              显示打样层
            </label>
            <label>
              <input
                type="checkbox"
                checked={oogVisible}
                onChange={(e) => setProof({ oogVisible: e.target.checked })}
              />{" "}
              显示越界掩图
            </label>
          </div>
          <p className="hint">离屏仅对纯素材打样（不含刀版线与网格），不受缩放平移裁切。</p>
          <div className="row">
            <button
              className="small"
              disabled={!proofImage}
              onClick={() => {
                if (proofImage) {
                  downloadBitmap(proofImage, "proof.png");
                }
              }}
            >
              打样预览 PNG
            </button>
            <button
              className="small"
              disabled={!proofImage}
              onClick={() => {
                const img = useEditor.getState().oogImage;
                if (img) {
                  downloadBitmap(img, "out_of_gamut.png");
                }
              }}
            >
              越界掩图 PNG
            </button>
          </div>
        </>
      ) : null}

      <VerifyCard disabled={locked || busy} />
    </>
  );
}

const INTENT_LABEL: Record<string, string> = {
  perceptual: "感知",
  relative: "相对比色",
  saturation: "饱和度",
  absolute: "绝对比色",
};

/* ---------------- 测量验证卡（闭环④ 的"测量"环节） ---------------- */

function VerifyCard({ disabled }: { disabled: boolean }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const verifyReport = useEditor((s) => s.verifyReport);
  const verifyStale = useEditor((s) => s.verifyStale);
  const setVerifyReport = useEditor((s) => s.setVerifyReport);
  const deviceProfilePath = useEditor((s) => s.deviceProfilePath);
  const proofIntent = useEditor((s) => s.proofIntent);
  const proofBpc = useEditor((s) => s.proofBpc);
  const toast = useEditor((s) => s.toast);
  const setBusy = useEditor((s) => s.setBusy);
  const busy = useEditor((s) => s.busy);
  const busyTask = useEditor((s) => s.busyTask);

  const [toleranceInput, setToleranceInput] = useState("");

  const run = async (file: File) => {
    setBusy(true, "verify");
    try {
      const tol = toleranceInput.trim() ? Number(toleranceInput) : undefined;
      const r = await api.verifyCgats(file, {
        profilePath: deviceProfilePath || undefined,
        intent: proofIntent,
        bpc: proofBpc,
        tolerance: tol != null && Number.isFinite(tol) && tol > 0 ? tol : undefined,
      });
      setVerifyReport(r);
      toast(
        `测量验证：${VERDICT_LABEL[r.verdict]}（ΔE00 均值 ${r.report.stats.mean.toFixed(2)}）`,
        r.passed ? "ok" : "error",
      );
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="divider" />
      <div className="row">
        <span className="section-title" style={{ margin: 0, flex: 1 }}>
          测量验证
        </span>
        {verifyReport ? (
          <span
            className={
              "badge " +
              (verifyStale
                ? "warn"
                : verifyReport.passed
                  ? "ok"
                  : verifyReport.verdict === "WARNING"
                    ? "warn"
                    : "bad")
            }
          >
            {verifyStale ? "已过期" : VERDICT_LABEL[verifyReport.verdict]}
          </span>
        ) : (
          <span className="badge">未测量</span>
        )}
      </div>
      <p className="hint">
        上传分光光度计导出的 CGATS.17 测量文件（含 LAB_L/A/B 列），自动继承 ③ 的输出档案、渲染意图与黑点补偿对标 ISO
        12647-2 Fogra39 判定。
      </p>
      <div className="row">
        <label style={{ flex: "0 0 auto" }}>容差覆盖 ΔE00</label>
        <input
          type="number"
          min={0.5}
          max={20}
          step={0.5}
          placeholder="默认 ISO"
          value={toleranceInput}
          onChange={(e) => setToleranceInput(e.target.value)}
          style={{ width: 92, flex: "0 0 auto" }}
        />
      </div>
      <div className="row">
        <button className="primary" style={{ flex: 1 }} disabled={disabled} onClick={() => fileRef.current?.click()}>
          {busy && busyTask === "verify" ? "验证中…" : "上传测量文件（CGATS）"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.csv,.cgats,.it8,.it87"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) {
              void run(f);
            }
            e.target.value = "";
          }}
        />
      </div>

      {verifyReport ? (
        <>
          <div className="check-list" style={{ marginTop: 6 }}>
            <div className="check-item pass">
              <span className="dot" style={{ background: verifyStale ? "var(--warn)" : "var(--accent)" }} />
              <span className="cid">ΔE00 统计</span>
              <span className="cdetail">
                均值 {verifyReport.report.stats.mean.toFixed(2)} · 最大 {verifyReport.report.stats.max.toFixed(2)} ·
                P95 {verifyReport.report.stats.percentiles["p95"]?.toFixed(2) ?? "—"}
                {verifyReport.tolerance != null ? ` · 容差 ${verifyReport.tolerance}` : ""}
              </span>
            </div>
          </div>
          {verifyReport.report.iso ? (
            <div className="check-list" style={{ marginTop: 4 }}>
              {verifyReport.report.iso.map((row) => (
                <div className={"check-item " + (row.passed ? "pass" : "fail")} key={row.patch_id}>
                  <span className="dot" />
                  <span className="cid">{row.target}</span>
                  <span className="cdetail">
                    ΔE00 {row.delta_e_00.toFixed(2)} / 容差 {row.tolerance}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          {verifyReport.report.channel_diagnostics?.hints.length ? (
            verifyReport.report.channel_diagnostics.hints.map((h, i) => (
              <p className="hint" key={i} style={{ color: "var(--warn)" }}>
                <Icon name="warn" size={11} /> {h}
              </p>
            ))
          ) : null}
          <details>
            <summary className="hint" style={{ cursor: "pointer", margin: "6px 0" }}>
              查看完整验证报告
            </summary>
            <pre className="report-pre">{JSON.stringify(verifyReport.report, null, 1)}</pre>
          </details>
        </>
      ) : null}
    </>
  );
}

function downloadBitmap(bmp: ImageBitmap, name: string): void {
  const canvas = document.createElement("canvas");
  canvas.width = bmp.width;
  canvas.height = bmp.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }
  ctx.drawImage(bmp, 0, 0);
  canvas.toBlob((blob) => {
    if (blob) {
      const url = URL.createObjectURL(blob);
      triggerDownload(url, name);
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
    }
  }, "image/png");
}
