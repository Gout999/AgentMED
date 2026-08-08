/** 「数据待接入」标注：视图结构照常渲染，但该数据源尚无 REST 端点（见 console/OPEN-ISSUES.md）。 */

interface DataPendingProps {
  issue?: string;
  className?: string;
}

export function DataPending({ issue, className }: DataPendingProps) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border border-dashed border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 ${className ?? ""}`}
      title={`数据待接入：${issue ?? "control-plane 未暴露对应 REST 端点"}`}
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
        <path d="M12 9v4M12 17h.01" strokeLinecap="round" />
        <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" strokeLinecap="round" />
      </svg>
      数据待接入
    </span>
  );
}
