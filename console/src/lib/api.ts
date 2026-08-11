import type {
  ApplicationView,
  CaseDetail,
  CaseEventsView,
  CaseSummary,
  CaseV5Readiness,
  ChangeSet,
  EnvironmentStatus,
  EvidenceResponse,
  Experiment,
  ExperimentFull,
  GateView,
  Healthz,
  ListResponse,
  NotificationView,
  ReadViewList,
  ReleaseDetail,
  ReleaseSummary,
  TrustDenialView,
  TrustLedgerView,
  WorkOrderView,
} from "./types";
import { guards, type Guard } from "./validators";

/** API calls use /api so Vite/nginx is always part of the Console data path. */

interface ErrorDetail {
  code?: string;
  message?: string;
}

function extractErrorDetail(value: unknown): ErrorDetail | null {
  if (typeof value !== "object" || value === null) return null;
  const outer = value as Record<string, unknown>;
  const nested = typeof outer.detail === "object" && outer.detail !== null
    ? (outer.detail as Record<string, unknown>)
    : outer;
  return {
    code: typeof nested.code === "string" ? nested.code : undefined,
    message: typeof nested.message === "string" ? nested.message : undefined,
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(status: number, detail: unknown, fallbackCode = "http_error") {
    const parsed = extractErrorDetail(detail);
    super(parsed?.message ?? (typeof detail === "string" ? detail : `请求失败（HTTP ${status}）`));
    this.name = "ApiError";
    this.status = status;
    this.code = parsed?.code ?? fallbackCode;
    this.detail = detail;
  }
}

async function request<T>(path: string, guard: Guard<T>, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, headers });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    const message = error instanceof Error ? error.message : String(error);
    throw new ApiError(0, { code: "network_error", message }, "network_error");
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(
      response.status,
      { code: "invalid_response", message: "control-plane 返回了非 JSON 响应" },
      "invalid_response",
    );
  }
  if (!response.ok) throw new ApiError(response.status, body);
  if (!guard(body)) {
    throw new ApiError(
      response.status,
      { code: "invalid_response", message: "control-plane 响应结构无效" },
      "invalid_response",
    );
  }
  return body;
}

function qs(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export const api = {
  healthz: (signal?: AbortSignal) => request<Healthz>("/healthz", guards.health, { signal }),
  getEnvironment: (signal?: AbortSignal) => request<EnvironmentStatus>("/v1/env", guards.environment, { signal }),

  listCases: (state?: string, signal?: AbortSignal) =>
    request<ListResponse<CaseSummary>>(`/v1/cases${qs({ state, limit: 100 })}`, guards.caseList, { signal }),
  getCase: (caseId: string, signal?: AbortSignal) =>
    request<CaseDetail>(`/v1/cases/${encodeURIComponent(caseId)}`, guards.caseDetail, { signal }),
  getCaseEvents: (caseId: string, signal?: AbortSignal) =>
    request<CaseEventsView>(`/v1/cases/${encodeURIComponent(caseId)}/events`, guards.caseEvents, { signal }),
  getCaseV5Readiness: (caseId: string, signal?: AbortSignal) =>
    request<CaseV5Readiness>(`/v1/cases/${encodeURIComponent(caseId)}/v5-readiness`, guards.caseV5Readiness, {
      signal,
    }),

  listExperiments: (state?: string, signal?: AbortSignal) =>
    request<ListResponse<Experiment>>(`/v1/experiments${qs({ state, limit: 100 })}`, guards.experimentList, { signal }),
  getExperiment: (id: string, signal?: AbortSignal) =>
    request<Experiment>(`/v1/experiments/${encodeURIComponent(id)}`, guards.experiment, { signal }),
  getExperimentFull: (id: string, signal?: AbortSignal) =>
    request<ExperimentFull>(`/v1/experiments/${encodeURIComponent(id)}${qs({ _view: "full" })}`, guards.experimentFull, {
      signal,
    }),

  listChangesets: (state?: string, signal?: AbortSignal) =>
    request<ListResponse<ChangeSet>>(`/v1/changesets${qs({ state, limit: 100 })}`, guards.changesetList, { signal }),
  listWorkOrders: (signal?: AbortSignal) =>
    request<ReadViewList<WorkOrderView>>(`/v1/workorders${qs({ limit: 100 })}`, guards.workorderList, { signal }),
  listGates: (signal?: AbortSignal) =>
    request<ReadViewList<GateView>>(`/v1/gates${qs({ limit: 100 })}`, guards.gateList, { signal }),
  listTrustLedger: (signal?: AbortSignal) =>
    request<ReadViewList<TrustLedgerView>>("/v1/trust/ledger", guards.trustLedgerList, { signal }),
  listTrustDenials: (signal?: AbortSignal) =>
    request<ReadViewList<TrustDenialView>>("/v1/trust/denials", guards.trustDenialList, { signal }),

  listReleases: (state?: string, signal?: AbortSignal) =>
    request<ListResponse<ReleaseSummary>>(`/v1/releases${qs({ state, limit: 100 })}`, guards.releaseList, { signal }),
  getRelease: (id: string, signal?: AbortSignal) =>
    request<ReleaseDetail>(`/v1/releases/${encodeURIComponent(id)}`, guards.releaseDetail, { signal }),
  listNotifications: (signal?: AbortSignal) =>
    request<ListResponse<NotificationView>>(`/v1/notifications${qs({ limit: 100 })}`, guards.notificationList, { signal }),
  getNotification: (id: string, signal?: AbortSignal) =>
    request<NotificationView>(`/v1/notifications/${encodeURIComponent(id)}`, guards.notification, { signal }),
  listEvidence: (caseId?: string, signal?: AbortSignal) =>
    request<EvidenceResponse>(`/v1/evidence${qs({ case_id: caseId, limit: 100 })}`, guards.evidenceResponse, { signal }),
  listApplications: (signal?: AbortSignal) =>
    request<ReadViewList<ApplicationView>>(`/v1/applications${qs({ limit: 100 })}`, guards.applicationList, { signal }),
};
