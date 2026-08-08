import { Link } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { MetricCard } from "../components/MetricCard";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { CASE_TERMINAL_STATES, EXPERIMENT_TERMINAL_STATES } from "../lib/constants";
import { formatTime, stateLabel, stateTone } from "../lib/format";
import { usePageData } from "../hooks/usePageData";
import type { CaseSummary } from "../lib/types";

interface RecentCase extends CaseSummary {
  title?: string;
}

interface OverviewData {
  activeCases: number;
  activeExperiments: number;
  pendingApprovals: number;
  recent: RecentCase[];
}

const fetchOverview = async (): Promise<OverviewData> => {
  const [casesRes, expsRes, csRes] = await Promise.all([
    api.listCases(),
    api.listExperiments(),
    api.listChangesets("AWAITING_APPROVAL"),
  ]);
  const activeCases = casesRes.items.filter((c) => !CASE_TERMINAL_STATES.has(c.state)).length;
  const activeExperiments = expsRes.items.filter((e) => !EXPERIMENT_TERMINAL_STATES.has(e.state)).length;

  const sorted = [...casesRes.items].sort((a, b) =>
    (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
  );
  const top = sorted.slice(0, 10);
  const details = await Promise.all(
    top.map((c) => api.getCase(c.case_id).catch(() => null)),
  );
  const recent: RecentCase[] = top.map((c, i) => ({
    ...c,
    title: (details[i]?.payload?.title as string | undefined) ?? undefined,
  }));

  return {
    activeCases,
    activeExperiments,
    pendingApprovals: csRes.items.length,
    recent,
  };
};

export function OverviewPage() {
  const { data, loading, error } = usePageData(fetchOverview);

  return (
    <div className="space-y-6">
      {/* 指标卡 */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="活跃 case"
          value={data?.activeCases ?? "—"}
          hint="非终态案件"
          icon={<CaseIcon />}
        />
        <MetricCard
          label="进行中实验"
          value={data?.activeExperiments ?? "—"}
          hint="非终态归因实验"
          icon={<ExperimentIcon />}
        />
        <MetricCard
          label="待审批"
          value={data?.pendingApprovals ?? "—"}
          hint="AWAITING_APPROVAL"
          icon={<ApprovalIcon />}
        />
        <MetricCard
          label="信任账本最新评估结论"
          value={<span className="text-base text-gray-500">暂无</span>}
          hint="数据待接入"
          valueClassName="text-base"
          icon={<TrustIcon />}
        />
      </div>

      {/* 最近 case */}
      <Card
        title="最近 case"
        extra={
          <Link to="/cases" className="text-xs font-medium text-brand-600 hover:text-brand-700">
            查看全部 →
          </Link>
        }
      >
        <AsyncBoundary
          loading={loading}
          error={error}
          dataEmpty={(data?.recent.length ?? 0) === 0}
          emptyHint="control-plane 尚无 case 数据"
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">case_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">摘要</th>
                  <th className="pb-2 pr-4 font-medium">更新时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {data?.recent.map((c) => (
                  <tr key={c.case_id} className="group">
                    <td className="py-2.5 pr-4">
                      <Link
                        to={`/cases/${c.case_id}`}
                        className="font-mono text-xs text-brand-600 hover:text-brand-700 hover:underline"
                      >
                        {c.case_id}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4">
                      <StatusChip label={stateLabel(c.state)} tone={stateTone(c.state)} />
                    </td>
                    <td className="max-w-[340px] truncate py-2.5 pr-4 text-xs text-gray-600">
                      {c.title ?? "—"}
                    </td>
                    <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">
                      {formatTime(c.updated_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>

      {/* 信任账本数据缺口说明 */}
      <p className="text-xs text-gray-400">
        指标卡「信任账本最新评估结论」对应数据待接入：trust_ledger 表在 control-plane 库但未暴露 REST 读端点
        （见 <a className="text-brand-600 hover:underline" href="/OPEN-ISSUES.md">console/OPEN-ISSUES.md #2</a>）。
      </p>
    </div>
  );
}

function CaseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M4 7h16M4 7l1-3h14l1 3M6 7v13a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ExperimentIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M9 3h6M10 3v6.5L4.8 18a2 2 0 0 0 1.8 3h10.8a2 2 0 0 0 1.8-3L14 9.5V3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ApprovalIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 7h8M8 11h8M8 15h5" strokeLinecap="round" />
      <path d="m14.5 17.5 1.5 1.5 3-3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrustIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M12 3 5 6v5c0 4.4 3 8 7 10 4-2 7-5.6 7-10V6l-7-3Z" strokeLinejoin="round" />
      <path d="m9 12 2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
