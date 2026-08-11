import type {
  ApplicationCatalogItem,
  ApplicationCatalogListResponse,
  ApplicationRecord,
  CatalogEnvironmentRecord,
  CatalogRecordEnvelope,
  CaseV5Binding,
  CaseV5IssueSnapshot,
  CaseV5Readiness,
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
  SystemComponentRecord,
  DependencyEdgeRecord,
  TrustDenialView,
  TrustLedgerView,
  WorkOrderView,
} from "./types";

export type Guard<T> = (value: unknown) => value is T;

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;
const WORKSPACE_ID = /^ws_[0-9A-Za-z]{8,64}$/;
const PROJECT_ID = /^proj_[0-9A-Za-z]{8,64}$/;
const PRINCIPAL_ID = /^prn_[0-9A-Za-z]{8,64}$/;
const APPLICATION_ID = /^app_[0-9A-Za-z]{8,64}$/;
const ENVIRONMENT_ID = /^env_[0-9A-Za-z]{8,64}$/;
const COMPONENT_ID = /^cmp_[0-9A-Za-z]{8,64}$/;
const EDGE_ID = /^de_[0-9A-Za-z]{8,64}$/;
const AUTHORITY_RECEIPT_ID = /^arec_[0-9A-Za-z]{8,64}$/;
const REQUEST_ID = /^req_[0-9A-Za-z]{8,64}$/;
const AUDIT_REF = /^audit:\/\/aud_[0-9A-Za-z]{8,64}$/;
const CURSOR = /^cur_[0-9A-Za-z_-]{8,512}$/;
const SLUG = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
const LOGICAL_NAME = /^[a-z0-9](?:[a-z0-9_-]{0,127})$/;
const AWARE_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const RECORD_HASH_RULE = "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)";

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function string(value: unknown): value is string {
  return typeof value === "string";
}

