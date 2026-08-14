import { useCallback } from "react";
import { AsyncBoundary } from "../components/AsyncState";
import { Card } from "../components/Card";
import { StatusChip } from "../components/StatusChip";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";
import { usePageData } from "../hooks/usePageData";
import type { ApplicationView } from "../lib/types";

/**
 * V5-1A Applications read model.
 *
 * 全态渲染硬指标：loading / empty / error / partial / UNKNOWN。
 * - partial：任一行的 envelope 完整性重验失败时显示黄色警示，坏行标 integrity_error，
 *   绝不把坏行当可信数据；
 * - UNKNOWN：字段被投影为 UNKNOWN（integrity 不可信）时行级标 UNKNOWN chip。
 */
export function ApplicationsPage() {
  const fetcher = useCallback(
    (signal: AbortSignal) => api.listApplications(signal).then((r) => r.items),
    [],
  );
  const { data, loading, error, reload, refreshError } = usePageData(fetcher, "applications");

  const partial = (data ?? []).some((item) => item.integrity_status !== "verified");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-900">AI 应用目录（V5-1A）</h1>
        {(data?.length ?? 0) > 0 && (
          <span className="text-xs tabular-nums text-gray-400">{data?.length ?? 0} 条</span>
        )}
      </div>

      {partial && (
        <div
          className="flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
          role="status"
        >
          <span>部分记录未通过 envelope 完整性重验，已标记 integrity_error，未作为可信数据展示。</span>
          <button type="button" onClick={reload} className="font-medium underline underline-offset-2">
            重试
          </button>
        </div>
      )}

      <Card>
        <AsyncBoundary
          loading={loading}
          error={error}
          dataEmpty={(data?.length ?? 0) === 0}
          emptyHint="control-plane 尚无 AI 应用注册记录"
          onRetry={reload}
          staleError={refreshError}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs uppercase tracking-wide text-gray-400">
                  <th className="pb-2 pr-4 font-medium">application_id</th>
                  <th className="pb-2 pr-4 font-medium">slug</th>
                  <th className="pb-2 pr-4 font-medium">显示名</th>
                  <th className="pb-2 pr-4 font-medium">关键度</th>
                  <th className="pb-2 pr-4 font-medium">生命周期</th>
                  <th className="pb-2 pr-4 font-medium">环境/组件</th>
                  <th className="pb-2 pr-4 font-medium">完整性</th>
                  <th className="pb-2 pr-4 font-medium">更新时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {data?.map((app) => (
                  <ApplicationRow key={app.application_id} app={app} />
                ))}
              </tbody>
            </table>
          </div>
        </AsyncBoundary>
      </Card>
    </div>
  );
}

function ApplicationRow({ app }: { app: ApplicationView }) {
  const untrusted = app.integrity_status !== "verified";
  return (
    <tr className="group">
      <td className="py-2.5 pr-4 font-mono text-xs text-brand-600">{app.application_id}</td>
      <td className="max-w-[180px] truncate py-2.5 pr-4 text-xs text-gray-600">
        {app.slug === "UNKNOWN" ? "—" : app.slug}
      </td>
      <td className="max-w-[220px] truncate py-2.5 pr-4 text-xs text-gray-700">
        {app.display_name === "UNKNOWN" ? "—" : app.display_name}
      </td>
      <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-600">
        {app.criticality === "UNKNOWN" ? "UNKNOWN" : app.criticality}
      </td>
      <td className="py-2.5 pr-4">
        <StatusChip
          label={app.lifecycle_state === "UNKNOWN" ? "UNKNOWN" : app.lifecycle_state}
          tone={app.lifecycle_state === "UNKNOWN" ? "gray" : "green"}
        />
      </td>
      <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-600">
        {app.environment_count} / {app.component_count}
      </td>
      <td className="py-2.5 pr-4">
        <StatusChip
          label={untrusted ? app.integrity_status : "verified"}
          tone={untrusted ? (app.integrity_status === "unknown" ? "gray" : "red") : "green"}
        />
      </td>
      <td className="py-2.5 pr-4 text-xs tabular-nums text-gray-500">{formatTime(app.updated_at)}</td>
    </tr>
  );
}
