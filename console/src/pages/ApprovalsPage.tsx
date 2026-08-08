import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { DataPending } from "../components/DataPending";
import { Digest } from "../components/Digest";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { formatTime, stateLabel, stateTone } from "../lib/format";
import { usePageData } from "../hooks/usePageData";
import type { ChangeSet } from "../lib/types";

/** 历史审批记录对应的终态集合 */
const HISTORY_STATES = new Set(["APPROVED", "REJECTED", "EXPIRED", "COMMITTED", "SUPERSEDED"]);

interface ApprovalsData {
  pending: ChangeSet[];
  history: ChangeSet[];
}

const fetchApprovals = async (): Promise<ApprovalsData> => {
  const [pending, all] = await Promise.all([
    api.listChangesets("AWAITING_APPROVAL"),
    api.listChangesets(),
  ]);
  return {
    pending: pending.items,
    history: all.items.filter((c) => HISTORY_STATES.has(c.state)),
  };
};

export function ApprovalsPage() {
  const { data, loading, error } = usePageData(fetchApprovals);

  return (
    <div className="space-y-6">
      <Card
        title={`待审批 WorkOrder（${data?.pending.length ?? "—"}）`}
        extra={<DataPending issue="workorder hash / 提请人无 REST 读端点（OPEN-ISSUES #6）" />}
        bodyClassName="p-4"
      >
        <AsyncBoundary
          loading={loading}
          error={error}
          dataEmpty={(data?.pending.length ?? 0) === 0}
          emptyHint="当前无待审批工单"
        >
          <div className="space-y-3">
            {data?.pending.map((cs) => (
              <WorkOrderCard key={cs.changeset_id} cs={cs} />
            ))}
          </div>
        </AsyncBoundary>
      </Card>

      <Card title="历史审批记录">
        <AsyncBoundary
          loading={loading}
          error={error}
          dataEmpty={(data?.history.length ?? 0) === 0}
          emptyHint="尚无已处理工单（审批写面在 release-admin MCP 侧）"
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">workorder_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">nonce</th>
                  <th className="pb-2 pr-4 font-medium">revision</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {data?.history.map((cs) => (
                  <tr key={cs.changeset_id}>
                    <td className="py-2.5 pr-4 font-mono text-xs text-gray-700">
                      {workorderId(cs.changeset_id)}
                    </td>
                    <td className="py-2.5 pr-4">
                      <StatusChip label={stateLabel(cs.state)} tone={stateTone(cs.state)} />
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-gray-500">
                      {String(cs.payload.nonce ?? "—")}
                    </td>
                    <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">{cs.revision}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>
    </div>
  );
}

function WorkOrderCard({ cs }: { cs: ChangeSet }) {
  const woId = workorderId(cs.changeset_id);
  const freezeAt = (cs.payload.expiry as string | undefined) ?? null;
  const gateRef = (cs.payload.gate_report_ref as string | undefined) ?? null;
  const nonce = (cs.payload.nonce as string | undefined) ?? null;

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50/50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-mono text-sm font-semibold text-gray-900">{woId}</h4>
            <StatusChip label={stateLabel(cs.state)} tone={stateTone(cs.state)} />
          </div>
          <p className="mt-1 text-xs text-gray-400">
            变更集 {cs.changeset_id} · revision {cs.revision}
          </p>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <dt className="text-xs font-medium text-gray-400">workorder hash</dt>
          <dd className="mt-0.5">
            <span className="inline-flex items-center gap-1.5">
              <Digest value={null} />
              <DataPending issue="workorders 表有 hash 但无 GET 端点（OPEN-ISSUES #6）" />
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-gray-400">freeze / 过期时间</dt>
          <dd className="mt-0.5 text-sm tabular-nums text-gray-700">{formatTime(freezeAt)}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-gray-400">提请人</dt>
          <dd className="mt-0.5 flex items-center gap-1.5">
            <span className="text-sm text-gray-500">—</span>
            <DataPending issue="author_agent 未并入 changeset aggregate（OPEN-ISSUES #6）" />
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-gray-400">证据摘要 · 门禁报告</dt>
          <dd className="mt-0.5 font-mono text-xs text-gray-700">{gateRef ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-gray-400">nonce（防重放）</dt>
          <dd className="mt-0.5 font-mono text-xs text-gray-700">{nonce ?? "—"}</dd>
        </div>
      </dl>

      {/* 批准 / 拒绝 —— 写面在 release-admin MCP，禁用并标注 */}
      <div className="mt-4 flex items-center gap-2 border-t border-gray-100 pt-3">
        <button
          type="button"
          disabled
          className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white opacity-40"
          title="审批写面由 release-admin MCP 完成（approval.request，hash 绑定 + nonce + expiry 校验）"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="m5 13 4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          批准
        </button>
        <button
          type="button"
          disabled
          className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white opacity-40"
          title="审批写面由 release-admin MCP 完成（approval.request，hash 绑定 + nonce + expiry 校验）"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M6 6l12 12M18 6 6 18" strokeLinecap="round" />
          </svg>
          拒绝
        </button>
        <span className="text-xs text-gray-400">经 release-admin MCP 审批</span>
      </div>
    </div>
  );
}

/** changeset_id 形如 cs_wo_xxx，去掉 cs_ 前缀即 workorder_id。 */
function workorderId(changesetId: string): string {
  return changesetId.startsWith("cs_") ? changesetId.slice(3) : changesetId;
}
