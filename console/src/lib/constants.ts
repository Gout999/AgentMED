/**
 * 全局常量与中文化映射。
 * 语义状态色一律用 Tailwind 主题色（green/amber/red/gray…），禁止内联色值。
 */

/** digest 截断位数（全局约定） */
export const DIGEST_SHORT_LEN = 12;

/** 轮询刷新间隔（ms） */
export const POLL_INTERVAL_MS = 10_000;

export type StatusTone = "gray" | "blue" | "indigo" | "purple" | "amber" | "orange" | "cyan" | "teal" | "green" | "red";

export type AggregateKind = "case" | "experiment" | "changeset" | "release" | "notification";
export type StateMeta = Record<string, { label: string; tone: StatusTone }>;

/** States are scoped by aggregate so identically named states never drift. */
export const CASE_STATE_META: StateMeta = {
  RECEIVED: { label: "已接收", tone: "gray" },
  OPEN: { label: "待派发", tone: "blue" },
  DISPATCHED: { label: "处理中", tone: "indigo" },
  ATTRIBUTING: { label: "归因中", tone: "purple" },
  AWAITING_FIX: { label: "待修复", tone: "amber" },
  AWAITING_APPROVAL: { label: "待审批", tone: "orange" },
  RELEASING: { label: "发布中", tone: "cyan" },
  NOTIFYING: { label: "通知中", tone: "teal" },
  ESCALATED: { label: "已升级", tone: "red" },
  CLOSED: { label: "已关闭", tone: "green" },
  MERGED: { label: "已合并", tone: "gray" },
  DUPLICATE_DISMISSED: { label: "重复已驳", tone: "gray" },
};

export const EXPERIMENT_STATE_META: StateMeta = {
  REQUESTED: { label: "已申请", tone: "gray" },
  PROTOCOL_FROZEN: { label: "协议冻结", tone: "indigo" },
  RUNNING: { label: "运行中", tone: "blue" },
  ANALYZING: { label: "分析中", tone: "purple" },
  VERDICT_COMPUTED: { label: "已出裁决", tone: "green" },
  CANCELLED: { label: "已取消", tone: "gray" },
};

export const CHANGESET_STATE_META: StateMeta = {
  DRAFTED: { label: "已起草", tone: "gray" },
  GATE_ATTACHED: { label: "门禁已过", tone: "indigo" },
  AWAITING_APPROVAL: { label: "待审批", tone: "orange" },
  APPROVED: { label: "已批准", tone: "green" },
  COMMITTED: { label: "已移交发布", tone: "teal" },
  REJECTED: { label: "已拒绝", tone: "red" },
  EXPIRED: { label: "已过期", tone: "gray" },
  SUPERSEDED: { label: "已被取代", tone: "gray" },
};

export const RELEASE_STATE_META: StateMeta = {
  REQUESTED: { label: "已请求", tone: "gray" },
  STAGING: { label: "预发布中", tone: "blue" },
  CANARYING: { label: "灰度中", tone: "amber" },
  VERIFYING: { label: "验证中", tone: "purple" },
  PROMOTING: { label: "晋升中", tone: "indigo" },
  COMPLETED: { label: "已完成", tone: "green" },
  ROLLING_BACK: { label: "回滚中", tone: "red" },
  ROLLED_BACK: { label: "已回滚", tone: "orange" },
  UNKNOWN: { label: "状态未知", tone: "red" },
  FAILED_ESCALATED: { label: "失败已升级", tone: "red" },
};

export const NOTIFICATION_STATE_META: StateMeta = {
  QUEUED: { label: "已排队", tone: "gray" },
  SENDING: { label: "发送中", tone: "blue" },
  RETRYING: { label: "等待重试", tone: "amber" },
  SENT: { label: "已发送", tone: "green" },
  DEAD_LETTERED: { label: "死信待人工", tone: "red" },
};

export const STATE_META_BY_AGGREGATE: Record<AggregateKind, StateMeta> = {
  case: CASE_STATE_META,
  experiment: EXPERIMENT_STATE_META,
  changeset: CHANGESET_STATE_META,
  release: RELEASE_STATE_META,
  notification: NOTIFICATION_STATE_META,
};

/** 实验三态裁决 → 语义色（ATTRIBUTED 绿 / INCONCLUSIVE 黄 / CONFOUNDED 红） */
export const VERDICT_META: Record<string, { label: string; tone: StatusTone }> = {
  ATTRIBUTED: { label: "归因成立", tone: "green" },
  INCONCLUSIVE: { label: "结论不明", tone: "amber" },
  CONFOUNDED: { label: "受混淆", tone: "red" },
};

/** 信任账本 autonomy_state → 状态 chip */
export const AUTONOMY_META: Record<string, { label: string; tone: StatusTone }> = {
  MANUAL: { label: "手动", tone: "gray" },
  ELIGIBLE: { label: "符合晋升条件", tone: "blue" },
  AWAITING_CONFIRMATION: { label: "待人工确认", tone: "amber" },
  AUTO_ENABLED: { label: "ACTIVE 自治", tone: "green" },
  SUSPENDED: { label: "冷却期", tone: "red" },
  BLOCKED_UNKNOWN: { label: "阻塞未知", tone: "red" },
};

/** case 终态集合（用于「活跃 case」口径） */
export const CASE_TERMINAL_STATES = new Set(["CLOSED", "MERGED", "DUPLICATE_DISMISSED"]);

/** experiment 终态集合（用于「进行中实验」口径） */
export const EXPERIMENT_TERMINAL_STATES = new Set(["VERDICT_COMPUTED", "CANCELLED"]);

/** 实验详情 5-cell 臂位顺序（C/RP/RK/RM/G） */
export const EXPERIMENT_CELLS = ["C", "RP", "RK", "RM", "G"] as const;

export const CELL_LABELS: Record<string, string> = {
  C: "对照 Control",
  RP: "提示词 Prompt",
  RK: "知识库 KB",
  RM: "模型参数 Model",
  G: "门禁 Guard",
};
