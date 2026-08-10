"""Generated frozen CaseLoop public v1 success wire models. Do not hand edit."""

from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


class WireModel(BaseModel):
    """Base for exact public payloads: undeclared fields fail closed."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


T = TypeVar("T")


def _require_unique(values: list[T], field_name: str) -> list[T]:
    fingerprints: list[str] = []
    for value in values:
        if isinstance(value, BaseModel):
            fingerprints.append(value.model_dump_json(exclude_none=False))
        else:
            fingerprints.append(repr(value))
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError(f"{field_name} items must be unique")
    return values


SchemaVersion = Literal["1.0"]
WorkspaceId = Annotated[str, Field(pattern=r"^ws_[0-9A-Za-z]{8,64}$")]
ProjectId = Annotated[str, Field(pattern=r"^proj_[0-9A-Za-z]{8,64}$")]
EnvironmentId = Annotated[str, Field(pattern=r"^env_[0-9A-Za-z]{8,64}$")]
GovernedAgentId = Annotated[str, Field(pattern=r"^ga_[0-9A-Za-z]{8,64}$")]
PrincipalId = Annotated[str, Field(pattern=r"^prn_[0-9A-Za-z]{8,64}$")]
SourceId = Annotated[str, Field(pattern=r"^src_[0-9A-Za-z]{8,64}$")]
SignalId = Annotated[str, Field(pattern=r"^sig_[0-9A-Za-z]{8,64}$")]
CaseId = Annotated[str, Field(pattern=r"^case_[0-9A-Za-z]{8,64}$")]
RequestId = Annotated[str, Field(pattern=r"^req_[0-9A-Za-z]{8,64}$")]
OperationId = Annotated[str, Field(pattern=r"^op_[0-9A-Za-z]{8,64}$")]
TraceEvidenceReceiptId = Annotated[str, Field(pattern=r"^ter_[0-9A-Za-z]{8,64}$")]
AgentRunRefId = Annotated[str, Field(pattern=r"^arr_[0-9A-Za-z]{8,64}$")]
IdempotencyReceiptId = Annotated[str, Field(pattern=r"^idemr_[0-9A-Za-z]{8,64}$")]
AuthorityReceiptId = Annotated[str, Field(pattern=r"^arec_[0-9A-Za-z]{8,64}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
AuditRef = Annotated[str, Field(pattern=r"^audit://aud_[0-9A-Za-z]{8,64}$")]
ArtifactMediaType = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
]
Scope = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_:-]*$")]

SignalKind = Literal[
    "external_feedback",
    "internal_feedback",
    "maintainer_report",
    "monitor_alert",
    "eval_regression",
    "runtime_failure",
    "policy_violation",
    "agent_self_report",
    "scheduled_inspection",
]
EvidenceFieldName = Literal[
    "trace.input", "trace.output", "observations.model", "observations.tools"
]
Completeness = Literal["COMPLETE", "PARTIAL", "UNKNOWN"]


class ArtifactRef(WireModel):
    uri: AnyUrl
    digest: Digest
    media_type: ArtifactMediaType


class Reporter(WireModel):
    kind: Literal["external_user", "maintainer", "monitor", "governed_agent", "system"]
    source_subject_ref: Annotated[str, Field(min_length=1, max_length=256)]


class SignalContent(WireModel):
    summary: Annotated[str, Field(min_length=1, max_length=500)]
    body: Annotated[str, Field(max_length=20_000)] | None
    attachments: Annotated[list[ArtifactRef], Field(max_length=20)]

    @field_validator("attachments")
    @classmethod
    def attachments_are_unique(cls, value: list[ArtifactRef]) -> list[ArtifactRef]:
        return _require_unique(value, "attachments")


class RunLocator(WireModel):
    source_kind: Literal["langfuse", "otel", "phoenix", "custom"]
    project_ref: Annotated[str, Field(min_length=1, max_length=256)]
    trace_id: Annotated[str, Field(min_length=1, max_length=256)]
    session_id: Annotated[str, Field(max_length=256)] | None
    root_observation_id: Annotated[str, Field(max_length=256)] | None


class SignalSubmission(WireModel):
    """POST /api/v1/signals body; authority is deliberately absent."""

    schema_version: SchemaVersion
    source_id: SourceId
    source_event_id: Annotated[str, Field(min_length=1, max_length=512)]
    source_event_version: Annotated[str, Field(min_length=1, max_length=64)]
    signal_kind: SignalKind
    reporter: Reporter
    project_id: ProjectId | None
    environment_id: EnvironmentId | None
    governed_agent_id: GovernedAgentId | None
    occurred_at: AwareDatetime
    content: SignalContent
    run_locator: RunLocator | None
    privacy_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]


class NextAction(WireModel):
    code: Literal[
        "NONE",
        "VIEW_CASE",
        "CORRELATE_TRACE",
        "WAIT_FOR_SOURCE_SYNC",
        "WAIT_FOR_INVESTIGATION",
        "WAIT_FOR_STOP",
        "WAIT_FOR_APPROVAL_EFFECT",
        "WAIT_FOR_EXTERNAL_OPERATION",
        "RECONCILE_UNKNOWN",
    ]
    command: Annotated[str, Field(max_length=256)] | None
    href: AnyUrl | None


class SignalBinding(WireModel):
    signal_id: SignalId
    signal_digest: Digest
    source_event_id: Annotated[str, Field(min_length=1, max_length=512)]
    duplicate_of_signal_id: SignalId | None


class CaseSummary(WireModel):
    case_id: CaseId
    status: Literal["OPEN", "RESOLVED"]
    revision: Annotated[StrictInt, Field(ge=1)]
    disposition: Literal["NEW", "LINKED_EXISTING", "DUPLICATE"]
    correlation_status: Literal["CORRELATED", "PARTIAL", "NEEDS_CORRELATION", "UNKNOWN"]
    triage_status: Literal["UNTRIAGED", "TRIAGED", "NEEDS_INPUT"]


class EvidenceSummary(WireModel):
    status: Completeness
    receipt_id: TraceEvidenceReceiptId
    receipt_digest: Digest
    agent_run_ref_id: AgentRunRefId | None
    missing_fields: list[Annotated[str, Field(min_length=1, max_length=128)]]

    @field_validator("missing_fields")
    @classmethod
    def missing_fields_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "missing_fields")

    @model_validator(mode="after")
    def completeness_matches_missing_fields(self) -> "EvidenceSummary":
        if self.status == "COMPLETE" and self.missing_fields:
            raise ValueError("COMPLETE evidence cannot have missing_fields")
        if self.status in {"PARTIAL", "UNKNOWN"} and not self.missing_fields:
            raise ValueError(f"{self.status} evidence requires missing_fields")
        return self


IdempotencyIntent = Literal["signals.submit"]


class IdempotencyResource(WireModel):
    kind: Literal["signal"]
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*_[0-9A-Za-z]{8,64}$")]


class IdempotencyReceipt(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    principal_id: PrincipalId
    intent: IdempotencyIntent
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]
    request_fingerprint: Digest
    resource: IdempotencyResource
    operation_id: OperationId | None
    request_id: RequestId
    audit_ref: AuditRef
    status: Literal["ACCEPTED", "COMPLETED"]
    response_digest: Digest
    created_at: AwareDatetime
    idempotency_receipt_id: IdempotencyReceiptId
    immutable: StrictBool
    hash_rule: Literal["jcs-rfc8785-v1+sha256(excluding:/receipt_digest)"]
    receipt_digest: Digest

    @model_validator(mode="after")
    def intent_binding_is_exact(self) -> "IdempotencyReceipt":
        if self.immutable is not True:
            raise ValueError("idempotency receipt must be immutable")
        expected: dict[str, tuple[str, str, bool, str]] = {
            "signals.submit": ("signal", "sig_", False, "COMPLETED"),
        }
        kind, prefix, operation_required, status = expected[self.intent]
        if self.resource.kind != kind or not self.resource.id.startswith(prefix):
            raise ValueError("idempotency resource does not match intent owner")
        if (self.operation_id is not None) is not operation_required:
            raise ValueError("idempotency operation_id does not match intent execution mode")
        if self.status != status:
            raise ValueError("idempotency status does not match intent execution mode")
        return self


class IdempotencyDelivery(WireModel):
    receipt: IdempotencyReceipt
    replayed: StrictBool


class SignalSubmissionResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    signal: SignalBinding
    case: CaseSummary
    evidence: EvidenceSummary
    missing_fields: list[Annotated[str, Field(min_length=1, max_length=128)]]
    next_action: NextAction
    idempotency: IdempotencyDelivery

    @field_validator("missing_fields")
    @classmethod
    def response_missing_fields_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "missing_fields")

    @model_validator(mode="after")
    def no_trace_state_is_fail_closed(self) -> "SignalSubmissionResponse":
        if self.missing_fields != self.evidence.missing_fields:
            raise ValueError("response missing_fields must equal evidence missing_fields")
        if self.evidence.agent_run_ref_id is None:
            actual = (
                self.case.status,
                self.case.correlation_status,
                self.case.triage_status,
                self.evidence.status,
            )
            expected = ("OPEN", "NEEDS_CORRELATION", "UNTRIAGED", "UNKNOWN")
            if actual != expected:
                raise ValueError(
                    "no-trace response requires OPEN, NEEDS_CORRELATION, "
                    "UNTRIAGED, and UNKNOWN"
                )
        return self


class CaseData(WireModel):
    case_id: CaseId
    status: Literal["OPEN", "RESOLVED"]
    revision: Annotated[StrictInt, Field(ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    project_id: ProjectId | None
    environment_id: EnvironmentId | None
    governed_agent_id: GovernedAgentId | None
    correlation_status: Literal["CORRELATED", "PARTIAL", "NEEDS_CORRELATION", "UNKNOWN"]
    triage_status: Literal["UNTRIAGED", "TRIAGED", "NEEDS_INPUT"]
    signal_refs: Annotated[list[SignalId], Field(min_length=1)]
    run_refs: list[AgentRunRefId]
    evidence_summary: EvidenceSummary
    input_summary: ArtifactRef | None
    output_summary: ArtifactRef | None
    opened_at: AwareDatetime
    updated_at: AwareDatetime
    resolved_at: AwareDatetime | None
    resolution_ref: ArtifactRef | None
    next_action: NextAction

    @field_validator("signal_refs", "run_refs")
    @classmethod
    def refs_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "reference list")


class CaseResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    data: CaseData


class TimelineEvent(WireModel):
    event_id: Annotated[str, Field(pattern=r"^evt_[0-9A-Za-z]{8,64}$")]
    event_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
    event_version: Literal["1.0"]
    occurred_at: AwareDatetime
    causation_id: Annotated[str, Field(max_length=128)] | None
    correlation_id: Annotated[str, Field(min_length=1, max_length=128)]
    actor_principal_id: PrincipalId
    transaction_id: Annotated[str, Field(pattern=r"^txn_[0-9A-Za-z]{8,64}$")]
    payload_ref: ArtifactRef
    payload_digest: Digest
    redaction_status: Literal["NOT_REQUIRED", "REDACTED", "UNKNOWN"]


class TimelineSnapshot(WireModel):
    watermark_event_id: Annotated[str, Field(pattern=r"^evt_[0-9A-Za-z]{8,64}$")]
    order: Literal["occurred_at,event_id"]
    filter_digest: Digest
    cursor_scope_digest: Digest


class TimelinePage(WireModel):
    limit: Annotated[StrictInt, Field(ge=1, le=200)]
    next_cursor: Annotated[str, Field(pattern=r"^cur_[0-9A-Za-z_-]{8,512}$")] | None
    has_more: StrictBool
    snapshot: TimelineSnapshot


class CaseTimelineData(WireModel):
    case_id: CaseId
    events: list[TimelineEvent]
    page: TimelinePage

    @field_validator("events")
    @classmethod
    def events_are_unique(cls, value: list[TimelineEvent]) -> list[TimelineEvent]:
        return _require_unique(value, "events")


class CaseTimelineResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    data: CaseTimelineData


class TraceQuery(WireModel):
    adapter_kind: Literal["langfuse", "otel", "phoenix", "custom"]
    endpoint_origin: AnyUrl
    source_version: Annotated[str, Field(min_length=1, max_length=128)]
    requested_at: AwareDatetime
    window_start: AwareDatetime
    window_end: AwareDatetime
    filters_digest: Digest


class ObservedEvidenceFieldResult(WireModel):
    name: EvidenceFieldName
    status: Literal["OBSERVED"]


class MissingEvidenceFieldResult(WireModel):
    name: EvidenceFieldName
    status: Literal["MISSING"]
    reason_digest: Digest


EvidenceFieldResult = Annotated[
    ObservedEvidenceFieldResult | MissingEvidenceFieldResult,
    Field(discriminator="status"),
]


class TerminalFailure(WireModel):
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    retryable: StrictBool
    message_digest: Digest


class TraceEvidenceReceipt(WireModel):
    schema_version: SchemaVersion
    receipt_id: TraceEvidenceReceiptId
    workspace_id: WorkspaceId
    source_id: SourceId
    signal_id: SignalId
    signal_digest: Digest
    collection_mode: Literal["SOURCE_QUERY", "NO_LOCATOR"]
    agent_run_ref_id: AgentRunRefId | None
    agent_run_ref_digest: Digest | None
    query: TraceQuery | None
    requested_fields: Annotated[list[EvidenceFieldName], Field(min_length=1)]
    field_results: Annotated[list[EvidenceFieldResult], Field(min_length=1)]
    completeness: Completeness
    artifact_ref: ArtifactRef | None
    source_payload_digest: Digest | None
    collected_at: AwareDatetime
    retention_expires_at: AwareDatetime | None
    deep_link: AnyUrl | None
    failure: TerminalFailure | None
    authority_receipt_id: AuthorityReceiptId
    immutable: StrictBool
    hash_rule: Literal["jcs-rfc8785-v1+sha256(excluding:/receipt_digest)"]
    receipt_digest: Digest

    @field_validator("requested_fields")
    @classmethod
    def requested_fields_are_unique(
        cls, value: list[EvidenceFieldName]
    ) -> list[EvidenceFieldName]:
        return _require_unique(value, "requested_fields")

    @field_validator("field_results")
    @classmethod
    def field_result_names_are_unique(
        cls, value: list[EvidenceFieldResult]
    ) -> list[EvidenceFieldResult]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("field_results names must be unique")
        return value

    @model_validator(mode="after")
    def evidence_relations_are_exact(self) -> "TraceEvidenceReceipt":
        if self.immutable is not True:
            raise ValueError("trace evidence receipt must be immutable")
        result_by_name = {result.name: result for result in self.field_results}
        if set(result_by_name) != set(self.requested_fields):
            raise ValueError("field_results must match requested_fields exactly")
        if (self.agent_run_ref_id is None) != (self.agent_run_ref_digest is None):
            raise ValueError("agent run reference id and digest must be present together")

        statuses = {result.status for result in self.field_results}
        if self.collection_mode == "NO_LOCATOR":
            all_fields = {
                "trace.input",
                "trace.output",
                "observations.model",
                "observations.tools",
            }
            if set(self.requested_fields) != all_fields or statuses != {"MISSING"}:
                raise ValueError("NO_LOCATOR requires all four fields to be MISSING")
            if any(
                value is not None
                for value in (
                    self.agent_run_ref_id,
                    self.agent_run_ref_digest,
                    self.query,
                    self.artifact_ref,
                    self.source_payload_digest,
                    self.deep_link,
                )
            ):
                raise ValueError("NO_LOCATOR cannot invent query, run, artifact, or link data")
            if (
                self.completeness != "UNKNOWN"
                or self.failure is None
                or self.failure.code != "NO_TRACE_LOCATOR"
                or self.failure.retryable
            ):
                raise ValueError("NO_LOCATOR requires UNKNOWN non-retryable NO_TRACE_LOCATOR")
        elif self.query is None:
            raise ValueError("SOURCE_QUERY requires query provenance")

        if self.completeness == "COMPLETE":
            if statuses != {"OBSERVED"} or self.artifact_ref is None or self.failure is not None:
                raise ValueError("COMPLETE evidence requires observed fields and artifact")
        elif self.completeness == "PARTIAL":
            if statuses != {"OBSERVED", "MISSING"} or self.artifact_ref is None or self.failure is not None:
                raise ValueError("PARTIAL evidence requires observed and missing fields plus artifact")
        elif "MISSING" not in statuses or self.failure is None:
            raise ValueError("UNKNOWN evidence requires a missing field and failure")
        return self


class EvidenceData(WireModel):
    receipt_kind: Literal["TRACE_EVIDENCE_RECEIPT"]
    receipt: TraceEvidenceReceipt
    receipt_digest: Digest
    verification_status: Literal["VERIFIED", "FAILED", "NOT_VERIFIED"]
    verified_at: AwareDatetime | None
    superseded_by: TraceEvidenceReceiptId | None

    @model_validator(mode="after")
    def digest_binds_receipt(self) -> "EvidenceData":
        if self.receipt_digest != self.receipt.receipt_digest:
            raise ValueError("receipt_digest must bind the returned receipt")
        return self


class EvidenceResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    data: EvidenceData


class CapabilityPrincipal(WireModel):
    principal_id: PrincipalId
    principal_type: Literal["human", "external_agent", "service", "connector"]
    scopes: list[Scope]
    credential_expires_at: AwareDatetime

    @field_validator("scopes")
    @classmethod
    def scopes_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "scopes")


FrozenIntentName = Literal[
    "signals.submit",
    "cases.get",
    "cases.timeline",
    "evidence.get",
    "capabilities.get",
]


class EnabledIntent(WireModel):
    name: FrozenIntentName
    scope: Scope
    execution_mode: Literal["synchronous", "asynchronous"]
    http: StrictBool
    cli: StrictBool

    @model_validator(mode="after")
    def advertised_transports_are_enabled(self) -> "EnabledIntent":
        if self.http is not True or self.cli is not True:
            raise ValueError("frozen enabled intents require http=true and cli=true")
        return self


class ServerCapabilitiesData(WireModel):
    server_version: Annotated[str, Field(min_length=1, max_length=128)]
    public_api_major: StrictInt
    supported_contract_versions: Annotated[
        list[Annotated[str, Field(pattern=r"^1\.[0-9]+$")]], Field(min_length=1)
    ]
    principal: CapabilityPrincipal
    enabled_intents: list[EnabledIntent]
    generated_at: AwareDatetime

    @field_validator("supported_contract_versions")
    @classmethod
    def versions_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "supported_contract_versions")

    @field_validator("public_api_major")
    @classmethod
    def major_is_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("public_api_major must be 1")
        return value

    @field_validator("enabled_intents")
    @classmethod
    def enabled_intents_are_unique(cls, value: list[EnabledIntent]) -> list[EnabledIntent]:
        return _require_unique(value, "enabled_intents")


class ServerCapabilitiesResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    data: ServerCapabilitiesData


__all__ = [
    "ArtifactRef",
    "CaseResponse",
    "CaseTimelineResponse",
    "EvidenceResponse",
    "EvidenceSummary",
    "IdempotencyDelivery",
    "IdempotencyReceipt",
    "NextAction",
    "ServerCapabilitiesResponse",
    "SignalSubmission",
    "SignalSubmissionResponse",
    "TraceEvidenceReceipt",
]
