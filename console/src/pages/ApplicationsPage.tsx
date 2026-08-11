import { useCallback, useState, type FormEvent, type ReactNode } from "react";
import { AsyncBoundary, ErrorState } from "../components/AsyncState";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { usePageData } from "../hooks/usePageData";
import { api, ApiError, type CatalogReadCredential } from "../lib/api";
import type {
  ApplicationCatalogItem,
  ApplicationCatalogListResponse,
  CatalogEnvironmentRecord,
  DependencyEdgeRecord,
  SystemComponentRecord,
} from "../lib/types";

const PAGE_SIZE = 25;

interface CredentialGateProps {
  notice: string | null;
  onAccept: (credential: CatalogReadCredential) => void;
}

/**
 * R2 application catalog. The operator supplies a scoped public credential;
 * the bearer exists only in React memory and is never persisted by Console.
 */
export function ApplicationsPage() {
  const [credential, setCredential] = useState<CatalogReadCredential | null>(null);
  const [credentialNotice, setCredentialNotice] = useState<string | null>(null);

  const acceptCredential = useCallback((next: CatalogReadCredential) => {
    setCredentialNotice(null);
    setCredential(next);
  }, []);

  const clearCredential = useCallback((notice: string | null = null) => {
    setCredential(null);
    setCredentialNotice(notice);
  }, []);

  if (credential === null) {
    return <CredentialGate notice={credentialNotice} onAccept={acceptCredential} />;
  }

  return (
    <ApplicationCatalog
      credential={credential}
      onClearCredential={() => clearCredential(null)}
      onCredentialRejected={() => clearCredential("凭证已失效或无权访问，请重新输入 scoped bearer。")}
    />
  );
}

function CredentialGate({ notice, onAccept }: CredentialGateProps) {
  const [workspaceId, setWorkspaceId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [bearerToken, setBearerToken] = useState("");

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next = {
      workspaceId: workspaceId.trim(),
      projectId: projectId.trim(),
      bearerToken: bearerToken.trim(),
    };
    if (!next.workspaceId || !next.projectId || !next.bearerToken) return;
    onAccept(next);
  };

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div>
        <h1 className="text-base font-semibold text-gray-900">AI 应用目录（R2）</h1>
        <p className="mt-1 text-xs text-gray-500">
          使用具有 applications:read scope 的短期凭证读取指定 workspace / project。
        </p>
      </div>

      {notice && (
        <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {notice}
        </div>
      )}

      <Card title="连接公开只读目录">
        <form className="space-y-4" onSubmit={submit}>
          <label className="block text-xs font-medium text-gray-700">
            Workspace ID
            <input
              name="workspace_id"
              required
              autoComplete="off"
              spellCheck={false}
              value={workspaceId}
              onChange={(event) => setWorkspaceId(event.target.value)}
              placeholder="ws_..."
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </label>
          <label className="block text-xs font-medium text-gray-700">
            Project ID（仅筛选，授权由服务端重新验证）
            <input
              name="project_id"
              required
              autoComplete="off"
              spellCheck={false}
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              placeholder="proj_..."
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </label>
          <label className="block text-xs font-medium text-gray-700">
            Scoped bearer
            <input
              name="bearer_token"
              type="password"
              required
              autoComplete="off"
              spellCheck={false}
              value={bearerToken}
              onChange={(event) => setBearerToken(event.target.value)}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </label>
          <p className="text-xs text-gray-500">
            Console 只在当前页面内存中保留凭证；刷新、离开页面或清除凭证后需重新输入。
          </p>
          <button
            type="submit"
            disabled={!workspaceId.trim() || !projectId.trim() || !bearerToken.trim()}
            className="rounded-lg bg-brand-600 px-4 py-2 text-xs font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            读取目录
          </button>
        </form>
      </Card>
    </div>
  );
}

interface ApplicationCatalogProps {
  credential: CatalogReadCredential;
  onClearCredential: () => void;
  onCredentialRejected: () => void;
}

function ApplicationCatalog({
  credential,
  onClearCredential,
  onCredentialRejected,
}: ApplicationCatalogProps) {
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([]);
  const [integrityFailure, setIntegrityFailure] = useState<string | null>(null);

  const fetcher = useCallback(async (signal: AbortSignal) => {
    try {
      const response = await api.listApplications(credential, { cursor, limit: PAGE_SIZE }, signal);
      setIntegrityFailure(null);
      return response;
    } catch (error) {
      if (isCredentialFailure(error)) onCredentialRejected();
      if (isIntegrityFailure(error)) setIntegrityFailure(formatCatalogError(error));
      throw new Error(formatCatalogError(error));
    }
  }, [credential, cursor, onCredentialRejected]);

  const { data, loading, error, reload, refreshError } = usePageData<ApplicationCatalogListResponse>(
    fetcher,
    `applications:${credential.workspaceId}:${credential.projectId}:${cursor ?? "first"}`,
    30_000,
  );

  const nextPage = () => {
    if (!data?.next_cursor) return;
    setCursorHistory((history) => [...history, cursor]);
    setCursor(data.next_cursor);
  };

  const previousPage = () => {
    if (cursorHistory.length === 0) return;
    const previous = cursorHistory[cursorHistory.length - 1];
    setCursorHistory((history) => history.slice(0, -1));
    setCursor(previous);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-base font-semibold text-gray-900">AI 应用目录（R2）</h1>
          <p className="mt-1 font-mono text-xs text-gray-500">
            {credential.workspaceId} / {credential.projectId}
          </p>
        </div>
        <button
          type="button"
          onClick={onClearCredential}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          清除凭证
        </button>
      </div>

      {integrityFailure ? (
        <ErrorState
          message={integrityFailure}
          onRetry={() => {
            setIntegrityFailure(null);
            reload();
          }}
        />
      ) : (
        <AsyncBoundary
          loading={loading}
          error={error}
          dataEmpty={(data?.items.length ?? 0) === 0}
          emptyHint="此授权范围内尚无 AI Application；服务端已先执行 workspace/project visibility filter。"
          onRetry={reload}
          staleError={refreshError}
        >
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500">
              <span>
                本页 {data?.items.length ?? 0} 条 · audit {data?.audit_ref ?? "—"}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={previousPage}
                  disabled={cursorHistory.length === 0}
                  className="rounded border border-gray-300 px-2.5 py-1 disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  onClick={nextPage}
                  disabled={!data?.next_cursor}
                  className="rounded border border-gray-300 px-2.5 py-1 disabled:opacity-40"
                >
                  下一页
                </button>
              </div>
            </div>
            {data?.items.map((item) => (
              <ApplicationCard key={item.application.application_id} item={item} />
            ))}
          </div>
        </AsyncBoundary>
      )}
    </div>
  );
}

