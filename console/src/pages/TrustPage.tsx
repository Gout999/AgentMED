import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { Digest } from "../components/Digest";
import { StatusChip } from "../components/StatusChip";
import { usePageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { AUTONOMY_META, type StatusTone } from "../lib/constants";
import { formatTime } from "../lib/format";

export function TrustPage() {
  const gates = usePageData((signal) => api.listGates(signal));
  const ledger = usePageData((signal) => api.listTrustLedger(signal));
  const denials = usePageData((signal) => api.listTrustDenials(signal));

  return (
    <div className="space-y-6">
      <Card title="门禁报告" bodyClassName="p-4">
        <p className="mb-4 max-w-3xl text-xs leading-relaxed text-gray-500">
          规则轨、裁判轨、确定性 contract/replay 与 live-provider E2E 分开显示。任何 error、skipped、
          integrity_error 或未知状态都不会在 Console 被改写成 PASS。
        </p>
        <AsyncBoundary
          loading={gates.loading}
          error={gates.error}
          dataEmpty={(gates.data?.items.length ?? 0) === 0}
          emptyHint="权威 gate_reports 表为空"
          onRetry={gates.reload}
          staleError={gates.refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">报告 / WorkOrder</th>
                  <th className="pb-2 pr-4 font-medium">规则轨</th>
                  <th className="pb-2 pr-4 font-medium">裁判轨</th>
                  <th className="pb-2 pr-4 font-medium">contract/replay</th>
                  <th className="pb-2 pr-4 font-medium">live E2E</th>
                  <th className="pb-2 pr-4 font-medium">总结论</th>
                  <th className="pb-2 pr-4 font-medium">证据</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {gates.data?.items.map((gate) => (
                  <tr key={gate.eval_id}>
                    <td className="py-2.5 pr-4">
                      <p className="font-mono text-xs text-gray-800">{gate.report_id}</p>
                      <p className="mt-0.5 font-mono text-[10px] text-gray-400">{gate.workorder_id}</p>
                    </td>
                    {[gate.rule_track, gate.judge_track, gate.deterministic_tests, gate.live_provider_e2e].map((status, index) => (
                      <td key={index} className="py-2.5 pr-4">
                        <StatusChip label={status || "UNKNOWN"} tone={gateTone(status)} />
                      </td>
                    ))}
                    <td className="py-2.5 pr-4">
                      <StatusChip
                        label={
                          gate.binding_status === "VERIFIED"
                            ? gate.verdict || "UNKNOWN"
                            : gate.binding_status === "UNBOUND"
                              ? "UNBOUND"
                              : "UNKNOWN"
                        }
                        tone={
                          gate.binding_status === "VERIFIED"
                            ? gateTone(gate.verdict)
                            : gate.binding_status === "UNBOUND"
                              ? "amber"
                              : "red"
                        }
                      />
                      <p className={`mt-1 text-[10px] ${gate.binding_status === "VERIFIED" ? "text-green-700" : "text-red-600"}`}>
                        binding {gate.binding_status}{gate.binding_error ? `: ${gate.binding_error}` : ""}
                      </p>
                      {gate.integrity_error && gate.integrity_error !== gate.binding_error ? <p className="mt-1 text-[10px] text-red-600">{gate.integrity_error}</p> : null}
                    </td>
                    <td className="py-2.5 pr-4">
                      <Digest value={gate.evidence_digest} />
                      <p className="mt-1 text-[10px] text-gray-400">dataset {gate.dataset_id}@{gate.dataset_version}</p>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>

      <Card title="信任账本" bodyClassName="p-4">
        <p className="mb-4 max-w-3xl text-xs leading-relaxed text-gray-500">
          一次 Release 行动只计一个样本；Wilson 双侧 95% 下界必须大于 0.9。R2 仍需逐次人工批准。
        </p>
        <AsyncBoundary
          loading={ledger.loading}
          error={ledger.error}
          dataEmpty={(ledger.data?.items.length ?? 0) === 0}
          emptyHint="权威 Trust Ledger 真实为空"
          onRetry={ledger.reload}
          staleError={ledger.refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">risk / action</th>
                  <th className="pb-2 pr-4 font-medium">epoch</th>
                  <th className="pb-2 pr-4 font-medium">样本</th>
                  <th className="pb-2 pr-4 font-medium">Wilson 95%</th>
                  <th className="pb-2 pr-4 font-medium">autonomy</th>
                  <th className="pb-2 pr-4 font-medium">晋升</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {ledger.data?.items.map((item) => {
                  const autonomy = AUTONOMY_META[item.autonomy_state];
                  return (
                    <tr key={`${item.risk_class}:${item.action_type}:${item.epoch}`}>
                      <td className="py-2.5 pr-4">
                        <p className="font-mono text-xs text-gray-700">{item.risk_class}</p>
                        <p className="mt-0.5 font-mono text-[10px] text-gray-400">{item.action_type}</p>
                      </td>
                      <td className="py-2.5 pr-4 text-xs tabular-nums">{item.epoch}</td>
                      <td className="py-2.5 pr-4 text-xs tabular-nums">{item.successes}/{item.trials}</td>
                      <td className="py-2.5 pr-4 font-mono text-xs">{item.LB.toFixed(6)} – {item.UB.toFixed(6)}</td>
                      <td className="py-2.5 pr-4">
                        <StatusChip label={autonomy?.label ?? item.autonomy_state} tone={autonomy?.tone ?? "gray"} />
                      </td>
                      <td className="py-2.5 pr-4">
                        <StatusChip
                          label={item.promotion_eligible ? "eligible" : "not eligible"}
                          tone={item.promotion_eligible ? "blue" : "amber"}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>

      <Card title="拒绝晋升审计" bodyClassName="p-4">
        <AsyncBoundary
          loading={denials.loading}
          error={denials.error}
          dataEmpty={(denials.data?.items.length ?? 0) === 0}
          emptyHint="尚无 trust.promotion_denied 审计"
          onRetry={denials.reload}
          staleError={denials.refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">时间</th>
                  <th className="pb-2 pr-4 font-medium">action / risk</th>
                  <th className="pb-2 pr-4 font-medium">拒绝原因</th>
                  <th className="pb-2 pr-4 font-medium">计数</th>
                  <th className="pb-2 pr-4 font-medium">trace</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {denials.data?.items.map((item) => (
                  <tr key={item.audit_id}>
                    <td className="py-2.5 pr-4 text-xs text-gray-500">{formatTime(item.ts)}</td>
                    <td className="py-2.5 pr-4">
                      <p className="font-mono text-xs text-gray-700">{item.action_type}</p>
                      <p className="font-mono text-[10px] text-gray-400">{item.risk_class}</p>
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-amber-800">{item.reason ?? "UNKNOWN"}</td>
                    <td className="py-2.5 pr-4 text-xs tabular-nums">{item.successes ?? "?"}/{item.trials ?? "?"}</td>
                    <td className="py-2.5 pr-4 font-mono text-[10px] text-gray-400">{item.trace_id || "UNKNOWN"}</td>
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

function gateTone(status: string | null | undefined): StatusTone {
  if (status === "passed") return "green";
  if (status === "failed" || status === "error" || status === "integrity_error") return "red";
  if (status === "skipped" || status === "inconclusive") return "amber";
  return "gray";
}
