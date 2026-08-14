"""Focused tests for the supported V5-1A/B/C local bootstrap entry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from app.bootstrap.v5_catalog_local import (
    BootstrapError,
    V5CatalogLocalBootstrapRequest,
    V5OperatorEnvironmentRotationRequest,
    V5OwnerReauthenticationRequest,
    execute_v5_catalog_local_bootstrap,
    execute_v5_operator_environment_rotation,
    execute_v5_owner_reauthentication,
    main,
)
from app.config import Settings
from app.models import Audit
from app.models.v4_tables import (
    ControllerRegistration,
    PublicCredential,
    PublicPrincipal,
    QualityCase,
    SourceConnection,
)
from app.models.v5_tables import AcceptanceCriteriaRevision
from app.models.v5_tables import AIApplication, ApplicationCaseBinding, Environment
from app.public_api.credential_resolver import (
    CredentialResolutionError,
    PublicCredentialResolver,
)
from app.public_api.models import SignalSubmission
from app.public_api.v5_models import (
    AcceptanceCriteriaConfirmRequest,
    AcceptanceCriteriaProposeRequest,
    CaseBindApplicationRequest,
    SystemManifestImportRequest,
)
from app.services.acceptance import AcceptanceService
from app.services.case_binding import CaseBindingService
from app.services.signal_intake import SignalIntakeService
from app.services.system_versions import SystemVersionsService
from app.services.v4_audit import V4AuditService


REPO = Path(__file__).resolve().parents[3]
CONTRACTS = REPO / "contracts" / "v5"
NOW = datetime(2026, 8, 11, 16, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_01J0000000000H01"
PROJECT = "proj_01J0000000000H01"
OWNER = "prn_01J0000000000H01"
OPERATOR = "prn_01J0000000000H02"
BEARER = "v5-first-case-local-bearer-material-000000000001"
JTI = "v5-first-case-local-jti-0001"
IMPORT_AT = NOW + timedelta(seconds=10)
ROTATION_ISSUED_AT = NOW + timedelta(seconds=20)
ROTATION_NOW = NOW + timedelta(seconds=30)
ROTATED_BEARER = "v5-first-case-rotated-bearer-material-000000000001"
ROTATED_JTI = "v5-first-case-rotated-jti-0001"
ROTATED_CREDENTIAL_ID = "cred_01J0000000000H03"
PROPOSED_AT = NOW + timedelta(minutes=1)
REAUTH_NOW = NOW + timedelta(minutes=2)
OWNER_BEARER = "v5-first-case-owner-reauth-bearer-000000000001"
OWNER_JTI = "v5-first-case-owner-reauth-jti-0001"


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        public_credential_hash_pepper=SecretStr(
            "v5-first-case-local-test-pepper-000000000001"
        ),
        public_cursor_signing_key=SecretStr(
            "v5-first-case-local-test-cursor-000000000001"
        ),
        require_mcp_role_tokens=False,
    )


def _payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "owner_principal": {
            "principal_id": OWNER,
            "subject": "v5-first-case-owner",
        },
        "principal": {
            "principal_id": OPERATOR,
            "subject": "v5-first-case-operator",
        },
        "credential": {
            "credential_id": "cred_01J0000000000H01",
            "bearer_token": BEARER,
            "jti": JTI,
            "issued_at": (NOW - timedelta(minutes=10)).isoformat(),
            "not_before": (NOW - timedelta(minutes=5)).isoformat(),
            "expires_at": (NOW + timedelta(days=30)).isoformat(),
        },
        "source": {
            "source_id": "src_01J0000000000H01",
            "connector_kind": "manual",
            "state": "ACTIVE",
            "credential_ref": None,
            "config": {"provider_origin": "https://caseloop.local"},
        },
        "controller": {
            "registration_id": "creg_01J0000000000H01",
            "principal_id": "prn_01J0000000000HC1",
        },
        "version_controller": {
            "registration_id": "creg_01J0000000000H02",
            "principal_id": "prn_01J0000000000HC2",
        },
        "case_controller": {
            "registration_id": "creg_01J0000000000H03",
            "principal_id": "prn_01J0000000000HC3",
        },
        "intake_controllers": {
            "signal": {
                "registration_id": "creg_01J0000000000H04",
                "principal_id": "prn_01J0000000000HC4",
            },
            "case": {
                "registration_id": "creg_01J0000000000H05",
                "principal_id": "prn_01J0000000000HC5",
            },
            "evidence": {
                "registration_id": "creg_01J0000000000H06",
                "principal_id": "prn_01J0000000000HC6",
            },
        },
        "secret_storage_ref": f"keyring://caseloop/local/{WORKSPACE}",
    }


def _request() -> V5CatalogLocalBootstrapRequest:
    return V5CatalogLocalBootstrapRequest.model_validate(_payload())


def _execute(session, request: V5CatalogLocalBootstrapRequest):
    return execute_v5_catalog_local_bootstrap(
        session,
        request,
        settings=_settings(),
        now=NOW,
        schema_verifier=lambda _session: None,
        contracts_root=CONTRACTS,
    )


def _operator_context(
    session,
    *,
    required_scope: str,
    evaluated_at: datetime,
    bearer: str = BEARER,
    environment_id: str | None = None,
):
    return PublicCredentialResolver(
        session,
        hash_pepper=_settings().public_credential_hash_pepper,
        expected_issuer=_settings().public_auth_issuer,
    ).resolve(
        SecretStr(bearer),
        requested_workspace_id=WORKSPACE,
        required_scope=required_scope,
        project_id=PROJECT,
        environment_id=environment_id,
        evaluated_at=evaluated_at,
    )


def _manifest() -> SystemManifestImportRequest:
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64
    return SystemManifestImportRequest.model_validate(
        {
            "schema_version": "2.0",
            "application": {
                "project_id": PROJECT,
                "slug": "v5-first-case",
                "display_name": "V5 First Case",
                "owner_principal_ids": [OWNER],
                "criticality": "P0",
                "data_classification": "INTERNAL",
                "governance_mode": "MANAGED",
            },
            "environment": {
                "logical_name": "local",
                "risk_classification": "MEDIUM",
            },
            "components": [
                {
                    "logical_name": "control-plane",
                    "component_kind": "APPLICATION_CODE",
                    "owner_principal_ids": [OWNER],
                    "criticality": "P0",
                    "data_classification": "INTERNAL",
                    "permission_classification": "READ_WRITE",
                    "effect_classification": "LOCAL",
                    "revision": {
                        "identity_locator": {"type": "git", "path": "."},
                        "identity_assurance": "IMMUTABLE_DIGEST",
                        "content_digest": digest_a,
                        "artifact_refs": [
                            {
                                "kind": "git_commit",
                                "ref": "local-first-case",
                                "digest": digest_b,
                            }
                        ],
                    },
                },
                {
                    "logical_name": "model-binding",
                    "component_kind": "MODEL_BINDING",
                    "owner_principal_ids": [OWNER],
                    "criticality": "P1",
                    "data_classification": "INTERNAL",
                    "permission_classification": "READ_ONLY",
                    "effect_classification": "NONE",
                    "revision": {
                        "identity_locator": {
                            "type": "provider",
                            "name": "local-model-alias",
                        },
                        "identity_assurance": "MUTABLE_ALIAS",
                        "provider_origin": "https://caseloop.local",
                        "resolved_at": IMPORT_AT.isoformat(),
                    },
                },
            ],
            "dependency_edges": [
                {
                    "from_component": "control-plane",
                    "to_component": "model-binding",
                    "relation": "INVOKES",
                    "required": True,
                }
            ],
            "approver_policy": {
                "logical_name": "local-approver-policy",
                "component_kind": "POLICY",
                "owner_principal_ids": [OWNER],
                "criticality": "P1",
                "data_classification": "CONFIDENTIAL",
                "permission_classification": "READ_ONLY",
                "effect_classification": "NONE",
                "revision": {
                    "identity_locator": {
                        "type": "file",
                        "path": "policies/local-approvers.yaml",
                    },
                    "identity_assurance": "UNKNOWN",
                    "unknown_reason": "local bootstrap has no immutable policy source",
                },
            },
        }
    )


def _import_system(session):
    return SystemVersionsService(
        session,
        clock=lambda: IMPORT_AT,
        contracts_root=CONTRACTS,
    ).import_manifest(
        _manifest(),
        principal=_operator_context(
            session,
            required_scope="system_manifests:import",
            evaluated_at=IMPORT_AT,
        ),
        idempotency_key="local-first-case-import-0001",
        request_id="req_01J0000000000H10",
    )


def _rotation_payload(imported) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "operation": "operator_environment_rotation",
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "principal": {
            "principal_id": OPERATOR,
            "subject": "v5-first-case-operator",
        },
        "previous_credential_id": "cred_01J0000000000H01",
        "credential": {
            "credential_id": ROTATED_CREDENTIAL_ID,
            "bearer_token": ROTATED_BEARER,
            "jti": ROTATED_JTI,
            "issued_at": ROTATION_ISSUED_AT.isoformat(),
            "not_before": ROTATION_ISSUED_AT.isoformat(),
            "expires_at": (ROTATION_NOW + timedelta(days=30)).isoformat(),
        },
        "exact_environment_binding": {
            "kind": "ENVIRONMENT",
            "id": imported.environment.environment_id,
            "revision": imported.environment.record_envelope.revision,
            "digest": imported.environment.record_envelope.record_digest,
        },
        "secret_storage_ref": (
            f"keyring://caseloop/local/{WORKSPACE}/operator-environment"
        ),
    }


def _rotation_execute(
    session,
    request: V5OperatorEnvironmentRotationRequest,
    *,
    audit_service: V4AuditService | None = None,
):
    return execute_v5_operator_environment_rotation(
        session,
        request,
        settings=_settings(),
        now=ROTATION_NOW,
        schema_verifier=lambda _session: None,
        audit_service=audit_service,
    )


def _import_and_rotate(session):
    imported = _import_system(session)
    session.commit()
    request = V5OperatorEnvironmentRotationRequest.model_validate(
        _rotation_payload(imported)
    )
    receipt = _rotation_execute(session, request)
    session.commit()
    return imported, request, receipt


def _create_exact_proposal(session) -> tuple[object, str, str, str]:
    imported, _rotation_request, _rotation_receipt = _import_and_rotate(session)
    environment_id = imported.environment.environment_id
    signal_at = NOW + timedelta(seconds=40)
    intake = SignalIntakeService(
        session,
        clock=lambda: signal_at,
        contracts_root=REPO / "contracts" / "v4",
    ).submit(
        SignalSubmission.model_validate(
            {
                "schema_version": "1.0",
                "source_id": "src_01J0000000000H01",
                "source_event_id": "local-first-case-event-0001",
                "source_event_version": "1",
                "signal_kind": "maintainer_report",
                "reporter": {
                    "kind": "maintainer",
                    "source_subject_ref": "v5-first-case-operator",
                },
                "project_id": PROJECT,
                "environment_id": environment_id,
                "governed_agent_id": None,
                "occurred_at": signal_at.isoformat(),
                "content": {
                    "summary": "Fresh local first system case",
                    "body": "The bounded tool was not selected.",
                    "attachments": [],
                },
                "run_locator": None,
                "privacy_classification": "INTERNAL",
            }
        ),
        principal=_operator_context(
            session,
            required_scope="signals:write",
            evaluated_at=signal_at,
            bearer=ROTATED_BEARER,
            environment_id=environment_id,
        ),
        idempotency_key="local-first-case-signal-0001",
        request_id="req_01J0000000000H00",
    )
    quality_case = session.get(QualityCase, intake.case.case_id)
    assert quality_case is not None
    CaseBindingService(
        session,
        clock=lambda: NOW + timedelta(seconds=50),
        contracts_root=CONTRACTS,
    ).bind_application(
        CaseBindApplicationRequest.model_validate(
            {
                "schema_version": "2.0",
                "case_id": quality_case.case_id,
                "case_revision": quality_case.revision,
                "case_digest": quality_case.record_digest,
                "application_id": imported.application.application_id,
                "environment_id": environment_id,
                "declared_system_version_set_binding_or_unknown": {
                    "kind": "SYSTEM_VERSION_SET",
                    "id": imported.system_version_set.system_version_set_id,
                    "revision": imported.system_version_set.record_envelope.revision,
                    "digest": (
                        imported.system_version_set.record_envelope.record_digest
                    ),
                },
                "issue_snapshot": None,
            }
        ),
        principal=_operator_context(
            session,
            required_scope="cases:bind",
            evaluated_at=NOW + timedelta(seconds=50),
            bearer=ROTATED_BEARER,
            environment_id=environment_id,
        ),
        idempotency_key="local-first-case-bind-0001",
        request_id="req_01J0000000000H11",
    )
    proposed = AcceptanceService(
        session,
        clock=lambda: PROPOSED_AT,
        contracts_root=CONTRACTS,
    ).propose(
        AcceptanceCriteriaProposeRequest.model_validate(
            {
                "schema_version": "2.0",
                "case_id": quality_case.case_id,
                "case_revision": quality_case.revision,
                "case_digest": quality_case.record_digest,
                "acceptance_source": {
                    "kind": "manual",
                    "title": "Wrong tool selected",
                },
                "reproducer_input": None,
                "reproducer_environment": None,
                "expected_behavior": {
                    "summary": "The bounded tool must be selected."
                },
                "oracle_or_evaluator": None,
                "applicable_workload_profile": {
                    "name": "local-once",
                    "concurrency": "SINGLE",
                },
                "applicable_deployment_profile": {
                    "name": "local-shadow",
                    "kind": "DEVELOPMENT",
                },
            }
        ),
        principal=_operator_context(
            session,
            required_scope="acceptance_criteria:propose",
            evaluated_at=PROPOSED_AT,
            bearer=ROTATED_BEARER,
            environment_id=environment_id,
        ),
        idempotency_key="local-first-case-propose-0001",
        request_id="req_01J0000000000H02",
    )
    proposal = proposed.acceptance_criteria_revision
    return (
        imported,
        quality_case.case_id,
        proposal.acceptance_criteria_revision_id,
        proposal.record_envelope.record_digest,
    )


def _reauth_payload(
    proposal_id: str,
    proposal_digest: str,
    *,
    bearer: str = OWNER_BEARER,
    jti: str = OWNER_JTI,
    issued_at: datetime = REAUTH_NOW - timedelta(seconds=30),
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "operation": "owner_reauthentication",
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "operator_principal_id": OPERATOR,
        "owner_principal": {
            "principal_id": OWNER,
            "subject": "v5-first-case-owner",
        },
        "credential": {
            "credential_id": "cred_01J0000000000H02",
            "bearer_token": bearer,
            "jti": jti,
            "issued_at": issued_at.isoformat(),
            "not_before": issued_at.isoformat(),
            "expires_at": (REAUTH_NOW + timedelta(minutes=30)).isoformat(),
        },
        "exact_proposed_revision_binding": {
            "kind": "ACCEPTANCE_CRITERIA_REVISION",
            "id": proposal_id,
            "revision": 1,
            "digest": proposal_digest,
        },
        "secret_storage_ref": f"keyring://caseloop/local/{WORKSPACE}/owner-reauth",
    }


def _reauth_execute(
    session,
    request: V5OwnerReauthenticationRequest,
    *,
    audit_service: V4AuditService | None = None,
):
    return execute_v5_owner_reauthentication(
        session,
        request,
        settings=_settings(),
        now=REAUTH_NOW,
        schema_verifier=lambda _session: None,
        audit_service=audit_service,
    )


def _count(session, model: type[object]) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


def test_first_case_bootstrap_provisions_all_local_authority_and_replays(
    sqlite_session,
) -> None:
    first = _execute(sqlite_session, _request())
    sqlite_session.commit()

    assert first.status == "CREATED"
    assert first.source.source_id == "src_01J0000000000H01"
    assert len(first.controllers) == 6
    assert [(item.owner, item.created) for item in first.controllers] == [
        ("application-catalog-controller", True),
        ("version-controller", True),
        ("case-controller", True),
        ("signal-controller", True),
        ("case-controller", True),
        ("evidence-controller", True),
    ]

    operator = sqlite_session.get(PublicPrincipal, OPERATOR)
    owner = sqlite_session.get(PublicPrincipal, OWNER)
    credential = sqlite_session.get(PublicCredential, "cred_01J0000000000H01")
    source = sqlite_session.get(SourceConnection, "src_01J0000000000H01")
    assert operator is not None and owner is not None and credential is not None
    assert source is not None
    assert operator.trust_roles == ["catalog_admin", "integrator"]
    assert owner.trust_roles == ["maintainer", "domain_reviewer"]
    assert set(operator.scopes) == {
        "capabilities:read",
        "signals:write",
        "cases:read",
        "cases:bind",
        "acceptance_criteria:read",
        "acceptance_criteria:propose",
        "applications:manage",
        "applications:read",
        "system_manifests:import",
        "system_versions:read",
    }
    assert owner.scopes == [
        "capabilities:read",
        "cases:read",
        "acceptance_criteria:read",
        "acceptance_criteria:confirm",
    ]
    assert credential.scopes == operator.scopes
    assert credential.credential_hash != BEARER
    assert credential.jti_digest != JTI

    second = _execute(sqlite_session, _request())
    sqlite_session.commit()
    assert second.status == "REUSED"
    assert all(item.created is False for item in second.controllers)
    assert _count(sqlite_session, SourceConnection) == 1
    assert _count(sqlite_session, PublicPrincipal) == 2
    assert _count(sqlite_session, PublicCredential) == 1
    assert _count(sqlite_session, ControllerRegistration) == 6

    serialized = json.dumps(
        {
            "receipt": second.model_dump(mode="json"),
            "audits": [
                {
                    "params_digest": row.params_digest,
                    "evidence_refs": row.evidence_refs,
                }
                for row in sqlite_session.scalars(sa.select(Audit)).all()
            ],
        },
        sort_keys=True,
    )
    assert BEARER not in serialized
    assert JTI not in serialized


def test_first_case_bootstrap_fails_closed_on_server_role_drift(sqlite_session) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    principal = sqlite_session.get(PublicPrincipal, OPERATOR)
    assert principal is not None
    principal.trust_roles = ["integrator"]
    sqlite_session.commit()

    with pytest.raises(BootstrapError, match="bootstrap.principal_drift"):
        _execute(sqlite_session, _request())
    sqlite_session.rollback()
    assert _count(sqlite_session, ControllerRegistration) == 6


def test_first_case_bootstrap_rejects_secret_shaped_source_config(sqlite_session) -> None:
    payload = _payload()
    payload["source"]["config"] = {"api_key": "must-not-be-persisted"}  # type: ignore[index]
    request = V5CatalogLocalBootstrapRequest.model_validate(payload)

    with pytest.raises(
        BootstrapError, match="bootstrap.source_config_contains_secret_key"
    ):
        _execute(sqlite_session, request)
    sqlite_session.rollback()
    assert _count(sqlite_session, SourceConnection) == 0
    assert _count(sqlite_session, PublicPrincipal) == 0
    assert _count(sqlite_session, ControllerRegistration) == 0


def test_first_case_bootstrap_audit_failure_rolls_back_everything(sqlite_session) -> None:
    failing_audit = V4AuditService(
        sqlite_session,
        clock=lambda: NOW,
        force_fail=False,
        fail_on_call=1,
    )
    with pytest.raises(Exception):
        execute_v5_catalog_local_bootstrap(
            sqlite_session,
            _request(),
            settings=_settings(),
            now=NOW,
            schema_verifier=lambda _session: None,
            audit_service=failing_audit,
            contracts_root=CONTRACTS,
        )
    sqlite_session.rollback()
    assert _count(sqlite_session, SourceConnection) == 0
    assert _count(sqlite_session, PublicPrincipal) == 0
    assert _count(sqlite_session, PublicCredential) == 0
    assert _count(sqlite_session, ControllerRegistration) == 0


def test_operator_rotates_after_import_without_mutating_old_claims(
    sqlite_session,
) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    imported = _import_system(sqlite_session)
    sqlite_session.commit()
    request = V5OperatorEnvironmentRotationRequest.model_validate(
        _rotation_payload(imported)
    )
    old_credential = sqlite_session.get(
        PublicCredential, "cred_01J0000000000H01"
    )
    assert old_credential is not None
    old_claims_digest = old_credential.claims_digest
    old_environment_ids = list(old_credential.environment_ids)

    first = _rotation_execute(sqlite_session, request)
    sqlite_session.commit()

    assert first.status == "CREATED"
    assert first.credential.created is True
    assert first.exact_environment_binding.id == imported.environment.environment_id
    old_credential = sqlite_session.get(
        PublicCredential, "cred_01J0000000000H01"
    )
    rotated_credential = sqlite_session.get(
        PublicCredential, ROTATED_CREDENTIAL_ID
    )
    operator = sqlite_session.get(PublicPrincipal, OPERATOR)
    assert old_credential is not None and rotated_credential is not None
    assert operator is not None
    assert old_credential.state == "REVOKED"
    assert old_credential.revoked_at is not None
    assert old_credential.claims_digest == old_claims_digest
    assert old_credential.environment_ids == old_environment_ids == []
    assert rotated_credential.state == "ACTIVE"
    assert rotated_credential.environment_ids == [
        imported.environment.environment_id
    ]
    assert operator.environment_ids == [imported.environment.environment_id]
    assert rotated_credential.claims_digest == operator.claims_digest

    try:
        _operator_context(
            sqlite_session,
            required_scope="signals:write",
            evaluated_at=ROTATION_NOW,
        )
    except CredentialResolutionError as exc:
        assert exc.code == "TOKEN_REVOKED"
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("the superseded operator credential remained active")
    resolved = _operator_context(
        sqlite_session,
        required_scope="signals:write",
        evaluated_at=ROTATION_NOW,
        bearer=ROTATED_BEARER,
        environment_id=imported.environment.environment_id,
    )
    assert resolved.credential_id == ROTATED_CREDENTIAL_ID
    assert resolved.environment_ids == [imported.environment.environment_id]

    replay = _rotation_execute(sqlite_session, request)
    sqlite_session.commit()
    assert replay.status == "REUSED"
    assert replay.credential.created is False
    assert replay.rotation_binding_digest == first.rotation_binding_digest
    assert _count(sqlite_session, PublicCredential) == 2

    serialized = json.dumps(
        {
            "receipt": replay.model_dump(mode="json"),
            "audits": [
                {
                    "params_digest": row.params_digest,
                    "evidence_refs": row.evidence_refs,
                }
                for row in sqlite_session.scalars(sa.select(Audit)).all()
            ],
        },
        sort_keys=True,
    )
    assert ROTATED_BEARER not in serialized
    assert ROTATED_JTI not in serialized


def test_operator_rotation_rejects_credential_reuse_and_rolls_back_on_audit_failure(
    sqlite_session,
) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    imported = _import_system(sqlite_session)
    sqlite_session.commit()
    reused_payload = _rotation_payload(imported)
    reused_payload["credential"]["bearer_token"] = BEARER  # type: ignore[index]
    reused_request = V5OperatorEnvironmentRotationRequest.model_validate(
        reused_payload
    )
    with pytest.raises(
        BootstrapError,
        match="bootstrap.operator_environment_rotation.credential_material_reused",
    ):
        _rotation_execute(sqlite_session, reused_request)
    sqlite_session.rollback()

    request = V5OperatorEnvironmentRotationRequest.model_validate(
        _rotation_payload(imported)
    )
    failing_audit = V4AuditService(
        sqlite_session,
        clock=lambda: ROTATION_NOW,
        fail_on_call=1,
    )
    with pytest.raises(Exception):
        _rotation_execute(
            sqlite_session,
            request,
            audit_service=failing_audit,
        )
    sqlite_session.rollback()
    old_credential = sqlite_session.get(
        PublicCredential, "cred_01J0000000000H01"
    )
    operator = sqlite_session.get(PublicPrincipal, OPERATOR)
    assert old_credential is not None and operator is not None
    assert old_credential.state == "ACTIVE"
    assert old_credential.revoked_at is None
    assert old_credential.environment_ids == []
    assert operator.environment_ids == []
    assert sqlite_session.get(PublicCredential, ROTATED_CREDENTIAL_ID) is None


def test_main_dispatches_operator_rotation_without_echoing_secrets(
    sqlite_session,
) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    imported = _import_system(sqlite_session)
    sqlite_session.commit()
    payload = _rotation_payload(imported)
    request = V5OperatorEnvironmentRotationRequest.model_validate(payload)
    receipt = _rotation_execute(sqlite_session, request)
    sqlite_session.rollback()
    output = StringIO()

    result = main(
        stdin=StringIO(json.dumps(payload)),
        stdout=output,
        operator_rotation_executor=lambda _request: receipt,
    )

    assert result == 0
    emitted = output.getvalue()
    assert json.loads(emitted)["operation"] == "operator_environment_rotation"
    assert ROTATED_BEARER not in emitted
    assert ROTATED_JTI not in emitted


def test_owner_reauthentication_issues_independent_fresh_resolvable_credential(
    sqlite_session,
) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    imported, case_id, proposal_id, proposal_digest = _create_exact_proposal(
        sqlite_session
    )
    sqlite_session.commit()
    request = V5OwnerReauthenticationRequest.model_validate(
        _reauth_payload(proposal_id, proposal_digest)
    )

    first = _reauth_execute(sqlite_session, request)
    sqlite_session.commit()
    assert first.status == "CREATED"
    assert first.credential.created is True
    assert first.exact_proposed_revision_binding.id == proposal_id
    assert first.exact_environment_binding.id == imported.environment.environment_id

    operator_credential = sqlite_session.get(
        PublicCredential, ROTATED_CREDENTIAL_ID
    )
    owner_credential = sqlite_session.get(
        PublicCredential, "cred_01J0000000000H02"
    )
    assert operator_credential is not None and owner_credential is not None
    assert owner_credential.principal_id == OWNER
    assert owner_credential.credential_hash != operator_credential.credential_hash
    assert owner_credential.jti_digest != operator_credential.jti_digest
    owner_issued_at = owner_credential.issued_at
    if owner_issued_at.tzinfo is None:
        owner_issued_at = owner_issued_at.replace(tzinfo=timezone.utc)
    assert owner_issued_at > PROPOSED_AT

    resolved = PublicCredentialResolver(
        sqlite_session,
        hash_pepper=_settings().public_credential_hash_pepper,
        expected_issuer=_settings().public_auth_issuer,
    ).resolve(
        SecretStr(OWNER_BEARER),
        requested_workspace_id=WORKSPACE,
        required_scope="acceptance_criteria:confirm",
        project_id=PROJECT,
        environment_id=imported.environment.environment_id,
        evaluated_at=REAUTH_NOW,
    )
    assert resolved.principal_id == OWNER
    assert resolved.credential_id == "cred_01J0000000000H02"
    assert resolved.issued_at > PROPOSED_AT

    confirmed = AcceptanceService(
        sqlite_session,
        clock=lambda: REAUTH_NOW + timedelta(seconds=5),
        contracts_root=CONTRACTS,
    ).confirm(
        AcceptanceCriteriaConfirmRequest.model_validate(
            {
                "schema_version": "2.0",
                "exact_proposed_revision_binding": {
                    "kind": "ACCEPTANCE_CRITERIA_REVISION",
                    "id": proposal_id,
                    "revision": 1,
                    "digest": proposal_digest,
                },
            }
        ),
        principal=resolved,
        idempotency_key="owner-reauth-confirm-0001",
        request_id="req_01J0000000000H01",
    )
    sqlite_session.commit()
    confirmed_record = confirmed.acceptance_criteria_revision
    assert confirmed_record.confirmation_status == "CONFIRMED"
    assert confirmed_record.exact_case_binding.case_id == case_id
    assert confirmed_record.proposer_principal == OPERATOR
    assert confirmed_record.confirmer_principal == OWNER
    assert confirmed_record.reauthentication_credential_binding is not None
    assert (
        confirmed_record.reauthentication_credential_binding.credential_id
        == "cred_01J0000000000H02"
    )
    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, Environment) == 1
    assert _count(sqlite_session, ApplicationCaseBinding) == 1
    assert _count(sqlite_session, AcceptanceCriteriaRevision) == 2

    replay = _reauth_execute(sqlite_session, request)
    sqlite_session.commit()
    assert replay.status == "REUSED"
    assert replay.credential.created is False
    assert replay.issuance_binding_digest == first.issuance_binding_digest
    assert _count(sqlite_session, PublicCredential) == 3

    serialized = json.dumps(
        {
            "receipt": replay.model_dump(mode="json"),
            "audits": [
                {
                    "params_digest": row.params_digest,
                    "evidence_refs": row.evidence_refs,
                }
                for row in sqlite_session.scalars(sa.select(Audit)).all()
            ],
        },
        sort_keys=True,
    )
    assert OWNER_BEARER not in serialized
    assert OWNER_JTI not in serialized


def test_owner_reauthentication_rejects_stale_or_operator_credential_material(
    sqlite_session,
) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    _imported, _case_id, proposal_id, proposal_digest = _create_exact_proposal(
        sqlite_session
    )
    sqlite_session.commit()

    stale = V5OwnerReauthenticationRequest.model_validate(
        _reauth_payload(proposal_id, proposal_digest, issued_at=PROPOSED_AT)
    )
    with pytest.raises(
        BootstrapError,
        match="bootstrap.owner_reauthentication.credential_not_fresh",
    ):
        _reauth_execute(sqlite_session, stale)
    sqlite_session.rollback()

    reused_operator_bearer = V5OwnerReauthenticationRequest.model_validate(
        _reauth_payload(proposal_id, proposal_digest, bearer=ROTATED_BEARER)
    )
    with pytest.raises(
        BootstrapError,
        match="bootstrap.owner_reauthentication.credential_material_reused",
    ):
        _reauth_execute(sqlite_session, reused_operator_bearer)
    sqlite_session.rollback()
    assert _count(sqlite_session, PublicCredential) == 2
    owner = sqlite_session.get(PublicPrincipal, OWNER)
    assert owner is not None
    assert owner.environment_ids == []


def test_owner_reauthentication_audit_failure_rolls_back_credential(
    sqlite_session,
) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    _imported, _case_id, proposal_id, proposal_digest = _create_exact_proposal(
        sqlite_session
    )
    sqlite_session.commit()
    request = V5OwnerReauthenticationRequest.model_validate(
        _reauth_payload(proposal_id, proposal_digest)
    )
    failing_audit = V4AuditService(
        sqlite_session,
        clock=lambda: REAUTH_NOW,
        fail_on_call=1,
    )

    with pytest.raises(Exception):
        _reauth_execute(sqlite_session, request, audit_service=failing_audit)
    sqlite_session.rollback()
    assert _count(sqlite_session, PublicCredential) == 2
    owner = sqlite_session.get(PublicPrincipal, OWNER)
    assert owner is not None
    assert owner.environment_ids == []


def test_main_dispatches_owner_reauthentication_without_echoing_secrets(
    sqlite_session,
) -> None:
    _execute(sqlite_session, _request())
    sqlite_session.commit()
    _imported, _case_id, proposal_id, proposal_digest = _create_exact_proposal(
        sqlite_session
    )
    sqlite_session.commit()
    payload = _reauth_payload(proposal_id, proposal_digest)
    request = V5OwnerReauthenticationRequest.model_validate(payload)
    receipt = _reauth_execute(sqlite_session, request)
    sqlite_session.rollback()
    output = StringIO()

    result = main(
        stdin=StringIO(json.dumps(payload)),
        stdout=output,
        reauthentication_executor=lambda _request: receipt,
    )

    assert result == 0
    emitted = output.getvalue()
    assert json.loads(emitted)["operation"] == "owner_reauthentication"
    assert OWNER_BEARER not in emitted
    assert OWNER_JTI not in emitted
