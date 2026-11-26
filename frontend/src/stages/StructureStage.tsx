/** ① 盒型结构：选型、尺寸、生成刀版、机械校验、3D 折叠预览。 */

import { useEffect, useState } from "react";
import { useEditor } from "../store/editor";
import { api, type BoxChoice, type BoxReportItem } from "../lib/api";
import { buildFoldPreviewUrl } from "../lib/boxPayload";
import { Icon } from "../components/icons";
import { PT_PER_MM } from "../editor/types";

export function StructureStage() {
  const boxSpec = useEditor((s) => s.boxSpec);
  const setBoxSpec = useEditor((s) => s.setBoxSpec);
  const generateDieline = useEditor((s) => s.generateDieline);
  const setStage = useEditor((s) => s.setStage);
  const busy = useEditor((s) => s.busy);
  const busyTask = useEditor((s) => s.busyTask);
  const dieline = useEditor((s) => s.dieline);
  const dielineName = useEditor((s) => s.dielineName);
  const ecma = useEditor((s) => s.dielineEcma);
  const fefco = useEditor((s) => s.dielineFefco);
  const netSize = useEditor((s) => s.netSize);
  const validation = useEditor((s) => s.validation);
  const units = useEditor((s) => s.units);

  const [choices, setChoices] = useState<BoxChoice[]>([]);
  const [report, setReport] = useState<BoxReportItem[]>([]);

  useEffect(() => {
    api
      .boxes()
      .then((d) => {
        setChoices(d.choices);
        setReport(d.report);
      })
      .catch(() => undefined);
  }, []);

  const current = report.find((r) => r.code === boxSpec.model_code);
  const fmt = (mm: number) => (units === "pt" ? `${(mm * PT_PER_MM).toFixed(1)} pt` : `${mm.toFixed(1)} mm`);

  const openFold = () => {
    window.open(buildFoldPreviewUrl(boxSpec), "_blank");
  };

  return (
    <>
      <div className="row">
        <select
          value={boxSpec.model_code}
          onChange={(e) => setBoxSpec({ model_code: e.target.value })}
          aria-label="盒型"
        >
          {choices.map((c) => (
            <option key={c.value} value={c.value} disabled={c.status !== "available"}>
              {c.label}
              {c.status !== "available" ? "（未实现）" : ""}
            </option>
          ))}
        </select>
      </div>
      {current ? (
        <p className="hint">
          ECMA {current.ecma || "—"} · FEFCO {current.fefco || "—"} · {current.family}
        </p>
      ) : null}
      <div className="grid3">
        <div className="field">
          <span>长 L (mm)</span>
          <input
            type="number"
            min={1}
            step={1}
            value={boxSpec.length}
            onChange={(e) => setBoxSpec({ length: Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <span>宽 W (mm)</span>
          <input
            type="number"
            min={1}
            step={1}
            value={boxSpec.width}
            onChange={(e) => setBoxSpec({ width: Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <span>高 H (mm)</span>
          <input
            type="number"
            min={1}
            step={1}
            value={boxSpec.height}
            onChange={(e) => setBoxSpec({ height: Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="grid2">
        <div className="field">
          <span>插耳深 (mm，可留空)</span>
          <input
            type="number"
            min={0}
            step={0.5}
            value={boxSpec.tuck_height ?? ""}
            onChange={(e) => setBoxSpec({ tuck_height: e.target.value === "" ? undefined : Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <span>糊口宽 (mm，可留空)</span>
          <input
            type="number"
            min={0}
            step={0.5}
            value={boxSpec.glue_width ?? ""}
            onChange={(e) => setBoxSpec({ glue_width: e.target.value === "" ? undefined : Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <span>缩收量 (mm，可留空)</span>
          <input
            type="number"
            min={0}
            step={0.1}
            value={boxSpec.shrink ?? ""}
            onChange={(e) => setBoxSpec({ shrink: e.target.value === "" ? undefined : Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <span>出血 (mm，可留空)</span>
          <input
            type="number"
            min={0}
            step={0.5}
            value={boxSpec.bleed ?? ""}
            onChange={(e) => setBoxSpec({ bleed: e.target.value === "" ? undefined : Number(e.target.value) })}
          />
        </div>
      </div>
      <div className="row">
        <button className="primary" style={{ flex: 1 }} disabled={busy} onClick={() => void generateDieline()}>
          {busy && busyTask === "dieline" ? "生成中…" : dieline ? "重新生成刀版" : "生成刀版"}
        </button>
      </div>
      {dielineName ? (
        <p className="hint">
          当前刀版：{dielineName}
          {ecma ? ` · ECMA ${ecma}` : ""}
          {fefco ? ` · FEFCO ${fefco}` : ""}
        </p>
      ) : null}

      {netSize ? (
        <>
          <div className="divider" />
          <div className="row">
            <span className="hint" style={{ margin: 0, flex: 1 }}>
              展开尺寸{" "}
              <b style={{ color: "var(--ink)" }}>
                {fmt(netSize.width)} × {fmt(netSize.height)}
              </b>
            </span>
            <button className="small ghost" onClick={openFold}>
              <Icon name="play" size={12} /> 3D 折叠
            </button>
          </div>
        </>
      ) : null}

      {dieline && dieline.panels.length > 0 ? (
        <details>
          <summary className="hint" style={{ cursor: "pointer", margin: "6px 0" }}>
            面板清单（{dieline.panels.length}）
          </summary>
          <div className="check-list">
            {dieline.panels.map((p) => (
              <div className="check-item pass" key={p.id}>
                <span className="dot" />
                <span className="cid">{p.name}</span>
                <span className="cdetail">
                  {p.w.toFixed(1)} × {p.h.toFixed(1)} mm @ ({p.x.toFixed(1)}, {p.y.toFixed(1)})
                </span>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      {validation ? (
        <>
          <div className="divider" />
          <div className="row">
            <span className="section-title" style={{ margin: 0, flex: 1 }}>
              机械校验（14 项）
            </span>
            <span className={"badge " + (validation.passed ? "ok" : "bad")}>
              {validation.passed ? "全部通过" : "存在问题"}
            </span>
          </div>
          <div className="check-list">
            {validation.checks.map((c) => (
              <div className={"check-item " + (c.passed ? "pass" : "fail")} key={c.id}>
                <span className="dot" />
                <span className="cid">{c.name}</span>
                <span className="cdetail">{c.detail}</span>
              </div>
            ))}
          </div>
          {validation.manual_review.length > 0 ? (
            <p className="hint">人工复核：{validation.manual_review.join("、")}</p>
          ) : null}
        </>
      ) : null}

      {dieline && validation?.passed ? (
        <div className="row" style={{ marginTop: 10 }}>
          <button className="ghost" style={{ flex: 1 }} onClick={() => setStage("content")}>
            下一步：导入素材 <Icon name="chevron-down" size={12} style={{ transform: "rotate(-90deg)" }} />
          </button>
        </div>
      ) : null}
    </>
  );
}
