"""Independent V5-1C JCS digest recomputation (verifier-only, throwaway).

Adversarial check: recompute the canonical JCS digests of TWO real persisted
records (one ApplicationCaseBinding, one AcceptanceCriteriaRevision) from their
persisted ``envelope_payload`` rows using **only** ``rfc8785`` + ``hashlib.sha256``
directly — never the service digest helpers ``v5_record_digest`` /
``canonical_digest`` / ``record_digest``.  The recomputed digest must match the
stored ``record_digest`` column byte-for-byte.

Also asserts the S1A case immutability invariant: the persisted
QualityCase.snapshot_payload / record_digest / state are byte-identical before
and after a binding write.

Run:
    cd control-plane && .venv/bin/python ../scripts/v5_1c_verify_recompute_digest.py
"""
from __future__ import annotations

import copy
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rfc8785
import sqlalchemy as sa
from sqlalchemy import create_engine, select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "control-plane"))

from app.models.v4_tables import PublicPrincipal, QualityCase  # noqa: E402
from app.models.v5_tables import (  # noqa: E402
    AIApplication,
    AcceptanceCriteriaRevision,
    ApplicationCaseBinding,
    Environment,
)
from app.public_api.auth_contract import AcceptedPrincipalContext  # noqa: E402
from app.public_api.v5_models import (  # noqa: E402
    AcceptanceCriteriaConfirmRequest,
    AcceptanceCriteriaProposeRequest,
    CaseBindApplicationRequest,
)
from app.services.acceptance import AcceptanceService  # noqa: E402
from app.services.case_binding import CaseBindingService  # noqa: E402
from app.services.v4_audit import V4AuditService  # noqa: E402
from app.services.v5_authority import build_v5_controller_registration_record  # noqa: E402
from app.utils.ids import (  # noqa: E402
    new_application_id,
    new_catalog_environment_id,
)
from app.utils.v4_integrity import canonical_digest, record_digest  # noqa: E402

NOW = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_01J0000000000001"
PROJECT = "proj_01J0000000000001"
OWNER = "prn_01J0000000000001"
BINDER = "prn_01J000000000000A"
HUMAN_PROPOSER = "prn_01J000000000000E"
CONFIRMER = "prn_01J000000000000C"
CASE_CTRL = "prn_01J000000000000D"
CASE_CTRL_REG = "creg_01J00000000000CD"
SUBJECT = "v5-1c-verify"
ISSUER = "https://auth.agentmed.dev"
AUDIENCES = ["agentmed-public-api"]
APP_ID = "app_01J0000000000001"
ENV_ID = "env_01J0000000000001"
CASE_ID = "case_01J0000000000001"
SIGNAL_ID = "sig_01J0000000000001"


def _claims(scopes: list[str]) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE,
            "project_ids": [PROJECT],
            "environment_ids": [],
            "scopes": scopes,
        }
    )


def _principal(
    *,
    principal_id: str,
    scopes: list[str],
    required_scope: str,
    principal_type: str = "human",
    issued_at: datetime | None = None,
) -> AcceptedPrincipalContext:
    issued = issued_at or NOW - timedelta(minutes=10)
    return AcceptedPrincipalContext.model_validate(
        {
            "schema_version": "1.0",
            "principal_id": principal_id,
            "principal_type": principal_type,
            "issuer": ISSUER,
            "subject": SUBJECT,
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE,
            "project_ids": [PROJECT],
            "environment_ids": [],
            "scopes": scopes,
            "credential_id": "cred_01J000000000000A",
            "jti_digest": "sha256:" + "a" * 64,
            "issued_at": issued,
            "not_before": issued,
            "expires_at": issued + timedelta(days=30),
            "revoked_at": None,
            "revocation_checked_at": NOW,
            "requested_context": {
                "workspace_id": WORKSPACE,
                "project_id": PROJECT,
                "environment_id": None,
                "required_scope": required_scope,
            },
            "evaluated_at": NOW,
            "claims_digest": _claims(scopes),
        }
    )


def _envelope_payload(*, recorded_by: str, receipt: str) -> dict:
    return {
        "schema_version": "2.0",
        "workspace_id": WORKSPACE,
        "revision": 1,
        "recorded_by_principal": recorded_by,
        "recorded_at": NOW.isoformat().replace("+00:00", "Z"),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
        "record_digest": "",
        "authority_receipt_id": receipt,
    }


def _sha256_hex(canonical_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes).hexdigest()


