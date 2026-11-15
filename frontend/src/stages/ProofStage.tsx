/** ④ 打样验证：软打样模拟 + 越界掩图 + CGATS 测量 ΔE00 验证（色彩闭环的收敛环节）。
 *  超标时给出行动按钮回跳 ②/③ 修正，形成"模拟 → 测量 → 反馈修正"循环。 */

import { useRef } from "react";
import { useEditor } from "../store/editor";
import { api, triggerDownload, type VerifyResponse } from "../lib/api";
import { snapshotMainCanvas } from "../editor/canvasRegistry";
import { contentBox } from "../editor/renderer";
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
  const proofStats = useEditor((s) => s.proofStats);
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
    // 与 renderer.drawOverlays 同一内容盒定义：快照裁剪区域 = 覆盖层贴回区域
    const state = useEditor.getState();
    const box = contentBox(
      state.dieline,
      state.layers.filter((l) => l.kind !== "proof" && l.kind !== "oog"),
    );
    if (box.w <= 0 || box.h <= 0) {
      toast("画布尚未就绪", "error");
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    const cam = state.camera;
    const blob = await snapshotMainCanvas({
      x: (box.x - cam.x) * cam.scale * dpr,
      y: (box.y - cam.y) * cam.scale * dpr,
      w: box.w * cam.scale * dpr,
      h: box.h * cam.scale * dpr,
    });
    if (!blob) {
      toast("画布尚未就绪", "error");
      return;
    }
    setBusy(true, "proof");
    try {
      const form = new FormData();
      form.append("file", new File([blob], "canvas.png", { type: "image/png" }));
      if (deviceProfilePath) {
        form.append("device_profile", deviceProfilePath);
      }
      form.append("intent", proofIntent);
      form.append("bpc", String(proofBpc));
      form.append("oog_threshold", String(proofThreshold));
      const r = await api.proof(form);
      const [proofBmp, oogBmp] = await Promise.all([
        (await fetch(api.downloadUrl(r.proof.id, r.proof.filename))).blob().then((b) => createImageBitmap(b)),
        (await fetch(api.downloadUrl(r.out_of_gamut.id, r.out_of_gamut.filename)))
          .blob()
          .then((b) => createImageBitmap(b)),
      ]);
      setProof({
        proofImage: proofBmp,
        oogImage: oogBmp,
        proofStats: r.stats,
        proofVisible: true,
      });
      toast(
        `打样完成：越界 ${(r.stats.oog_ratio * 100).toFixed(2)}%（ΔE>${r.stats.threshold}）`,
        "ok",
      );
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className={"loop-banner " + verdict}>
        <span className="loop-dot" />
        <span className="loop-text">
          {verdict === "idle" && (
            <>
              <b>色彩闭环未开始</b> — 运行软打样检查色域越界，再上传测量文件完成 ΔE00 验证。
            </>
          )}
          {verdict === "run" && (
            <>
              <b>已模拟，未测量</b> — 上传 CGATS 测量文件，对标 Fogra39 完成 ΔE00 判定。
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
          {busy && busyTask === "proof" ? "打样计算中…" : proofStats ? "重新打样（画布快照）" : "开始打样（画布快照）"}
        </button>
      </div>
      <p className="hint">
        意图 {INTENT_LABEL[proofIntent] ?? proofIntent} · 黑点补偿{proofBpc ? "开" : "关"} · 阈值 ΔE{proofThreshold}
        （在 ③ 调整）
        {!deviceProfilePath ? " · 未选输出档案，以内置 sRGB 演示" : ""}
      </p>

      {proofStats ? (
        <>
          <div className="divider" />
          <div className="check-list">
            <div className="check-item">
              <span className="dot" style={{ background: "var(--accent)" }} />
              <span className="cid">打样预览</span>
              <span className="cdetail">
                {proofVisible ? "显示中" : "已隐藏"} · ΔE均值 {proofStats.delta_e_mean}
              </span>
            </div>
            <div className={"check-item " + (proofStats.oog_ratio > 0.02 ? "fail" : "pass")}>
              <span className="dot" />
              <span className="cid">越界像素</span>
              <span className="cdetail">
                {proofStats.oog_count} 个（{(proofStats.oog_ratio * 100).toFixed(2)}%），最大 ΔE {proofStats.delta_e_max}
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
          {proofImage ? <p className="hint">提示：修改素材后需重新打样（覆盖层是静态快照）。</p> : null}
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
  const setVerifyReport = useEditor((s) => s.setVerifyReport);
  const deviceProfilePath = useEditor((s) => s.deviceProfilePath);
  const toast = useEditor((s) => s.toast);
  const setBusy = useEditor((s) => s.setBusy);
  const busy = useEditor((s) => s.busy);
  const busyTask = useEditor((s) => s.busyTask);

  const run = async (file: File) => {
    setBusy(true, "verify");
    try {
      const r = await api.verifyCgats(file, {
        profilePath: deviceProfilePath || undefined,
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
          <span className={"badge " + (verifyReport.passed ? "ok" : verifyReport.verdict === "WARNING" ? "warn" : "bad")}>
            {VERDICT_LABEL[verifyReport.verdict]}
          </span>
        ) : (
          <span className="badge">未测量</span>
        )}
      </div>
      <p className="hint">
        上传分光光度计导出的 CGATS.17 测量文件（含 LAB_L/A/B 列），对标 ISO 12647-2
        Fogra39 九色目标判定；选择输出档案时同时给出档案推导参考。
      </p>
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
              <span className="dot" style={{ background: "var(--accent)" }} />
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
