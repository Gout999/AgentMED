import type { StatusTone } from "../lib/constants";

const TONE_CLASSES: Record<StatusTone, string> = {
  gray: "bg-gray-100 text-gray-700 ring-gray-200",
  blue: "bg-blue-50 text-blue-700 ring-blue-200",
  indigo: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  purple: "bg-purple-50 text-purple-700 ring-purple-200",
  amber: "bg-amber-50 text-amber-700 ring-amber-200",
  orange: "bg-orange-50 text-orange-700 ring-orange-200",
  cyan: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  teal: "bg-teal-50 text-teal-700 ring-teal-200",
  green: "bg-green-50 text-green-700 ring-green-200",
  red: "bg-red-50 text-red-700 ring-red-200",
};

interface StatusChipProps {
  label: string;
  tone: StatusTone;
  /** 可选：chip 右侧的小点指示（如「ACTIVE」自治态） */
  dot?: "on" | "off" | "pulse";
}

/** 语义状态 pill。主色靛蓝，语义色仅用于状态区分。 */
export function StatusChip({ label, tone, dot }: StatusChipProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASSES[tone]}`}
    >
      {dot === "on" && <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />}
      {dot === "off" && <span className="h-1.5 w-1.5 rounded-full bg-current opacity-40" aria-hidden />}
      {dot === "pulse" && (
        <span className="relative flex h-1.5 w-1.5" aria-hidden>
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
        </span>
      )}
      {label}
    </span>
  );
}
