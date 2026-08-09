import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { stateLabel, stateTone, formatTime } from "../lib/format";
import { usePageData } from "../hooks/usePageData";
import type { CaseSummary } from "../lib/types";

const FILTERS = [
  { value: "", label: "全部" },
  { value: "RECEIVED", label: "已接收" },
  { value: "OPEN", label: "待派发" },
  { value: "DISPATCHED", label: "处理中" },
  { value: "ATTRIBUTING", label: "归因中" },
  { value: "AWAITING_FIX", label: "待修复" },
  { value: "AWAITING_APPROVAL", label: "待审批" },
  { value: "RELEASING", label: "发布中" },
  { value: "NOTIFYING", label: "通知中" },
  { value: "ESCALATED", label: "已升级" },
  { value: "CLOSED", label: "已关闭" },
  { value: "MERGED", label: "已合并" },
  { value: "DUPLICATE_DISMISSED", label: "重复已驳" },
] as const;

export function CasesPage() {
  const [state, setState] = useState<string>("");

  const fetcher = useCallback(
    (signal: AbortSignal) => api.listCases(state || undefined, signal).then((r) => r.items),
    [state],
  );
  const { data, loading, error, reload, refreshError } = usePageData(fetcher, `cases:${state}`);

  return (
    <div className="space-y-4">
      {/* 状态筛选 chip 组 */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => {
          const active = state === f.value;
          return (
            <button
              key={f.value}
              type="button"
              onClick={() => setState(f.value)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                active
                  ? "bg-brand-600 text-white shadow-sm"
                  : "bg-white text-gray-600 ring-1 ring-inset ring-gray-200 hover:bg-gray-50"
              }`}
            >
              {f.label}
            </button>
          );
        })}
        {(data?.length ?? 0) > 0 && (
          <span className="ml-auto text-xs tabular-nums text-gray-400">{data?.length ?? 0} 条</span>
        )}
      </div>

      <Card>
        <AsyncBoundary
          loading={loading}
          error={error}
          dataEmpty={(data?.length ?? 0) === 0}
          emptyHint={state ? `该状态（${state}）暂无 case` : "control-plane 尚无 case 数据"}
          onRetry={reload}
          staleError={refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">case_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">摘要</th>
                  <th className="pb-2 pr-4 font-medium">revision</th>
                  <th className="pb-2 pr-4 font-medium">更新时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {data?.map((c) => (
                  <CaseRow key={c.case_id} c={c} />
                ))}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>
    </div>
  );
}

function CaseRow({ c }: { c: CaseSummary }) {
  return (
    <tr className="group">
      <td className="py-2.5 pr-4">
        <Link
          to={`/cases/${c.case_id}`}
          className="font-mono text-xs text-brand-600 hover:text-brand-700 hover:underline"
        >
          {c.case_id}
        </Link>
      </td>
      <td className="py-2.5 pr-4">
        <StatusChip label={stateLabel("case", c.state)} tone={stateTone("case", c.state)} />
      </td>
      <td className="max-w-[320px] truncate py-2.5 pr-4 text-xs text-gray-600">{c.title ?? "—"}</td>
      <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-600">{c.revision}</td>
      <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">{formatTime(c.updated_at)}</td>
    </tr>
  );
}
