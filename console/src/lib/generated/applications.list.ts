/**
 * GENERATED FILE — DO NOT EDIT BY HAND.
 *
 * Produced by the contracts/compiler activated-operation compiler from the
 * frozen C1 inputs contracts/v5/intent-registry.yaml and
 * contracts/v5/schemas/applications.list.schema.json (+ referenced schemas).
 *
 * This is a static copy consumed by the Console. It must stay byte-identical
 * to contracts/v5/generated/ts/applications.list.ts: re-run the compiler emit
 * (`cd contracts/compiler && python3 -m compiler emit`) after any C1
 * regeneration and re-sync this copy. Compiler output is deterministic (no
 * timestamps, no absolute paths); two runs produce identical bytes.
 */

/**
 * Guard for the activated applications.list response, translated from
 * contracts/v5/schemas/applications.list.schema.json#/$defs/response and its
 * references (common.schema.json, records.schema.json) with JSON Schema
 * 2020-12 semantics: closed objects (additionalProperties: false), exact key
 * sets, pattern/format/enum/min-max constraints, the revision-binding oneOf
 * and the lifecycle/binding allOf if/then branches. Cross-record consistency
 * rules that are not part of the schema are intentionally absent; the Console
 * shadow layer compares this guard against the legacy validator and falls back
 * to the legacy result on disagreement.
 */

type RecordValue = Record<string, unknown>;

export interface RecordEnvelope {
  schema_version: "2.0";
  workspace_id: string;
  revision: number;
  recorded_by_principal: string;
  recorded_at: string;
  immutable: true;
  hash_rule: "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)";
  record_digest: string;
  authority_receipt_id: string;
}

export interface ExactApplicationBinding {
  kind: "AI_APPLICATION";
  id: string;
  revision: number;
  digest: string;
}

export interface ExactSystemComponentBinding {
  kind: "SYSTEM_COMPONENT";
  id: string;
  revision: number;
  digest: string;
}

interface ApplicationRecordBase {
  record_envelope: RecordEnvelope;
  application_id: string;
  workspace_id: string;
  project_id: string;
  slug: string;
  display_name: string;
  owner_principal_ids: string[];
  criticality: "P0" | "P1" | "P2" | "P3";
  data_classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
  governance_mode: "MANAGED" | "OBSERVED";
  lifecycle_state: "REGISTERED" | "ACTIVE" | "ARCHIVED";
}

/** Revision-1 record carries the null binding and lifecycle REGISTERED; revision >= 2 carries the exact binding and lifecycle ACTIVE/ARCHIVED (records.schema.json applicationRecord oneOf + allOf). */
export type ApplicationRecord =
  | (ApplicationRecordBase & { exact_previous_application_binding_or_null: null })
  | (ApplicationRecordBase & { exact_previous_application_binding: ExactApplicationBinding });

export interface EnvironmentRecord {
  record_envelope: RecordEnvelope;
  environment_id: string;
  workspace_id: string;
  application_id: string;
  logical_name: string;
  risk_classification: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  lifecycle_state: "ACTIVE" | "RETIRED";
}

interface SystemComponentRecordBase {
  record_envelope: RecordEnvelope;
  component_id: string;
  workspace_id: string;
  application_id: string;
  component_kind:
    | "APPLICATION_CODE" | "AGENT" | "MODEL_BINDING" | "PROMPT" | "DATASET"
    | "INDEX" | "EMBEDDING" | "RETRIEVER" | "SKILL" | "MCP_SERVER"
    | "TOOL_SCHEMA" | "POLICY" | "MEMORY_POLICY" | "RUNTIME_PROFILE" | "CONNECTOR";
  logical_name: string;
  owner_principal_ids: string[];
  criticality: "P0" | "P1" | "P2" | "P3";
  data_classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
  permission_classification: "READ_ONLY" | "READ_WRITE" | "ELEVATED";
  effect_classification: "NONE" | "LOCAL" | "EXTERNAL";
  dataset_role: "RUNTIME_DATA" | "EVALUATION_DATA" | "SEALED_HOLDOUT" | null;
  lifecycle_state: "REGISTERED" | "ACTIVE" | "DEPRECATED" | "RETIRED";
}

/** Revision-1 record carries the null binding and lifecycle REGISTERED; revision >= 2 carries the exact binding and lifecycle ACTIVE/DEPRECATED/RETIRED (records.schema.json componentRecord oneOf + allOf). */
export type SystemComponentRecord =
  | (SystemComponentRecordBase & { exact_previous_system_component_binding_or_null: null })
  | (SystemComponentRecordBase & { exact_previous_system_component_binding: ExactSystemComponentBinding });

