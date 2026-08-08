import { useState } from "react";
import { useLocation } from "react-router-dom";
import { DEMO_APP_BASELINE_DIGEST } from "../lib/constants";
import { digestHex } from "../lib/format";
import { emitRefresh } from "../lib/refreshBus";

const TITLES: Array<{ prefix: string; title: string }> = [
  { prefix: "/cases", title: "案例" },
  { prefix: "/experiments", title: "实验" },
  { prefix: "/approvals", title: "审批" },
  { prefix: "/trust", title: "门禁与信任" },
];

export function TopBar() {
  const { pathname } = useLocation();
  const [spinning, setSpinning] = useState(false);

  const title =
    pathname === "/"
      ? "总览"
      : TITLES.find((t) => pathname.startsWith(t.prefix))?.title ?? "总览";

  const handleRefresh = () => {
    setSpinning(true);
    emitRefresh();
    window.setTimeout(() => setSpinning(false), 600);
  };

  const full = DEMO_APP_BASELINE_DIGEST;
  const hex = digestHex(full);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-gray-200 bg-white/85 px-6 backdrop-blur">
      <div className="flex items-center gap-2">
        <h1 className="text-[15px] font-semibold text-gray-900">{title}</h1>
        <span className="text-xs text-gray-400">/</span>
        <span className="text-xs text-gray-400">运营后台</span>
      </div>

      <div className="flex items-center gap-3">
        {/* 当前环境指示：demo-app 基线版本 digest */}
        <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5">
          <span className="flex items-center gap-1.5 text-xs font-medium text-gray-600">
            <span className="relative flex h-2 w-2" aria-hidden>
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
            </span>
            demo-app 基线
          </span>
          <span
            className="font-mono text-xs text-gray-700"
            title={full}
            aria-label={`demo-app 基线版本 digest ${full}`}
          >
            {hex.slice(0, 12)}
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
            className={spinning ? "animate-spin" : ""}
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
