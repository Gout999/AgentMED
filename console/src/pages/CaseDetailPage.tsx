import { Link, useParams } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { usePageData, type PageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { formatTime, stateLabel, stateTone } from "../lib/format";
import type { CaseDetail, CaseEventsView, CaseV5Readiness, EvidenceResponse } from "../lib/types";

const CASE_PATH = [
  "RECEIVED",
  "OPEN",
  "DISPATCHED",
  "ATTRIBUTING",
  "AWAITING_FIX",
  "AWAITING_APPROVAL",
  "RELEASING",
  "NOTIFYING",
  "CLOSED",
];

export function CaseDetailPage() {
  const { id = "" } = useParams();
  const detail = usePageData((signal) => api.getCase(id, signal), `case:${id}:detail`);
  const events = usePageData((signal) => api.getCaseEvents(id, signal), `case:${id}:events`);
  const evidence = usePageData((signal) => api.listEvidence(id, signal), `case:${id}:evidence`);
  const governance = usePageData((signal) => api.getCaseV5Readiness(id, signal), `case:${id}:v5`);

  return (
    <div className="space-y-5">
      <BackLink to="/cases" label="案例列表" />
      <AsyncBoundary
        loading={detail.loading}
        error={detail.error}
        dataEmpty={!detail.data}
        emptyHint="case 不存在"
        onRetry={detail.reload}
        staleError={detail.refreshError}
      >
        {detail.data ? (
          <CaseDetailBody data={detail.data} events={events} evidence={evidence} governance={governance} />
        ) : null}
      </AsyncBoundary>
    </div>
  );
}

function CaseDetailBody({
  data,
  events,
  evidence,
  governance,
}: {
  data: CaseDetail;
  events: PageData<CaseEventsView>;
  evidence: PageData<EvidenceResponse>;
  governance: PageData<CaseV5Readiness>;
}) {
  return (
    <>
      <Card>
        {data.payload.integrity_status === "integrity_error" ? (
          <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
            QualityCase 未通过完整性重验：{String(data.payload.integrity_error ?? "UNKNOWN")}。状态与正文均按 UNKNOWN 展示。
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-mono text-base font-semibold text-gray-900">{data.case_id}</h2>
              <StatusChip
                label={stateLabel("case", data.state)}
                tone={stateTone("case", data.state)}
              />
            </div>
            <p className="mt-1 text-xs text-gray-400">
              revision {data.revision} · {data.event_count} 个事件 · 更新于 {formatTime(data.updated_at)}
            </p>
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(data.payload).map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-xs font-medium text-gray-400">{key}</dt>
              <dd className="mt-0.5 break-all text-sm text-gray-700">{displayValue(value)}</dd>
            </div>
          ))}
          {Object.keys(data.payload).length === 0 ? (
            <p className="text-sm text-gray-400">无 payload 字段</p>
          ) : null}
        </dl>
      </Card>

      <Card title="事件时间线" bodyClassName="p-4">
        <div className="mb-5">
          <p className="mb-2 text-xs font-medium text-gray-400">契约主路径 · 当前位置高亮</p>
          <ol className="flex flex-wrap items-center gap-y-2">
            {CASE_PATH.map((state, index) => {
              const current = state === data.state;
              const passed = CASE_PATH.indexOf(data.state) > index;
              return (
                <li key={state} className="flex items-center">
                  {index > 0 ? (
                    <span className={`mx-1 h-px w-4 ${passed ? "bg-brand-300" : "bg-gray-200"}`} aria-hidden />
                  ) : null}
                  <span
                    className={`rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${
                      current
                        ? "bg-brand-600 text-white ring-brand-600"
                        : passed
                          ? "bg-brand-50 text-brand-700 ring-brand-200"
                          : "bg-gray-50 text-gray-400 ring-gray-200"
                    }`}
                  >
                    {stateLabel("case", state)}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>

        <AsyncBoundary
          loading={events.loading}
          error={events.error}
          dataEmpty={(events.data?.items.length ?? 0) === 0}
          emptyHint="该 Case 尚无事件"
          onRetry={events.reload}
          staleError={events.refreshError}
        >
          <ol className="space-y-3" aria-label="Case 事件时间线">
            {events.data?.items.map((event) => (
              <li key={event.event_id} className="rounded-lg border border-gray-100 bg-gray-50/50 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-gray-200 px-1.5 py-0.5 font-mono text-[10px] text-gray-600">
                      seq {event.seq}
                    </span>
                    <code className="text-xs font-semibold text-gray-800">{event.event_type}</code>
                  </div>
                  <time className="text-xs tabular-nums text-gray-400">{formatTime(event.occurred_at)}</time>
                </div>
                <p className="mt-1 text-xs text-gray-500">actor {event.actor}</p>
                <p className="mt-1 break-all font-mono text-[10px] text-gray-400">
                  event {event.event_id} · caused by {event.causation_id}
                  {event.trace_id ? ` · trace ${event.trace_id}` : " · trace UNKNOWN"}
                </p>
                {Object.keys(event.payload).length > 0 ? (
                  <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded bg-white p-2 text-[10px] leading-relaxed text-gray-600">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                ) : null}
              </li>
            ))}
          </ol>
        </AsyncBoundary>
      </Card>

      <CaseV5GovernanceCard governance={governance} />

      <Card title="证据状态" bodyClassName="p-4">
        <AsyncBoundary
          loading={evidence.loading}
          error={evidence.error}
          dataEmpty={(evidence.data?.items.length ?? 0) === 0}
          emptyHint="该 Case 尚无已记录的证据引用"
          onRetry={evidence.reload}
          staleError={evidence.refreshError}
        >
          {evidence.data?.warning ? (
            <p className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Artifact 内容存储未接入：这里只证明引用或 digest 已写入权威事件；内容验证状态为 UNKNOWN。
            </p>
          ) : null}
          <ul className="space-y-2">
            {evidence.data?.items.map((item) => (
              <li key={item.evidence_id} className="rounded-lg border border-gray-100 px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-gray-700">{item.kind}</span>
                  <StatusChip
                    label={item.binding_status}
                    tone={item.binding_status === "BOUND" ? "green" : item.binding_status === "UNKNOWN" ? "red" : "blue"}
                  />
                  {item.integrity_status !== "recorded" ? (
                    <StatusChip
                      label={`${item.integrity_status}: ${item.integrity_error ?? "UNKNOWN"}`}
                      tone="red"
                    />
                  ) : null}
                  <StatusChip label="artifact UNKNOWN" tone="amber" />
                </div>
                <p className="mt-1 break-all font-mono text-[10px] text-gray-500">
                  {item.reference ?? item.digest ?? "UNKNOWN"}
                </p>
                <p className="mt-1 text-[10px] text-gray-400">
                  {item.source_type}:{item.source_id} · {formatTime(item.recorded_at)}
                </p>
              </li>
            ))}
          </ul>
        </AsyncBoundary>
      </Card>
    </>
  );
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined) return "—";
  return JSON.stringify(value) ?? String(value);
}

function CaseV5GovernanceCard({
  governance,
}: {
  governance: PageData<CaseV5Readiness>;
}) {
  const readiness = governance.data?.case_readiness;
  const binding = governance.data?.application_binding;
  const hasIntegrityError = governance.data
    ? governance.data.case_integrity_status === "integrity_error"
      || governance.data.binding_integrity_status === "integrity_error"
      || governance.data.acceptance_integrity_status === "integrity_error"
      || governance.data.issue_snapshot_integrity_status === "integrity_error"
    : false;
  const bindingUntrusted = governance.data?.binding_integrity_status !== "verified";
  return (
    <Card title="V5 治理 · 系统绑定 / acceptance readiness" bodyClassName="p-4">
      <AsyncBoundary
        loading={governance.loading}
        error={governance.error}
        dataEmpty={!governance.data}
        emptyHint="该 Case 无 V5 治理投影"
        onRetry={governance.reload}
        staleError={governance.refreshError}
      >
        <div className="space-y-4">
          {hasIntegrityError ? (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
              治理投影存在 integrity_error；相关记录已隐藏，readiness 强制为 UNKNOWN。
              {governance.data?.case_integrity_error ? ` case: ${governance.data.case_integrity_error}` : ""}
              {governance.data?.binding_integrity_error ? ` binding: ${governance.data.binding_integrity_error}` : ""}
              {governance.data?.acceptance_integrity_error ? ` acceptance: ${governance.data.acceptance_integrity_error}` : ""}
              {governance.data?.issue_snapshot_integrity_error ? ` issue: ${governance.data.issue_snapshot_integrity_error}` : ""}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-gray-400">acceptance readiness</span>
            <StatusChip
              label={readiness === "READY" ? "READY" : readiness === "NEEDS_ACCEPTANCE_CRITERIA" ? "NEEDS_ACCEPTANCE_CRITERIA" : "UNKNOWN"}
              tone={readiness === "READY" && !hasIntegrityError ? "green" : readiness === "NEEDS_ACCEPTANCE_CRITERIA" ? "amber" : "red"}
            />
            {governance.data ? (
              <span className="text-xs tabular-nums text-gray-400">
                {governance.data.acceptance_proposal_count} 个可信草稿 · {governance.data.confirmed_acceptance_count} 个可信已确认 · {governance.data.executable_acceptance_count} 个可执行
              </span>
            ) : null}
          </div>
          {readiness === "NEEDS_ACCEPTANCE_CRITERIA" ? (
            <p className="text-xs text-gray-500">
              {(governance.data?.confirmed_acceptance_count ?? 0) === 0
                ? "该 Case 尚无可信的已确认验收标准；确认前 Gate 不可启动。"
                : "验收标准已确认，但 ResolutionContract 仍待 V5-4 materialization（或验收字段仍不完整），因此尚不可执行，Gate 不可启动。"}
            </p>
          ) : null}

          <div>
            <p className="mb-1 text-xs font-medium text-gray-400">application binding</p>
            {binding ? (
              <ul className="space-y-1 text-xs text-gray-700">
                <li className="flex flex-wrap gap-2">
                  <span className="font-mono">{binding.application_id}</span>
                  <span className="text-gray-400">/</span>
                  <span className="font-mono">{binding.environment_id}</span>
                  <StatusChip label={bindingUntrusted ? governance.data?.binding_integrity_status ?? "UNKNOWN" : "verified"} tone={bindingUntrusted ? "red" : "green"} />
                </li>
                <li className="break-all font-mono text-[10px] text-gray-400">
                  exact case · rev {binding.exact_case_binding.case_revision} · {binding.exact_case_binding.case_digest}
                </li>
                <li className="text-[10px] text-gray-400">
                  声明版本：{JSON.stringify(binding.declared_system_version_set_binding_or_unknown)}
                </li>
              </ul>
            ) : (
              <p className="text-xs text-gray-400">
                {governance.data?.binding_integrity_status === "integrity_error" ? "integrity_error · binding 已隐藏" : "UNKNOWN · 尚未绑定到 AI 应用"}
              </p>
            )}
          </div>

          {governance.data?.issue_snapshot ? (
            <div>
              <p className="mb-1 text-xs font-medium text-gray-400">issue 快照（只读 data）</p>
              <ul className="space-y-1 text-xs text-gray-700">
                <li className="truncate">
                  {governance.data.issue_snapshot.source_url ? (
                    <a className="text-brand-600 hover:underline" href={governance.data.issue_snapshot.source_url} target="_blank" rel="noreferrer">
                      {governance.data.issue_snapshot.external_repo
                        ? `${governance.data.issue_snapshot.external_repo}#${governance.data.issue_snapshot.external_issue_number}`
                        : governance.data.issue_snapshot.source_url}
                    </a>
                  ) : (
                    <span>manual source</span>
                  )}
                  {" · "}{governance.data.issue_snapshot.title ?? "UNTITLED"}
                </li>
                <li className="flex flex-wrap gap-2 text-[10px] text-gray-400">
                  {governance.data.issue_snapshot.edited_flag ? <StatusChip label="edited" tone="amber" /> : null}
                  {governance.data.issue_snapshot.deleted_flag ? <StatusChip label="deleted" tone="red" /> : null}
                  {governance.data.issue_snapshot.instruction_markers_detected ? (
                    <StatusChip label="injection markers (data only)" tone="amber" />
                  ) : null}
                </li>
              </ul>
            </div>
          ) : governance.data?.issue_snapshot_integrity_status === "integrity_error" ? (
            <p className="text-xs text-red-700">issue 快照 integrity_error，未作为可信数据展示。</p>
          ) : null}

          <div>
            <p className="mb-1 text-xs font-medium text-gray-400">missing evidence</p>
            {(governance.data?.missing_evidence.length ?? 0) > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {governance.data?.missing_evidence.map((field) => (
                  <li key={field} className="rounded bg-amber-50 px-1.5 py-0.5 font-mono text-[10px] text-amber-800">
                    {field}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-gray-400">无缺项（或证据完整性为 UNKNOWN）</p>
            )}
          </div>
        </div>
      </AsyncBoundary>
    </Card>
  );
}

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-brand-600">
      <span aria-hidden>←</span>
      {label}
    </Link>
  );
}