export interface DependencyEdgeRecord {
  record_envelope: RecordEnvelope;
  edge_id: string;
  workspace_id: string;
  application_id: string;
  from_component_id: string;
  to_component_id: string;
  relation: "DEPENDS_ON" | "INVOKES" | "DATA_FLOW" | "CONTAINS" | "REFERENCES";
  required: boolean;
  edge_digest: string;
}

export interface ApplicationListItem {
  application: ApplicationRecord;
  environments: EnvironmentRecord[];
  system_components: SystemComponentRecord[];
  dependency_edges: DependencyEdgeRecord[];
}

export interface ApplicationsListResponse {
  schema_version: "2.0";
  workspace_id: string;
  request_id: string;
  audit_ref: string;
  items: ApplicationListItem[];
  next_cursor: string | null;
}

const PATTERNS = {
  workspaceId: /^ws_[0-9A-Za-z]{8,64}$/,
  projectId: /^proj_[0-9A-Za-z]{8,64}$/,
  principalId: /^prn_[0-9A-Za-z]{8,64}$/,
  requestId: /^req_[0-9A-Za-z]{8,64}$/,
  authorityReceiptId: /^arec_[0-9A-Za-z]{8,64}$/,
  applicationId: /^app_[0-9A-Za-z]{8,64}$/,
  environmentId: /^env_[0-9A-Za-z]{8,64}$/,
  componentId: /^cmp_[0-9A-Za-z]{8,64}$/,
  edgeId: /^de_[0-9A-Za-z]{8,64}$/,
  digest: /^sha256:[0-9a-f]{64}$/,
  auditRef: /^audit:\/\/aud_[0-9A-Za-z]{8,64}$/,
  cursor: /^cur_[0-9A-Za-z_-]{8,512}$/,
  slug: /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/,
  logicalName: /^[a-z0-9](?:[a-z0-9_-]{0,127})$/,
  recordedAt: /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/,
} as const;

const ENUMS = {
  criticality: ["P0", "P1", "P2", "P3"],
  dataClassification: ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"],
  governanceMode: ["MANAGED", "OBSERVED"],
  lifecycleState: ["REGISTERED", "ACTIVE", "ARCHIVED"],
  environmentLifecycleState: ["ACTIVE", "RETIRED"],
  componentLifecycleState: ["REGISTERED", "ACTIVE", "DEPRECATED", "RETIRED"],
  riskClassification: ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
  permissionClassification: ["READ_ONLY", "READ_WRITE", "ELEVATED"],
  effectClassification: ["NONE", "LOCAL", "EXTERNAL"],
  componentKind: [
    "APPLICATION_CODE", "AGENT", "MODEL_BINDING", "PROMPT", "DATASET",
    "INDEX", "EMBEDDING", "RETRIEVER", "SKILL", "MCP_SERVER",
    "TOOL_SCHEMA", "POLICY", "MEMORY_POLICY", "RUNTIME_PROFILE", "CONNECTOR",
  ],
  datasetRole: ["RUNTIME_DATA", "EVALUATION_DATA", "SEALED_HOLDOUT"],
  dependencyRelation: ["DEPENDS_ON", "INVOKES", "DATA_FLOW", "CONTAINS", "REFERENCES"],
} as const;

