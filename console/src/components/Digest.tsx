import { shortDigest } from "../lib/format";

interface DigestProps {
  value: string | null | undefined;
  /** 截断位数（默认 12） */
  len?: number;
  /** 带复制按钮（审批页 hash 完整可复核） */
  copyable?: boolean;
  className?: string;
}

/** digest/hash 一律 font-mono 截断 12 位，hover 显示全值。 */
export function Digest({ value, len, copyable, className }: DigestProps) {
  const full = value ?? "";
  const short = shortDigest(full, len);
  return (
    <span className={`inline-flex items-center gap-1 font-mono text-xs ${className ?? ""}`}>
      <span title={full} className="cursor-help">
        {short}
      </span>
      {copyable && full && (
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard?.writeText(full);
          }}
          className="rounded p-0.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-brand-600"
          title="复制完整 hash"
          aria-label="复制完整 hash"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        </button>
      )}
    </span>
  );
}
