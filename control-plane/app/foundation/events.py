"""Closed major-aware event specifications and exact-binding verifiers.

Extracted verbatim from ``app.services.v4_event_store`` during the V5 C2
foundation wave.  This module defines data and verification mechanics only:
no domain service, API, CLI, Console or adapter import is allowed.  Allowed
imports are stdlib, ``app.models`` and ``app.utils``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models import Event, Outbox
from app.utils.v4_integrity import V4IntegrityError, canonical_digest

from app.foundation import graph as _foundation_graph


V4_DOMAIN_EVENT_CHANNEL = "v4.domain.events"
V5_DOMAIN_EVENT_CHANNEL = "v5.domain.events"


class V4EventStoreError(ValueError):
    """Stable validation failure before a v4 event is persisted."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class V4EventIntegrityError(ValueError):
    """A persisted v4 event or its transactional outbox row is not exact."""


@dataclass(frozen=True)
class EventRoute:
    owner: str
    subject_kind: str
    subject_revisioned: bool
    required: frozenset[str]
    subject_id_field: str
    subject_digest_field: str | None
    aggregate_id_field: str


@dataclass(frozen=True)
class V5EventRoute:
    owner: str
    subject_kind: str
    required: frozenset[str]
    self_binding_field: str
    previous_binding_field: str | None = None
    null_previous_binding_field: str | None = None
    lifecycle_state: str | None = None
    self_revision: int | None = None
    previous_revision: int | None = None
    manifest_only_activation: bool = False
    dependent_bindings: tuple[tuple[str, str, int | None], ...] = ()
    dependent_binding_lists: tuple[tuple[str, str, int | None], ...] = ()
    # V5-2A: routes may pin a dedicated outbox channel (e.g. the versioned
    # Work event channel from D-016).  None keeps the shared V5 domain
    # channel, preserving every pre-2A route byte-for-byte.
    channel: str | None = None
    # V5-2A: state-machine aggregates advance one revision per event, so the
    # exact self binding cannot pin a fixed revision.  True requires an
    # integer revision >= 1 of any value (unlike self_revision=None, which
    # requires a null revision).
    dynamic_revision: bool = False


_CONTROLLER_FIELDS = frozenset(
    {
        "subject_kind",
        "subject_id",
        "subject_revision",
        "subject_digest",
        "authority_receipt_id",
    }
)


EXACT_BINDING_FIELDS = frozenset({"kind", "id", "revision", "digest"})
MANIFEST_ACTIVATION_CONTEXT_FIELDS = frozenset(
    {
        "root_intent",
        "workflow_owner",
        "authenticated_request_digest",
        "manifest_digest",
        "idempotency_key",
        "workspace_id",
        "initiating_principal_id",
        "initiating_principal_type",
        "initiating_command_audit_ref",
    }
)


