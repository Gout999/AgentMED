import { Link } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { Digest } from "../components/Digest";
import { StatusChip } from "../components/StatusChip";
import { usePageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { formatTime, stateLabel, stateTone } from "../lib/format";

export function OperationsPage() {
  const releases = usePageData((signal) => api.listReleases(undefined, signal));
  const notifications = usePageData((signal) => api.listNotifications(signal));
  const evidence = usePageData((signal) => api.listEvidence(undefined, signal));

  return (
    <div className="space-y-6">
      <Card title="Release Controller 状态" bodyClassName="p-4">
        <AsyncBoundary
          loading={releases.loading}
          error={releases.error}
          dataEmpty={(releases.data?.items.length ?? 0) === 0}
          emptyHint="权威 Release 聚合为空"
          onRetry={releases.reload}
          staleError={releases.refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">release_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">revision</th>
                  <th className="pb-2 pr-4 font-medium">解释</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {releases.data?.items.map((release) => (
                  <tr key={release.release_id}>
                    <td className="py-2.5 pr-4 font-mono text-xs text-gray-800">{release.release_id}</td>
                    <td className="py-2.5 pr-4">
                      <StatusChip
                        label={stateLabel("release", release.state)}
                        tone={stateTone("release", release.state)}
                      />
                    </td>
                    <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-600">{release.revision}</td>
                    <td className="py-2.5 pr-4 text-xs text-gray-500">
                      {release.state === "UNKNOWN" ? "写结果不可考；禁止盲重试，必须 reconcile" : "权威 event-sourced 状态"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>

      <Card title="Notification 与原渠道回复" bodyClassName="p-4">
        <AsyncBoundary
          loading={notifications.loading}
          error={notifications.error}
          dataEmpty={(notifications.data?.items.length ?? 0) === 0}
          emptyHint="权威 Notification 聚合为空"
          onRetry={notifications.reload}
          staleError={notifications.refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">notification_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">Case / channel</th>
                  <th className="pb-2 pr-4 font-medium">provider receipt</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {notifications.data?.items.map((notification) => {
                  const caseId = typeof notification.payload.case_id === "string" ? notification.payload.case_id : null;
                  const channel = typeof notification.payload.channel === "string" ? notification.payload.channel : "UNKNOWN";
                  const receiptDigest = typeof notification.payload.receipt_digest === "string" ? notification.payload.receipt_digest : null;
                  return (
                    <tr key={notification.notification_id}>
                      <td className="py-2.5 pr-4 font-mono text-xs text-gray-800">{notification.notification_id}</td>
                      <td className="py-2.5 pr-4">
                        <StatusChip
                          label={stateLabel("notification", notification.state)}
                          tone={stateTone("notification", notification.state)}
                        />
                      </td>
                      <td className="py-2.5 pr-4 text-xs text-gray-600">
                        {caseId ? <Link to={`/cases/${caseId}`} className="font-mono text-brand-600 hover:underline">{caseId}</Link> : "UNKNOWN"}
                        <span className="ml-2 text-gray-400">{channel}</span>
                      </td>
                      <td className="py-2.5 pr-4">
                        {receiptDigest ? <Digest value={receiptDigest} /> : <StatusChip label="UNKNOWN / 未 ACK" tone="amber" />}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>

      <Card title="Evidence bindings" bodyClassName="p-4">
        <AsyncBoundary
          loading={evidence.loading}
          error={evidence.error}
          dataEmpty={(evidence.data?.items.length ?? 0) === 0}
          emptyHint="尚无已记录 evidence 引用或 digest"
          onRetry={evidence.reload}
          staleError={evidence.refreshError}
        >
          {evidence.data?.warning ? (
            <p className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Artifact store 未接入；这里只显示权威控制面记录。来源完整性失败或 digest 非法时绑定会明确降为 UNKNOWN。
            </p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[880px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">来源</th>
                  <th className="pb-2 pr-4 font-medium">kind</th>
                  <th className="pb-2 pr-4 font-medium">binding</th>
                  <th className="pb-2 pr-4 font-medium">integrity</th>
                  <th className="pb-2 pr-4 font-medium">ref / digest</th>
                  <th className="pb-2 pr-4 font-medium">artifact</th>
                  <th className="pb-2 pr-4 font-medium">记录时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {evidence.data?.items.map((item) => (
                  <tr key={item.evidence_id}>
                    <td className="py-2.5 pr-4">
                      <p className="text-xs text-gray-700">{item.source_type}</p>
                      <p className="font-mono text-[10px] text-gray-400">{item.source_id}</p>
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs text-gray-700">{item.kind}</td>
                    <td className="py-2.5 pr-4">
                      <StatusChip
                        label={item.binding_status}
                        tone={item.binding_status === "BOUND" ? "green" : item.binding_status === "UNKNOWN" ? "red" : "blue"}
                      />
                    </td>
                    <td className="py-2.5 pr-4">
                      <StatusChip
                        label={item.integrity_status === "recorded" ? "recorded" : `${item.integrity_status}: ${item.integrity_error ?? "UNKNOWN"}`}
                        tone={item.integrity_status === "recorded" ? "blue" : "red"}
                      />
                    </td>
                    <td className="max-w-[280px] break-all py-2.5 pr-4 font-mono text-[10px] text-gray-600">
                      {item.reference ?? item.digest ?? "UNKNOWN"}
                    </td>
                    <td className="py-2.5 pr-4"><StatusChip label={item.artifact_status} tone="amber" /></td>
                    <td className="py-2.5 pr-4 text-xs text-gray-500">{formatTime(item.recorded_at)}</td>
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