function isRecord(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: RecordValue, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function isOneOf<T extends readonly string[]>(value: unknown, options: T): value is T[number] {
  return typeof value === "string" && (options as readonly string[]).includes(value);
}

function isDigest(value: unknown): boolean {
  return typeof value === "string" && PATTERNS.digest.test(value);
}

function isOwnerPrincipalIds(value: unknown): value is string[] {
  return Array.isArray(value)
    && value.length >= 1
    && value.length <= 32
    && value.every((item) => typeof item === "string" && PATTERNS.principalId.test(item))
    && new Set(value).size === value.length;
}

function isRecordEnvelope(value: unknown): value is RecordEnvelope {
  return isRecord(value)
    && hasOnlyKeys(value, [
      "schema_version", "workspace_id", "revision", "recorded_by_principal", "recorded_at",
      "immutable", "hash_rule", "record_digest", "authority_receipt_id",
    ])
    && value.schema_version === "2.0"
    && typeof value.workspace_id === "string" && PATTERNS.workspaceId.test(value.workspace_id)
    && Number.isInteger(value.revision) && (value.revision as number) >= 1
    && typeof value.recorded_by_principal === "string" && PATTERNS.principalId.test(value.recorded_by_principal)
    && typeof value.recorded_at === "string" && PATTERNS.recordedAt.test(value.recorded_at)
    && value.immutable === true
    && value.hash_rule === "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"
    && isDigest(value.record_digest)
    && typeof value.authority_receipt_id === "string" && PATTERNS.authorityReceiptId.test(value.authority_receipt_id);
}

function isExactApplicationBinding(value: unknown): value is ExactApplicationBinding {
  return isRecord(value)
    && hasOnlyKeys(value, ["kind", "id", "revision", "digest"])
    && value.kind === "AI_APPLICATION"
    && typeof value.id === "string" && PATTERNS.applicationId.test(value.id)
    && Number.isInteger(value.revision) && (value.revision as number) >= 1
    && isDigest(value.digest);
}

function isExactSystemComponentBinding(value: unknown): value is ExactSystemComponentBinding {
  return isRecord(value)
    && hasOnlyKeys(value, ["kind", "id", "revision", "digest"])
    && value.kind === "SYSTEM_COMPONENT"
    && typeof value.id === "string" && PATTERNS.componentId.test(value.id)
    && Number.isInteger(value.revision) && (value.revision as number) >= 1
    && isDigest(value.digest);
}

function isApplicationRecord(value: unknown): value is ApplicationRecord {
  if (!isRecord(value)) return false;
  const hasOrNull = "exact_previous_application_binding_or_null" in value;
  const hasExact = "exact_previous_application_binding" in value;
  if (hasOrNull === hasExact) return false; // oneOf: exactly one binding key
  if (!hasOnlyKeys(value, [
    "record_envelope", "application_id", "workspace_id", "project_id", "slug", "display_name",
    "owner_principal_ids", "criticality", "data_classification", "governance_mode",
    "lifecycle_state",
    hasOrNull ? "exact_previous_application_binding_or_null" : "exact_previous_application_binding",
  ])) return false;
  if (!isRecordEnvelope(value.record_envelope)) return false;
  if (typeof value.application_id !== "string" || !PATTERNS.applicationId.test(value.application_id)) return false;
  if (typeof value.workspace_id !== "string" || !PATTERNS.workspaceId.test(value.workspace_id)) return false;
  if (typeof value.project_id !== "string" || !PATTERNS.projectId.test(value.project_id)) return false;
  if (typeof value.slug !== "string" || value.slug.length < 1 || value.slug.length > 64 || !PATTERNS.slug.test(value.slug)) return false;
  if (typeof value.display_name !== "string" || value.display_name.length < 1 || value.display_name.length > 256) return false;
  if (!isOwnerPrincipalIds(value.owner_principal_ids)) return false;
  if (!isOneOf(value.criticality, ENUMS.criticality)) return false;
  if (!isOneOf(value.data_classification, ENUMS.dataClassification)) return false;
  if (!isOneOf(value.governance_mode, ENUMS.governanceMode)) return false;
  if (!isOneOf(value.lifecycle_state, ENUMS.lifecycleState)) return false;
  if (hasOrNull) {
    // allOf if: null binding present → lifecycle REGISTERED and the null key is literally null
    if (value.exact_previous_application_binding_or_null !== null) return false;
    return value.lifecycle_state === "REGISTERED";
  }
  if (!isExactApplicationBinding(value.exact_previous_application_binding)) return false;
  return value.lifecycle_state === "ACTIVE" || value.lifecycle_state === "ARCHIVED";
}

function isEnvironmentRecord(value: unknown): value is EnvironmentRecord {
  return isRecord(value)
    && hasOnlyKeys(value, [
      "record_envelope", "environment_id", "workspace_id", "application_id", "logical_name",
      "risk_classification", "lifecycle_state",
    ])
    && isRecordEnvelope(value.record_envelope)
    && typeof value.environment_id === "string" && PATTERNS.environmentId.test(value.environment_id)
    && typeof value.workspace_id === "string" && PATTERNS.workspaceId.test(value.workspace_id)
    && typeof value.application_id === "string" && PATTERNS.applicationId.test(value.application_id)
    && typeof value.logical_name === "string" && value.logical_name.length >= 1 && value.logical_name.length <= 128 && PATTERNS.logicalName.test(value.logical_name)
    && isOneOf(value.risk_classification, ENUMS.riskClassification)
    && isOneOf(value.lifecycle_state, ENUMS.environmentLifecycleState);
}

function isSystemComponentRecord(value: unknown): value is SystemComponentRecord {
  if (!isRecord(value)) return false;
  const hasOrNull = "exact_previous_system_component_binding_or_null" in value;
  const hasExact = "exact_previous_system_component_binding" in value;
  if (hasOrNull === hasExact) return false; // oneOf: exactly one binding key
  if (!hasOnlyKeys(value, [
    "record_envelope", "component_id", "workspace_id", "application_id", "component_kind",
    "logical_name", "owner_principal_ids", "criticality", "data_classification",
    "permission_classification", "effect_classification", "dataset_role", "lifecycle_state",
    hasOrNull ? "exact_previous_system_component_binding_or_null" : "exact_previous_system_component_binding",
  ])) return false;
  if (!isRecordEnvelope(value.record_envelope)) return false;
  if (typeof value.component_id !== "string" || !PATTERNS.componentId.test(value.component_id)) return false;
  if (typeof value.workspace_id !== "string" || !PATTERNS.workspaceId.test(value.workspace_id)) return false;
  if (typeof value.application_id !== "string" || !PATTERNS.applicationId.test(value.application_id)) return false;
  if (!isOneOf(value.component_kind, ENUMS.componentKind)) return false;
  if (typeof value.logical_name !== "string" || value.logical_name.length < 1 || value.logical_name.length > 128 || !PATTERNS.logicalName.test(value.logical_name)) return false;
  if (!isOwnerPrincipalIds(value.owner_principal_ids)) return false;
  if (!isOneOf(value.criticality, ENUMS.criticality)) return false;
  if (!isOneOf(value.data_classification, ENUMS.dataClassification)) return false;
  if (!isOneOf(value.permission_classification, ENUMS.permissionClassification)) return false;
  if (!isOneOf(value.effect_classification, ENUMS.effectClassification)) return false;
  if (value.dataset_role !== null && !isOneOf(value.dataset_role, ENUMS.datasetRole)) return false;
  if (!isOneOf(value.lifecycle_state, ENUMS.componentLifecycleState)) return false;
  if (hasOrNull) {
    // allOf if: null binding present → lifecycle REGISTERED and the null key is literally null
    if (value.exact_previous_system_component_binding_or_null !== null) return false;
    return value.lifecycle_state === "REGISTERED";
  }
  if (!isExactSystemComponentBinding(value.exact_previous_system_component_binding)) return false;
  return value.lifecycle_state === "ACTIVE"
    || value.lifecycle_state === "DEPRECATED"
    || value.lifecycle_state === "RETIRED";
}

function isDependencyEdgeRecord(value: unknown): value is DependencyEdgeRecord {
  return isRecord(value)
    && hasOnlyKeys(value, [
      "record_envelope", "edge_id", "workspace_id", "application_id", "from_component_id",
      "to_component_id", "relation", "required", "edge_digest",
    ])
    && isRecordEnvelope(value.record_envelope)
    && typeof value.edge_id === "string" && PATTERNS.edgeId.test(value.edge_id)
    && typeof value.workspace_id === "string" && PATTERNS.workspaceId.test(value.workspace_id)
    && typeof value.application_id === "string" && PATTERNS.applicationId.test(value.application_id)
    && typeof value.from_component_id === "string" && PATTERNS.componentId.test(value.from_component_id)
    && typeof value.to_component_id === "string" && PATTERNS.componentId.test(value.to_component_id)
    && isOneOf(value.relation, ENUMS.dependencyRelation)
    && typeof value.required === "boolean"
    && isDigest(value.edge_digest);
}

function isApplicationListItem(value: unknown): value is ApplicationListItem {
  return isRecord(value)
    && hasOnlyKeys(value, ["application", "environments", "system_components", "dependency_edges"])
    && isApplicationRecord(value.application)
    && Array.isArray(value.environments) && value.environments.every(isEnvironmentRecord)
    && Array.isArray(value.system_components) && value.system_components.every(isSystemComponentRecord)
    && Array.isArray(value.dependency_edges) && value.dependency_edges.every(isDependencyEdgeRecord);
}

/** applications.list response guard (applications.list.schema.json#/$defs/response). */
export function applicationsListGuard(value: unknown): value is ApplicationsListResponse {
  return isRecord(value)
    && hasOnlyKeys(value, ["schema_version", "workspace_id", "request_id", "audit_ref", "items", "next_cursor"])
    && value.schema_version === "2.0"
    && typeof value.workspace_id === "string" && PATTERNS.workspaceId.test(value.workspace_id)
    && typeof value.request_id === "string" && PATTERNS.requestId.test(value.request_id)
    && typeof value.audit_ref === "string" && PATTERNS.auditRef.test(value.audit_ref)
    && Array.isArray(value.items) && value.items.every(isApplicationListItem)
    && (value.next_cursor === null
      || (typeof value.next_cursor === "string" && PATTERNS.cursor.test(value.next_cursor)));
}