EVENT_ROUTES: dict[tuple[str, str], EventRoute] = {
    ("signal", "signal.received"): EventRoute(
        owner="signal-controller",
        subject_kind="SIGNAL_RECORD",
        subject_revisioned=False,
        required=frozenset(
            {"signal_id", "signal_digest", "source_id", "source_event_id"}
        ),
        subject_id_field="signal_id",
        subject_digest_field="signal_digest",
        aggregate_id_field="signal_id",
    ),
    ("quality_case", "case.opened"): EventRoute(
        owner="case-controller",
        subject_kind="QUALITY_CASE",
        subject_revisioned=True,
        required=frozenset({"case_id", "opening_signal_id"}),
        subject_id_field="case_id",
        subject_digest_field=None,
        aggregate_id_field="case_id",
    ),
    ("signal", "signal_case_link.linked"): EventRoute(
        owner="signal-controller",
        subject_kind="SIGNAL_CASE_LINK",
        subject_revisioned=True,
        required=frozenset({"signal_id", "case_id", "link_digest"}),
        subject_id_field="subject_id",
        subject_digest_field="link_digest",
        aggregate_id_field="signal_id",
    ),
    ("evidence_receipt", "evidence.recorded"): EventRoute(
        owner="evidence-controller",
        subject_kind="TRACE_EVIDENCE_RECEIPT",
        subject_revisioned=False,
        required=frozenset({"receipt_id", "evidence_digest", "completeness"}),
        subject_id_field="receipt_id",
        subject_digest_field="evidence_digest",
        aggregate_id_field="receipt_id",
    ),
    # Pre-R1 V5 construction routes intentionally remain on their original v4
    # envelope until their producers send a frozen major-2 payload.  This is a
    # compatibility boundary, not a reinterpretation of historical rows.
    ("ai_application", "application.registered"): EventRoute(
        owner="application-catalog-controller",
        subject_kind="AI_APPLICATION",
        subject_revisioned=True,
        required=frozenset(
            {"application_id", "project_id", "slug", "lifecycle_state"}
        ),
        subject_id_field="application_id",
        subject_digest_field=None,
        aggregate_id_field="application_id",
    ),
    ("environment", "environment.registered"): EventRoute(
        owner="application-catalog-controller",
        subject_kind="ENVIRONMENT",
        subject_revisioned=True,
        required=frozenset(
            {"environment_id", "application_id", "logical_name", "lifecycle_state"}
        ),
        subject_id_field="environment_id",
        subject_digest_field=None,
        aggregate_id_field="environment_id",
    ),
    ("system_component", "system_component.registered"): EventRoute(
        owner="application-catalog-controller",
        subject_kind="SYSTEM_COMPONENT",
        subject_revisioned=True,
        required=frozenset(
            {
                "component_id",
                "application_id",
                "component_kind",
                "logical_name",
                "lifecycle_state",
            }
        ),
        subject_id_field="component_id",
        subject_digest_field=None,
        aggregate_id_field="component_id",
    ),
    ("dependency_edge", "dependency_edge.recorded"): EventRoute(
        owner="application-catalog-controller",
        subject_kind="DEPENDENCY_EDGE",
        subject_revisioned=False,
        required=frozenset(
            {
                "edge_id",
                "application_id",
                "from_component_id",
                "to_component_id",
                "relation",
                "edge_digest",
            }
        ),
        subject_id_field="edge_id",
        subject_digest_field=None,
        aggregate_id_field="edge_id",
    ),
    # V5-1B version-controller routes.  The business payload mirrors the
    # frozen contracts/v5/events.yaml ``payload_required`` fields flattened to
    # the shared controller fields; ``exact_*_binding`` is a later-slice item.
    ("component_revision", "component_revision.recorded"): EventRoute(
        owner="version-controller",
        subject_kind="COMPONENT_REVISION",
        subject_revisioned=False,
        required=frozenset(
            {
                "component_revision_id",
                "component_id",
                "component_kind",
                "identity_assurance",
                "configuration_digest",
            }
        ),
        subject_id_field="component_revision_id",
        subject_digest_field=None,
        aggregate_id_field="component_revision_id",
    ),
    ("topology_revision", "topology_revision.recorded"): EventRoute(
        owner="version-controller",
        subject_kind="TOPOLOGY_REVISION",
        subject_revisioned=False,
        required=frozenset(
            {"topology_revision_id", "application_id", "topology_digest"}
        ),
        subject_id_field="topology_revision_id",
        subject_digest_field=None,
        aggregate_id_field="topology_revision_id",
    ),
    ("system_version_set", "system_version_set.recorded"): EventRoute(
        owner="version-controller",
        subject_kind="SYSTEM_VERSION_SET",
        subject_revisioned=False,
        required=frozenset(
            {
                "system_version_set_id",
                "application_id",
                "declared_environment_id",
                "version_set_digest",
            }
        ),
        subject_id_field="system_version_set_id",
        subject_digest_field=None,
        aggregate_id_field="system_version_set_id",
    ),
    ("bootstrap_attestation", "bootstrap_attestation.recorded"): EventRoute(
        owner="version-controller",
        subject_kind="BOOTSTRAP_ATTESTATION",
        subject_revisioned=False,
        required=frozenset(
            {
                "bootstrap_attestation_id",
                "application_id",
                "environment_id",
                "attester_principal_id",
                "attester_trust_role",
                "attestation_scope",
            }
        ),
        subject_id_field="bootstrap_attestation_id",
        subject_digest_field=None,
        aggregate_id_field="bootstrap_attestation_id",
    ),
    ("system_assignment", "system_assignment.recorded"): EventRoute(
        owner="version-controller",
        subject_kind="SYSTEM_ASSIGNMENT",
        subject_revisioned=True,
        required=frozenset(
            {
                "assignment_id",
                "application_id",
                "environment_id",
                "generation",
                "exposure",
            }
        ),
        subject_id_field="assignment_id",
        subject_digest_field=None,
        aggregate_id_field="assignment_id",
    ),
    # V5-1C case-controller routes.  The business payload mirrors the frozen
    # contracts/v5/events.yaml ``payload_required`` fields; the exact_*_binding
    # items are the explicit binding objects the events contract demands.  The
    # aggregate id is the subject id (a singleton immutable record, so the
    # subject revision must be null).
    ("application_case_binding", "case.application_bound"): EventRoute(
        owner="case-controller",
        subject_kind="APPLICATION_CASE_BINDING",
        subject_revisioned=False,
        required=frozenset(
            {
                "exact_application_case_binding",
                "exact_case_binding",
                "application_id",
                "environment_id",
                "declared_system_version_set_binding_or_unknown",
            }
        ),
        subject_id_field="subject_id",
        subject_digest_field=None,
        aggregate_id_field="subject_id",
    ),
    ("acceptance_criteria_revision", "acceptance_criteria.proposed"): EventRoute(
        owner="case-controller",
        subject_kind="ACCEPTANCE_CRITERIA_REVISION",
        subject_revisioned=False,
        required=frozenset(
            {
                "exact_acceptance_criteria_revision_binding",
                "exact_case_binding",
                "exact_resolution_contract_binding",
                "confirmation_status",
                "proposer_principal",
                "proposed_at",
                "acceptance_source",
                "expected_behavior",
                "applicable_workload_profile",
                "applicable_deployment_profile",
                "acceptance_digest",
            }
        ),
        subject_id_field="subject_id",
        subject_digest_field=None,
        aggregate_id_field="subject_id",
    ),
    ("acceptance_criteria_revision", "acceptance_criteria.confirmed"): EventRoute(
        owner="case-controller",
        subject_kind="ACCEPTANCE_CRITERIA_REVISION",
        subject_revisioned=False,
        required=frozenset(
            {
                "exact_acceptance_criteria_revision_binding",
                "exact_previous_proposed_revision_binding",
                "exact_case_binding",
                "exact_resolution_contract_binding",
                "confirmation_status",
                "confirmer_principal",
                "confirmed_at",
                "acceptance_source",
                "expected_behavior",
                "applicable_workload_profile",
                "applicable_deployment_profile",
                "acceptance_digest",
            }
        ),
        subject_id_field="subject_id",
        subject_digest_field=None,
        aggregate_id_field="subject_id",
    ),
}


