import { DIGEST_SHORT_LEN, STATE_META, VERDICT_META, type StatusTone } from "./constants";

/** ISO 时间 → 本地可读（2026-08-08 18:43）。无效输入回退原始串。 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** ISO 时间 → 仅时间 HH:mm:ss（时间线内紧凑展示） */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** 相对时间：多久之前（用于「最近更新」一类的紧凑展示） */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return iso;
  const diff = Date.now() - d;
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  return `${day} 天前`;
}

/** 截断 digest/hash 到固定位数（默认 12），全值保留给 tooltip。 */
export function shortDigest(value: string | null | undefined, len: number = DIGEST_SHORT_LEN): string {
  if (!value) return "—";
  const clean = value.replace(/^sha256:/, "");
  if (clean.length <= len) return value;
  return `${clean.slice(0, len)}…`;
}

/** 从 sha256:xxxx… 取纯 hex（供显示短 digest 时去掉前缀） */
export function digestHex(value: string | null | undefined): string {
  if (!value) return "";
  return value.replace(/^sha256:/, "");
}

/** 状态 → 中文标签；未知状态回退原串。 */
export function stateLabel(state: string | null | undefined): string {
  if (!state) return "—";
  return STATE_META[state]?.label ?? state;
}

/** 状态 → 语义色 tone；未知状态 gray。 */
export function stateTone(state: string | null | undefined): StatusTone {
  if (!state) return "gray";
  return STATE_META[state]?.tone ?? "gray";
}

/** 实验裁决 → 中文标签；非三态值原样返回。 */
export function verdictLabel(verdict: string | null | undefined): string {
  if (!verdict) return "—";
  return VERDICT_META[verdict]?.label ?? verdict;
}

/** 读取聚合 payload 中任意深度的字段（兜底保护）。 */
export function pluck(payload: Record<string, unknown> | undefined, key: string): unknown {
  return payload?.[key];
}
