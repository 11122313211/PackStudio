import { describe, expect, it } from "vitest";
import { buildFoldPreviewUrl, buildStructurePayload } from "./boxPayload";

describe("boxPayload", () => {
  it("将工艺参数平铺放在请求顶层而非嵌套 parameters_mm", () => {
    const payload = buildStructurePayload({
      model_code: "carton.box_v2.straight",
      length: 120,
      width: 60,
      height: 90,
      tuck_height: 20,
      glue_width: 15,
      shrink: 0.4,
      bleed: 4,
    });
    expect(payload).toEqual({
      model_code: "carton.box_v2.straight",
      dimensions: { length: 120, width: 60, height: 90, unit: "mm" },
      tuck_height: 20,
      glue_width: 15,
      shrink: 0.4,
      bleed: 4,
    });
    expect(payload).not.toHaveProperty("parameters_mm");
  });

  it("省略未填写的可选工艺参数", () => {
    const payload = buildStructurePayload({
      model_code: "carton.box_v2.mailer",
      length: 200,
      width: 150,
      height: 50,
    });
    expect(payload).toEqual({
      model_code: "carton.box_v2.mailer",
      dimensions: { length: 200, width: 150, height: 50, unit: "mm" },
    });
  });

  it("3D 折叠预览 URL 完整透传已填写的工艺参数", () => {
    const url = buildFoldPreviewUrl({
      model_code: "carton.box_v2.straight",
      length: 100,
      width: 50,
      height: 80,
      tuck_height: 18,
      bleed: 3.5,
    });
    expect(url).toContain("/api/dieline/fold?");
    expect(url).toContain("tuck_height=18");
    expect(url).toContain("bleed=3.5");
    expect(url).not.toContain("glue_width=");
  });
});
