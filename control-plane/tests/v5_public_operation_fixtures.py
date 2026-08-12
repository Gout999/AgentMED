"""Shared durable-operation fixtures for SQLite and disposable PostgreSQL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.v4_tables import (
    ControllerRegistration,
    PublicPrincipal,
    QualityCase,
    Signal,
    SignalContent,
    SourceConnection,
)
from app.models.v5_tables import AIApplication, ApplicationCaseBinding, Environment
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.utils.v4_integrity import canonical_digest, record_digest
from app.utils.v5_integrity import v5_record_digest

NOW = datetime(2026, 8, 13, 3, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_01J0000000000P01"
PROJECT = "proj_01J0000000000P01"
APPLICATION = "app_01J0000000000P01"
ENVIRONMENT = "env_01J0000000000P01"
CASE = "case_01J0000000000P01"
SIGNAL = "sig_01J0000000000P01"
PRINCIPAL = "prn_01J0000000000P01"
OTHER_PRINCIPAL = "prn_01J0000000000P02"
CONTROLLER = "prn_01J0000000000P03"
ISSUER = "https://auth.caseloop.dev"
SUBJECT = "v5-2b-operation-agent"
AUDIENCES = ["caseloop-public-api"]
SCOPES = ["investigations:start", "operations:read", "operations:cancel"]

WORK_COMMANDS = [
    "work.request",
    "work.claim",
    "work.heartbeat",
    "work.cancel-request",
    "work.exhaust",
    "attempts.start",
    "attempts.record-receipt",
    "attempts.complete",
    "attempts.fail",
    "attempts.mark-unknown",
    "attempts.cancel",
    "attempts.reconcile",
]
AUTOMATION_COMMANDS = [
    "automation-requests.start-investigation",
    "automation-requests.request-stop",
]


def _claims(
    *,
    principal_type: str,
    project_ids: list[str],
    environment_ids: list[str],
    scopes: list[str],
) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "principal_type": principal_type,
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE,
            "project_ids": project_ids,
            "environment_ids": environment_ids,
            "scopes": scopes,
        }
    )


def principal_context(
    required_scope: str,
    *,
    principal_id: str = PRINCIPAL,
    principal_type: str = "external_agent",
    project_ids: list[str] | None = None,
    environment_ids: list[str] | None = None,
    scopes: list[str] | None = None,
) -> AcceptedPrincipalContext:
    projects = [PROJECT] if project_ids is None else project_ids
    environments = [ENVIRONMENT] if environment_ids is None else environment_ids
    granted = list(SCOPES if scopes is None else scopes)
    return AcceptedPrincipalContext.model_validate(
        {
            "schema_version": "1.0",
            "principal_id": principal_id,
            "principal_type": principal_type,
            "issuer": ISSUER,
            "subject": SUBJECT,
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE,
            "project_ids": projects,
            "environment_ids": environments,
            "scopes": granted,
            "credential_id": "cred_01J0000000000P01",
            "jti_digest": "sha256:" + "1" * 64,
            "issued_at": NOW - timedelta(minutes=5),
            "not_before": NOW - timedelta(minutes=5),
            "expires_at": NOW + timedelta(days=1),
            "revoked_at": None,
            "revocation_checked_at": NOW,
            "requested_context": {
                "workspace_id": WORKSPACE,
                "project_id": projects[0] if projects else None,
                "environment_id": environments[0] if environments else None,
                "required_scope": required_scope,
            },
            "evaluated_at": NOW,
            "claims_digest": _claims(
                principal_type=principal_type,
                project_ids=projects,
                environment_ids=environments,
                scopes=granted,
            ),
        }
    )


def _record_envelope(*, principal_id: str, receipt_id: str) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "workspace_id": WORKSPACE,
        "revision": 1,
        "recorded_by_principal": principal_id,
        "recorded_at": NOW.isoformat().replace("+00:00", "Z"),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
        "record_digest": "",
        "authority_receipt_id": receipt_id,
    }


def _seed_controller(session, *, owner: str, commands: list[str], suffix: str) -> None:
    identity = canonical_digest(
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE,
            "owner": owner,
            "controller_principal": CONTROLLER,
            "principal_type": "CONTROLLER_SERVICE",
            "service": "caseloop-control-plane",
        }
    )
    registration_id = f"creg_01J0000000000P{suffix}"
    audit = V4AuditService(session, clock=lambda: NOW).record(
        workspace_id=WORKSPACE,
        actor_principal=PRINCIPAL,
        action="controllers.register",
        target=registration_id,
        params={"owner": owner, "service_identity_digest": identity},
        transaction_id=f"txn_seed_operation_{suffix}",
        evidence_refs={
            "owner": owner,
            "controller_registration_id": registration_id,
            "controller_principal": CONTROLLER,
        },
        occurred_at=NOW - timedelta(minutes=1),
    )
    built = build_v5_controller_registration_record(
        controller_registration_id=registration_id,
        workspace_id=WORKSPACE,
        owner=owner,
        controller_principal=CONTROLLER,
        allowed_commands=commands,
        service_identity_digest=identity,
        registered_by_human_principal=PRINCIPAL,
        registration_audit_ref=audit.audit_ref,
        valid_from=NOW - timedelta(minutes=2),
        registered_at=NOW - timedelta(minutes=1),
    )
    session.add(ControllerRegistration(**built.row_values))


def seed_public_operation_world(session) -> str:
    """Seed the minimum exact case/catalog/auth/authority slice."""
    from app.public_api.credential_resolver import digest_public_subject

    claims = _claims(
        principal_type="external_agent",
        project_ids=[PROJECT],
        environment_ids=[ENVIRONMENT],
        scopes=list(SCOPES),
    )
    session.add(
        PublicPrincipal(
            principal_id=PRINCIPAL,
            workspace_id=WORKSPACE,
            principal_type="external_agent",
            state="ACTIVE",
            subject_digest=digest_public_subject(SUBJECT),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT],
            environment_ids=[ENVIRONMENT],
            trust_roles=[],
            scopes=list(SCOPES),
            claims_digest=claims,
            revoked_at=None,
        )
    )
    session.add(
        PublicPrincipal(
            principal_id=OTHER_PRINCIPAL,
            workspace_id=WORKSPACE,
            principal_type="external_agent",
            state="ACTIVE",
            subject_digest=digest_public_subject(SUBJECT + "-other"),
            audiences=list(AUDIENCES),
            project_ids=[],
            environment_ids=[],
            trust_roles=[],
            scopes=list(SCOPES),
            claims_digest=_claims(
                principal_type="external_agent",
                project_ids=[],
                environment_ids=[],
                scopes=list(SCOPES),
            ),
            revoked_at=None,
        )
    )
    _seed_controller(
        session, owner="work-controller", commands=WORK_COMMANDS, suffix="11"
    )
    _seed_controller(
        session,
        owner="automation-request-controller",
        commands=AUTOMATION_COMMANDS,
        suffix="12",
    )

    app_payload = {
        "application_id": APPLICATION,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "slug": "operation-fixture",
        "display_name": "Operation Fixture",
        "owner_principal_ids": [PRINCIPAL],
        "criticality": "P2",
        "data_classification": "INTERNAL",
        "governance_mode": "MANAGED",
        "lifecycle_state": "ACTIVE",
        "record_envelope": _record_envelope(
            principal_id=PRINCIPAL, receipt_id="arec_01J0000000000P11"
        ),
    }
    app_digest = v5_record_digest(app_payload)
    app_payload["record_envelope"]["record_digest"] = app_digest
    session.add(
        AIApplication(
            application_id=APPLICATION,
            workspace_id=WORKSPACE,
            project_id=PROJECT,
            slug="operation-fixture",
            display_name="Operation Fixture",
            owner_principal_ids=[PRINCIPAL],
            criticality="P2",
            data_classification="INTERNAL",
            governance_mode="MANAGED",
            lifecycle_state="ACTIVE",
            revision=1,
            envelope_payload=app_payload,
            record_digest=app_digest,
            authority_receipt_id="arec_01J0000000000P11",
            recorded_by_principal=PRINCIPAL,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    env_payload = {
        "environment_id": ENVIRONMENT,
        "workspace_id": WORKSPACE,
        "application_id": APPLICATION,
        "logical_name": "local-shadow",
        "risk_classification": "LOW",
        "lifecycle_state": "ACTIVE",
        "record_envelope": _record_envelope(
            principal_id=PRINCIPAL, receipt_id="arec_01J0000000000P12"
        ),
    }
    env_digest = v5_record_digest(env_payload)
    env_payload["record_envelope"]["record_digest"] = env_digest
    session.add(
        Environment(
            environment_id=ENVIRONMENT,
            workspace_id=WORKSPACE,
            application_id=APPLICATION,
            logical_name="local-shadow",
            risk_classification="LOW",
            lifecycle_state="ACTIVE",
            revision=1,
            envelope_payload=env_payload,
            record_digest=env_digest,
            authority_receipt_id="arec_01J0000000000P12",
            recorded_by_principal=PRINCIPAL,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    source_payload = {
        "workspace_id": WORKSPACE,
        "source_id": "src_01J0000000000P01",
        "kind": "manual",
    }
    session.add(
        SourceConnection(
            source_id=source_payload["source_id"],
            workspace_id=WORKSPACE,
            connector_kind="manual",
            state="ACTIVE",
            credential_ref=None,
            config={},
            connection_digest=canonical_digest(source_payload),
            revision=1,
            created_by_principal=PRINCIPAL,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    content_payload = {"summary": "operation fixture signal"}
    content_digest = canonical_digest(content_payload)
    session.add(
        SignalContent(
            signal_content_id="sc_01J0000000000P01",
            workspace_id=WORKSPACE,
            uri="inline://v5-2b-operation-fixture",
            media_type="application/json",
            content_digest=content_digest,
            content_payload=content_payload,
            privacy_classification="INTERNAL",
            redaction_status="NOT_REQUIRED",
            raw_content_persisted=False,
            retention_expires_at=None,
            created_at=NOW,
        )
    )
    # Flush each legacy FK layer explicitly: these models intentionally have
    # no ORM relationships, so PostgreSQL must not depend on unit-of-work
    # insertion heuristics to discover the source/content -> signal order.
    session.flush()
    signal_payload = {"signal_id": SIGNAL, "workspace_id": WORKSPACE}
    signal_digest = canonical_digest(signal_payload)
    session.add(
        Signal(
            signal_id=SIGNAL,
            workspace_id=WORKSPACE,
            project_id=PROJECT,
            environment_id=ENVIRONMENT,
            governed_agent_id=None,
            source_id=source_payload["source_id"],
            source_event_id="fixture-v5-2b-operation",
            source_event_version="1",
            source_payload_digest=content_digest,
            adapter_kind="manual",
            provider_origin="https://caseloop.local",
            signal_kind="quality_report",
            reporter_kind="external_agent",
            reporter_ref=PRINCIPAL,
            occurred_at=NOW,
            observed_at=NOW,
            signal_content_id="sc_01J0000000000P01",
            content_ref={"uri": "inline://v5-2b-operation-fixture", "digest": content_digest},
            agent_run_ref_id=None,
            privacy={"classification": "INTERNAL"},
            completeness="COMPLETE",
            missing_fields=[],
            untrusted_content=True,
            envelope_payload=signal_payload,
            signal_digest=signal_digest,
            authority_receipt_id="arec_01J0000000000P13",
            created_at=NOW,
        )
    )
    session.flush()
    case_payload = {
        "schema_version": "1.0",
        "case_id": CASE,
        "workspace_id": WORKSPACE,
        "revision": 1,
        "status": "OPEN",
        "title": "Operation fixture case",
        "project_id": PROJECT,
        "environment_id": ENVIRONMENT,
        "governed_agent_id": None,
        "correlation_status": "CORRELATED",
        "triage_status": "TRIAGED",
        "opening_signal_id": SIGNAL,
        "authority_receipt_id": "arec_01J0000000000P14",
        "opened_at": NOW.isoformat().replace("+00:00", "Z"),
        "updated_at": NOW.isoformat().replace("+00:00", "Z"),
        "resolved_at": None,
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_digest)",
        "record_digest": "",
    }
    case_digest = record_digest(case_payload, self_digest_field="record_digest")
    case_payload["record_digest"] = case_digest
    session.add(
        QualityCase(
            case_id=CASE,
            workspace_id=WORKSPACE,
            state="OPEN",
            revision=1,
            title="Operation fixture case",
            project_id=PROJECT,
            environment_id=ENVIRONMENT,
            governed_agent_id=None,
            correlation_status="CORRELATED",
            triage_status="TRIAGED",
            opening_signal_id=SIGNAL,
            snapshot_payload=case_payload,
            record_digest=case_digest,
            authority_receipt_id="arec_01J0000000000P14",
            opened_at=NOW,
            updated_at=NOW,
            resolved_at=None,
        )
    )
    session.flush()
    binding_payload = {
        "schema_version": "2.0",
        "application_case_binding_id": "acb_01J0000000000P01",
        "workspace_id": WORKSPACE,
        "exact_case_binding": {
            "case_id": CASE,
            "case_revision": 1,
            "case_digest": case_digest,
        },
        "application_id": APPLICATION,
        "environment_id": ENVIRONMENT,
        "declared_system_version_set_binding_or_unknown": None,
        "binding_digest": canonical_digest(
            {
                "case_id": CASE,
                "case_revision": 1,
                "case_digest": case_digest,
                "application_id": APPLICATION,
                "environment_id": ENVIRONMENT,
            }
        ),
        "record_envelope": _record_envelope(
            principal_id=PRINCIPAL, receipt_id="arec_01J0000000000P15"
        ),
    }
    binding_record_digest = v5_record_digest(binding_payload)
    binding_payload["record_envelope"]["record_digest"] = binding_record_digest
    session.add(
        ApplicationCaseBinding(
            application_case_binding_id=binding_payload[
                "application_case_binding_id"
            ],
            workspace_id=WORKSPACE,
            case_id=CASE,
            case_revision=1,
            case_digest=case_digest,
            application_id=APPLICATION,
            environment_id=ENVIRONMENT,
            declared_system_version_set_binding_or_unknown=None,
            binding_digest=binding_payload["binding_digest"],
            envelope_payload=binding_payload,
            record_digest=binding_record_digest,
            authority_receipt_id="arec_01J0000000000P15",
            recorded_by_principal=PRINCIPAL,
            created_at=NOW,
        )
    )
    session.commit()
    return case_digest
