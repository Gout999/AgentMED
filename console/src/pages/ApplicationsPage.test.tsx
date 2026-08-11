import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApplicationCatalogListResponse } from "../lib/types";

const { listApplicationsMock } = vi.hoisted(() => ({
  listApplicationsMock: vi.fn(),
}));

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, listApplications: listApplicationsMock },
  };
});

import { ApiError } from "../lib/api";
import { ApplicationsPage } from "./ApplicationsPage";

const workspaceId = "ws_01J0000000000001";
const projectId = "proj_01J000000000001";
const token = "opaque-ui-memory-only";
const digest = `sha256:${"1".repeat(64)}`;

function envelope(revision = 1) {
  return {
    schema_version: "2.0" as const,
    workspace_id: workspaceId,
    revision,
    recorded_by_principal: "prn_01J000000000000A",
    recorded_at: "2026-08-11T12:00:00Z",
    immutable: true as const,
    hash_rule: "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)" as const,
    record_digest: digest,
    authority_receipt_id: "arec_01J0000000000001",
  };
}

function response(nextCursor: string | null = null): ApplicationCatalogListResponse {
  return {
    schema_version: "2.0",
    workspace_id: workspaceId,
    request_id: "req_01J0000000000001",
    audit_ref: "audit://aud_01J0000000000001",
    items: [{
      application: {
        record_envelope: envelope(),
        application_id: "app_01J0000000000001",
        workspace_id: workspaceId,
        project_id: projectId,
        slug: "case-loop",
        display_name: "CaseLoop",
        owner_principal_ids: ["prn_01J000000000000A"],
        criticality: "P1",
        data_classification: "INTERNAL",
        governance_mode: "MANAGED",
        lifecycle_state: "REGISTERED",
        exact_previous_application_binding_or_null: null,
      },
      environments: [{
        record_envelope: envelope(),
        environment_id: "env_01J0000000000001",
        workspace_id: workspaceId,
        application_id: "app_01J0000000000001",
        logical_name: "production",
        risk_classification: "HIGH",
        lifecycle_state: "ACTIVE",
      }],
      system_components: [{
        record_envelope: envelope(),
        component_id: "cmp_01J0000000000001",
        workspace_id: workspaceId,
        application_id: "app_01J0000000000001",
        component_kind: "AGENT",
        logical_name: "triage_agent",
        owner_principal_ids: ["prn_01J000000000000A"],
        criticality: "P1",
        data_classification: "INTERNAL",
        permission_classification: "READ_WRITE",
        effect_classification: "LOCAL",
        dataset_role: null,
        lifecycle_state: "REGISTERED",
        exact_previous_system_component_binding_or_null: null,
      }],
      dependency_edges: [],
    }],
    next_cursor: nextCursor,
  };
}

async function connect(renderer: ReactTestRenderer, bearer = token) {
  const root = renderer.root;
  await act(async () => {
    root.findByProps({ name: "workspace_id" }).props.onChange({ target: { value: workspaceId } });
    root.findByProps({ name: "project_id" }).props.onChange({ target: { value: projectId } });
    root.findByProps({ name: "bearer_token" }).props.onChange({ target: { value: bearer } });
  });
  await act(async () => {
    root.findByType("form").props.onSubmit({ preventDefault: vi.fn() });
    await Promise.resolve();
  });
}

afterEach(() => {
  listApplicationsMock.mockReset();
});

