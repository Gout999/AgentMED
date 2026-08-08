import { useLocation } from "react-router-dom";
import { usePageData } from "../hooks/usePageData";
import { api } from "../lib/api";
import { digestHex } from "../lib/format";
import { emitRefresh } from "../lib/refreshBus";
import type { EnvironmentVersion } from "../lib/types";

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;

function isActiveEnvironment(value: unknown): value is EnvironmentVersion {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<EnvironmentVersion>;
  return candidate.status === "active"
    && typeof candidate.versionset_id === "string"
    && candidate.versionset_id.length > 0
    && typeof candidate.digest === "string"
    && SHA256_DIGEST.test(candidate.digest)
    && Number.isInteger(candidate.revision)
    && (candidate.revision ?? 0) > 0;
}

const TITLES: Array<{ prefix: string; title: string }> = [
  { prefix: "/cases", title: "案例" },
  { prefix: "/experiments", title: "实验" },
  { prefix: "/approvals", title: "审批" },
  { prefix: "/trust", title: "门禁与信任" },
  { prefix: "/operations", title: "发布、通知与证据" },
];

export function TopBar() {
  const { pathname } = useLocation();
  const env = usePageData((signal) => api.getEnvironment(signal));

  const title =
    pathname === "/"
      ? "总览"
      : TITLES.find((t) => pathname.startsWith(t.prefix))?.title ?? "总览";

  const handleRefresh = () => {
    emitRefresh();
  };

  const environment = env.data?.demo_app;
  const available = isActiveEnvironment(environment);
  const full = available ? environment.digest : null;
  const hex = digestHex(full);
  const unknown =
    env.error !== null || environment === "unavailable" || (!env.loading && environment == null);
  const tone = available ? "green" : unknown ? "red" : "amber";
  const environmentLabel = available
    ? hex.slice(0, 12)
    : env.loading
      ? "读取中"
      : "UNKNOWN";
  const environmentTitle = available
    ? `${environment.versionset_id} · ${environment.digest} · revision ${environment.revision}`
    : env.error ?? env.refreshError ?? "Quality API 无 active VersionSet 或不可达";

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-gray-200 bg-white/85 px-6 backdrop-blur">
      <div className="flex items-center gap-2">
        <h1 className="text-[15px] font-semibold text-gray-900">{title}</h1>
        <span className="text-xs text-gray-400">/</span>
        <span className="text-xs text-gray-400">运营后台</span>
      </div>

      <div className="flex items-center gap-3">
        <div
          className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5"
          title={environmentTitle}
          aria-label={`demo-app 环境 ${environmentLabel}`}
        >
          <span className="flex items-center gap-1.5 text-xs font-medium text-gray-600">
            <span className="relative flex h-2 w-2" aria-hidden>
              {tone === "green" ? <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-60" /> : null}
              <span className={`relative inline-flex h-2 w-2 rounded-full ${tone === "green" ? "bg-green-500" : tone === "red" ? "bg-red-500" : "bg-amber-500"}`} />
            </span>
            demo-app 基线
          </span>
          <span className={`font-mono text-xs ${unknown ? "font-semibold text-red-700" : "text-gray-700"}`}>
            {environmentLabel}
          </span>
        </div>

        <button
          type="button"
          onClick={handleRefresh}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          aria-label="手动刷新"
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            className={env.refreshing ? "animate-spin" : ""}
            aria-hidden
          >
            <path d="M21 12a9 9 0 1 1-2.64-6.36" strokeLinecap="round" />
            <path d="M21 3v6h-6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          刷新
        </button>
      </div>
    </header>
  );
}
