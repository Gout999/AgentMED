import type {
  CaseDetail,
  CaseSummary,
  ChangeSet,
  Experiment,
  Healthz,
  ListResponse,
  Release,
} from "./types";

/** API 客户端：一律走相对路径 /api/*（nginx 反代 → control-plane:8090）。 */

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `请求失败（HTTP ${status}）`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init?.body) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, { ...init, headers });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | undefined>): string {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") s.set(k, String(v));
  }
  const out = s.toString();
  return out ? `?${out}` : "";
}

export const api = {
  healthz: () => request<Healthz>("/healthz"),

  listCases: (state?: string) =>
    request<ListResponse<CaseSummary>>(`/v1/cases${qs({ state, limit: 100 })}`),

  getCase: (caseId: string) => request<CaseDetail>(`/v1/cases/${encodeURIComponent(caseId)}`),

  listExperiments: (state?: string) =>
    request<ListResponse<Experiment>>(`/v1/experiments${qs({ state, limit: 100 })}`),

  getExperiment: (id: string) => request<Experiment>(`/v1/experiments/${encodeURIComponent(id)}`),

  listChangesets: (state?: string) =>
    request<ListResponse<ChangeSet>>(`/v1/changesets${qs({ state, limit: 100 })}`),

  listReleases: (state?: string) =>
    request<ListResponse<Release>>(`/v1/releases${qs({ state, limit: 100 })}`),
};