function nonEmptyString(value: unknown): value is string {
  return string(value) && value.length > 0;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function uniqueStrings(value: unknown, pattern?: RegExp): value is string[] {
  return Array.isArray(value)
    && value.length > 0
    && value.every((item) => typeof item === "string" && (pattern === undefined || pattern.test(item)))
    && new Set(value).size === value.length;
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

const catalogEnvelope: Guard<CatalogRecordEnvelope> = (value): value is CatalogRecordEnvelope => record(value)
  && exactKeys(value, [
    "schema_version", "workspace_id", "revision", "recorded_by_principal", "recorded_at",
    "immutable", "hash_rule", "record_digest", "authority_receipt_id",
  ])
  && value.schema_version === "2.0"
  && typeof value.workspace_id === "string" && WORKSPACE_ID.test(value.workspace_id)
  && integer(value.revision) && value.revision >= 1
  && typeof value.recorded_by_principal === "string" && PRINCIPAL_ID.test(value.recorded_by_principal)
  && typeof value.recorded_at === "string" && AWARE_DATETIME.test(value.recorded_at)
  && value.immutable === true
  && value.hash_rule === RECORD_HASH_RULE
  && typeof value.record_digest === "string" && SHA256_DIGEST.test(value.record_digest)
  && typeof value.authority_receipt_id === "string" && AUTHORITY_RECEIPT_ID.test(value.authority_receipt_id);

function exactBinding(
  value: unknown,
  kind: "AI_APPLICATION" | "SYSTEM_COMPONENT",
  idPattern: RegExp,
): value is Record<string, unknown> {
  return record(value)
    && exactKeys(value, ["kind", "id", "revision", "digest"])
    && value.kind === kind
    && typeof value.id === "string" && idPattern.test(value.id)
    && integer(value.revision) && value.revision >= 1
    && typeof value.digest === "string" && SHA256_DIGEST.test(value.digest);
}

const applicationRecord: Guard<ApplicationRecord> = (value): value is ApplicationRecord => {
  if (!record(value) || !catalogEnvelope(value.record_envelope)) return false;
  const revision = value.record_envelope.revision;
  const revisionKeys = revision === 1
    ? ["exact_previous_application_binding_or_null"]
    : ["exact_previous_application_binding"];
  const validShape = exactKeys(value, [
    "record_envelope", "application_id", "workspace_id", "project_id", "slug", "display_name",
    "owner_principal_ids", "criticality", "data_classification", "governance_mode", "lifecycle_state",
    ...revisionKeys,
  ]);
  if (!validShape
    || typeof value.application_id !== "string" || !APPLICATION_ID.test(value.application_id)
    || typeof value.workspace_id !== "string" || !WORKSPACE_ID.test(value.workspace_id)
    || value.workspace_id !== value.record_envelope.workspace_id
    || typeof value.project_id !== "string" || !PROJECT_ID.test(value.project_id)
    || typeof value.slug !== "string" || !SLUG.test(value.slug)
    || typeof value.display_name !== "string" || value.display_name.length < 1 || value.display_name.length > 256
    || !uniqueStrings(value.owner_principal_ids, PRINCIPAL_ID)
    || !["P0", "P1", "P2", "P3"].includes(String(value.criticality))
    || !["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"].includes(String(value.data_classification))
    || !["MANAGED", "OBSERVED"].includes(String(value.governance_mode))
    || !["REGISTERED", "ACTIVE", "ARCHIVED"].includes(String(value.lifecycle_state))) return false;
  if (revision === 1) {
    return value.lifecycle_state === "REGISTERED" && value.exact_previous_application_binding_or_null === null;
  }
  return exactBinding(value.exact_previous_application_binding, "AI_APPLICATION", APPLICATION_ID)
    && value.exact_previous_application_binding.id === value.application_id
    && value.exact_previous_application_binding.revision === revision - 1;
};

const environmentRecord: Guard<CatalogEnvironmentRecord> = (value): value is CatalogEnvironmentRecord => record(value)
  && exactKeys(value, [
    "record_envelope", "environment_id", "workspace_id", "application_id", "logical_name",
    "risk_classification", "lifecycle_state",
  ])
  && catalogEnvelope(value.record_envelope)
  && typeof value.environment_id === "string" && ENVIRONMENT_ID.test(value.environment_id)
  && typeof value.workspace_id === "string" && WORKSPACE_ID.test(value.workspace_id)
  && value.workspace_id === value.record_envelope.workspace_id
  && typeof value.application_id === "string" && APPLICATION_ID.test(value.application_id)
  && typeof value.logical_name === "string" && LOGICAL_NAME.test(value.logical_name)
  && ["LOW", "MEDIUM", "HIGH", "CRITICAL"].includes(String(value.risk_classification))
  && ["ACTIVE", "RETIRED"].includes(String(value.lifecycle_state));

const COMPONENT_KINDS = [
  "APPLICATION_CODE", "AGENT", "MODEL_BINDING", "PROMPT", "DATASET", "INDEX", "EMBEDDING",
  "RETRIEVER", "SKILL", "MCP_SERVER", "TOOL_SCHEMA", "POLICY", "MEMORY_POLICY",
  "RUNTIME_PROFILE", "CONNECTOR",
];

const componentRecord: Guard<SystemComponentRecord> = (value): value is SystemComponentRecord => {
  if (!record(value) || !catalogEnvelope(value.record_envelope)) return false;
  const revision = value.record_envelope.revision;
  const revisionKeys = revision === 1
    ? ["exact_previous_system_component_binding_or_null"]
    : ["exact_previous_system_component_binding"];
  if (!exactKeys(value, [
    "record_envelope", "component_id", "workspace_id", "application_id", "component_kind",
    "logical_name", "owner_principal_ids", "criticality", "data_classification",
    "permission_classification", "effect_classification", "dataset_role", "lifecycle_state",
    ...revisionKeys,
  ])
    || typeof value.component_id !== "string" || !COMPONENT_ID.test(value.component_id)
    || typeof value.workspace_id !== "string" || !WORKSPACE_ID.test(value.workspace_id)
    || value.workspace_id !== value.record_envelope.workspace_id
    || typeof value.application_id !== "string" || !APPLICATION_ID.test(value.application_id)
    || !COMPONENT_KINDS.includes(String(value.component_kind))
    || typeof value.logical_name !== "string" || !LOGICAL_NAME.test(value.logical_name)
    || !uniqueStrings(value.owner_principal_ids, PRINCIPAL_ID)
    || !["P0", "P1", "P2", "P3"].includes(String(value.criticality))
    || !["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"].includes(String(value.data_classification))
    || !["READ_ONLY", "READ_WRITE", "ELEVATED"].includes(String(value.permission_classification))
    || !["NONE", "LOCAL", "EXTERNAL"].includes(String(value.effect_classification))
    || !(value.dataset_role === null || ["RUNTIME_DATA", "EVALUATION_DATA", "SEALED_HOLDOUT"].includes(String(value.dataset_role)))
    || !["REGISTERED", "ACTIVE", "DEPRECATED", "RETIRED"].includes(String(value.lifecycle_state))) return false;
  if (revision === 1) {
    return value.lifecycle_state === "REGISTERED" && value.exact_previous_system_component_binding_or_null === null;
  }
  return exactBinding(value.exact_previous_system_component_binding, "SYSTEM_COMPONENT", COMPONENT_ID)
    && value.exact_previous_system_component_binding.id === value.component_id
    && value.exact_previous_system_component_binding.revision === revision - 1;
};

const edgeRecord: Guard<DependencyEdgeRecord> = (value): value is DependencyEdgeRecord => record(value)
  && exactKeys(value, [
    "record_envelope", "edge_id", "workspace_id", "application_id", "from_component_id",
    "to_component_id", "relation", "required", "edge_digest",
  ])
  && catalogEnvelope(value.record_envelope)
  && typeof value.edge_id === "string" && EDGE_ID.test(value.edge_id)
  && typeof value.workspace_id === "string" && WORKSPACE_ID.test(value.workspace_id)
  && value.workspace_id === value.record_envelope.workspace_id
  && typeof value.application_id === "string" && APPLICATION_ID.test(value.application_id)
  && typeof value.from_component_id === "string" && COMPONENT_ID.test(value.from_component_id)
  && typeof value.to_component_id === "string" && COMPONENT_ID.test(value.to_component_id)
  && value.from_component_id !== value.to_component_id
  && ["DEPENDS_ON", "INVOKES", "DATA_FLOW", "CONTAINS", "REFERENCES"].includes(String(value.relation))
  && typeof value.required === "boolean"
  && typeof value.edge_digest === "string" && SHA256_DIGEST.test(value.edge_digest);

const applicationCatalogItem: Guard<ApplicationCatalogItem> = (value): value is ApplicationCatalogItem => {
  if (!record(value)
    || !exactKeys(value, ["application", "environments", "system_components", "dependency_edges"])
    || !applicationRecord(value.application)
    || !Array.isArray(value.environments) || !value.environments.every(environmentRecord)
    || !Array.isArray(value.system_components) || !value.system_components.every(componentRecord)
    || !Array.isArray(value.dependency_edges) || !value.dependency_edges.every(edgeRecord)) return false;
  const workspaceId = value.application.workspace_id;
  const applicationId = value.application.application_id;
  const componentIds = new Set(value.system_components.map((component) => component.component_id));
  const records = [...value.environments, ...value.system_components, ...value.dependency_edges];
  return records.every((item) => item.workspace_id === workspaceId && item.application_id === applicationId)
    && value.dependency_edges.every((edge) => (
      componentIds.has(edge.from_component_id) && componentIds.has(edge.to_component_id)
    ));
};

const applicationCatalogList: Guard<ApplicationCatalogListResponse> = (value): value is ApplicationCatalogListResponse => {
  if (!record(value)
    || !exactKeys(value, ["schema_version", "workspace_id", "request_id", "audit_ref", "items", "next_cursor"])
    || value.schema_version !== "2.0"
    || typeof value.workspace_id !== "string" || !WORKSPACE_ID.test(value.workspace_id)
    || typeof value.request_id !== "string" || !REQUEST_ID.test(value.request_id)
    || typeof value.audit_ref !== "string" || !AUDIT_REF.test(value.audit_ref)
    || !Array.isArray(value.items) || !value.items.every(applicationCatalogItem)
    || !(value.next_cursor === null || (typeof value.next_cursor === "string" && CURSOR.test(value.next_cursor)))) return false;
  return value.items.every((item) => item.application.workspace_id === value.workspace_id)
    && new Set(value.items.map((item) => item.application.application_id)).size === value.items.length;
};

const caseV5Binding: Guard<CaseV5Binding> = (value): value is CaseV5Binding => record(value)
  && nonEmptyString(value.application_case_binding_id)
  && nonEmptyString(value.application_id)
  && nonEmptyString(value.environment_id)
  && record(value.exact_case_binding)
  && nonEmptyString((value.exact_case_binding as Record<string, unknown>).case_id)
  && integer((value.exact_case_binding as Record<string, unknown>).case_revision)
  && SHA256_DIGEST.test(String((value.exact_case_binding as Record<string, unknown>).case_digest))
  && (value.declared_system_version_set_binding_or_unknown === null
    || record(value.declared_system_version_set_binding_or_unknown))
  && SHA256_DIGEST.test(String(value.record_digest));

const caseV5IssueSnapshot: Guard<CaseV5IssueSnapshot> = (value): value is CaseV5IssueSnapshot => record(value)
  && nonEmptyString(value.issue_snapshot_id)
  && nonEmptyString(value.source_kind)
  && nonEmptyString(value.source_url)
  && nonEmptyString(value.external_repo)
  && integer(value.external_issue_number)
  && nullableString(value.title)
  && typeof value.edited_flag === "boolean"
  && typeof value.deleted_flag === "boolean"
  && typeof value.instruction_markers_detected === "boolean"
  && SHA256_DIGEST.test(String(value.snapshot_digest));

const caseV5Readiness: Guard<CaseV5Readiness> = (value): value is CaseV5Readiness => record(value)
  && nonEmptyString(value.case_id)
  && integer(value.case_revision)
  && (value.application_binding === null || caseV5Binding(value.application_binding))
  && (value.binding_integrity_status === "verified"
    || value.binding_integrity_status === "integrity_error")
  && nullableString(value.binding_integrity_error)
  && (value.case_readiness === "NEEDS_ACCEPTANCE_CRITERIA" || value.case_readiness === "READY")
  && integer(value.acceptance_proposal_count)
  && integer(value.confirmed_acceptance_count)
  && Array.isArray(value.missing_evidence)
  && value.missing_evidence.every((item) => typeof item === "string")
  && (value.issue_snapshot === null || caseV5IssueSnapshot(value.issue_snapshot));

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
  applicationCatalogList,
  caseV5Readiness,
};
