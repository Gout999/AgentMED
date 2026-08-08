import { Link, useParams } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { usePageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { CELL_LABELS, EXPERIMENT_CELLS, VERDICT_META } from "../lib/constants";
import { stateLabel, stateTone, verdictLabel } from "../lib/format";
import type { ExperimentFull } from "../lib/types";

export function ExperimentDetailPage() {
  const { id = "" } = useParams();
  const view = usePageData((signal) => api.getExperimentFull(id, signal), `experiment:${id}`);

  return (
    <div className="space-y-5">
      <Link to="/experiments" className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-brand-600">
        <span aria-hidden>←</span>
        实验列表
      </Link>
      <AsyncBoundary
        loading={view.loading}
        error={view.error}
        dataEmpty={!view.data}
        emptyHint="实验不存在"
        onRetry={view.reload}
        staleError={view.refreshError}
      >
        {view.data ? <ExperimentDetailBody data={view.data} /> : null}
      </AsyncBoundary>
    </div>
  );
}

function ExperimentDetailBody({ data }: { data: ExperimentFull }) {
  const caseId = typeof data.payload.case_id === "string" ? data.payload.case_id : null;
  const layer = typeof data.payload.hypothesis_layer === "string" ? data.payload.hypothesis_layer : null;
  const protocol = typeof data.payload.protocol_version === "string" ? data.payload.protocol_version : null;
  const verdictMeta = data.verdict ? VERDICT_META[data.verdict] : null;
  const cells = new Map(data.cells.map((cell) => [cell.cell, cell]));

  return (
    <>
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-mono text-base font-semibold text-gray-900">{data.experiment_id}</h2>
          <StatusChip
            label={stateLabel("experiment", data.state)}
            tone={stateTone("experiment", data.state)}
          />
        </div>
        <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="关联 case" value={caseId} mono />
          <Field label="假设层" value={layer} />
          <Field label="协议版本" value={protocol} />
          <Field label="revision" value={String(data.revision)} />
        </dl>
      </Card>

      <Card
        title="归因裁决"
        extra={
          data.verdict ? (
            <StatusChip label={verdictLabel(data.verdict)} tone={verdictMeta?.tone ?? "gray"} />
          ) : (
            <StatusChip label="尚未产出" tone="gray" />
          )
        }
        bodyClassName="p-4"
      >
        {data.verdict ? (
          <div className={`rounded-lg px-5 py-6 ring-1 ring-inset ${verdictClass(data.verdict)}`}>
            <p className="text-3xl font-bold tracking-tight text-gray-900">{verdictMeta?.label ?? data.verdict}</p>
            <p className="mt-2 text-xs text-gray-600">
              fault layer: <span className="font-mono font-semibold">{data.attributed_layer ?? "UNKNOWN"}</span>
            </p>
          </div>
        ) : (
          <p className="rounded-lg border border-dashed border-gray-200 bg-gray-50 px-4 py-5 text-sm text-gray-500">
            当前状态 {stateLabel("experiment", data.state)}；权威事件尚未写入 verdict，不推断结论。
          </p>
        )}
      </Card>

      <Card title="5-cell 恢复率" bodyClassName="p-4">
        <div className="space-y-2.5">
          {EXPERIMENT_CELLS.map((cellName) => {
            const cell = cells.get(cellName);
            const rate = typeof cell?.recovery_rate === "number" ? cell.recovery_rate : null;
            const width = rate === null ? 0 : Math.max(0, Math.min(1, rate)) * 100;
            return (
              <div key={cellName} className="flex items-center gap-3">
                <span className="w-32 shrink-0 text-xs text-gray-600">{CELL_LABELS[cellName]}</span>
                <div className="relative h-5 flex-1 overflow-hidden rounded-md bg-gray-100">
                  {rate !== null ? (
                    <span className="block h-full rounded-md bg-brand-500" style={{ width: `${width}%` }} />
                  ) : null}
                  <span className="absolute inset-0 flex items-center justify-center text-[10px] text-gray-600">
                    {rate === null ? "UNKNOWN · 未记录 cell 结果" : `arm order ${cell?.arm_order_index ?? "—"}`}
                  </span>
                </div>
                <span className={`w-16 shrink-0 text-right text-xs tabular-nums ${rate === null ? "text-red-600" : "text-gray-700"}`}>
                  {rate === null ? "UNKNOWN" : rate.toFixed(3)}
                </span>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="Δ 效应量与 95% CI" bodyClassName="p-4">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                <th className="pb-2 pr-4 font-medium">臂位</th>
                <th className="pb-2 pr-4 font-medium">Δ 恢复率</th>
                <th className="pb-2 pr-4 font-medium">95% CI</th>
              </tr>
            </thead>
            <tbody>
              {EXPERIMENT_CELLS.filter((cell) => cell !== "C").map((cell) => {
                const delta = data.deltas?.[cell];
                const interval = data.confidence_intervals?.[cell];
                return (
                  <tr key={cell} className="border-b border-gray-50">
                    <td className="py-2.5 pr-4 text-xs text-gray-600">{CELL_LABELS[cell]}</td>
                    <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-700">
                      {typeof delta === "number" ? delta.toFixed(4) : "UNKNOWN"}
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-amber-700">
                      {interval === undefined || interval === null
                        ? "UNKNOWN · 事件未提供样本量/方差"
                        : JSON.stringify(interval)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Evidence 引用" bodyClassName="p-4">
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="evidence_bundle_ref" value={data.evidence_bundle_ref} mono />
          <Field label="attribution report_ref" value={data.report_ref} mono />
        </dl>
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          引用来自权威 experiment event；artifact 内容尚未由控制面读取，验证状态为 UNKNOWN。
        </p>
      </Card>
    </>
  );
}

function verdictClass(verdict: string): string {
  if (verdict === "ATTRIBUTED") return "bg-green-50 ring-green-200";
  if (verdict === "INCONCLUSIVE") return "bg-amber-50 ring-amber-200";
  return "bg-red-50 ring-red-200";
}

function Field({ label, value, mono }: { label: string; value: string | null; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-gray-400">{label}</dt>
      <dd className={`mt-0.5 break-all text-sm text-gray-700 ${mono ? "font-mono text-xs" : ""}`}>
        {value ?? "UNKNOWN"}
      </dd>
    </div>
  );
}