function ApplicationCard({ item }: { item: ApplicationCatalogItem }) {
  const { application } = item;
  return (
    <Card
      title={(
        <span className="flex flex-wrap items-center gap-2">
          <span>{application.display_name}</span>
          <StatusChip
            label={application.lifecycle_state}
            tone={application.lifecycle_state === "ACTIVE" ? "green" : application.lifecycle_state === "REGISTERED" ? "blue" : "gray"}
          />
        </span>
      )}
      extra={<span className="font-mono text-xs text-gray-400">rev {application.record_envelope.revision}</span>}
    >
      <dl className="grid gap-2 text-xs text-gray-600 sm:grid-cols-2 lg:grid-cols-4">
        <CatalogField label="application_id" value={application.application_id} mono />
        <CatalogField label="slug" value={application.slug} />
        <CatalogField label="criticality" value={application.criticality} />
        <CatalogField label="governance" value={application.governance_mode} />
      </dl>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <CatalogSection title={`Environments (${item.environments.length})`} empty="暂无 Environment">
          {item.environments.map((environment) => <EnvironmentLine key={environment.environment_id} value={environment} />)}
        </CatalogSection>
        <CatalogSection title={`System Components (${item.system_components.length})`} empty="暂无 SystemComponent">
          {item.system_components.map((component) => <ComponentLine key={component.component_id} value={component} />)}
        </CatalogSection>
        <CatalogSection title={`Dependency Edges (${item.dependency_edges.length})`} empty="暂无 DependencyEdge">
          {item.dependency_edges.map((edge) => <EdgeLine key={edge.edge_id} value={edge} />)}
        </CatalogSection>
      </div>
    </Card>
  );
}

function CatalogField({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-gray-400">{label}</dt>
      <dd className={`mt-0.5 break-all text-gray-700 ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

function CatalogSection({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string;
  children: ReactNode;
}) {
  const values = Array.isArray(children) ? children : [children];
  return (
    <section className="rounded-lg border border-gray-100 bg-gray-50/60 p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</h3>
      {values.length === 0 ? (
        <p className="mt-3 text-xs text-gray-400">{empty}</p>
      ) : (
        <div className="mt-2 divide-y divide-gray-200/70">{children}</div>
      )}
    </section>
  );
}

function EnvironmentLine({ value }: { value: CatalogEnvironmentRecord }) {
  return (
    <div className="flex items-center justify-between gap-2 py-2 text-xs">
      <div>
        <div className="font-medium text-gray-700">{value.logical_name}</div>
        <div className="font-mono text-gray-400">{value.environment_id}</div>
      </div>
      <StatusChip label={value.lifecycle_state} tone={value.lifecycle_state === "ACTIVE" ? "green" : "gray"} />
    </div>
  );
}

function ComponentLine({ value }: { value: SystemComponentRecord }) {
  return (
    <div className="flex items-center justify-between gap-2 py-2 text-xs">
      <div>
        <div className="font-medium text-gray-700">{value.logical_name}</div>
        <div className="text-gray-400">{value.component_kind} · {value.component_id}</div>
      </div>
      <StatusChip label={value.lifecycle_state} tone={value.lifecycle_state === "ACTIVE" ? "green" : "gray"} />
    </div>
  );
}

function EdgeLine({ value }: { value: DependencyEdgeRecord }) {
  return (
    <div className="py-2 text-xs">
      <div className="font-medium text-gray-700">{value.relation}{value.required ? " · required" : ""}</div>
      <div className="break-all font-mono text-gray-400">
        {value.from_component_id} → {value.to_component_id}
      </div>
    </div>
  );
}

function isCredentialFailure(error: unknown): boolean {
  return error instanceof ApiError
    && (error.status === 401 || error.status === 403 || ["TOKEN_INVALID", "TOKEN_EXPIRED"].includes(error.code));
}

function isIntegrityFailure(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  const code = error.code.toUpperCase();
  return code === "INVALID_RESPONSE"
    || code === "INTERNAL_ERROR"
    || code.includes("INTEGRITY")
    || code.includes("DIGEST")
    || code.includes("AUTHORITY_RECEIPT");
}

function formatCatalogError(error: unknown): string {
  if (isIntegrityFailure(error)) {
    const apiError = error as ApiError;
    return `integrity_error / fail-closed (${apiError.code}): ${apiError.message}`;
  }
  if (error instanceof ApiError) {
    if (error.status === 0) return `catalog_unavailable / UNKNOWN: ${error.message}`;
    return `${error.code} / fail-closed: ${error.message}`;
  }
  return `catalog_unavailable / UNKNOWN: ${error instanceof Error ? error.message : "unknown error"}`;
}
