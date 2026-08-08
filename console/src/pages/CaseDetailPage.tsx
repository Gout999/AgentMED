import { Link, useParams } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { DataPending } from "../components/DataPending";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { formatTime, stateLabel, stateTone } from "../lib/format";
import { usePageData } from "../hooks/usePageData";
import type { CaseDetail } from "../lib/types";

/** case 状态机主路径（契约：contracts/events/state-machines.yaml），用于展示当前案件位置。 */
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

/** payload 中视为「证据引用」的字段（evidence/bundle/digest 关键字或 *_ref 结尾，排除 app_ref 元数据）。 */
const isEvidenceKey = (k: string) =>
  k !== "app_ref" && /evidence|bundle|digest|_ref$/i.test(k) && typeof k === "string";

export function CaseDetailPage() {
  const { id = "" } = useParams();
  const { data, loading, error } = usePageData(() => api.getCase(id));

  return (
    <div className="space-y-5">
      <BackLink to="/cases" label="案例列表" />

      <AsyncBoundary loading={loading} error={error} dataEmpty={!data} emptyHint="case 不存在或已删除">
        {data && <CaseDetailBody data={data} />}
      </AsyncBoundary>
    </div>
  );
}

function CaseDetailBody({ data }: { data: CaseDetail }) {
  const evidenceRefs = Object.entries(data.payload)
    .filter(([k]) => isEvidenceKey(k))
    .map(([k, v]) => ({ key: k, value: String(v ?? "") }));

  return (
    <>
      {/* 头部：case_id + 状态 + 元信息 */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-mono text-base font-semibold text-gray-900">{data.case_id}</h2>
              <StatusChip label={stateLabel(data.state)} tone={stateTone(data.state)} />
            </div>
            <p className="mt-1 text-xs text-gray-400">
              revision {data.revision} · {data.event_count} 个事件 · 更新于 {formatTime(data.updated_at)}
            </p>
          </div>
        </div>

        {/* payload 基础信息 */}
        <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(data.payload).map(([k, v]) => (
            <div key={k} className="min-w-0">
              <dt className="text-xs font-medium text-gray-400">{k}</dt>
              <dd className="mt-0.5 break-all text-sm text-gray-700">
                {typeof v === "string" ? v : JSON.stringify(v)}
              </dd>
            </div>
          ))}
          {Object.keys(data.payload).length === 0 && (
            <p className="text-sm text-gray-400">无 payload 字段</p>
          )}
        </dl>
      </Card>

      {/* 时间线（事件流 + Agent 建议记录）—— 数据待接入 */}
      <Card
        title="时间线"
        extra={<DataPending issue="control-plane 未暴露 events 读端点（OPEN-ISSUES #3）" />}
        bodyClassName="p-4"
      >
        <p className="mb-4 max-w-2xl text-xs leading-relaxed text-gray-500">
          事件流与 Agent 建议记录按时间戳排序展示。当前 control-plane 未提供
          <code className="mx-1 rounded bg-gray-100 px-1 py-0.5 font-mono text-[11px]">GET /v1/cases/{"{id}"}/events</code>
          端点（事件存于 PG events 表，仅 MCP <code className="mx-1 rounded bg-gray-100 px-1 py-0.5 font-mono text-[11px]">case.timeline</code> 可达），
          该区块待接入后按 ts 渲染。
        </p>

        {/* 契约状态机主路径 —— 展示当前案件在规范流程中的位置（契约数据，非运行事件） */}
        <div className="mb-5">
          <p className="mb-2 text-xs font-medium text-gray-400">状态机主路径（契约）· 当前位置高亮</p>
          <ol className="flex flex-wrap items-center gap-y-2">
            {CASE_PATH.map((s, i) => {
              const isCurrent = s === data.state;
              const passed = CASE_PATH.indexOf(data.state) > i;
              return (
                <li key={s} className="flex items-center">
                  {i > 0 && (
                    <span className={`mx-1 h-px w-4 ${passed ? "bg-brand-300" : "bg-gray-200"}`} aria-hidden />
                  )}
                  <span
                    className={`rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${
                      isCurrent
                        ? "bg-brand-600 text-white ring-brand-600"
                        : passed
                          ? "bg-brand-50 text-brand-700 ring-brand-200"
                          : "bg-gray-50 text-gray-400 ring-gray-200"
                    }`}
                  >
                    {stateLabel(s)}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>

        <div className="rounded-lg border border-dashed border-gray-200 bg-gray-50/60 p-4 text-xs text-gray-400">
          事件流水（complaint.received → case.opened → case.dispatched → …）待接入后在此按时间倒序渲染，
          状态迁移（如 OPEN → DISPATCHED）会高亮展示。
        </div>
      </Card>

      {/* 证据引用面板 */}
      <Card
        title="证据引用"
        extra={<DataPending issue="case payload 未投影证据 ref（OPEN-ISSUES #4）" />}
        bodyClassName="p-4"
      >
        {evidenceRefs.length > 0 ? (
          <ul className="space-y-2">
            {evidenceRefs.map((r) => (
              <li key={r.key} className="flex items-center gap-2 text-xs">
                <span className="text-gray-500">{r.key}</span>
                <code className="font-mono text-brand-700">{r.value}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-gray-400">
            当前 case payload 无证据引用字段（text_ref / gate_report_ref / evidence_bundle_ref 等随案件推进
            到归因、审批阶段后才会出现）。
          </p>
        )}
      </Card>
    </>
  );
}

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link to={to} className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-brand-600">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="m15 18-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {label}
    </Link>
  );
}
