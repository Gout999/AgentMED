import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const workspaceId = "ws_01J0000000000001";
const projectId = "proj_01J000000000001";
const applicationId = "app_01J0000000000001";
const principalId = "prn_01J000000000000A";
const digest = `sha256:${"1".repeat(64)}`;

function catalogResponse(overrides: Record<string, unknown> = {}) {
  const envelope = {
    schema_version: "2.0",
    workspace_id: workspaceId,
    revision: 1,
    recorded_by_principal: principalId,
    recorded_at: "2026-08-11T12:00:00Z",
    immutable: true,
    hash_rule: "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
    record_digest: digest,
    authority_receipt_id: "arec_01J0000000000001",
  };
  return {
    schema_version: "2.0",
    workspace_id: workspaceId,
    request_id: "req_01J0000000000001",
    audit_ref: "audit://aud_01J0000000000001",
    items: [{
      application: {
        record_envelope: envelope,
        application_id: applicationId,
        workspace_id: workspaceId,
        project_id: projectId,
        slug: "case-loop",
        display_name: "CaseLoop",
        owner_principal_ids: [principalId],
        criticality: "P1",
        data_classification: "INTERNAL",
        governance_mode: "MANAGED",
        lifecycle_state: "REGISTERED",
        exact_previous_application_binding_or_null: null,
      },
      environments: [],
      system_components: [],
      dependency_edges: [],
    }],
    next_cursor: "cur_01J0000000000001",
    ...overrides,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/v1/env")) return Promise.resolve(jsonResponse({ demo_app: "unavailable" }));
    if (url.includes("/events")) {
      return Promise.resolve(jsonResponse({
        case_id: "case/a b",
        aggregate_type: "case",
        items: [],
        evidence_refs: {},
      }));
    }
    if (url.includes("/experiments/") && url.includes("_view=full")) {
      return Promise.resolve(jsonResponse({
        experiment_id: "exp/a b",
        state: "VERDICT_COMPUTED",
        revision: 1,
        payload: {},
        cells: [],
        deltas: null,
        confidence_intervals: null,
        verdict: null,
        attributed_layer: null,
        evidence_bundle_ref: null,
        report_ref: null,
      }));
    }
    if (url.includes("/releases/release")) {
      return Promise.resolve(jsonResponse({ release_id: "release/a b", state: "REQUESTED", revision: 1, payload: {} }));
    }
    if (url.includes("/notifications/notification")) {
      return Promise.resolve(jsonResponse({ notification_id: "notification/a b", state: "QUEUED", revision: 1, payload: {} }));
    }
    if (url.includes("/evidence")) {
      return Promise.resolve(jsonResponse({
        items: [],
        artifact_store: "unavailable",
        warning: "artifact_content_unavailable",
      }));
    }
    if (url.includes("/workorders") || url.includes("/gates") || url.includes("/trust/")) {
      return Promise.resolve(jsonResponse({ items: [] }));
    }
    return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("CaseLoop Console API client", () => {
  it("targets every authoritative T8/read endpoint through /api", async () => {
    await api.getEnvironment();
    await api.getCaseEvents("case/a b");
    await api.getExperimentFull("exp/a b");
    await api.listWorkOrders();
    await api.listGates();
    await api.listTrustLedger();
    await api.listTrustDenials();
    await api.listReleases();
    await api.getRelease("release/a b");
    await api.listNotifications();
    await api.getNotification("notification/a b");
    await api.listEvidence("case/a b");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/env",
      "/api/v1/cases/case%2Fa%20b/events",
      "/api/v1/experiments/exp%2Fa%20b?_view=full",
      "/api/v1/workorders?limit=100",
      "/api/v1/gates?limit=100",
      "/api/v1/trust/ledger",
      "/api/v1/trust/denials",
      "/api/v1/releases?limit=100",
      "/api/v1/releases/release%2Fa%20b",
      "/api/v1/notifications?limit=100",
      "/api/v1/notifications/notification%2Fa%20b",
      "/api/v1/evidence?case_id=case%2Fa+b&limit=100",
    ]);
  });

  it("preserves env unavailable and read-view warnings", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ demo_app: "unavailable" }))
      .mockResolvedValueOnce(jsonResponse({ items: [], warning: "source_unavailable" }));

    await expect(api.getEnvironment()).resolves.toEqual({ demo_app: "unavailable" });
    await expect(api.listTrustLedger()).resolves.toEqual({
      items: [],
      warning: "source_unavailable",
    });
  });

  it("does not coerce skipped, error, or integrity failure into pass", async () => {
    const gate = {
      eval_id: "eval_1",
      workorder_id: "wo_1",
      workorder_hash: null,
      report_id: "gate_1",
      rule_track: "error",
      judge_track: "skipped",
      deterministic_tests: "passed",
      live_provider_e2e: "skipped",
      verdict: "error",
      report_hash: "1".repeat(64),
      binding_digest: null,
      target_versionset_id: "vs_1",
      target_revision: 1,
      dataset_id: "regression",
      dataset_version: "1",
      evidence_digest: `sha256:${"2".repeat(64)}`,
      status: "integrity_error",
      integrity_error: "hash_mismatch",
      binding_status: "UNKNOWN",
      binding_error: "hash_mismatch",
      created_at: null,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [gate] }));
    const result = await api.listGates();
    expect(result.items[0]).toMatchObject(gate);
    expect(Object.values(result.items[0])).not.toContain("PASS");
  });

  it("extracts FastAPI detail code and message", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { detail: { code: "gate_failed", message: "GateReport is not releasable" } },
        422,
      ),
    );
    await expect(api.getRelease("release_1")).rejects.toMatchObject({
      status: 422,
      code: "gate_failed",
      message: "GateReport is not releasable",
    });
  });

  it("reports network and malformed JSON failures explicitly", async () => {
    fetchMock.mockRejectedValueOnce(new Error("connection refused"));
    await expect(api.listCases()).rejects.toMatchObject({
      status: 0,
      code: "network_error",
      message: "connection refused",
    });

    fetchMock.mockResolvedValueOnce(new Response("not-json", { status: 200 }));
    await expect(api.listCases()).rejects.toMatchObject({
      status: 200,
      code: "invalid_response",
    });
  });

  it("rejects malformed objects instead of rendering a trusted or green state", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ demo_app: { status: "active" } }));
    await expect(api.getEnvironment()).rejects.toMatchObject({
      status: 200,
      code: "invalid_response",
    });

    fetchMock.mockResolvedValueOnce(jsonResponse({
      items: [{
        risk_class: "R2",
        action_type: "release",
        epoch: 1,
        successes: 3,
        trials: 3,
        autonomy_state: "MANUAL",
        LB: "0.438494",
        UB: 1,
        promotion_eligible: false,
        suspended_until: null,
        pending_promotion_ref: null,
        sample_rule: null,
        last_action_ref: null,
        updated_at: null,
      }],
    }));
    await expect(api.listTrustLedger()).rejects.toMatchObject({
      status: 200,
      code: "invalid_response",
    });

    fetchMock.mockResolvedValueOnce(jsonResponse({
      items: [{
        eval_id: "eval_cross_field",
        workorder_id: "wo_cross_field",
        workorder_hash: null,
        report_id: "gate_cross_field",
        rule_track: "passed",
        judge_track: "passed",
        deterministic_tests: "passed",
        live_provider_e2e: "passed",
        verdict: "passed",
        report_hash: "1".repeat(64),
        binding_digest: null,
        target_versionset_id: "vs_cross_field",
        target_revision: 1,
        dataset_id: "regression",
        dataset_version: "1",
        evidence_digest: `sha256:${"2".repeat(64)}`,
        status: "completed",
        binding_status: "UNKNOWN",
        binding_error: null,
        created_at: null,
      }],
    }));
    await expect(api.listGates()).rejects.toMatchObject({
      status: 200,
      code: "invalid_response",
    });
  });

  it("authenticates the public application list without placing the bearer in URL or body", async () => {
    const bearerToken = "opaque-catalog-token-never-persist";
    fetchMock.mockResolvedValueOnce(jsonResponse(catalogResponse()));

    await expect(api.listApplications(
      { workspaceId, projectId, bearerToken },
      { limit: 25, cursor: "cur_previous0001" },
    )).resolves.toMatchObject({ workspace_id: workspaceId, items: [{ application: { project_id: projectId } }] });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(url).toBe(
      `/api/api/v2/applications?project_id=${projectId}&limit=25&cursor=cur_previous0001`,
    );
    expect(url).not.toContain(bearerToken);
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect(headers.get("Authorization")).toBe(`Bearer ${bearerToken}`);
    expect(headers.get("X-CaseLoop-Workspace-ID")).toBe(workspaceId);
    expect(headers.get("X-CaseLoop-Contract-Version")).toBe("2.0");
    expect(headers.get("X-CaseLoop-Client-Version")).toBe("console/r2");
  });

  it("redacts the bearer from public API error objects even if a server reflects it", async () => {
    const bearerToken = "opaque-reflected-secret";
    fetchMock.mockResolvedValueOnce(jsonResponse({
      error: {
        code: "TOKEN_INVALID",
        message: `invalid ${bearerToken}`,
        details: { provider_message: bearerToken },
      },
    }, 401));

    const error = await api.listApplications({ workspaceId, projectId, bearerToken }).catch((caught) => caught);
    expect(error).toMatchObject({ status: 401, code: "TOKEN_INVALID" });
    expect(error.message).toContain("[REDACTED_SECRET]");
    expect(JSON.stringify({
      message: error.message,
      status: error.status,
      code: error.code,
      detail: error.detail,
    })).not.toContain(bearerToken);
  });

  it("fails closed on malformed and cross-scope catalog responses", async () => {
    const credential = { workspaceId, projectId, bearerToken: "opaque-valid-token" };
    fetchMock.mockResolvedValueOnce(jsonResponse(catalogResponse({ unexpected: true })));
    await expect(api.listApplications(credential)).rejects.toMatchObject({
      status: 200,
      code: "invalid_response",
    });

    fetchMock.mockResolvedValueOnce(jsonResponse(catalogResponse({
      workspace_id: "ws_01J0000000000002",
    })));
    await expect(api.listApplications(credential)).rejects.toMatchObject({
      status: 200,
      code: "invalid_response",
    });

    const crossProject = catalogResponse();
    (crossProject.items[0].application as Record<string, unknown>).project_id = "proj_01J000000000002";
    fetchMock.mockResolvedValueOnce(jsonResponse(crossProject));
    await expect(api.listApplications(credential)).rejects.toMatchObject({
      status: 200,
      code: "integrity_error",
    });
  });

  it("accepts only the revision-matched application and component previous-binding union", async () => {
    const credential = { workspaceId, projectId, bearerToken: "opaque-union-token" };
    const active = catalogResponse();
    const application = active.items[0].application as Record<string, unknown>;
    const activeEnvelope = {
      ...(application.record_envelope as Record<string, unknown>),
      revision: 2,
    };
    application.record_envelope = activeEnvelope;
    application.lifecycle_state = "ACTIVE";
    delete application.exact_previous_application_binding_or_null;
    application.exact_previous_application_binding = {
      kind: "AI_APPLICATION",
      id: applicationId,
      revision: 1,
      digest,
    };
    (active.items[0] as unknown as Record<string, unknown>).system_components = [{
      record_envelope: { ...activeEnvelope, revision: 2 },
      component_id: "cmp_01J0000000000001",
      workspace_id: workspaceId,
      application_id: applicationId,
      component_kind: "AGENT",
      logical_name: "triage_agent",
      owner_principal_ids: [principalId],
      criticality: "P1",
      data_classification: "INTERNAL",
      permission_classification: "READ_WRITE",
      effect_classification: "LOCAL",
      dataset_role: null,
      lifecycle_state: "ACTIVE",
      exact_previous_system_component_binding: {
        kind: "SYSTEM_COMPONENT",
        id: "cmp_01J0000000000001",
        revision: 1,
        digest,
      },
    }];
    fetchMock.mockResolvedValueOnce(jsonResponse(active));
    await expect(api.listApplications(credential)).resolves.toMatchObject({
      items: [{
        application: { lifecycle_state: "ACTIVE", exact_previous_application_binding: { revision: 1 } },
        system_components: [{ exact_previous_system_component_binding: { revision: 1 } }],
      }],
    });

    const invalidApplication = catalogResponse();
    (invalidApplication.items[0].application.record_envelope as Record<string, unknown>).revision = 2;
    (invalidApplication.items[0].application as Record<string, unknown>).lifecycle_state = "ACTIVE";
    fetchMock.mockResolvedValueOnce(jsonResponse(invalidApplication));
    await expect(api.listApplications(credential)).rejects.toMatchObject({ code: "invalid_response" });

    const invalidComponent = catalogResponse();
    (invalidComponent.items[0] as unknown as Record<string, unknown>).system_components = [{
      record_envelope: invalidComponent.items[0].application.record_envelope,
      component_id: "cmp_01J0000000000001",
      workspace_id: workspaceId,
      application_id: applicationId,
      component_kind: "AGENT",
      logical_name: "triage_agent",
      owner_principal_ids: [principalId],
      criticality: "P1",
      data_classification: "INTERNAL",
      permission_classification: "READ_WRITE",
      effect_classification: "LOCAL",
      dataset_role: null,
      lifecycle_state: "REGISTERED",
      exact_previous_system_component_binding_or_null: null,
      exact_previous_system_component_binding: {
        kind: "SYSTEM_COMPONENT",
        id: "cmp_01J0000000000001",
        revision: 1,
        digest,
      },
    }];
    fetchMock.mockResolvedValueOnce(jsonResponse(invalidComponent));
    await expect(api.listApplications(credential)).rejects.toMatchObject({ code: "invalid_response" });
  });
});
