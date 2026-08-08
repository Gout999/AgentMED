import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { stateLabel, stateTone } from "../lib/format";
import { usePageData } from "../hooks/usePageData";
import type { Experiment } from "../lib/types";

const FILTERS = [
  { value: "", label: "全部" },
  { value: "REQUESTED", label: "已申请" },
  { value: "PROTOCOL_FROZEN", label: "协议冻结" },
  { value: "RUNNING", label: "运行中" },
  { value: "ANALYZING", label: "分析中" },
  { value: "VERDICT_COMPUTED", label: "已出裁决" },
  { value: "CANCELLED", label: "已取消" },
] as const;

export function ExperimentsPage() {
  const [state, setState] = useState<string>("");

  const fetcher = useCallback(
    (signal: AbortSignal) => api.listExperiments(state || undefined, signal).then((r) => r.items),
    [state],
  );
  const { data, loading, error, reload, refreshError } = usePageData(fetcher, `experiments:${state}`);

  return (
    <div className="space-y-4">
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
          emptyHint={state ? `该状态（${state}）暂无实验` : "control-plane 尚无实验数据"}
          onRetry={reload}
          staleError={refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">experiment_id</th>
                  <th className="pb-2 pr-4 font-medium">状态</th>
                  <th className="pb-2 pr-4 font-medium">关联 case</th>
                  <th className="pb-2 pr-4 font-medium">假设层</th>
                  <th className="pb-2 pr-4 font-medium">revision</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {data?.map((e) => (
                  <ExperimentRow key={e.experiment_id} e={e} />
                ))}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>
    </div>
  );
}

function ExperimentRow({ e }: { e: Experiment }) {
  const caseId = (e.payload.case_id as string | undefined) ?? "—";
  const layer = (e.payload.hypothesis_layer as string | undefined) ?? null;
  return (
    <tr className="group">
      <td className="py-2.5 pr-4">
        <Link
          to={`/experiments/${e.experiment_id}`}
          className="font-mono text-xs text-brand-600 hover:text-brand-700 hover:underline"
        >
          {e.experiment_id}
        </Link>
      </td>
      <td className="py-2.5 pr-4">
        <StatusChip label={stateLabel("experiment", e.state)} tone={stateTone("experiment", e.state)} />
      </td>
      <td className="py-2.5 pr-4">
        {caseId === "—" ? (
          <span className="text-xs text-gray-400">—</span>
        ) : (
          <Link
            to={`/cases/${caseId}`}
            className="font-mono text-xs text-gray-600 hover:text-brand-600 hover:underline"
          >
            {caseId}
          </Link>
        )}
      </td>
      <td className="py-2.5 pr-4 text-xs text-gray-600">{layer ?? "—"}</td>
      <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-600">{e.revision}</td>
    </tr>
  );
}
