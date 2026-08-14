import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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

  it("preserves V5 integrity_error as UNKNOWN instead of a trusted readiness", async () => {
    const projection = {
      case_id: "case_1",
      case_revision: 1,
      case_integrity_status: "verified",
      case_integrity_error: null,
      application_binding: null,
      binding_integrity_status: "integrity_error",
      binding_integrity_error: "v5.binding_integrity_error:record_digest_mismatch",
      case_readiness: "UNKNOWN",
      acceptance_integrity_status: "verified",
      acceptance_integrity_error: null,
      acceptance_proposal_count: 1,
      confirmed_acceptance_count: 0,
      executable_acceptance_count: 0,
      missing_evidence: ["trace_id"],
      issue_snapshot: null,
      issue_snapshot_integrity_status: "integrity_error",
      issue_snapshot_integrity_error: "v5.issue_snapshot_integrity_error:digest_mismatch",
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(projection));

    await expect(api.getCaseV5Readiness("case_1")).resolves.toEqual(projection);
  });

  it("accepts confirmed-but-not-executable V5 readiness only as NEEDS", async () => {
    const digest = `sha256:${"a".repeat(64)}`;
    const projection = {
      case_id: "case_1",
      case_revision: 1,
      case_integrity_status: "verified",
      case_integrity_error: null,
      application_binding: {
        application_case_binding_id: "acb_12345678",
        application_id: "app_12345678",
        environment_id: "env_12345678",
        exact_case_binding: {
          case_id: "case_1",
          case_revision: 1,
          case_digest: digest,
        },
        declared_system_version_set_binding_or_unknown: {
          kind: "UNKNOWN",
          reason: "not declared",
        },
        record_digest: digest,
      },
      binding_integrity_status: "verified",
      binding_integrity_error: null,
      case_readiness: "NEEDS_ACCEPTANCE_CRITERIA",
      acceptance_integrity_status: "verified",
      acceptance_integrity_error: null,
      acceptance_proposal_count: 1,
      confirmed_acceptance_count: 1,
      executable_acceptance_count: 0,
      missing_evidence: [],
      issue_snapshot: null,
      issue_snapshot_integrity_status: "missing",
      issue_snapshot_integrity_error: null,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(projection));

    await expect(api.getCaseV5Readiness("case_1")).resolves.toEqual(projection);
  });

  it("rejects a READY projection whose integrity or executable count is untrusted", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      case_id: "case_1",
      case_revision: 1,
      case_integrity_status: "verified",
      case_integrity_error: null,
      application_binding: null,
      binding_integrity_status: "integrity_error",
      binding_integrity_error: "digest_mismatch",
      case_readiness: "READY",
      acceptance_integrity_status: "verified",
      acceptance_integrity_error: null,
      acceptance_proposal_count: 0,
      confirmed_acceptance_count: 1,
      executable_acceptance_count: 0,
      missing_evidence: [],
      issue_snapshot: null,
      issue_snapshot_integrity_status: "missing",
      issue_snapshot_integrity_error: null,
    }));

    await expect(api.getCaseV5Readiness("case_1")).rejects.toMatchObject({
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
});