def _v5_recompute(envelope_payload: dict) -> str:
    """INDEPENDENT recompute — rfc8785 + sha256 only, no service helper."""
    body = copy.deepcopy(dict(envelope_payload))
    env = body.get("record_envelope")
    if not isinstance(env, dict) or "record_digest" not in env:
        raise RuntimeError("independent: record_envelope/record_digest missing")
    del body["record_envelope"]["record_digest"]
    return _sha256_hex(rfc8785.dumps(body))


def _v4_recompute(record: dict, self_field: str) -> str:
    """INDEPENDENT recompute of a v4 self-digest (case snapshot / receipt)."""
    body = {k: v for k, v in record.items() if k != self_field}
    return _sha256_hex(rfc8785.dumps(body))


def main() -> int:
    engine = create_engine("sqlite://", future=True)
    from app.models import Base  # noqa: E402

    Base.metadata.create_all(engine)
    failures: list[str] = []

    with engine.begin() as session:
        from app.public_api.credential_resolver import digest_public_subject

        def _seed(pid: str, scopes: list[str], ptype: str = "human") -> None:
            session.add(
                PublicPrincipal(
                    principal_id=pid,
                    workspace_id=WORKSPACE,
                    principal_type=ptype,
                    state="ACTIVE",
                    subject_digest=digest_public_subject(SUBJECT),
                    audiences=list(AUDIENCES),
                    project_ids=[PROJECT],
                    environment_ids=[],
                    scopes=list(scopes),
                    claims_digest=_claims(scopes),
                    revoked_at=None,
                )
            )

        _seed(BINDER, ["cases:bind", "cases:read", "acceptance_criteria:propose"])
        _seed(HUMAN_PROPOSER, ["acceptance_criteria:propose", "acceptance_criteria:confirm"])
        _seed(CONFIRMER, ["acceptance_criteria:confirm", "acceptance_criteria:read"])

        # case controller registration
        audit = V4AuditService(session, clock=lambda: NOW)
        recorded = audit.record(
            workspace_id=WORKSPACE,
            actor_principal=OWNER,
            action="controllers.register",
            target=CASE_CTRL_REG,
            params={
                "owner": "case-controller",
                "service_identity_digest": canonical_digest(
                    {
                        "schema_version": "1.0",
                        "workspace_id": WORKSPACE,
                        "owner": "case-controller",
                        "controller_principal": CASE_CTRL,
                        "principal_type": "CONTROLLER_SERVICE",
                        "service": "agentmed-control-plane",
                    }
                ),
            },
            transaction_id="txn_v5_1c_case_controller",
            evidence_refs={
                "owner": "case-controller",
                "controller_registration_id": CASE_CTRL_REG,
                "controller_principal": CASE_CTRL,
            },
            occurred_at=NOW,
        )
        built = build_v5_controller_registration_record(
            controller_registration_id=CASE_CTRL_REG,
            workspace_id=WORKSPACE,
            owner="case-controller",
            controller_principal=CASE_CTRL,
            allowed_commands=[
                "cases.bind-application",
                "acceptance-criteria.propose",
                "acceptance-criteria.confirm",
            ],
            service_identity_digest=canonical_digest(
                {
                    "schema_version": "1.0",
                    "workspace_id": WORKSPACE,
                    "owner": "case-controller",
                    "controller_principal": CASE_CTRL,
                    "principal_type": "CONTROLLER_SERVICE",
                    "service": "agentmed-control-plane",
                }
            ),
            registered_by_human_principal=OWNER,
            registration_audit_ref=recorded.audit_ref,
            valid_from=NOW - timedelta(minutes=1),
            registered_at=NOW,
            contracts_root=ROOT / "contracts" / "v5",
        )
        from app.models.v4_tables import ControllerRegistration

        session.add(ControllerRegistration(**built.row_values))
        session.flush()

        # catalog app + env
        app_payload = {
            "application_id": APP_ID,
            "workspace_id": WORKSPACE,
            "project_id": PROJECT,
            "slug": "fixture-ai-app",
            "display_name": "Fixture AI App",
            "owner_principal_ids": [OWNER],
            "criticality": "P2",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
            "lifecycle_state": "ACTIVE",
            "record_envelope": _envelope_payload(recorded_by=BINDER, receipt="arec_01J0000000000001"),
        }
        # use the service helper only to *seed* the dependent catalog digest (not
        # the V5-1C records under test); the recompute below is what we trust.
        from app.utils.v5_integrity import v5_record_digest as _v5

        app_payload["record_envelope"]["record_digest"] = _v5(app_payload)
        session.add(
            AIApplication(
                application_id=APP_ID,
                workspace_id=WORKSPACE,
                project_id=PROJECT,
                slug="fixture-ai-app",
                display_name="Fixture AI App",
                owner_principal_ids=[OWNER],
                criticality="P2",
                data_classification="INTERNAL",
                governance_mode="MANAGED",
                lifecycle_state="ACTIVE",
                revision=1,
                envelope_payload=app_payload,
                record_digest=app_payload["record_envelope"]["record_digest"],
                authority_receipt_id="arec_01J0000000000001",
                recorded_by_principal=BINDER,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        env_payload = {
            "environment_id": ENV_ID,
            "workspace_id": WORKSPACE,
            "application_id": APP_ID,
            "logical_name": "local-shadow",
            "risk_classification": "LOW",
            "lifecycle_state": "ACTIVE",
            "record_envelope": _envelope_payload(recorded_by=BINDER, receipt="arec_01J0000000000002"),
        }
        env_payload["record_envelope"]["record_digest"] = _v5(env_payload)
        session.add(
            Environment(
                environment_id=ENV_ID,
                workspace_id=WORKSPACE,
                application_id=APP_ID,
                logical_name="local-shadow",
                risk_classification="LOW",
                lifecycle_state="ACTIVE",
                revision=1,
                envelope_payload=env_payload,
                record_digest=env_payload["record_envelope"]["record_digest"],
                authority_receipt_id="arec_01J0000000000002",
                recorded_by_principal=BINDER,
                created_at=NOW,
                updated_at=NOW,
            )
        )

        # S1A case — snapshot BEFORE binding (capture exact bytes)
        case_payload = {
            "schema_version": "1.0",
            "case_id": CASE_ID,
            "workspace_id": WORKSPACE,
            "revision": 1,
            "status": "OPEN",
            "title": "Fixture maintainer report",
            "project_id": PROJECT,
            "environment_id": None,
            "governed_agent_id": None,
            "correlation_status": "NEEDS_CORRELATION",
            "triage_status": "UNTRIAGED",
            "opening_signal_id": SIGNAL_ID,
            "authority_receipt_id": "arec_01J0000000000003",
            "opened_at": NOW.isoformat().replace("+00:00", "Z"),
            "updated_at": NOW.isoformat().replace("+00:00", "Z"),
            "resolved_at": None,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_digest)",
            "record_digest": "",
        }
        case_payload["record_digest"] = record_digest(
            case_payload, self_digest_field="record_digest"
        )
        session.add(
            QualityCase(
                case_id=CASE_ID,
                workspace_id=WORKSPACE,
                state="OPEN",
                revision=1,
                title="Fixture maintainer report",
                project_id=PROJECT,
                environment_id=None,
                governed_agent_id=None,
                correlation_status="NEEDS_CORRELATION",
                triage_status="UNTRIAGED",
                opening_signal_id=SIGNAL_ID,
                snapshot_payload=case_payload,
                record_digest=case_payload["record_digest"],
                authority_receipt_id="arec_01J0000000000003",
                opened_at=NOW,
                updated_at=NOW,
                resolved_at=None,
            )
        )
        session.flush()
        case_before = session.get(QualityCase, CASE_ID)
        before_snapshot = copy.deepcopy(case_before.snapshot_payload)
        before_digest = case_before.record_digest
        before_state = case_before.state

        binder = _principal(
            principal_id=BINDER,
            scopes=["cases:bind", "cases:read", "acceptance_criteria:propose"],
            required_scope="cases:bind",
        )
        bind_svc = CaseBindingService(session, clock=lambda: NOW)
        bind_resp = bind_svc.bind_application(
            CaseBindApplicationRequest(
                schema_version="2.0",
                case_id=CASE_ID,
                case_revision=1,
                case_digest=case_payload["record_digest"],
                application_id=APP_ID,
                environment_id=ENV_ID,
                declared_system_version_set_binding_or_unknown=None,
                issue_snapshot=None,
            ),
            principal=binder,
            idempotency_key="idem-bind-1",
        )
        session.flush()

        # ---- RECOMPUTE #1: ApplicationCaseBinding record_digest ----
        binding_row = session.scalar(
            select(ApplicationCaseBinding).where(
                ApplicationCaseBinding.workspace_id == WORKSPACE,
                ApplicationCaseBinding.case_id == CASE_ID,
            )
        )
        assert binding_row is not None, "binding row missing"
        recompute_binding = _v5_recompute(binding_row.envelope_payload)
        if recompute_binding != binding_row.record_digest:
            failures.append(
                f"BINDING record_digest mismatch: independent={recompute_binding} "
                f"stored={binding_row.record_digest}"
            )
        # also verify the binding_digest (a canonical_digest over 3 fields)
        expected_binding_digest = _sha256_hex(
            rfc8785.dumps(
                {
                    "application_id": APP_ID,
                    "environment_id": ENV_ID,
                    "declared_system_version_set_binding_or_unknown": None,
                }
            )
        )
        if expected_binding_digest != binding_row.binding_digest:
            failures.append(
                f"binding_digest mismatch: independent={expected_binding_digest} "
                f"stored={binding_row.binding_digest}"
            )

        # ---- S1A immutability invariant: case unchanged after bind ----
        case_after = session.get(QualityCase, CASE_ID)
        if case_after.snapshot_payload != before_snapshot:
            failures.append("S1A snapshot_payload changed across bind write")
        if case_after.record_digest != before_digest:
            failures.append("S1A record_digest changed across bind write")
        if case_after.state != before_state:
            failures.append("S1A state changed across bind write")
        if case_after.state != "OPEN":
            failures.append(f"S1A case not OPEN after bind: {case_after.state}")

        # ---- RECOMPUTE #2: AcceptanceCriteriaRevision record_digest (PROPOSED) ----
        proposer = _principal(
            principal_id=HUMAN_PROPOSER,
            scopes=["acceptance_criteria:propose", "acceptance_criteria:confirm"],
            required_scope="acceptance_criteria:propose",
        )
        acc_svc = AcceptanceService(session, clock=lambda: NOW)
        propose_resp = acc_svc.propose(
            AcceptanceCriteriaProposeRequest(
                schema_version="2.0",
                case_id=CASE_ID,
                case_revision=1,
                case_digest=case_payload["record_digest"],
                acceptance_source={"kind": "github_issue", "url": "https://example/i/1"},
                expected_behavior={"behavior": "fail_with_index_error"},
                applicable_workload_profile={"profile": "schema_dsl"},
                applicable_deployment_profile={"profile": "library"},
            ),
            principal=proposer,
            idempotency_key="idem-propose-1",
        )
        session.flush()
        prop_row = session.scalar(
            select(AcceptanceCriteriaRevision).where(
                AcceptanceCriteriaRevision.workspace_id == WORKSPACE,
                AcceptanceCriteriaRevision.case_id == CASE_ID,
                AcceptanceCriteriaRevision.confirmation_status == "PROPOSED",
            )
        )
        assert prop_row is not None, "proposed revision missing"
        recompute_prop = _v5_recompute(prop_row.envelope_payload)
        if recompute_prop != prop_row.record_digest:
            failures.append(
                f"ACCEPTANCE(PROPOSED) record_digest mismatch: "
                f"independent={recompute_prop} stored={prop_row.record_digest}"
            )
        # independent recompute of acceptance_digest (canonical_digest over 8 fields)
        ad = _sha256_hex(
            rfc8785.dumps(
                {
                    "confirmation_status": "PROPOSED",
                    "acceptance_source": {
                        "kind": "github_issue",
                        "url": "https://example/i/1",
                    },
                    "reproducer_input": None,
                    "reproducer_environment": None,
                    "expected_behavior": {"behavior": "fail_with_index_error"},
                    "oracle_or_evaluator": None,
                    "applicable_workload_profile": {"profile": "schema_dsl"},
                    "applicable_deployment_profile": {"profile": "library"},
                }
            )
        )
        if ad != prop_row.acceptance_digest:
            failures.append(
                f"acceptance_digest mismatch: independent={ad} "
                f"stored={prop_row.acceptance_digest}"
            )

        # ---- CONFIRM: human confirmer, reauthenticated (issued_at AFTER proposed) ----
        confirmer = _principal(
            principal_id=CONFIRMER,
            scopes=["acceptance_criteria:confirm", "acceptance_criteria:read"],
            required_scope="acceptance_criteria:confirm",
            issued_at=NOW + timedelta(minutes=1),  # fresh credential, after proposal
        )
        confirm_resp = acc_svc.confirm(
            AcceptanceCriteriaConfirmRequest(
                schema_version="2.0",
                exact_proposed_revision_binding={
                    "id": prop_row.acceptance_criteria_revision_id,
                    "digest": prop_row.record_digest,
                },
            ),
            principal=confirmer,
            idempotency_key="idem-confirm-1",
        )
        session.flush()
        conf_row = session.scalar(
            select(AcceptanceCriteriaRevision).where(
                AcceptanceCriteriaRevision.workspace_id == WORKSPACE,
                AcceptanceCriteriaRevision.case_id == CASE_ID,
                AcceptanceCriteriaRevision.confirmation_status == "CONFIRMED",
            )
        )
        assert conf_row is not None, "confirmed revision missing"
        recompute_conf = _v5_recompute(conf_row.envelope_payload)
        if recompute_conf != conf_row.record_digest:
            failures.append(
                f"ACCEPTANCE(CONFIRMED) record_digest mismatch: "
                f"independent={recompute_conf} stored={conf_row.record_digest}"
            )
        if conf_row.proposer_principal == conf_row.confirmer_principal:
            failures.append("CONFIRMED record has proposer == confirmer")
        if conf_row.proposer_principal == HUMAN_PROPOSER and conf_row.confirmer_principal != CONFIRMER:
            failures.append("CONFIRMED record confirmer mismatch")

        # ---- proposer-cannot-self-confirm: same principal must be rejected ----
        same = _principal(
            principal_id=HUMAN_PROPOSER,
            scopes=["acceptance_criteria:propose", "acceptance_criteria:confirm"],
            required_scope="acceptance_criteria:confirm",
            issued_at=NOW + timedelta(minutes=2),
        )
        # need a fresh PROPOSED to attempt self-confirm
        self_propose_resp = acc_svc.propose(
            AcceptanceCriteriaProposeRequest(
                schema_version="2.0",
                case_id=CASE_ID,
                case_revision=1,
                case_digest=case_payload["record_digest"],
                acceptance_source={"kind": "github_issue", "url": "https://example/i/2"},
                expected_behavior={"behavior": "x"},
                applicable_workload_profile={"profile": "p"},
                applicable_deployment_profile={"profile": "l"},
            ),
            principal=_principal(
                principal_id=HUMAN_PROPOSER,
                scopes=["acceptance_criteria:propose", "acceptance_criteria:confirm"],
                required_scope="acceptance_criteria:propose",
                issued_at=NOW + timedelta(minutes=2),
            ),
            idempotency_key="idem-propose-2",
        )
        session.flush()
        prop2 = session.scalar(
            select(AcceptanceCriteriaRevision).where(
                AcceptanceCriteriaRevision.workspace_id == WORKSPACE,
                AcceptanceCriteriaRevision.confirmation_status == "PROPOSED",
                AcceptanceCriteriaRevision.acceptance_source["url"].as_string()
                == "https://example/i/2",
            )
        )
        self_confirm_rejected = False
        try:
            acc_svc.confirm(
                AcceptanceCriteriaConfirmRequest(
                    schema_version="2.0",
                    exact_proposed_revision_binding={
                        "id": prop2.acceptance_criteria_revision_id,
                        "digest": prop2.record_digest,
                    },
                ),
                principal=same,
                idempotency_key="idem-self-confirm",
            )
        except Exception as exc:
            self_confirm_rejected = True
            print(f"[ok] self-confirm rejected: {type(exc).__name__}")
        if not self_confirm_rejected:
            failures.append("proposer self-confirm was NOT rejected")

    print("\n=== INDEPENDENT DIGEST RECOMPUTATION REPORT ===")
    print(f"binding record_digest independent == stored: {recompute_binding == binding_row.record_digest}")
    print(f"binding_digest independent == stored: {expected_binding_digest == binding_row.binding_digest}")
    print(f"acceptance(PROPOSED) record_digest independent == stored: {recompute_prop == prop_row.record_digest}")
    print(f"acceptance_digest independent == stored: {ad == prop_row.acceptance_digest}")
    print(f"acceptance(CONFIRMED) record_digest independent == stored: {recompute_conf == conf_row.record_digest}")
    print(f"S1A case immutable across bind: {case_after.snapshot_payload == before_snapshot and case_after.record_digest == before_digest and case_after.state == 'OPEN'}")
    print(f"proposer self-confirm rejected: {self_confirm_rejected}")
    print(f"FAILURES: {len(failures)}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
