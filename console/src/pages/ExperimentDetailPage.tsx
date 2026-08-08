import { Link, useParams } from "react-router-dom";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { DataPending } from "../components/DataPending";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { CELL_LABELS, EXPERIMENT_CELLS, VERDICT_META } from "../lib/constants";
import { stateLabel, stateTone, verdictLabel } from "../lib/format";
import { usePageData } from "../hooks/usePageData";
import type { Experiment } from "../lib/types";

export function ExperimentDetailPage() {
  const { id = "" } = useParams();
  const { data, loading, error } = usePageData(() => api.getExperiment(id));

  return (
    <div className="space-y-5">
      <Link to="/experiments" className="inline-flex items-center gap-1 text-xs font-medium text-gray-500 hover:text-brand-600">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="m15 18-6-6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        实验列表
      </Link>

      <AsyncBoundary loading={loading} error={error} dataEmpty={!data} emptyHint="实验不存在或已删除">
        {data && <ExperimentDetailBody data={data} />}
      </AsyncBoundary>
    </div>
  );
}

function ExperimentDetailBody({ data }: { data: Experiment }) {
  const caseId = (data.payload.case_id as string | undefined) ?? null;
  const layer = (data.payload.hypothesis_layer as string | undefined) ?? null;
  const protocol = (data.payload.protocol_version as string | undefined) ?? null;
  const verdict = (data.payload.verdict as string | undefined) ?? null;
  const reportRef = (data.payload.report_ref as string | undefined) ?? null;
  const attributedLayer = (data.payload.attributed_layer as string | undefined) ?? null;

  const verdictMeta = verdict ? VERDICT_META[verdict] : null;

  return (
    <>
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-mono text-base font-semibold text-gray-900">{data.experiment_id}</h2>
          <StatusChip label={stateLabel(data.state)} tone={stateTone(data.state)} />
        </div>
        <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="关联 case" value={caseId} mono />
          <Field label="假设层" value={layer} />
          <Field label="协议版本" value={protocol} />
          <Field label="revision" value={String(data.revision)} />
        </dl>
      </Card>

      {/* 三态裁决大字结论 */}
      <Card
        title="归因裁决"
        extra={
          verdict ? (
            <StatusChip label={verdictLabel(verdict)} tone={verdictMeta?.tone ?? "gray"} />
          ) : (
            <DataPending issue="实验尚未出裁决（payload 无 verdict，需推进到 VERDICT_COMPUTED）" />
          )
        }
        bodyClassName="p-4"
      >
        {verdict ? (
          <div
            className={`flex items-center justify-between rounded-lg px-5 py-6 ring-1 ring-inset ${
              verdict === "ATTRIBUTED"
                ? "bg-green-50 ring-green-200"
                : verdict === "INCONCLUSIVE"
                  ? "bg-amber-50 ring-amber-200"
                  : "bg-red-50 ring-red-200"
            }`}
          >
            <div>
              <p className="text-3xl font-bold tracking-tight text-gray-900">{verdictMeta?.label}</p>
              <p className="mt-1 text-xs text-gray-500">
                ATTRIBUTED 归因成立 · INCONCLUSIVE 结论不明 · CONFOUNDED 受混淆（必要时升级 2³ 全因子）
              </p>
            </div>
            {reportRef && (
              <code className="font-mono text-xs text-gray-500" title={reportRef}>
                {reportRef}
              </code>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed border-gray-200 bg-gray-50/60 px-5 py-6">
            <p className="text-sm text-gray-500">
              当前实验状态为 <span className="font-medium text-gray-700">{stateLabel(data.state)}</span>，尚未产出裁决。
            </p>
            <p className="text-xs text-gray-400">
              裁决数据（ATTRIBUTED / INCONCLUSIVE / CONFOUNDED 三态）随实验推进写入 payload.verdict 后自动展示。
            </p>
          </div>
        )}
      </Card>

      {/* 5-cell 臂恢复率对比 */}
      <Card
        title="5-cell 臂恢复率对比"
        extra={<DataPending issue="cell 级 recovery_rate 未投影到实验详情（OPEN-ISSUES #5）" />}
        bodyClassName="p-4"
      >
        <p className="mb-4 text-xs text-gray-400">
          对照实验五臂（对照 C / 提示词 RP / 知识库 RK / 模型参数 RM / 门禁 G）各臂恢复率横向对比。
          当前 REST 视图仅投影最后一个 <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[11px]">cell_progress</code>，
          cell 级数据在事件中待接入。
        </p>
        <CellBarsPlaceholder />
      </Card>

      {/* Δ 效应量与 95%CI */}
      <Card
        title="Δ 效应量（vs 对照）与 95% CI"
        extra={<DataPending issue="verdict deltas 未并入 aggregate payload（OPEN-ISSUES #5）" />}
        bodyClassName="p-4"
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead>
              <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                <th className="pb-2 pr-4 font-medium">臂位</th>
                <th className="pb-2 pr-4 font-medium">Δ 恢复率</th>
                <th className="pb-2 pr-4 font-medium">95% CI 下界</th>
                <th className="pb-2 pr-4 font-medium">95% CI 上界</th>
              </tr>
            </thead>
            <tbody>
              {EXPERIMENT_CELLS.filter((c) => c !== "C").map((c) => (
                <tr key={c} className="border-b border-gray-50">
                  <td className="py-2.5 pr-4 text-xs text-gray-600">{CELL_LABELS[c]}</td>
                  <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">待接入</td>
                  <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">待接入</td>
                  <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">待接入</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 归因层标注 */}
      <Card title="归因层" bodyClassName="p-4">
        {attributedLayer ? (
          <p className="text-sm text-gray-700">
            <span className="font-mono text-brand-700">{attributedLayer}</span>
          </p>
        ) : (
          <div className="flex items-center gap-2">
            <p className="text-xs text-gray-400">未标注归因层</p>
            <DataPending issue="attributed_layer 在 verdict 事件 payload，未并入 aggregate（OPEN-ISSUES #5）" />
          </div>
        )}
      </Card>
    </>
  );
}

function Field({ label, value, mono }: { label: string; value: string | null; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-medium text-gray-400">{label}</dt>
      <dd className={`mt-0.5 break-all text-sm text-gray-700 ${mono ? "font-mono text-xs" : ""}`}>{value ?? "—"}</dd>
    </div>
  );
}

/** 5-cell 水平条形对比占位（数据待接入）：固定 categorical 序 C→G，基线 C 用灰、处理臂用主色。 */
function CellBarsPlaceholder() {
  return (
    <div className="space-y-2.5">
      {EXPERIMENT_CELLS.map((cell, i) => (
        <div key={cell} className="flex items-center gap-3">
          <span className="w-32 shrink-0 text-xs text-gray-600">{CELL_LABELS[cell]}</span>
          <div className="relative h-4 flex-1 overflow-hidden rounded-md bg-gray-100">
            {i === 0 && (
              <span className="absolute inset-0 flex items-center justify-center text-[10px] text-gray-400">
                对照基线 · 0.00
              </span>
            )}
            {i > 0 && (
              <span className="absolute inset-0 flex items-center justify-center text-[10px] text-gray-300">
                恢复率待接入
              </span>
            )}
          </div>
          <span className="w-12 shrink-0 text-right text-xs tabular-nums text-gray-400">
            {i === 0 ? "0.00" : "—"}
          </span>
        </div>
      ))}
    </div>
  );
}
