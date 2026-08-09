import type { ReactNode } from "react";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { Digest } from "../components/Digest";
import { StatusChip } from "../components/StatusChip";
import { usePageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { formatTime, stateLabel, stateTone } from "../lib/format";
import type { WorkOrderView } from "../lib/types";

export function ApprovalsPage() {
  const view = usePageData((signal) => api.listWorkOrders(signal));
  const items = view.data?.items ?? [];
  const pending = items.filter((item) => item.state === "AWAITING_APPROVAL");
  // Keep every authoritative WorkOrder visible. Pre-approval, terminal, and
  // UNKNOWN lifecycle values must not disappear merely because the UI does
  // not recognize a newer backend state.
  const history = items.filter((item) => item.state !== "AWAITING_APPROVAL");

  return (
    <div className="space-y-6">
      <Card title={`待审批 WorkOrder（${view.loading ? "—" : pending.length}）`} bodyClassName="p-4">
        <AsyncBoundary
          loading={view.loading}
          error={view.error}
          dataEmpty={pending.length === 0}
          emptyHint="当前无待审批 WorkOrder"
          onRetry={view.reload}
          staleError={view.refreshError}
        >
          <div className="space-y-3">
            {pending.map((workorder) => (
              <WorkOrderCard key={workorder.workorder_id} workorder={workorder} />
            ))}
          </div>
        </AsyncBoundary>
      </Card>

      <Card title="其他及历史 WorkOrder">
        <AsyncBoundary
          loading={view.loading}
          error={view.error}
          dataEmpty={history.length === 0}
          emptyHint="尚无已处理 WorkOrder"
          onRetry={view.reload}
          staleError={view.refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">workorder_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">hash</th>
                  <th className="pb-2 pr-4 font-medium">nonce</th>
                  <th className="pb-2 pr-4 font-medium">冻结至</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {history.map((workorder) => (
                  <tr key={workorder.workorder_id}>
                    <td className="py-2.5 pr-4 font-mono text-xs text-gray-700">{workorder.workorder_id}</td>
                    <td className="py-2.5 pr-4">
                      <StatusChip
                        label={stateLabel("changeset", workorder.state)}
                        tone={stateTone("changeset", workorder.state)}
                      />
                    </td>
                    <td className="py-2.5 pr-4"><Digest value={workorder.hash} /></td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-gray-500">{workorder.nonce ?? "UNKNOWN"}</td>
                    <td className="py-2.5 pr-4 text-xs text-gray-500">{formatTime(workorder.freeze_at)}</td>
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

function WorkOrderCard({ workorder }: { workorder: WorkOrderView }) {
  const gateUri = workorder.gate_report_ref?.uri ?? null;
  const gateDigest = workorder.gate_report_ref?.digest ?? null;
  return (
    <article className="rounded-xl border border-gray-200 bg-gray-50/50 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="font-mono text-sm font-semibold text-gray-900">{workorder.workorder_id}</h4>
            <StatusChip
              label={stateLabel("changeset", workorder.state)}
              tone={stateTone("changeset", workorder.state)}
            />
            {workorder.projection_warning ? <StatusChip label="projection UNKNOWN" tone="red" /> : null}
            <StatusChip
              label={workorder.workorder_integrity_status === "verified" ? "WorkOrder verified" : "WorkOrder UNKNOWN"}
              tone={workorder.workorder_integrity_status === "verified" ? "green" : "red"}
            />
            <StatusChip
              label={workorder.gate_integrity_status === "verified" ? "Gate binding verified" : "Gate binding UNKNOWN"}
              tone={workorder.gate_integrity_status === "verified" ? "green" : "red"}
            />
          </div>
          <p className="mt-1 text-xs text-gray-400">
            {workorder.changeset_id} · case {workorder.case_id ?? "UNKNOWN"} · {workorder.channel}
          </p>
        </div>
      </div>

      {workorder.projection_warning ? (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          ChangeSet 投影与不可变 WorkOrder 不一致：{workorder.projection_warning}。页面仍显示 WorkOrder 权威值。
        </p>
      ) : null}

      {workorder.workorder_integrity_status !== "verified" ? (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          WorkOrder 内容 hash 无法通过完整性验证：
          {workorder.workorder_integrity_error ?? "UNKNOWN"}。可变投影字段与完整 payload 已从读面隐藏。
        </p>
      ) : null}

      {workorder.gate_integrity_status !== "verified" ? (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          GateReport 与 WorkOrder 绑定无法通过完整性验证：
          {workorder.gate_integrity_error ?? "UNKNOWN"}。不得据此审批或发布。
        </p>
      ) : null}

      <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="workorder hash"><Digest value={workorder.hash} copyable /></Field>
        <Field label="target VersionSet"><Digest value={workorder.target_versionset_digest} /></Field>
        <Field label="target VersionSet id"><code className="break-all font-mono text-xs">{workorder.gate_target_versionset_id ?? "UNKNOWN"}</code></Field>
        <Field label="target revision"><span>{workorder.gate_target_revision ?? "UNKNOWN"}</span></Field>
        <Field label="freeze / expiry"><span>{formatTime(workorder.freeze_at)}</span></Field>
        <Field label="提请人"><span>{workorder.requester ?? "UNKNOWN"}</span></Field>
        <Field label="nonce"><code className="break-all font-mono text-xs">{workorder.nonce ?? "UNKNOWN"}</code></Field>
        <Field label="创建时间"><span>{formatTime(workorder.created_at)}</span></Field>
        <Field label="GateReport URI"><code className="break-all font-mono text-xs">{gateUri ?? "UNKNOWN"}</code></Field>
        <Field label="GateReport digest"><Digest value={gateDigest} /></Field>
        <Field label="Gate binding digest"><Digest value={workorder.gate_binding_digest} /></Field>
      </dl>

      <p className="mt-4 border-t border-gray-100 pt-3 text-xs text-gray-400">
        本页只读。审批写面必须由授权主体对这个 exact hash、nonce、expiry 与上方已验证的
        target revision 操作；任何 UNKNOWN 都必须 fail closed。
      </p>
    </article>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-gray-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-gray-700">{children}</dd>
    </div>
  );
}
