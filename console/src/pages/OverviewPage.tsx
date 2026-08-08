import { Link } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { MetricCard } from "../components/MetricCard";
import { StatusChip } from "../components/StatusChip";
import { usePageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { CASE_TERMINAL_STATES, EXPERIMENT_TERMINAL_STATES } from "../lib/constants";
import { formatTime, stateLabel, stateTone } from "../lib/format";

export function OverviewPage() {
  const cases = usePageData((signal) => api.listCases(undefined, signal));
  const experiments = usePageData((signal) => api.listExperiments(undefined, signal));
  const workorders = usePageData((signal) => api.listWorkOrders(signal));
  const trust = usePageData((signal) => api.listTrustLedger(signal));

  const caseItems = cases.data?.items ?? [];
  const experimentItems = experiments.data?.items ?? [];
  const workorderItems = workorders.data?.items ?? [];
  const activeCases = caseItems.filter((item) => !CASE_TERMINAL_STATES.has(item.state)).length;
  const activeExperiments = experimentItems.filter(
    (item) => !EXPERIMENT_TERMINAL_STATES.has(item.state),
  ).length;
  const pendingApprovals = workorderItems.filter((item) => item.state === "AWAITING_APPROVAL").length;
  const latestTrust = [...(trust.data?.items ?? [])].sort((a, b) =>
    (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
  )[0];
  const recent = [...caseItems]
    .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
    .slice(0, 10);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="活跃 case"
          value={metricValue(cases.loading, cases.error, activeCases)}
          hint={cases.refreshError ? "数据已过期 · 刷新失败" : "非终态案件"}
          icon={<CaseIcon />}
        />
        <MetricCard
          label="进行中实验"
          value={metricValue(experiments.loading, experiments.error, activeExperiments)}
          hint={experiments.refreshError ? "数据已过期 · 刷新失败" : "非终态归因实验"}
          icon={<ExperimentIcon />}
        />
        <MetricCard
          label="待审批"
          value={metricValue(workorders.loading, workorders.error, pendingApprovals)}
          hint={workorders.refreshError ? "数据已过期 · 刷新失败" : "权威 WorkOrder + ChangeSet 状态"}
          icon={<ApprovalIcon />}
        />
        <MetricCard
          label="信任账本最新评估"
          value={
            trust.error ? (
              <span className="text-base font-semibold text-red-700">UNKNOWN</span>
            ) : latestTrust ? (
              <span className="text-base text-gray-800">
                {latestTrust.successes}/{latestTrust.trials} · {latestTrust.autonomy_state}
              </span>
            ) : trust.loading ? (
              "—"
            ) : (
              <span className="text-base text-gray-500">真实空</span>
            )
          }
          hint={
            latestTrust
              ? `Wilson 双侧 95% LB ${latestTrust.LB.toFixed(4)} · ${latestTrust.risk_class}`
              : trust.refreshError ?? "无 Trust 样本"
          }
          valueClassName="text-base"
          icon={<TrustIcon />}
        />
      </div>

      <Card
        title="最近 case"
        extra={
          <Link to="/cases" className="text-xs font-medium text-brand-600 hover:text-brand-700">
            查看全部 →
          </Link>
        }
      >
        <AsyncBoundary
          loading={cases.loading}
          error={cases.error}
          dataEmpty={recent.length === 0}
          emptyHint="control-plane 尚无 case 数据"
          onRetry={cases.reload}
          staleError={cases.refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">case_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">revision</th>
                  <th className="pb-2 pr-4 font-medium">摘要</th>
                  <th className="pb-2 pr-4 font-medium">更新时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {recent.map((item) => (
                  <tr key={item.case_id}>
                    <td className="py-2.5 pr-4">
                      <Link
                        to={`/cases/${item.case_id}`}
                        className="font-mono text-xs text-brand-600 hover:underline"
                      >
                        {item.case_id}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4">
                      <StatusChip
                        label={stateLabel("case", item.state)}
                        tone={stateTone("case", item.state)}
                      />
                    </td>
                    <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-600">{item.revision}</td>
                    <td className="max-w-[340px] truncate py-2.5 pr-4 text-xs text-gray-600">
                      {item.title ?? "—"}
                    </td>
                    <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">
                      {formatTime(item.updated_at)}
                    </td>
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

function metricValue(loading: boolean, error: string | null, value: number) {
  if (error) return <span className="text-base font-semibold text-red-700">UNKNOWN</span>;
  return loading ? "—" : value;
}

function CaseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <path d="M4 7h16M4 7l1-3h14l1 3M6 7v13a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ExperimentIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <path d="M9 3h6M10 3v6.5L4.8 18a2 2 0 0 0 1.8 3h10.8a2 2 0 0 0 1.8-3L14 9.5V3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ApprovalIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 7h8M8 11h8M8 15h5" strokeLinecap="round" />
      <path d="m14.5 17.5 1.5 1.5 3-3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrustIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <path d="M12 3 5 6v5c0 4.4 3 8 7 10 4-2 7-5.6 7-10V6l-7-3Z" strokeLinejoin="round" />
      <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
