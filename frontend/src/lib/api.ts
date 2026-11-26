/** 类型化 API 客户端：统一 {ok,data|error} 包裹。 */

export interface ApiEnvelope<T> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string; detail?: Record<string, unknown> };
}

export class ApiFailure extends Error {
  code: string;
  detail: Record<string, unknown>;
  constructor(code: string, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.code = code;
    this.detail = detail;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  let body: ApiEnvelope<T>;
  try {
    body = (await resp.json()) as ApiEnvelope<T>;
  } catch {
    throw new ApiFailure("HTTP_" + resp.status, `请求失败（HTTP ${resp.status}）`);
  }
  if (!resp.ok || !body.ok) {
    const err = body.error ?? { code: "HTTP_" + resp.status, message: `HTTP ${resp.status}` };
    throw new ApiFailure(err.code, err.message, err.detail ?? {});
  }
  return body.data as T;
}

function json(payload: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

/* ---------------- 类型 ---------------- */

export interface BoxChoice {
  value: string;
  label: string;
  status: "available" | "not_implemented";
}

export interface BoxReportItem {
  code: string;
  name_zh: string;
  aliases: string[];
  implemented: boolean;
  ecma: string;
  fefco: string;
  family: string;
}

export interface ProfileInfo {
  path: string;
  file_size: number;
  description: string;
  device_class: string;
  color_space: string;
  version: string;
  is_hdr: boolean;
}

export interface CheckResult {
  id: string;
  name: string;
  passed: boolean;
  detail: string;
}

export interface ValidationInfo {
  passed: boolean;
  checks: CheckResult[];
  manual_review: string[];
}

export interface Bounds {
  min_x: number;
  min_y: number;
  max_x: number;
  max_y: number;
}

export interface DielineResponse {
  svg: string;
  metadata: Record<string, unknown> & {
    box_type: string;
    box_name_zh?: string;
    ecma?: string;
    fefco?: string;
    family?: string;
    dimensions_mm?: Record<string, number>;
    parameters_mm?: Record<string, number>;
    panels?: { id: string; name: string; x: number; y: number; w: number; h: number }[];
  };
  bounds: Bounds;
  net_size_mm: { width: number; height: number };
  validation: ValidationInfo;
}

export interface ExportResponse {
  id: string;
  filename: string;
  mime: string;
  size: number;
}

export interface ProofResponse {
  proof: { id: string; filename: string };
  out_of_gamut: { id: string; filename: string };
  stats: {
    oog_count: number;
    oog_ratio: number;
    delta_e_max: number;
    delta_e_mean: number;
    threshold: number;
    metric?: "de00" | "de76";
    scaled?: boolean;
  };
}

export interface ConvertReport {
  src: string;
  dst: string;
  op: string;
  ok: boolean;
  error_code: string | null;
  message: string | null;
  src_profile: string | null;
  dst_profile: string | null;
}

export interface ConvertResponse {
  id: string;
  filename: string;
  color_space?: string;
  assumed_src_srgb?: boolean;
  preview?: { id: string; filename: string } | null;
  report: ConvertReport;
}

export interface LabResponse {
  lab: [number, number, number];
  color_space?: string;
  device_values?: number[];
  tac?: number | null;
}

export interface PdfAuditReport {
  // pdfaudit 数据模型字段按引擎契约透传（宽松类型）
  [key: string]: unknown;
}

/* ---- 闭环③：档案质量校验 ---- */

export interface ProfileValidationIssue {
  severity: "INFO" | "WARNING" | "ERROR";
  code: string;
  message: string;
  location: string | null;
}

export interface ProfileValidationReport {
  valid: boolean;
  issues: ProfileValidationIssue[];
  counts: { error: number; warning: number; info: number };
}

export interface ValidateResponse {
  report: ProfileValidationReport;
  filename?: string;
}

/* ---- 闭环④：CGATS 测量验证（ΔE00 对标 ISO 12647-2 / Fogra39） ---- */

export interface VerifyPatch {
  patch_id: string;
  measured_lab: [number, number, number];
  reference_lab: [number, number, number];
  delta_e_76: number;
  delta_e_00: number;
  device: number[] | null;
}

export interface VerifyIsoRow {
  patch_id: string;
  target: string;
  measured_lab: [number, number, number];
  reference_lab: [number, number, number];
  delta_e_00: number;
  tolerance: number;
  passed: boolean;
}

export interface VerifyResponse {
  verdict: "PASS" | "WARNING" | "FAIL";
  passed: boolean;
  reference: string | null;
  tolerance: number | null;
  report: {
    profile_description: string;
    intent: string;
    bpc: boolean;
    reference_name: string | null;
    patches: VerifyPatch[];
    stats: {
      count: number;
      mean: number;
      max: number;
      stdev: number;
      percentiles: Record<string, number>;
      worst: VerifyPatch[];
    };
    iso: VerifyIsoRow[] | null;
    passed: boolean;
    tolerance: number | null;
    channel_diagnostics: {
      channels: {
        channel: string;
        coverage_pct: number;
        active_count: number;
        mean_de: number | null;
        solid_de: number | null;
      }[];
      hints: string[];
    } | null;
  };
}

/* ---------------- 接口 ---------------- */

export const api = {
  health: () => request<{ app: string; version: string }>("/api/health"),

  boxes: () => request<{ choices: BoxChoice[]; report: BoxReportItem[] }>("/api/boxes"),

  dieline: (spec: Record<string, unknown>) => request<DielineResponse>("/api/dieline", json(spec)),

  exportDieline: (spec: Record<string, unknown>, format: string) =>
    request<ExportResponse>("/api/export", json({ ...spec, format })),

  downloadUrl: (id: string, filename: string) => `/api/download/${id}/${encodeURIComponent(filename)}`,

  profiles: (refresh = false) =>
    request<{ profiles: ProfileInfo[] }>(refresh ? "/api/icc/profiles?refresh=1" : "/api/icc/profiles"),

  lab: (
    rgb: [number, number, number],
    opts?: string | { profile?: string; intent?: string; bpc?: boolean },
  ) => {
    const body =
      typeof opts === "string"
        ? { rgb, profile: opts }
        : { rgb, profile: opts?.profile, intent: opts?.intent, bpc: opts?.bpc };
    return request<LabResponse>("/api/icc/lab", json(body));
  },

  proof: (form: FormData) => request<ProofResponse>("/api/icc/proof", { method: "POST", body: form }),

  /** 图像 ICC 操作（assign/embed/strip/convert），结果落为可下载文件（闭环②：素材赋档/转换）。 */
  convert: (form: FormData) => request<ConvertResponse>("/api/icc/convert", { method: "POST", body: form }),

  pdfAudit: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ report: PdfAuditReport }>("/api/pdf/audit", { method: "POST", body: form });
  },

  /** 档案质量校验（闭环③）：19 条结构规则 + 必需 tag 表。 */
  validateProfile: (path: string) =>
    request<ValidateResponse>(`/api/icc/validate?path=${encodeURIComponent(path)}`),

  /** CGATS 测量验证（闭环④）：ΔE00 统计 + ISO 12647-2/Fogra39 判定。 */
  verifyCgats: (
    file: File,
    opts: { profilePath?: string; intent?: string; bpc?: boolean; reference?: string; tolerance?: number } = {},
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (opts.profilePath) {
      form.append("profile_path", opts.profilePath);
    }
    if (opts.intent) {
      form.append("intent", opts.intent);
    }
    if (opts.bpc != null) {
      form.append("bpc", String(opts.bpc));
    }
    if (opts.reference) {
      form.append("reference", opts.reference);
    }
    if (opts.tolerance != null) {
      form.append("tolerance", String(opts.tolerance));
    }
    return request<VerifyResponse>("/api/icc/verify", { method: "POST", body: form });
  },
};

/** 触发浏览器下载。 */
export function triggerDownload(url: string, filename: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
