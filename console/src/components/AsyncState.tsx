import type { ReactNode } from "react";

interface AsyncBoundaryProps {
  loading: boolean;
  error: string | null;
  dataEmpty: boolean;
  emptyHint?: ReactNode;
  children: ReactNode;
}

/**
 * 页面数据三态渲染：加载中 / 错误 / 空态 / 正常。
 * 空态与错误态是验收硬指标，必须做。
 */
export function AsyncBoundary({ loading, error, dataEmpty, emptyHint, children }: AsyncBoundaryProps) {
  if (loading && error === null) {
    return <LoadingRows />;
  }
  if (error !== null) {
    return <ErrorState message={error} />;
  }
  if (dataEmpty) {
    return <EmptyState hint={emptyHint} />;
  }
  return <>{children}</>;
}

export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-10 animate-pulse rounded-lg bg-gray-100" />
      ))}
    </div>
  );
}

export function LoadingCard() {
  return (
    <div className="h-24 animate-pulse rounded-xl bg-gray-100" aria-busy="true" aria-label="加载中" />
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-red-200 bg-red-50/50 px-6 py-12 text-center">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="text-red-400">
        <circle cx="12" cy="12" r="10" />
        <path d="M12 8v4M12 16h.01" strokeLinecap="round" />
      </svg>
      <p className="text-sm font-medium text-red-700">数据加载失败</p>
      <p className="max-w-md text-xs text-red-600/80">{message}</p>
    </div>
  );
}

export function EmptyState({ hint }: { hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-6 py-12 text-center">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="text-gray-300">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M9 21V9" />
      </svg>
      <p className="text-sm font-medium text-gray-500">暂无数据</p>
      {hint && <p className="max-w-md text-xs text-gray-400">{hint}</p>}
    </div>
  );
}
