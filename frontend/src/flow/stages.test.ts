import { describe, expect, it } from "vitest";
import type { EditorState } from "../store/editor";
import { deriveStageViews, loopVerdict } from "./stages";

function makeState(overrides: Partial<EditorState> = {}): EditorState {
  return {
    boxSpec: { model_code: "carton.box_v2.straight", length: 90, width: 50, height: 130 },
    dieline: null,
    dielineName: "",
    dielineEcma: "",
    dielineFefco: "",
    netSize: null,
    validation: null,
    layers: [],
    guides: [],
    camera: { x: 0, y: 0, scale: 3 },
    tool: "select",
    showGrid: true,
    units: "mm",
    selectionId: null,
    measure: { active: false, start: null, end: null },
    spacePanning: false,
    cursor: null,
    proofVisible: false,
    oogVisible: false,
    deviceProfilePath: "",
    proofIntent: "relative",
    proofBpc: true,
    proofThreshold: 4,
    proofMetric: "de00",
    proofStats: null,
    proofStale: false,
    verifyStale: false,
    picked: null,
    proofImage: null,
    oogImage: null,
    stage: "structure",
    audit: null,
    verifyReport: null,
    profiles: [],
    exportCount: 0,
    theme: "dark",
    toasts: [],
    busy: false,
    busyTask: null,
    fitRequest: 0,
    past: [],
    future: [],
    ...overrides,
  } as EditorState;
}

const DUMMY_DIELINE = { segments: [], panels: [], viewBox: { x: 0, y: 0, w: 300, h: 200 } };
const DUMMY_LAYER = {
  id: "l1",
  name: "art.png",
  kind: "image" as const,
  x: 0,
  y: 0,
  w: 100,
  h: 100,
  rotation: 0,
  opacity: 1,
  visible: true,
  locked: false,
  bitmap: null,
};

describe("五阶段色彩闭环软门控与过期联动", () => {
  it("初始空状态：仅①和③可用，②④⑤锁定", () => {
    const views = deriveStageViews(makeState());
    expect(views.structure.status).toBe("ready");
    expect(views.content.status).toBe("locked");
    expect(views.color.status).toBe("ready");
    expect(views.proof.status).toBe("locked");
    expect(views.deliver.status).toBe("locked");
  });

  it("打样与测量通过后进入 pass，内容变更标 stale 后④转为 issue", () => {
    const passed = makeState({
      dieline: DUMMY_DIELINE,
      dielineName: "直线盒",
      netSize: { width: 300, height: 200 },
      validation: { passed: true, checks: [], manual_review: [] },
      layers: [DUMMY_LAYER],
      deviceProfilePath: "C:/Windows/System32/spool/drivers/color/RSWOP.icm",
      proofStats: { oog_count: 0, oog_ratio: 0, delta_e_max: 1.2, delta_e_mean: 0.4, threshold: 4, metric: "de00" },
      verifyReport: {
        verdict: "PASS",
        passed: true,
        reference: "Fogra39",
        tolerance: 5,
        report: {
          profile_description: "RSWOP",
          intent: "relative_colorimetric",
          bpc: true,
          reference_name: "Fogra39",
          patches: [],
          stats: { count: 9, mean: 0.6, max: 1.1, stdev: 0.2, percentiles: { p95: 1.0 }, worst: [] },
          iso: [],
          passed: true,
          tolerance: 5,
          channel_diagnostics: null,
        },
      },
    });
    expect(loopVerdict(passed)).toBe("pass");
    expect(deriveStageViews(passed).proof.status).toBe("done");

    const stale = { ...passed, proofStale: true };
    expect(loopVerdict(stale)).toBe("stale");
    expect(deriveStageViews(stale).proof.status).toBe("issue");
    expect(deriveStageViews(stale).proof.summary).toContain("已过期");
  });
});
