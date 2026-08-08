import type { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** 数值语义色 tone 映射（Tailwind 主题色，禁止内联色值） */
  valueClassName?: string;
  icon?: ReactNode;
}

/** 总览指标卡：大数字 + 标签 + 可选说明。 */
export function MetricCard({ label, value, hint, valueClassName, icon }: MetricCardProps) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      {icon && (
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          {icon}
        </div>
      )}
      <div className="min-w-0">
        <p className="text-xs font-medium text-gray-500">{label}</p>
        <p className={`mt-1 text-2xl font-semibold tabular-nums text-gray-900 ${valueClassName ?? ""}`}>{value}</p>
        {hint && <p className="mt-1 text-xs text-gray-400">{hint}</p>}
      </div>
    </div>
  );
}
