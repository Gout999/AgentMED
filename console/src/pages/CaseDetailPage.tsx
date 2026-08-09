import { Link, useParams } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { usePageData, type PageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { formatTime, stateLabel, stateTone } from "../lib/format";
import type { CaseDetail, CaseEventsView, EvidenceResponse } from "../lib/types";

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
        {detail.data ? <CaseDetailBody data={detail.data} events={events} evidence={evidence} /> : null}
      </AsyncBoundary>
    </div>
  );
}

function CaseDetailBody({
  data,
  events,
  evidence,
}: {
  data: CaseDetail;
  events: PageData<CaseEventsView>;
  evidence: PageData<EvidenceResponse>;
}) {
  return (
    <>
      <Card>
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

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-brand-600">
      <span aria-hidden>←</span>
      {label}
    </Link>
  );
}
