/** 刀版请求载荷构造器（唯一事实来源）：供刀版生成、3D 折叠预览与五格式导出共用。 */

import type { BoxSpec } from "../editor/types";

const OPTIONAL_MM_KEYS = ["tuck_height", "glue_width", "shrink", "bleed"] as const;

/** 组装后端 StructureSpec.from_dict() 所需的顶层字段载荷。 */
export function buildStructurePayload(boxSpec: BoxSpec): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    model_code: boxSpec.model_code,
    dimensions: {
      length: boxSpec.length,
      width: boxSpec.width,
      height: boxSpec.height,
      unit: "mm",
    },
  };
  for (const key of OPTIONAL_MM_KEYS) {
    const val = boxSpec[key];
    if (val != null && Number.isFinite(val)) {
      payload[key] = val;
    }
  }
  return payload;
}

/** 构造 3D 折叠预览 URL（/api/dieline/fold），透传所有已填写的工艺参数。 */
export function buildFoldPreviewUrl(boxSpec: BoxSpec): string {
  const q = new URLSearchParams({
    model_code: boxSpec.model_code,
    length: String(boxSpec.length),
    width: String(boxSpec.width),
    height: String(boxSpec.height),
  });
  for (const key of OPTIONAL_MM_KEYS) {
    const val = boxSpec[key];
    if (val != null && Number.isFinite(val)) {
      q.set(key, String(val));
    }
  }
  return `/api/dieline/fold?${q.toString()}`;
}