describe("ApplicationsPage authenticated catalog", () => {
  it("starts behind an operator credential gate and never renders the bearer", async () => {
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(<ApplicationsPage />);
    });
    expect(renderer.root.findByProps({ name: "bearer_token" }).props.type).toBe("password");
    expect(listApplicationsMock).not.toHaveBeenCalled();

    listApplicationsMock.mockResolvedValue(response());
    await connect(renderer);
    expect(JSON.stringify(renderer.toJSON())).not.toContain(token);
    renderer.unmount();
  });

  it("renders real Application, Environment, Component and explicit empty Edge state", async () => {
    const catalog = response();
    catalog.items.push({
      ...catalog.items[0],
      application: {
        ...catalog.items[0].application,
        record_envelope: envelope(2),
        application_id: "app_01J0000000000002",
        slug: "case-loop-active",
        display_name: "CaseLoop Active",
        lifecycle_state: "ACTIVE",
        exact_previous_application_binding_or_null: undefined,
        exact_previous_application_binding: {
          kind: "AI_APPLICATION",
          id: "app_01J0000000000002",
          revision: 1,
          digest,
        },
      },
      environments: [],
      system_components: [],
      dependency_edges: [],
    });
    listApplicationsMock.mockResolvedValue(catalog);
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(<ApplicationsPage />);
    });
    await connect(renderer);

    const rendered = JSON.stringify(renderer.toJSON());
    expect(rendered).toContain("CaseLoop");
    expect(rendered).toContain("REGISTERED");
    expect(rendered).toContain("ACTIVE");
    expect(rendered).toContain("production");
    expect(rendered).toContain("triage_agent");
    expect(rendered).toContain("暂无 DependencyEdge");
    renderer.unmount();
  });

  it("renders explicit loading and authorized-empty states", async () => {
    let resolve!: (value: ApplicationCatalogListResponse) => void;
    listApplicationsMock.mockReturnValue(new Promise((done) => {
      resolve = done;
    }));
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(<ApplicationsPage />);
    });
    await connect(renderer);
    expect(JSON.stringify(renderer.toJSON())).toContain("加载中");

    await act(async () => {
      resolve({ ...response(), items: [] });
      await Promise.resolve();
    });
    const rendered = JSON.stringify(renderer.toJSON());
    expect(rendered).toContain("暂无数据");
    expect(rendered).toContain("visibility filter");
    renderer.unmount();
  });

  it("clears the in-memory credential after 401/403 or credential expiry", async () => {
    listApplicationsMock.mockRejectedValue(new ApiError(401, {
      error: { code: "TOKEN_EXPIRED", message: "The bearer expired." },
    }));
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(<ApplicationsPage />);
    });
    await connect(renderer);

    const rendered = JSON.stringify(renderer.toJSON());
    expect(rendered).toContain("凭证已失效或无权访问");
    expect(renderer.root.findByProps({ name: "bearer_token" })).toBeTruthy();
    expect(rendered).not.toContain(token);
    renderer.unmount();
  });

  it("shows an HTTP integrity failure as a whole-page fail-closed error", async () => {
    listApplicationsMock.mockRejectedValue(new ApiError(409, {
      error: { code: "CATALOG_INTEGRITY_ERROR", message: "record digest mismatch" },
    }));
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(<ApplicationsPage />);
    });
    await connect(renderer);

    const rendered = JSON.stringify(renderer.toJSON());
    expect(rendered).toContain("integrity_error / fail-closed");
    expect(rendered).toContain("record digest mismatch");
    expect(rendered).not.toContain("部分记录");
    renderer.unmount();
  });

  it("uses only the server-issued opaque cursor for the next page", async () => {
    listApplicationsMock
      .mockResolvedValueOnce(response("cur_01J0000000000001"))
      .mockResolvedValueOnce({ ...response(), items: [] });
    let renderer!: ReactTestRenderer;
    await act(async () => {
      renderer = create(<ApplicationsPage />);
    });
    await connect(renderer);
    await act(async () => {
      const next = renderer.root.findAllByType("button").find((button) => button.children.includes("下一页"));
      next?.props.onClick();
      await Promise.resolve();
    });

    expect(listApplicationsMock).toHaveBeenLastCalledWith(
      { workspaceId, projectId, bearerToken: token },
      { cursor: "cur_01J0000000000001", limit: 25 },
      expect.any(AbortSignal),
    );
    renderer.unmount();
  });
});
