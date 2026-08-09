/** Typed control-plane read contracts used by the production Console. */

export interface ListResponse<T> {
  items: T[];
  next_cursor: number | null;
}

export interface ReadViewList<T> {
  items: T[];
  warning?: string;
}

export interface CaseSummary {
  case_id: string;
  state: string;
  revision: number;
  title: string | null;
  updated_at: string | null;
}

export interface CaseDetail {
  case_id: string;
  state: string;
  revision: number;
  payload: Record<string, unknown>;
  updated_at: string | null;
  event_count: number;
}

export interface CaseEvent {
  seq: number;
  event_id: string;
  event_type: string;
  actor: string;
  causation_id: string;
  correlation_id: string;
  trace_id: string | null;
  occurred_at: string | null;
  payload: Record<string, unknown>;
  evidence_refs: Record<string, unknown>;
}

export interface CaseEventsView {
  case_id: string;
  aggregate_type: "case";
  items: CaseEvent[];
  evidence_refs: Record<string, unknown>;
}

export interface Experiment {
  experiment_id: string;
  state: string;
  revision: number;
  payload: Record<string, unknown>;
}

export interface ExperimentCell {
  cell: string | null;
  arm_order_index: number | null;
  recovery_rate: number | null;
}

export interface ExperimentFull extends Experiment {
  cells: ExperimentCell[];
  deltas: Record<string, number> | null;
  confidence_intervals: Record<string, unknown> | null;
  verdict: string | null;
  attributed_layer: string | null;
  evidence_bundle_ref: string | null;
  report_ref: string | null;
}

export interface ChangeSet {
  changeset_id: string;
  state: string;
  revision: number;
  payload: Record<string, unknown>;
}

export interface WorkOrderView {
  workorder_id: string;
  changeset_id: string;
  case_id: string | null;
  hash: string;
  freeze_at: string | null;
  requester: string | null;
  channel: string;
  nonce: string | null;
  state: string;
  gate_report_ref: { uri?: string; digest?: string } | null;
  target_versionset_digest: string | null;
  created_at: string | null;
  projection_warning: string | null;
  workorder_integrity_status: "verified" | "integrity_error";
  workorder_integrity_error: string | null;
  gate_integrity_status: "verified" | "integrity_error";
  gate_integrity_error: string | null;
  gate_binding_digest: string | null;
  gate_target_revision: number | null;
  gate_target_versionset_id: string | null;
}

export interface GateView {
  eval_id: string;
  workorder_id: string;
  workorder_hash: string | null;
  report_id: string;
  rule_track: string;
  judge_track: string;
  deterministic_tests: string;
  live_provider_e2e: string;
  verdict: string;
  report_hash: string;
  binding_digest: string | null;
  target_versionset_id: string;
  target_revision: number;
  dataset_id: string;
  dataset_version: string;
  evidence_digest: string;
  status: "completed" | "integrity_error" | string;
  integrity_error?: string;
  binding_status: "VERIFIED" | "UNBOUND" | "UNKNOWN";
  binding_error: string | null;
  created_at: string | null;
}

export interface TrustLedgerView {
  risk_class: string;
  action_type: string;
  epoch: number;
  successes: number;
  trials: number;
  autonomy_state: string;
  LB: number;
  UB: number;
  promotion_eligible: boolean;
  suspended_until: string | null;
  pending_promotion_ref: string | null;
  sample_rule: string | null;
  last_action_ref: string | null;
  updated_at: string | null;
}

export interface TrustDenialView {
  audit_id: string;
  ts: string | null;
  actor: string;
  action: string;
  target: string;
  risk_class: string;
  action_type: string;
  result: string;
  trace_id: string;
  reason: string | null;
  successes: number | null;
  trials: number | null;
  trust_entry_id: string | null;
}

export interface ReleaseSummary {
  release_id: string;
  state: string;
  revision: number;
}

export interface ReleaseDetail extends ReleaseSummary {
  payload: Record<string, unknown>;
}

export interface NotificationView {
  notification_id: string;
  state: string;
  revision: number;
  payload: Record<string, unknown>;
}

export interface EvidenceView {
  evidence_id: string;
  source_type: string;
  source_id: string;
  case_id: string | null;
  kind: string;
  reference: string | null;
  digest: string | null;
  binding_status: "BOUND" | "DIGEST_RECORDED" | "REFERENCE_RECORDED" | "UNKNOWN";
  integrity_status: "recorded" | "invalid_digest" | "source_integrity_error";
  integrity_error: string | null;
  artifact_status: "UNKNOWN";
  recorded_at: string | null;
  trace_id: string | null;
}

export interface EvidenceResponse extends ReadViewList<EvidenceView> {
  artifact_store: "unavailable";
  warning: "artifact_content_unavailable" | string;
}

export interface EnvironmentVersion {
  versionset_id: string;
  digest: string;
  status: string;
  revision: number;
}

export interface EnvironmentStatus {
  demo_app: EnvironmentVersion | "unavailable";
}

export interface Healthz {
  status: string;
  version: string;
}
