import type {
  CaseDetail,
  CaseEvent,
  CaseEventsView,
  CaseSummary,
  ChangeSet,
  EnvironmentStatus,
  EvidenceResponse,
  EvidenceView,
  Experiment,
  ExperimentCell,
  ExperimentFull,
  GateView,
  Healthz,
  ListResponse,
  NotificationView,
  ReadViewList,
  ReleaseDetail,
  ReleaseSummary,
  TrustDenialView,
  TrustLedgerView,
  WorkOrderView,
} from "./types";

export type Guard<T> = (value: unknown) => value is T;

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function string(value: unknown): value is string {
  return typeof value === "string";
}

function nonEmptyString(value: unknown): value is string {
  return string(value) && value.length > 0;
}

function nullableString(value: unknown): value is string | null {
  return value === null || string(value);
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function integer(value: unknown): value is number {
  return Number.isInteger(value);
}

function nullableInteger(value: unknown): value is number | null {
  return value === null || integer(value);
}

function optionalString(value: unknown): boolean {
  return value === undefined || string(value);
}

function listGuard<T>(item: Guard<T>): Guard<ListResponse<T>> {
  return (value: unknown): value is ListResponse<T> => record(value)
    && Array.isArray(value.items)
    && value.items.every(item)
    && nullableInteger(value.next_cursor);
}

function readListGuard<T>(item: Guard<T>): Guard<ReadViewList<T>> {
  return (value: unknown): value is ReadViewList<T> => record(value)
    && Array.isArray(value.items)
    && value.items.every(item)
    && optionalString(value.warning);
}

const aggregate = (value: unknown, idKey: string): value is Record<string, unknown> => record(value)
  && nonEmptyString(value[idKey])
  && nonEmptyString(value.state)
  && integer(value.revision)
  && record(value.payload);

const caseSummary: Guard<CaseSummary> = (value): value is CaseSummary => record(value)
  && nonEmptyString(value.case_id)
  && nonEmptyString(value.state)
  && integer(value.revision)
  && nullableString(value.title)
  && nullableString(value.updated_at);

const caseDetail: Guard<CaseDetail> = (value): value is CaseDetail => aggregate(value, "case_id")
  && nullableString(value.updated_at)
  && integer(value.event_count)
  && value.event_count >= 0;

const caseEvent: Guard<CaseEvent> = (value): value is CaseEvent => record(value)
  && integer(value.seq)
  && value.seq > 0
  && nonEmptyString(value.event_id)
  && nonEmptyString(value.event_type)
  && nonEmptyString(value.actor)
  && string(value.causation_id)
  && string(value.correlation_id)
  && nullableString(value.trace_id)
  && nullableString(value.occurred_at)
  && record(value.payload)
  && record(value.evidence_refs);

const caseEvents: Guard<CaseEventsView> = (value): value is CaseEventsView => record(value)
  && nonEmptyString(value.case_id)
  && value.aggregate_type === "case"
  && Array.isArray(value.items)
  && value.items.every(caseEvent)
  && record(value.evidence_refs);

const experiment: Guard<Experiment> = (value): value is Experiment => aggregate(value, "experiment_id");

const experimentCell: Guard<ExperimentCell> = (value): value is ExperimentCell => record(value)
  && nullableString(value.cell)
  && nullableInteger(value.arm_order_index)
  && (value.recovery_rate === null || finiteNumber(value.recovery_rate));

function numberRecordOrNull(value: unknown): value is Record<string, number> | null {
  return value === null || (record(value) && Object.values(value).every(finiteNumber));
}

const experimentFull: Guard<ExperimentFull> = (value): value is ExperimentFull => {
  if (!experiment(value)) return false;
  const full = value as unknown as Record<string, unknown>;
  return Array.isArray(full.cells)
    && full.cells.every(experimentCell)
    && numberRecordOrNull(full.deltas)
    && (full.confidence_intervals === null || record(full.confidence_intervals))
    && nullableString(full.verdict)
    && nullableString(full.attributed_layer)
    && nullableString(full.evidence_bundle_ref)
    && nullableString(full.report_ref);
};

const changeset: Guard<ChangeSet> = (value): value is ChangeSet => aggregate(value, "changeset_id");

function gateRef(value: unknown): boolean {
  return value === null || (record(value)
    && optionalString(value.uri)
    && optionalString(value.digest));
}

const workorder: Guard<WorkOrderView> = (value): value is WorkOrderView => record(value)
  && nonEmptyString(value.workorder_id)
  && nonEmptyString(value.changeset_id)
  && nullableString(value.case_id)
  && nonEmptyString(value.hash)
  && nullableString(value.freeze_at)
  && nullableString(value.requester)
  && nonEmptyString(value.channel)
  && nullableString(value.nonce)
  && nonEmptyString(value.state)
  && gateRef(value.gate_report_ref)
  && nullableString(value.target_versionset_digest)
  && nullableString(value.created_at)
  && nullableString(value.projection_warning)
  && (value.workorder_integrity_status === "verified" || value.workorder_integrity_status === "integrity_error")
  && nullableString(value.workorder_integrity_error)
  && (value.gate_integrity_status === "verified" || value.gate_integrity_status === "integrity_error")
  && nullableString(value.gate_integrity_error)
  && nullableString(value.gate_binding_digest)
  && nullableInteger(value.gate_target_revision)
  && nullableString(value.gate_target_versionset_id);

const gate: Guard<GateView> = (value): value is GateView => record(value)
  && nonEmptyString(value.eval_id)
  && nonEmptyString(value.workorder_id)
  && nullableString(value.workorder_hash)
  && nonEmptyString(value.report_id)
  && nonEmptyString(value.rule_track)
  && nonEmptyString(value.judge_track)
  && nonEmptyString(value.deterministic_tests)
  && nonEmptyString(value.live_provider_e2e)
  && nonEmptyString(value.verdict)
  && nonEmptyString(value.report_hash)
  && nullableString(value.binding_digest)
  && nonEmptyString(value.target_versionset_id)
  && integer(value.target_revision)
  && nonEmptyString(value.dataset_id)
  && nonEmptyString(value.dataset_version)
  && nonEmptyString(value.evidence_digest)
  && nonEmptyString(value.status)
  && optionalString(value.integrity_error)
  && (value.binding_status === "VERIFIED" || value.binding_status === "UNBOUND" || value.binding_status === "UNKNOWN")
  && nullableString(value.binding_error)
  && nullableString(value.created_at)
  && (
    (value.binding_status === "VERIFIED"
      && value.status === "completed"
      && value.binding_error === null)
    || (value.binding_status === "UNBOUND"
      && value.status === "unbound"
      && value.binding_error === null)
    || (value.binding_status === "UNKNOWN"
      && value.status === "integrity_error"
      && value.verdict === "error"
      && nonEmptyString(value.binding_error)
      && nonEmptyString(value.integrity_error))
  );

const trustLedger: Guard<TrustLedgerView> = (value): value is TrustLedgerView => record(value)
  && nonEmptyString(value.risk_class)
  && nonEmptyString(value.action_type)
  && integer(value.epoch)
  && integer(value.successes)
  && integer(value.trials)
  && nonEmptyString(value.autonomy_state)
  && finiteNumber(value.LB)
  && finiteNumber(value.UB)
  && typeof value.promotion_eligible === "boolean"
  && nullableString(value.suspended_until)
  && nullableString(value.pending_promotion_ref)
  && nullableString(value.sample_rule)
  && nullableString(value.last_action_ref)
  && nullableString(value.updated_at);

const trustDenial: Guard<TrustDenialView> = (value): value is TrustDenialView => record(value)
  && nonEmptyString(value.audit_id)
  && nullableString(value.ts)
  && nonEmptyString(value.actor)
  && nonEmptyString(value.action)
  && nonEmptyString(value.target)
  && string(value.risk_class)
  && string(value.action_type)
  && nonEmptyString(value.result)
  && string(value.trace_id)
  && nullableString(value.reason)
  && nullableInteger(value.successes)
  && nullableInteger(value.trials)
  && nullableString(value.trust_entry_id);

const releaseSummary: Guard<ReleaseSummary> = (value): value is ReleaseSummary => record(value)
  && nonEmptyString(value.release_id)
  && nonEmptyString(value.state)
  && integer(value.revision);

const releaseDetail: Guard<ReleaseDetail> = (value): value is ReleaseDetail => releaseSummary(value)
  && record((value as unknown as Record<string, unknown>).payload);

const notification: Guard<NotificationView> = (value): value is NotificationView => aggregate(value, "notification_id");

const evidence: Guard<EvidenceView> = (value): value is EvidenceView => record(value)
  && nonEmptyString(value.evidence_id)
  && nonEmptyString(value.source_type)
  && nonEmptyString(value.source_id)
  && nullableString(value.case_id)
  && nonEmptyString(value.kind)
  && nullableString(value.reference)
  && nullableString(value.digest)
  && ["BOUND", "DIGEST_RECORDED", "REFERENCE_RECORDED", "UNKNOWN"].includes(String(value.binding_status))
  && ["recorded", "invalid_digest", "source_integrity_error"].includes(String(value.integrity_status))
  && nullableString(value.integrity_error)
  && value.artifact_status === "UNKNOWN"
  && nullableString(value.recorded_at)
  && nullableString(value.trace_id);

const evidenceResponse: Guard<EvidenceResponse> = (value): value is EvidenceResponse => readListGuard(evidence)(value)
  && (value as unknown as Record<string, unknown>).artifact_store === "unavailable"
  && nonEmptyString((value as unknown as Record<string, unknown>).warning);

const environment: Guard<EnvironmentStatus> = (value): value is EnvironmentStatus => {
  if (!record(value)) return false;
  if (value.demo_app === "unavailable") return true;
  return record(value.demo_app)
    && nonEmptyString(value.demo_app.versionset_id)
    && string(value.demo_app.digest)
    && SHA256_DIGEST.test(value.demo_app.digest)
    && value.demo_app.status === "active"
    && integer(value.demo_app.revision)
    && value.demo_app.revision > 0;
};

const health: Guard<Healthz> = (value): value is Healthz => record(value)
  && nonEmptyString(value.status)
  && nonEmptyString(value.version);

export const guards = {
  health,
  environment,
  caseList: listGuard(caseSummary),
  caseDetail,
  caseEvents,
  experimentList: listGuard(experiment),
  experiment,
  experimentFull,
  changesetList: listGuard(changeset),
  workorderList: readListGuard(workorder),
  gateList: readListGuard(gate),
  trustLedgerList: readListGuard(trustLedger),
  trustDenialList: readListGuard(trustDenial),
  releaseList: listGuard(releaseSummary),
  releaseDetail,
  notificationList: listGuard(notification),
  notification,
  evidenceResponse,
};