V5_EVENT_ROUTES: dict[tuple[str, str], V5EventRoute] = {
    # V5-2B AutomationRequest owner facts.  The public Operation remains a
    # projection over this request and the linked WorkTask/Attempt.
    ("automation_request", "automation_request.investigation_submitted"): V5EventRoute(
        owner="automation-request-controller",
        subject_kind="AUTOMATION_REQUEST",
        required=frozenset(
            {
                "exact_automation_request_binding",
                "exact_work_task_binding",
                "exact_case_binding",
                "application_id",
                "environment_id",
                "request_digest",
                "budget_digest",
                "requester_principal",
            }
        ),
        self_binding_field="exact_automation_request_binding",
        dependent_bindings=(("exact_work_task_binding", "WORK_TASK", None),),
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("automation_request", "automation_request.stop_requested"): V5EventRoute(
        owner="automation-request-controller",
        subject_kind="AUTOMATION_REQUEST",
        required=frozenset(
            {
                "exact_automation_request_binding",
                "operation_id",
                "reason",
                "requested_by_principal",
            }
        ),
        self_binding_field="exact_automation_request_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("ai_application", "application.registered"): V5EventRoute(
        owner="application-catalog-controller",
        subject_kind="AI_APPLICATION",
        required=frozenset(
            {
                "exact_previous_application_binding_or_null",
                "exact_application_binding",
                "project_id",
                "slug",
                "lifecycle_state",
            }
        ),
        self_binding_field="exact_application_binding",
        null_previous_binding_field="exact_previous_application_binding_or_null",
        lifecycle_state="REGISTERED",
        self_revision=1,
    ),
    ("ai_application", "application.activated"): V5EventRoute(
        owner="application-catalog-controller",
        subject_kind="AI_APPLICATION",
        required=frozenset(
            {
                "exact_previous_application_binding",
                "exact_application_binding",
                "lifecycle_state",
                "manifest_activation_context",
                "initiating_command_audit_ref",
            }
        ),
        self_binding_field="exact_application_binding",
        previous_binding_field="exact_previous_application_binding",
        lifecycle_state="ACTIVE",
        self_revision=2,
        previous_revision=1,
        manifest_only_activation=True,
    ),
    ("system_component", "system_component.registered"): V5EventRoute(
        owner="application-catalog-controller",
        subject_kind="SYSTEM_COMPONENT",
        required=frozenset(
            {
                "exact_previous_system_component_binding_or_null",
                "exact_system_component_binding",
                "application_id",
                "component_kind",
                "logical_name",
                "lifecycle_state",
            }
        ),
        self_binding_field="exact_system_component_binding",
        null_previous_binding_field=(
            "exact_previous_system_component_binding_or_null"
        ),
        lifecycle_state="REGISTERED",
        self_revision=1,
    ),
    ("system_component", "system_component.activated"): V5EventRoute(
        owner="application-catalog-controller",
        subject_kind="SYSTEM_COMPONENT",
        required=frozenset(
            {
                "exact_previous_system_component_binding",
                "exact_system_component_binding",
                "lifecycle_state",
                "manifest_activation_context",
                "initiating_command_audit_ref",
            }
        ),
        self_binding_field="exact_system_component_binding",
        previous_binding_field="exact_previous_system_component_binding",
        lifecycle_state="ACTIVE",
        self_revision=2,
        previous_revision=1,
        manifest_only_activation=True,
    ),
    ("environment", "environment.registered"): V5EventRoute(
        owner="application-catalog-controller",
        subject_kind="ENVIRONMENT",
        required=frozenset(
            {"exact_environment_binding", "application_id", "logical_name", "lifecycle_state"}
        ),
        self_binding_field="exact_environment_binding",
        self_revision=1,
    ),
    ("dependency_edge", "dependency_edge.recorded"): V5EventRoute(
        owner="application-catalog-controller",
        subject_kind="DEPENDENCY_EDGE",
        required=frozenset(
            {"exact_dependency_edge_binding", "application_id", "from_component_id", "to_component_id", "relation", "edge_digest"}
        ),
        self_binding_field="exact_dependency_edge_binding",
        self_revision=1,
    ),
    ("component_revision", "component_revision.recorded"): V5EventRoute(
        owner="version-controller",
        subject_kind="COMPONENT_REVISION",
        required=frozenset(
            {"exact_component_revision_binding", "exact_system_component_binding", "component_kind", "identity_assurance", "configuration_digest"}
        ),
        self_binding_field="exact_component_revision_binding",
        self_revision=1,
        dependent_bindings=(("exact_system_component_binding", "SYSTEM_COMPONENT", 2),),
    ),
    ("topology_revision", "topology_revision.recorded"): V5EventRoute(
        owner="version-controller",
        subject_kind="TOPOLOGY_REVISION",
        required=frozenset(
            {"exact_topology_revision_binding", "application_id", "exact_edge_revision_bindings", "topology_digest"}
        ),
        self_binding_field="exact_topology_revision_binding",
        self_revision=1,
        dependent_binding_lists=(("exact_edge_revision_bindings", "DEPENDENCY_EDGE", 1),),
    ),
    ("system_version_set", "system_version_set.recorded"): V5EventRoute(
        owner="version-controller",
        subject_kind="SYSTEM_VERSION_SET",
        required=frozenset(
            {"exact_system_version_set_binding", "application_id", "declared_environment_id", "exact_component_revision_bindings", "exact_topology_revision_binding", "version_set_digest"}
        ),
        self_binding_field="exact_system_version_set_binding",
        self_revision=1,
        dependent_bindings=(("exact_topology_revision_binding", "TOPOLOGY_REVISION", 1),),
        dependent_binding_lists=(("exact_component_revision_bindings", "COMPONENT_REVISION", 1),),
    ),
    ("bootstrap_attestation", "bootstrap_attestation.recorded"): V5EventRoute(
        owner="version-controller",
        subject_kind="BOOTSTRAP_ATTESTATION",
        required=frozenset(
            {"exact_bootstrap_attestation_binding", "application_id", "environment_id", "exact_initial_system_version_set_binding", "attester_principal_id", "attester_trust_role", "attestation_scope"}
        ),
        self_binding_field="exact_bootstrap_attestation_binding",
        self_revision=1,
        dependent_bindings=(("exact_initial_system_version_set_binding", "SYSTEM_VERSION_SET", 1),),
    ),
    ("system_assignment", "system_assignment.recorded"): V5EventRoute(
        owner="version-controller",
        subject_kind="SYSTEM_ASSIGNMENT",
        required=frozenset(
            {"exact_assignment_binding", "exact_bootstrap_attestation_binding", "exact_initial_system_version_set_binding", "application_id", "environment_id", "generation", "exposure"}
        ),
        self_binding_field="exact_assignment_binding",
        self_revision=1,
        dependent_bindings=(
            ("exact_bootstrap_attestation_binding", "BOOTSTRAP_ATTESTATION", 1),
            ("exact_initial_system_version_set_binding", "SYSTEM_VERSION_SET", 1),
        ),
    ),
    # ---------------------------------------------------------------
    # V5-2A Work Kernel (D-016).  27 events across worker_task / attempt /
    # proposal / proposal_decision, owner and machine semantics reused from
    # V4, envelope major-2, dedicated outbox channel ``v5.work.events`` so the
    # Work dispatcher never shares a consumer lane with the catalog stream.
    # ``self_revision=None`` allows the monotonically increasing aggregate
    # revision that state-machine projection advance produces.
    # ---------------------------------------------------------------
    ("worker_task", "work.requested"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "task_kind", "input_digest", "requester_principal"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.claimed"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "exact_attempt_binding", "worker_principal", "fencing_token", "lease_expires_at"}),
        self_binding_field="exact_work_task_binding",
        dependent_bindings=(("exact_attempt_binding", "WORK_ATTEMPT", None),),
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.heartbeat_recorded"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "attempt_id", "fencing_token", "lease_expires_at"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.retry_scheduled"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "failed_attempt_id", "reason"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.cancel_requested"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "reason", "requested_by_principal"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.cancelled"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "terminal_attempt_id", "cancellation_receipt_digest"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.completed"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "terminal_attempt_id", "accepted_proposal_id_or_null"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.exhausted"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "terminal_attempt_id", "attempts_used", "reason"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.blocked_unknown"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "unknown_attempt_id", "reconciliation_required"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.unknown_reconciled_retry"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "unknown_attempt_id", "reconciliation_receipt_digest"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("worker_task", "work.unknown_reconciled_completed"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_TASK",
        required=frozenset({"exact_work_task_binding", "unknown_attempt_id", "reconciliation_receipt_digest", "accepted_proposal_id_or_null"}),
        self_binding_field="exact_work_task_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.created"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "worker_task_id", "attempt_number", "worker_identity", "fence_token", "claim_event_id", "fallback_of_attempt_id_or_null"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.starting"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "capability_id", "runtime_adapter"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.started"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "runtime_session"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.receipt_recorded"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "receipt_kind", "receipt_digest", "issuer"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.output_recorded"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "output_digest", "stream_complete"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.succeeded"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "output_digest", "terminal_receipt_digest"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.failed"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "failure_code", "terminal_receipt_digest_or_null"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.timed_out"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "timeout_kind"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.cancel_requested"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "reason"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.cancelled"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "cancellation_receipt_digest"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.unknown"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "ambiguity_reason", "reconciliation_required"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.reconciled_succeeded"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "reconciliation_receipt_digest", "output_digest"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("attempt", "attempt.reconciled_failed"): V5EventRoute(
        owner="work-controller",
        subject_kind="WORK_ATTEMPT",
        required=frozenset({"exact_attempt_binding", "reconciliation_receipt_digest", "failure_code"}),
        self_binding_field="exact_attempt_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("proposal", "proposal.submitted"): V5EventRoute(
        owner="proposal-controller",
        subject_kind="WORK_PROPOSAL",
        required=frozenset({"exact_proposal_binding", "proposal_digest", "worker_task_id", "authored_by_principal", "submitted_by_principal", "controlled_action_not_started"}),
        self_binding_field="exact_proposal_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("proposal_decision", "proposal.accepted"): V5EventRoute(
        owner="proposal-controller",
        subject_kind="WORK_PROPOSAL_DECISION",
        required=frozenset({"exact_proposal_decision_binding", "proposal_id", "proposal_digest", "downstream_intent", "downstream_command", "downstream_reaction_id"}),
        self_binding_field="exact_proposal_decision_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
    ("proposal_decision", "proposal.rejected"): V5EventRoute(
        owner="proposal-controller",
        subject_kind="WORK_PROPOSAL_DECISION",
        required=frozenset({"exact_proposal_decision_binding", "proposal_id", "proposal_digest", "reason_code"}),
        self_binding_field="exact_proposal_decision_binding",
        channel="v5.work.events",
        dynamic_revision=True,
    ),
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _validate_route_payload(route: EventRoute, payload: dict[str, Any]) -> str:
    expected_fields = route.required | _CONTROLLER_FIELDS
    if set(payload) != expected_fields:
        raise V4EventStoreError("v4.event_payload_fields_mismatch")
    if payload.get("subject_kind") != route.subject_kind:
        raise V4EventStoreError("v4.event_subject_kind_mismatch")
    expected_subject_id = payload.get(route.subject_id_field)
    if payload.get("subject_id") != expected_subject_id:
        raise V4EventStoreError("v4.event_subject_id_mismatch")
    revision = payload.get("subject_revision")
    if route.subject_revisioned:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise V4EventStoreError("v4.event_subject_revision_invalid")
    elif revision is not None:
        raise V4EventStoreError("v4.event_singleton_revision_must_be_null")
    if route.subject_digest_field is not None and payload.get(
        "subject_digest"
    ) != payload.get(route.subject_digest_field):
        raise V4EventStoreError("v4.event_subject_digest_mismatch")
    try:
        return canonical_digest(payload)
    except V4IntegrityError as exc:
        raise V4EventStoreError("v4.event_payload_integrity_invalid") from exc


def validate_exact_binding(
    value: Any,
    *,
    contract_major: int,
    kind: str | None = None,
    revision: int | None = None,
    allow_null_revision: bool = False,
) -> dict[str, Any]:
    if contract_major != 2:
        raise V4EventStoreError("v5.exact_binding_major_unsupported")
    if not isinstance(value, dict) or set(value) != EXACT_BINDING_FIELDS:
        raise V4EventStoreError("v5.exact_binding_fields_mismatch")
    if kind is not None and value.get("kind") != kind:
        raise V4EventStoreError("v5.exact_binding_kind_mismatch")
    binding_id = value.get("id")
    binding_revision = value.get("revision")
    binding_digest = value.get("digest")
    if not isinstance(binding_id, str) or not binding_id:
        raise V4EventStoreError("v5.exact_binding_id_invalid")
    if allow_null_revision:
        revision_invalid = binding_revision is not None
    else:
        revision_invalid = (
            not isinstance(binding_revision, int)
            or isinstance(binding_revision, bool)
            or binding_revision < 1
            or (revision is not None and binding_revision != revision)
        )
    if revision_invalid:
        raise V4EventStoreError("v5.exact_binding_revision_invalid")
    if (
        not isinstance(binding_digest, str)
        or not binding_digest.startswith("sha256:")
        or len(binding_digest) != 71
        or any(character not in "0123456789abcdef" for character in binding_digest[7:])
    ):
        raise V4EventStoreError("v5.exact_binding_digest_invalid")
    return value


def validate_manifest_activation_context(
    value: Any, *, workspace_id: str
) -> None:
    if not isinstance(value, dict) or set(value) != MANIFEST_ACTIVATION_CONTEXT_FIELDS:
        raise V4EventStoreError("v5.manifest_activation_context_fields_mismatch")
    if value.get("workspace_id") != workspace_id:
        raise V4EventStoreError("v5.manifest_activation_workspace_mismatch")
    if (
        value.get("root_intent") != "system-manifests.import"
        or value.get("workflow_owner") != "manifest_import_coordinator"
    ):
        raise V4EventStoreError("v5.manifest_activation_authority_mismatch")
    string_fields = MANIFEST_ACTIVATION_CONTEXT_FIELDS - {"initiating_principal_type"}
    if not all(isinstance(value.get(field), str) and value[field] for field in string_fields):
        raise V4EventStoreError("v5.manifest_activation_context_invalid")
    if value.get("initiating_principal_type") not in {"human", "service"}:
        raise V4EventStoreError("v5.manifest_activation_principal_type_invalid")


def _validate_v5_route_payload(
    route: V5EventRoute,
    payload: dict[str, Any],
    *,
    workspace_id: str,
) -> tuple[str, dict[str, Any]]:
    """Validate the closed major-2 payload and return digest + exact subject."""

    if set(payload) != route.required:
        raise V4EventStoreError("v5.event_payload_fields_mismatch")
    self_binding = validate_exact_binding(
        payload.get(route.self_binding_field),
        contract_major=2,
        kind=route.subject_kind,
        revision=route.self_revision,
        allow_null_revision=(
            route.self_revision is None and not route.dynamic_revision
        ),
    )
    if route.null_previous_binding_field is not None and payload.get(
        route.null_previous_binding_field
    ) is not None:
        raise V4EventStoreError("v5.event_initial_previous_binding_must_be_null")
    if route.previous_binding_field is not None:
        previous = validate_exact_binding(
            payload.get(route.previous_binding_field),
            contract_major=2,
            kind=route.subject_kind,
            revision=route.previous_revision,
        )
        if (
            previous["id"] != self_binding["id"]
            or self_binding["revision"] != previous["revision"] + 1
            or previous["digest"] == self_binding["digest"]
        ):
            raise V4EventStoreError("v5.event_previous_new_binding_mismatch")
    if (
        route.lifecycle_state is not None
        and payload.get("lifecycle_state") != route.lifecycle_state
    ):
        raise V4EventStoreError("v5.event_lifecycle_state_mismatch")
    if route.manifest_only_activation:
        validate_manifest_activation_context(
            payload.get("manifest_activation_context"),
            workspace_id=workspace_id,
        )
        audit_ref = payload.get("initiating_command_audit_ref")
        if not isinstance(audit_ref, str) or not audit_ref.startswith("audit://aud_"):
            raise V4EventStoreError("v5.event_initiating_command_audit_ref_invalid")
        if payload["manifest_activation_context"].get(
            "initiating_command_audit_ref"
        ) != audit_ref:
            raise V4EventStoreError("v5.event_initiating_command_audit_ref_mismatch")
    for field, kind, revision in route.dependent_bindings:
        validate_exact_binding(
            payload.get(field),
            contract_major=2,
            kind=kind,
            revision=revision,
            allow_null_revision=(
                revision is None and not route.dynamic_revision
            ),
        )
    for field, kind, revision in route.dependent_binding_lists:
        bindings = payload.get(field)
        if not isinstance(bindings, list) or len(
            {canonical_digest(binding) for binding in bindings}
        ) != len(bindings):
            raise V4EventStoreError("v5.event_dependent_binding_list_invalid")
        for binding in bindings:
            validate_exact_binding(
                binding,
                contract_major=2,
                kind=kind,
                revision=revision,
                allow_null_revision=revision is None,
            )
    try:
        return canonical_digest(payload), self_binding
    except V4IntegrityError as exc:
        raise V4EventStoreError("v5.event_payload_integrity_invalid") from exc


def v5_routing_key(exact_subject_binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_major": 2,
        "resource_kind": exact_subject_binding["kind"],
        "subject_id": exact_subject_binding["id"],
    }


def select_v5_route(
    aggregate_type: str, event_type: str, payload: dict[str, Any]
) -> V5EventRoute | None:
    """Activate major 2 only when its frozen self binding is present.

    Activated routes have no legacy variant and are therefore always major 2.
    Registered/recorded routes remain byte-compatible for committed producers
    until those producers opt in by supplying the frozen exact self binding.
    """

    route = V5_EVENT_ROUTES.get((aggregate_type, event_type))
    if route is None:
        return None
    if (aggregate_type, event_type) not in EVENT_ROUTES:
        return route
    return route if route.self_binding_field in payload else None


def validate_v4_event_row(
    row: Event | None,
    *,
    workspace_id: str,
    event_type: str,
    transaction_id: str,
    actor_principal: str,
    subject_kind: str,
    subject_id: str,
    subject_revision: int | None,
    subject_digest: str,
    authority_receipt_id: str,
) -> Event:
    """Recompute a persisted event digest and verify its exact route binding."""

    if row is None:
        raise V4EventIntegrityError("v4.event_missing")
    route = EVENT_ROUTES.get((row.aggregate_type, row.event_type))
    if route is None:
        raise V4EventIntegrityError("v4.event_route_mismatch")
    try:
        payload_digest = _validate_route_payload(route, row.payload or {})
    except V4EventStoreError as exc:
        raise V4EventIntegrityError("v4.event_payload_binding_mismatch") from exc
    payload = row.payload or {}
    if (
        row.contract_version != "v4"
        or row.event_version != "1.0"
        or row.event_contract_major is not None
        or row.routing_key is not None
        or row.exact_subject_binding is not None
        or row.authority_receipt_id is not None
        or row.workspace_id != workspace_id
        or row.event_type != event_type
        or row.aggregate_id != payload.get(route.aggregate_id_field)
        or row.transaction_id != transaction_id
        or row.actor != route.owner
        or row.actor_principal != actor_principal
        or row.trace_id is not None
        or not isinstance(row.seq, int)
        or isinstance(row.seq, bool)
        or row.seq < 1
        or not isinstance(row.causation_id, str)
        or not row.causation_id
        or not isinstance(row.correlation_id, str)
        or not row.correlation_id
        or _as_utc(row.created_at) != _as_utc(row.occurred_at)
        or row.payload_digest != payload_digest
        or payload.get("subject_kind") != subject_kind
        or payload.get("subject_id") != subject_id
        or payload.get("subject_revision") != subject_revision
        or payload.get("subject_digest") != subject_digest
        or payload.get("authority_receipt_id") != authority_receipt_id
    ):
        raise V4EventIntegrityError("v4.event_binding_mismatch")
    return row


def validate_v5_event_row(
    row: Event | None,
    *,
    workspace_id: str,
    event_type: str,
    transaction_id: str,
    actor_principal: str,
    subject_kind: str,
    subject_id: str,
    subject_revision: int,
    subject_digest: str,
    authority_receipt_id: str,
) -> Event:
    """Recompute and verify one persisted frozen major-2 event envelope."""

    if row is None:
        raise V4EventIntegrityError("v5.event_missing")
    route = V5_EVENT_ROUTES.get((row.aggregate_type, row.event_type))
    if route is None:
        raise V4EventIntegrityError("v5.event_route_mismatch")
    try:
        payload_digest, exact_subject = _validate_v5_route_payload(
            route,
            row.payload or {},
            workspace_id=workspace_id,
        )
    except V4EventStoreError as exc:
        raise V4EventIntegrityError("v5.event_payload_binding_mismatch") from exc
    routing_key = v5_routing_key(exact_subject)
    if (
        row.contract_version != "v5"
        or row.event_version != "2.0"
        or row.event_contract_major != 2
        or row.workspace_id != workspace_id
        or row.event_type != event_type
        or row.aggregate_id != exact_subject["id"]
        or row.transaction_id != transaction_id
        or row.actor != route.owner
        or row.actor_principal != actor_principal
        or row.trace_id is not None
        or not isinstance(row.seq, int)
        or isinstance(row.seq, bool)
        or row.seq < 1
        or not isinstance(row.causation_id, str)
        or not row.causation_id
        or not isinstance(row.correlation_id, str)
        or not row.correlation_id
        or _as_utc(row.created_at) != _as_utc(row.occurred_at)
        or row.payload_digest != payload_digest
        or row.routing_key != routing_key
        or row.exact_subject_binding != exact_subject
        or row.authority_receipt_id != authority_receipt_id
        or exact_subject.get("kind") != subject_kind
        or exact_subject.get("id") != subject_id
        or exact_subject.get("revision") != subject_revision
        or exact_subject.get("digest") != subject_digest
    ):
        raise V4EventIntegrityError("v5.event_binding_mismatch")
    return row


def require_exactly_one(items: list[Any], what: str) -> Any:
    """Return the single item of ``items`` or fail with the legacy code.

    Canonical implementation lives in ``app.foundation.graph``; this shim
    preserves the stage-1 legacy error contract (``V4EventIntegrityError``
    carrying ``what`` as the code) for existing callers.
    """
    try:
        return _foundation_graph.require_exactly_one(items, what)
    except _foundation_graph.GraphVerificationError as exc:
        if exc.failure_kind == "cardinality":
            raise V4EventIntegrityError(what) from exc
        raise


def v4_outbox_envelope(event: Event) -> dict[str, Any]:
    """Return the exact immutable outbox envelope derived from one v4 event."""

    return {
        "contract_version": "v4",
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_version": "1.0",
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "occurred_at": _wire_time(event.occurred_at),
        "causation_id": event.causation_id,
        "correlation_id": event.correlation_id,
        "actor_principal": event.actor_principal,
        "transaction_id": event.transaction_id,
        "payload": event.payload,
        "payload_digest": event.payload_digest,
    }


def validate_v4_outbox_row(row: Outbox | None, *, event: Event) -> Outbox:
    """Verify immutable event/outbox bindings without inspecting delivery state."""

    if row is None:
        raise V4EventIntegrityError("v4.outbox_missing")
    envelope = v4_outbox_envelope(event)
    envelope_digest = canonical_digest(envelope)
    if (
        row.contract_version != "v4"
        or row.event_contract_major is not None
        or row.source_event_id != event.event_id
        or row.source_event_seq != event.seq
        or row.channel != V4_DOMAIN_EVENT_CHANNEL
        or row.workspace_id != event.workspace_id
        or row.aggregate_type != event.aggregate_type
        or row.aggregate_id != event.aggregate_id
        or row.event_type != event.event_type
        or row.event_version != event.event_version
        or row.transaction_id != event.transaction_id
        or row.actor_principal != event.actor_principal
        or row.payload != envelope
        or row.payload_digest != envelope_digest
        or _as_utc(row.created_at) != _as_utc(event.occurred_at)
    ):
        raise V4EventIntegrityError("v4.outbox_binding_mismatch")
    return row


def v5_outbox_envelope(event: Event) -> dict[str, Any]:
    """Return the closed required major-2 envelope without current-state rebinding."""

    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_version": event.event_version,
        "event_contract_major": event.event_contract_major,
        "workspace_id": event.workspace_id,
        "transaction_id": event.transaction_id,
        "occurred_at": _wire_time(event.occurred_at),
        "actor_principal": event.actor_principal,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "routing_key": event.routing_key,
        "exact_subject_binding": event.exact_subject_binding,
        "authority_receipt_id": event.authority_receipt_id,
        "payload": event.payload,
        "payload_digest": event.payload_digest,
    }


def validate_v5_outbox_row(
    row: Outbox | None,
    *,
    event: Event,
    expected_channel: str | None = None,
) -> Outbox:
    """Verify a major-2 event and outbox carry the exact same envelope."""

    if row is None:
        raise V4EventIntegrityError("v5.outbox_missing")
    envelope = v5_outbox_envelope(event)
    envelope_digest = canonical_digest(envelope)
    if (
        row.contract_version != "v5"
        or row.event_contract_major != 2
        or row.source_event_id != event.event_id
        or row.source_event_seq != event.seq
        or row.channel != (expected_channel or V5_DOMAIN_EVENT_CHANNEL)
        or row.workspace_id != event.workspace_id
        or row.aggregate_type != event.aggregate_type
        or row.aggregate_id != event.aggregate_id
        or row.event_type != event.event_type
        or row.event_version != event.event_version
        or row.transaction_id != event.transaction_id
        or row.actor_principal != event.actor_principal
        or row.payload != envelope
        or row.payload_digest != envelope_digest
        or _as_utc(row.created_at) != _as_utc(event.occurred_at)
    ):
        raise V4EventIntegrityError("v5.outbox_binding_mismatch")
    return row


__all__ = [
    "V4_DOMAIN_EVENT_CHANNEL",
    "V5_DOMAIN_EVENT_CHANNEL",
    "EXACT_BINDING_FIELDS",
    "MANIFEST_ACTIVATION_CONTEXT_FIELDS",
    "EventRoute",
    "V5EventRoute",
    "EVENT_ROUTES",
    "V5_EVENT_ROUTES",
    "V4EventIntegrityError",
    "V4EventStoreError",
    "validate_exact_binding",
    "validate_manifest_activation_context",
    "select_v5_route",
    "v5_routing_key",
    "validate_v4_event_row",
    "validate_v5_event_row",
    "v4_outbox_envelope",
    "v5_outbox_envelope",
    "validate_v4_outbox_row",
    "validate_v5_outbox_row",
    "require_exactly_one",
]
