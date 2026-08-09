"""Release Controller 单元测试（FakeQualityClient + SQLite）。

覆盖：WorkOrder hash 校验 / Approval nonce / 灰度全链路（promote|rollback）/
UNKNOWN→reconcile / 幂等 Idempotency-Key。
"""
from datetime import datetime, timedelta, timezone
import hashlib

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.tables import Approval, GateReportRecord, ReleaseClosure, WorkOrder
from app.quality.client import FakeQualityClient, QualityAPIError
from app.services.audit import AuditWriteError
from app.services.release_service import ReleaseService, ReleaseServiceError
from app.services import release_service as release_service_module
from app.utils.jcs import canonical_json_digest

from tests.conftest import (
    make_action_approval,
    make_approval,
    make_workorder,
    register_gate_for_workorder,
    register_release_verification,
    register_workorder_with_lease,
)


def _settings(**kw) -> Settings:
    base = dict(
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
        canary_observation_seconds=0,
    )
    base.update(kw)
    return Settings(**base)


def _svc(session, quality=None, settings=None) -> tuple[ReleaseService, FakeQualityClient]:
    q = quality or FakeQualityClient()
    q.seed_versionset(
        "vs_baseline0000000000000001", status="active", revision=1, digest="sha256:" + "a" * 64
    )
    q.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    return ReleaseService(session, q, settings or _settings()), q


def _full_release(session, svc, quality, case_id, seed: int):
    """register + grant + start，返回 (release_id, workorder, approval_id)。"""
    nonce = f"00000000-0000-0000-0000-{seed:012d}"
    wo = make_workorder(workorder_id=f"wo_{seed:012d}", nonce=nonce, case_id=case_id)
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    session.flush()
    ap = make_approval(wo, f"ap_{seed}")
    svc.grant_approval(ap)
    session.flush()
    rel = svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    # Release-controller unit tests isolate Quality lifecycle semantics.  The
    # durable closure row is a prerequisite fixture; end-to-end binding to the
    # original complaint is covered by B1/outbox integration tests.
    session.add(
        ReleaseClosure(
            release_id=rel["release_id"],
            case_id=case_id,
            channel="feishu-mock:test:",
            thread_ref=f"thread:{seed}",
            body_ref=f"file:///tmp/caseloop-release-{seed}.txt",
            body_digest="sha256:" + "d" * 64,
            status="configured",
        )
    )
    session.flush()
    return rel["release_id"], wo, ap["approval_id"]


def _action_approval(
    session,
    svc: ReleaseService,
    wo: dict,
    release_id: str,
    action: str,
    idempotency_key: str,
    *,
    reason: str = "manual",
) -> str:
    """Register/reuse one explicit human grant for a test lifecycle action."""

    suffix = hashlib.sha256(
        f"{release_id}:{action}:{idempotency_key}".encode()
    ).hexdigest()[:16]
    approval_id = f"ap_action_{suffix}"
    if session.get(Approval, approval_id) is not None:
        return approval_id
    aggregate = svc.store.get_aggregate("release", release_id)
    assert aggregate is not None
    context = svc._expected_action_context(
        aggregate,
        action,
        params={"reason": reason} if action == "rollback" else None,
    )
    svc.grant_approval(
        make_action_approval(
            wo,
            approval_id=approval_id,
            release_id=release_id,
            action=action,
            target_revision=context["target_revision"],
            params=context["params"],
        )
    )
    session.flush()
    return approval_id


def _canary(session, svc, wo, release_id, idempotency_key, *, percent=None):
    approval_id = _action_approval(
        session,
        svc,
        wo,
        release_id,
        "canary",
        idempotency_key,
    )
    return svc.canary(
        release_id,
        percent=percent,
        idempotency_key=idempotency_key,
        approval_id=approval_id,
    )


def _promote(session, svc, wo, release_id, idempotency_key):
    approval_id = _action_approval(
        session,
        svc,
        wo,
        release_id,
        "promote",
        idempotency_key,
    )
    return svc.promote(
        release_id,
        idempotency_key=idempotency_key,
        approval_id=approval_id,
    )


def _rollback(session, svc, wo, release_id, idempotency_key, *, reason="manual"):
    approval_id = _action_approval(
        session,
        svc,
        wo,
        release_id,
        "rollback",
        idempotency_key,
        reason=reason,
    )
    return svc.rollback(
        release_id,
        reason=reason,
        idempotency_key=idempotency_key,
        approval_id=approval_id,
    )


def test_register_workorder_valid_and_duplicate(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg1", nonce="00000000-0000-0000-0000-000000000001", case_id="case_x")
    register_gate_for_workorder(svc, wo)
    r = register_workorder_with_lease(svc, wo)
    assert r["duplicate"] is False
    r2 = register_workorder_with_lease(svc, wo)
    assert r2["duplicate"] is True


def test_register_workorder_hash_mismatch(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg2", nonce="00000000-0000-0000-0000-000000000002", case_id="case_x")
    wo["hash"] = "0" * 64  # 篡改
    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(wo)
    assert exc.value.code == "hash_mismatch"


def test_grant_approval_nonce_binding(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg3", nonce="00000000-0000-0000-0000-000000000003", case_id="case_x")
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    sqlite_session.flush()
    ap = make_approval(wo, "ap_abcdefg3")
    # 篡改 workorder_hash → 拒绝
    bad = {**ap, "workorder_hash": "1" * 64}
    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(bad)
    assert exc.value.code == "hash_mismatch"


def test_grant_approval_nonce_replay(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg4", nonce="00000000-0000-0000-0000-000000000004", case_id="case_x")
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    sqlite_session.flush()
    ap = make_approval(wo, "ap_abcdefg4a")
    svc.grant_approval(ap)
    sqlite_session.flush()
    # 同一 nonce 再登记 → nonce_replay
    ap2 = make_approval(wo, "ap_abcdefg4b")
    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(ap2)
    assert exc.value.code == "nonce_replay"


def test_approval_expiry_cannot_outlive_workorder(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_expirybind1",
        nonce="00000000-0000-0000-0000-000000000032",
        case_id="case_x",
    )
    wo["expiry"] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    approval = make_approval(wo, "ap_expirybind1")
    approval["expiry"] = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()

    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(approval)

    assert exc.value.code == "validation_failed"


def test_start_rejects_expired_workorder_even_if_grant_was_valid(sqlite_session, monkeypatch):
    svc, _ = _svc(sqlite_session)
    now = datetime.now(timezone.utc)
    wo = make_workorder(
        workorder_id="wo_expiredstart1",
        nonce="00000000-0000-0000-0000-000000000033",
        case_id="case_x",
    )
    wo["expiry"] = (now + timedelta(minutes=10)).isoformat()
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    approval = make_approval(wo, "ap_expiredstart1")
    svc.grant_approval(approval)

    class _FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = now + timedelta(minutes=11)
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(release_service_module, "datetime", _FutureDateTime)
    with pytest.raises(ReleaseServiceError) as exc:
        svc.start_release(
            workorder_id=wo["workorder_id"],
            approval_id=approval["approval_id"],
            versionset_id="vs_demo001fixedversionset01",
        )
    assert exc.value.code == "approval_expired"


def test_start_release_nonce_consumed_and_replay(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    wo = make_workorder(workorder_id="wo_abcdefg5", nonce="00000000-0000-0000-0000-000000000005", case_id=case_id)
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    sqlite_session.flush()
    ap = make_approval(wo, "ap_abcdefg5")
    svc.grant_approval(ap)
    sqlite_session.flush()
    r1 = svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    assert r1["state"] == "REQUESTED"
    sqlite_session.flush()
    # nonce 已消费 → 重放拒绝
    with pytest.raises(ReleaseServiceError) as exc:
        svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    assert exc.value.code == "nonce_replay"


def test_promote_full_path(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 1)
    st = svc.stage(rid, idempotency_key="idem-p-1")
    assert st["state"] == "STAGING"
    sqlite_session.flush()
    ca = _canary(sqlite_session, svc, wo, rid, "idem-p-2")
    assert ca["state"] == "CANARYING"
    sqlite_session.flush()
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_canarypass01",
    )
    vr = svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    assert vr["verification"] == "passed"
    sqlite_session.flush()
    pr = _promote(sqlite_session, svc, wo, rid, "idem-p-3")
    assert pr["state"] == "COMPLETED"
    sqlite_session.flush()
    # 远端 VS 已 active
    vs = q.get_versionset("vs_demo001fixedversionset01")
    assert vs["status"] == "active"


def test_exact_promote_retry_survives_closure_dispatch_after_lost_response(sqlite_session):
    svc, quality = _svc(sqlite_session)
    release_id, workorder, _ = _full_release(
        sqlite_session, svc, quality, "case_promote_retry", 103
    )
    svc.stage(release_id, idempotency_key="idem-promote-retry-stage")
    sqlite_session.flush()
    _canary(
        sqlite_session,
        svc,
        workorder,
        release_id,
        "idem-promote-retry-canary",
    )
    sqlite_session.flush()
    verification = register_release_verification(
        svc,
        workorder,
        quality.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_promoteretry103",
    )
    svc.record_verification(
        release_id,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    first = _promote(
        sqlite_session, svc, workorder, release_id, "idem-promote-retry-final"
    )
    closure = sqlite_session.get(ReleaseClosure, release_id)
    assert closure is not None
    closure.status = "queued"  # RELEASE_PROMOTED has advanced the continuation.
    sqlite_session.flush()

    retried = _promote(
        sqlite_session, svc, workorder, release_id, "idem-promote-retry-final"
    )

    assert retried["duplicate"] is True
    assert retried["operation_id"] == first["operation_id"]
    assert retried["status"] == "succeeded"
    assert quality.call_log.count("promote") == 1


def test_quality_write_sees_committed_intent_and_consumed_grant(
    sqlite_session, sqlite_engine, monkeypatch
):
    """The remote write boundary must be preceded by a durable operation anchor.

    Use an independent ORM session inside the fake Quality call.  Seeing both
    the pending ControllerOperation and consumed action grant there proves the
    controller committed its authorization intent before crossing the network
    boundary, rather than merely flushing it in the caller's transaction.
    """

    svc, quality = _svc(sqlite_session)
    release_id, workorder, _ = _full_release(
        sqlite_session,
        svc,
        quality,
        "case_durable_intent",
        102,
    )
    svc.stage(release_id, idempotency_key="idem-durable-intent-stage")
    approval_id = _action_approval(
        sqlite_session,
        svc,
        workorder,
        release_id,
        "canary",
        "idem-durable-intent-canary",
    )
    original_canary = quality.canary
    observed: dict[str, object] = {}

    def observe_committed_intent(*args, **kwargs):
        observer_factory = sessionmaker(
            bind=sqlite_engine,
            autoflush=False,
            autocommit=False,
        )
        with observer_factory() as observer:
            operation = (
                observer.query(release_service_module.ControllerOperation)
                .filter_by(idempotency_key="idem-durable-intent-canary")
                .one()
            )
            grant = observer.get(Approval, approval_id)
            observed.update(
                operation_status=operation.status,
                operation_approval_id=operation.approval_id,
                grant_status=grant.status if grant is not None else None,
                grant_consumed_operation_id=(grant.payload or {}).get(
                    "consumed_operation_id"
                )
                if grant is not None
                else None,
            )
        return original_canary(*args, **kwargs)

    monkeypatch.setattr(quality, "canary", observe_committed_intent)
    result = svc.canary(
        release_id,
        idempotency_key="idem-durable-intent-canary",
        approval_id=approval_id,
    )

    assert result["state"] == "CANARYING"
    assert observed["operation_status"] == "pending"
    assert observed["operation_approval_id"] == approval_id
    assert observed["grant_status"] == "consumed"
    assert observed["grant_consumed_operation_id"] == result["operation_id"]


def test_post_canary_gate_is_rejected_until_observation_window_completes(sqlite_session):
    svc, q = _svc(
        sqlite_session,
        settings=_settings(canary_observation_seconds=60),
    )
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_observation", 101)
    svc.stage(rid, idempotency_key="idem-observation-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-observation-canary")
    context = svc.verification_context(rid)
    assert context["canary_observation"]["complete"] is False
    assert context["canary_observation"]["required_seconds"] == 60
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_observation101",
    )

    with pytest.raises(ReleaseServiceError) as exc:
        svc.record_verification(
            rid,
            eval_id=verification["eval_id"],
            report_hash=canonical_json_digest(verification, prefix=False),
        )

    assert exc.value.code == "illegal_transition"
    assert "observation window" in exc.value.message
    assert svc.get_release(rid)["state"] == "CANARYING"


def test_rollback_full_path(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 2)
    svc.stage(rid, idempotency_key="idem-rb-1")
    sqlite_session.flush()
    _canary(sqlite_session, svc, wo, rid, "idem-rb-2")
    sqlite_session.flush()
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="failed",
        eval_id="eval_canaryfail02",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    sqlite_session.flush()
    rb = _rollback(sqlite_session, svc, wo, rid, "idem-rb-3")
    assert rb["state"] == "ROLLED_BACK"
    sqlite_session.flush()
    vs = q.get_versionset("vs_demo001fixedversionset01")
    assert vs["status"] == "rolled_back"


def test_post_canary_error_blocks_promote_and_allows_only_approved_rollback(sqlite_session):
    svc, quality = _svc(sqlite_session)
    release_id, workorder, _ = _full_release(
        sqlite_session, svc, quality, "case_error_compensation", 106
    )
    svc.stage(release_id, idempotency_key="idem-error-stage")
    _canary(
        sqlite_session,
        svc,
        workorder,
        release_id,
        "idem-error-canary",
    )
    verification = register_release_verification(
        svc,
        workorder,
        quality.get_versionset("vs_demo001fixedversionset01"),
        overall_status="error",
        eval_id="eval_errorcompensation106",
    )
    recorded = svc.record_verification(
        release_id,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    assert recorded["state"] == "VERIFYING"
    assert recorded["verification"] == "error"

    calls_before = list(quality.call_log)
    with pytest.raises(ReleaseServiceError) as exc:
        svc.promote(release_id, idempotency_key="idem-error-promote")
    assert exc.value.code == "illegal_transition"
    assert quality.call_log == calls_before

    rolled_back = _rollback(
        sqlite_session,
        svc,
        workorder,
        release_id,
        "idem-error-rollback",
        reason="post-canary evaluator error",
    )
    assert rolled_back["state"] == "ROLLED_BACK"
    assert quality.get_versionset("vs_demo001fixedversionset01")["status"] == "rolled_back"
    rollback_started = next(
        event
        for event in svc.store.list_events(release_id)
        if event.event_type == "release.rollback_started"
    )
    assert rollback_started.payload["reason"] == "post-canary evaluator error"


def test_promote_without_verification_fails_before_quality_write(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 20)
    svc.stage(rid, idempotency_key="idem-noverify-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-noverify-canary")
    calls_before = list(q.call_log)
    with pytest.raises(ReleaseServiceError) as exc:
        svc.promote(rid, idempotency_key="idem-noverify-promote")
    assert exc.value.code == "illegal_transition"
    assert q.call_log == calls_before


def test_stage_rejects_revision_drift_before_quality_write(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_x", 23)
    q._vs["vs_demo001fixedversionset01"].revision = 2
    calls_before = list(q.call_log)

    with pytest.raises(ReleaseServiceError) as exc:
        svc.stage(rid, idempotency_key="idem-drift-stage")

    assert exc.value.code == "revision_conflict"
    assert q.call_log == calls_before


def test_promote_rejects_drift_after_verification_before_quality_write(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 24)
    svc.stage(rid, idempotency_key="idem-drift-promote-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-drift-promote-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_driftpromote24",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    q._vs["vs_demo001fixedversionset01"].revision += 1
    calls_before = list(q.call_log)

    with pytest.raises(ReleaseServiceError) as exc:
        approval_id = _action_approval(
            sqlite_session,
            svc,
            wo,
            rid,
            "promote",
            "idem-drift-promote",
        )
        svc.promote(
            rid,
            idempotency_key="idem-drift-promote",
            approval_id=approval_id,
        )

    assert exc.value.code == "revision_conflict"
    assert q.call_log == calls_before


def test_post_canary_report_requires_final_workorder_authorization_binding(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 35)
    svc.stage(rid, idempotency_key="idem-authz-gate-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-authz-gate-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_authzbinding35",
    )
    row = sqlite_session.get(GateReportRecord, verification["eval_id"])
    assert row is not None and row.authorization_digest
    row.authorization_digest = "sha256:" + "0" * 64
    sqlite_session.flush()

    with pytest.raises(ReleaseServiceError) as exc:
        svc.record_verification(
            rid,
            eval_id=verification["eval_id"],
            report_hash=canonical_json_digest(verification, prefix=False),
        )

    assert exc.value.code == "hash_mismatch"


def test_unknown_receipt_blocks_new_write_until_reconcile(sqlite_session, monkeypatch):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_x", 21)

    def malformed_stage(*_args, **_kwargs):
        q.call_log.append("stage")
        return {
            "operation_id": "op_malformedreceipt01",
            "result": {"revision": 2, "status": "staged"},
        }

    monkeypatch.setattr(q, "stage", malformed_stage)
    unknown = svc.stage(rid, idempotency_key="idem-malformed-stage")
    assert unknown["status"] == "unknown"
    assert unknown["reconcile_required"] is True
    calls_before = list(q.call_log)
    with pytest.raises(ReleaseServiceError) as exc:
        svc.stage(rid, idempotency_key="idem-malformed-retry")
    assert exc.value.code == "illegal_transition"
    assert q.call_log == calls_before


def test_idempotency_key_cannot_cross_operation_kind(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_x", 22)
    svc.stage(rid, idempotency_key="idem-cross-kind")
    with pytest.raises(ReleaseServiceError) as exc:
        svc.canary(rid, idempotency_key="idem-cross-kind")
    assert exc.value.code == "idempotency_conflict"


def test_idempotency_key_cannot_change_canary_percent(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 26)
    svc.stage(rid, idempotency_key="idem-canary-param-stage")
    approval_id = _action_approval(
        sqlite_session,
        svc,
        wo,
        rid,
        "canary",
        "idem-canary-param",
    )
    svc.canary(
        rid,
        percent=5,
        idempotency_key="idem-canary-param",
        approval_id=approval_id,
    )
    calls_before = list(q.call_log)

    with pytest.raises(ReleaseServiceError) as exc:
        svc.canary(
            rid,
            percent=25,
            idempotency_key="idem-canary-param",
            approval_id=approval_id,
        )

    assert exc.value.code == "idempotency_conflict"
    assert q.call_log == calls_before


def test_caller_cannot_skip_controller_owned_canary_step(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_x", 34)
    svc.stage(rid, idempotency_key="idem-owned-canary-stage")
    calls_before = list(q.call_log)

    with pytest.raises(ReleaseServiceError) as exc:
        svc.canary(rid, percent=100, idempotency_key="idem-owned-canary-jump")

    assert exc.value.code == "validation_failed"
    assert q.call_log == calls_before


def test_idempotency_key_dedupe(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 3)
    r1 = svc.stage(rid, idempotency_key="idem-dup-1")
    sqlite_session.flush()
    r2 = svc.stage(rid, idempotency_key="idem-dup-1")  # 同 key 重放
    assert r2["duplicate"] is True
    assert r2["operation_id"] == r1["operation_id"]


def test_unknown_then_reconcile(sqlite_session):
    q = FakeQualityClient()
    q.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    svc, _ = _svc(sqlite_session, quality=q)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 4)
    svc.stage(rid, idempotency_key="idem-un-1")
    sqlite_session.flush()
    # 后续 op 进入 pending → 轮询超时 → UNKNOWN
    q.unknown_ops = True
    unk = _canary(sqlite_session, svc, wo, rid, "idem-un-2")
    assert unk["state"] == "UNKNOWN"
    assert unk["status"] == "unknown"
    sqlite_session.flush()
    rc = svc.reconcile(rid)
    assert rc["state"] == "CANARYING"
    assert rc["remote_status"] == "canary"
    assert rc["action"] == "apply_canary"
    lifecycle_events = [
        event
        for event in svc.store.list_events(rid)
        if event.event_type in ("release.staged", "release.canary_started")
    ]
    assert [event.event_type for event in lifecycle_events] == [
        "release.staged",
        "release.canary_started",
    ]
    assert [event.payload["revision"] for event in lifecycle_events] == [2, 3]
    sqlite_session.flush()


def test_reconcile_replays_never_accepted_stage_with_same_idempotency_key(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_x", 27)
    q.fail_next = "network"
    unknown = svc.stage(rid, idempotency_key="idem-resume-stage")
    assert unknown["status"] == "unknown"
    assert unknown["state"] == "UNKNOWN"
    assert svc.get_release(rid)["state"] == "UNKNOWN"
    events = svc.store.list_events(rid)
    assert events[-1].event_type == "release.unknown_detected"
    assert events[-1].payload["kind"] == "stage"

    reconciled = svc.reconcile(rid)
    assert reconciled["action"] == "resume"
    assert reconciled["state"] == "STAGING"
    assert reconciled["remote_operation_id"].startswith("op_")
    assert q.call_log.count("stage") == 2


def test_reconcile_replays_never_accepted_promote_and_confirms_exact_receipt(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 32)
    svc.stage(rid, idempotency_key="idem-resume-promote-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-resume-promote-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_resumepromote32",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    approval_id = _action_approval(
        sqlite_session,
        svc,
        wo,
        rid,
        "promote",
        "idem-resume-promote",
    )
    q.fail_next = "network"
    unknown = svc.promote(
        rid,
        idempotency_key="idem-resume-promote",
        approval_id=approval_id,
    )
    assert unknown["state"] == "UNKNOWN"
    events = svc.store.list_events(rid)
    assert events[-1].event_type == "release.unknown_detected"
    assert events[-1].payload["kind"] == "promote"

    reconciled = svc.reconcile(rid)
    assert reconciled["action"] == "confirm_promote"
    assert reconciled["state"] == "COMPLETED"
    assert reconciled["remote_operation_id"].startswith("op_")
    assert q.call_log.count("promote") == 2


def test_reconcile_applied_promote_persists_promoted_receipt(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 33)
    svc.stage(rid, idempotency_key="idem-applied-promote-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-applied-promote-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_appliedpromote33",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    approval_id = _action_approval(
        sqlite_session,
        svc,
        wo,
        rid,
        "promote",
        "idem-applied-promote",
    )
    q.unknown_ops = True
    unknown = svc.promote(
        rid,
        idempotency_key="idem-applied-promote",
        approval_id=approval_id,
    )
    assert unknown["state"] == "UNKNOWN"
    assert q.get_versionset("vs_demo001fixedversionset01")["status"] == "active"

    reconciled = svc.reconcile(rid)
    assert reconciled["action"] == "confirm_promote"
    assert reconciled["state"] == "COMPLETED"
    lifecycle_events = [
        event
        for event in svc.store.list_events(rid)
        if event.event_type in (
            "release.unknown_detected",
            "release.reconciled",
            "release.promoted",
        )
    ]
    assert [event.event_type for event in lifecycle_events[-3:]] == [
        "release.unknown_detected",
        "release.reconciled",
        "release.promoted",
    ]
    promoted = lifecycle_events[-1]
    assert promoted.payload["revision"] == 4
    assert promoted.payload["operation_id"] == reconciled["remote_operation_id"]
    assert promoted.payload["operation_id"] != unknown["operation_id"]
    release = svc.get_release(rid)
    assert release["payload"]["promoted"] is True
    assert release["payload"]["remote_revision"] == 4


def test_reconcile_replays_never_accepted_canary_without_duplicate_stage(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 30)
    svc.stage(rid, idempotency_key="idem-resume-canary-stage")
    q.fail_next = "network"
    unknown = _canary(sqlite_session, svc, wo, rid, "idem-resume-canary")
    assert unknown["state"] == "UNKNOWN"

    reconciled = svc.reconcile(rid)
    assert reconciled["action"] == "apply_canary"
    assert reconciled["state"] == "CANARYING"
    assert [
        event.event_type
        for event in svc.store.list_events(rid)
        if event.event_type == "release.staged"
    ] == ["release.staged"]

    assert q.call_log.count("canary") == 2


def test_reconcile_replays_never_accepted_rollback_to_terminal_receipt(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_x", 31)
    svc.stage(rid, idempotency_key="idem-resume-rb-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-resume-rb-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="failed",
        eval_id="eval_resumerollback31",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    q.fail_next = "network"
    unknown = _rollback(sqlite_session, svc, wo, rid, "idem-resume-rollback")
    assert unknown["state"] == "UNKNOWN"

    reconciled = svc.reconcile(rid)
    assert reconciled["action"] == "compensate"
    assert reconciled["state"] == "ROLLED_BACK"
    assert q.call_log.count("rollback") == 2


def test_reconcile_keeps_delayed_promote_unknown_until_exact_operation_applies(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_delayed_promote", 51)
    svc.stage(rid, idempotency_key="idem-delayed-promote-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-delayed-promote-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_delayedpromote51",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    approval_id = _action_approval(
        sqlite_session,
        svc,
        wo,
        rid,
        "promote",
        "idem-delayed-promote",
    )
    q.defer_effects = True
    unknown = svc.promote(
        rid,
        idempotency_key="idem-delayed-promote",
        approval_id=approval_id,
    )
    assert unknown["state"] == "UNKNOWN"
    aggregate = svc.store.get_aggregate("release", rid)
    operation = sqlite_session.get(
        release_service_module.ControllerOperation,
        aggregate.payload["unknown_op"],
    )
    assert operation.remote_operation_id is not None

    still_unknown = svc.reconcile(rid)
    assert still_unknown["state"] == "UNKNOWN"
    assert still_unknown["action"] == "wait"
    assert svc.get_release(rid)["payload"]["unknown_op"] == operation.operation_id

    q.complete_pending(operation.remote_operation_id)
    resolved = svc.reconcile(rid)
    assert resolved["state"] == "COMPLETED"
    assert resolved["action"] == "confirm_promote"
    promoted = [
        event for event in svc.store.list_events(rid) if event.event_type == "release.promoted"
    ]
    assert len(promoted) == 1
    assert promoted[0].payload["operation_id"] == operation.remote_operation_id


def test_reconcile_fails_closed_on_failed_operation_with_visible_transition(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_inconsistent_receipt", 56)
    svc.stage(rid, idempotency_key="idem-inconsistent-stage")
    q.unknown_ops = True
    unknown = _canary(sqlite_session, svc, wo, rid, "idem-inconsistent-canary")
    aggregate = svc.store.get_aggregate("release", rid)
    operation = sqlite_session.get(
        release_service_module.ControllerOperation,
        aggregate.payload["unknown_op"],
    )
    q.complete_pending(operation.remote_operation_id, status="failed")

    with pytest.raises(ReleaseServiceError) as exc:
        svc.reconcile(rid)

    assert exc.value.code == "hash_mismatch"
    assert svc.get_release(rid)["state"] == "UNKNOWN"
    assert unknown["operation_id"] == operation.operation_id


def test_invalid_remote_revision_fails_before_quality_write(sqlite_session, monkeypatch):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_x", 28)
    original = q.get_versionset

    def invalid_revision(versionset_id):
        return {**original(versionset_id), "revision": "1"}

    monkeypatch.setattr(q, "get_versionset", invalid_revision)
    calls_before = list(q.call_log)
    with pytest.raises(ReleaseServiceError) as exc:
        svc.stage(rid, idempotency_key="idem-invalid-revision")
    assert exc.value.code == "quality_api_error"
    assert q.call_log == calls_before


def test_mismatched_remote_receipt_enters_unknown(sqlite_session, monkeypatch):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_x", 29)

    def wrong_target(*_args, **_kwargs):
        q.call_log.append("stage")
        return {
            "operation_id": "op_wrongtarget0001",
            "status": "succeeded",
            "kind": "stage",
            "versionset_id": "vs_another_target",
            "result": {"revision": 2, "status": "staged"},
        }

    monkeypatch.setattr(q, "stage", wrong_target)
    result = svc.stage(rid, idempotency_key="idem-wrong-receipt-target")
    assert result["status"] == "unknown"
    assert result["reconcile_required"] is True


def test_reconcile_loop_converges(sqlite_session):
    q = FakeQualityClient()
    q.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    svc, _ = _svc(sqlite_session, quality=q)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 5)
    svc.stage(rid, idempotency_key="idem-lp-1")
    sqlite_session.flush()
    q.unknown_ops = True
    _canary(sqlite_session, svc, wo, rid, "idem-lp-2")
    sqlite_session.flush()
    last = svc.reconcile_loop(rid, max_attempts=3)
    assert last["state"] == "CANARYING"


def test_audit_failure_blocks_release_write(sqlite_session):
    settings = _settings(audit_force_fail=True)
    q = FakeQualityClient()
    q.seed_versionset(
        "vs_demo001fixedversionset01",
        status="draft",
        revision=1,
        digest="sha256:" + "b" * 64,
    )
    svc = ReleaseService(sqlite_session, q, settings)
    wo = make_workorder(workorder_id="wo_abcdefg6", nonce="00000000-0000-0000-0000-000000000006", case_id="case_x")
    # Seed the gate under a healthy audit service, then exercise WorkOrder audit failure.
    healthy = ReleaseService(sqlite_session, q, _settings())
    register_gate_for_workorder(healthy, wo)
    sqlite_session.flush()
    with pytest.raises(AuditWriteError):
        register_workorder_with_lease(svc, wo)
    # 审计失败 → 业务拒绝（同事务回滚后 WorkOrder 不落库）
    sqlite_session.rollback()
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is None


def test_audit_failure_blocks_quality_write_before_remote_call(sqlite_session):
    healthy, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, healthy, q, "case_x", 25)
    failing = ReleaseService(sqlite_session, q, _settings(audit_force_fail=True))
    calls_before = list(q.call_log)

    with pytest.raises(AuditWriteError):
        failing.stage(rid, idempotency_key="idem-audit-before-stage")

    assert q.call_log == calls_before


def test_audit_failure_blocks_unknown_reconcile_replay_before_remote_call(sqlite_session):
    healthy, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, healthy, q, "case_reconcile_audit", 54)
    q.fail_next = "network"
    unknown = healthy.stage(rid, idempotency_key="idem-audit-reconcile-stage")
    assert unknown["state"] == "UNKNOWN"
    calls_before = list(q.call_log)
    failing = ReleaseService(sqlite_session, q, _settings(audit_force_fail=True))

    with pytest.raises(AuditWriteError):
        failing.reconcile(rid)

    assert q.call_log == calls_before
    assert failing.get_release(rid)["state"] == "UNKNOWN"


def test_canary_requires_fresh_action_approval(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, _, _ = _full_release(sqlite_session, svc, q, "case_action_required", 40)
    svc.stage(rid, idempotency_key="idem-action-required-stage")
    calls_before = list(q.call_log)

    with pytest.raises(ReleaseServiceError) as exc:
        svc.canary(rid, idempotency_key="idem-action-required-canary")

    assert exc.value.code == "validation_failed"
    assert q.call_log == calls_before


def test_action_grant_cannot_be_used_as_initial_release_approval(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_action_not_initial", 41)
    svc.stage(rid, idempotency_key="idem-action-not-initial-stage")
    action_id = _action_approval(
        sqlite_session,
        svc,
        wo,
        rid,
        "canary",
        "idem-action-not-initial-canary",
    )

    with pytest.raises(ReleaseServiceError) as exc:
        svc.start_release(
            workorder_id=wo["workorder_id"],
            approval_id=action_id,
            versionset_id="vs_demo001fixedversionset01",
        )

    assert exc.value.code == "hash_mismatch"


def test_action_grant_nonce_is_consumed_by_exact_operation(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_action_nonce", 42)
    svc.stage(rid, idempotency_key="idem-action-nonce-stage")
    key = "idem-action-nonce-canary"
    approval_id = _action_approval(sqlite_session, svc, wo, rid, "canary", key)

    first = svc.canary(rid, idempotency_key=key, approval_id=approval_id)
    grant = sqlite_session.get(Approval, approval_id)
    assert grant is not None and grant.status == "consumed"
    assert grant.payload["consumed_operation_id"] == first["operation_id"]
    assert grant.payload["nonce_consumed"] is True

    replay = svc.canary(rid, idempotency_key=key, approval_id=approval_id)
    assert replay["duplicate"] is True
    assert replay["operation_id"] == first["operation_id"]


def test_stage_rechecks_expiry_after_release_start(sqlite_session, monkeypatch):
    svc, q = _svc(sqlite_session)
    now = datetime.now(timezone.utc)
    wo = make_workorder(
        workorder_id="wo_expire_after_start",
        nonce="00000000-0000-0000-0000-000000000043",
        case_id="case_expire_after_start",
    )
    wo["expiry"] = (now + timedelta(minutes=10)).isoformat()
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    approval = make_approval(wo, "ap_expire_after_start")
    svc.grant_approval(approval)
    release = svc.start_release(
        workorder_id=wo["workorder_id"],
        approval_id=approval["approval_id"],
        versionset_id="vs_demo001fixedversionset01",
    )

    class _AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            value = now + timedelta(minutes=11)
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(release_service_module, "datetime", _AfterExpiry)
    calls_before = list(q.call_log)
    with pytest.raises(ReleaseServiceError) as exc:
        svc.stage(release["release_id"], idempotency_key="idem-expired-late-stage")

    assert exc.value.code == "approval_expired"
    assert q.call_log == calls_before


def test_stage_recomputes_workorder_hash_after_start(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_wo_tamper", 44)
    stored = sqlite_session.get(WorkOrder, wo["workorder_id"])
    stored.payload = {
        **stored.payload,
        "diff": {**stored.payload["diff"], "content": "post-approval replacement"},
    }
    sqlite_session.flush()
    calls_before = list(q.call_log)

    with pytest.raises(ReleaseServiceError) as exc:
        svc.stage(rid, idempotency_key="idem-wo-tamper-stage")

    assert exc.value.code == "hash_mismatch"
    assert q.call_log == calls_before


def test_rollback_wrong_restored_digest_stays_unknown(sqlite_session, monkeypatch):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_wrong_restore", 45)
    svc.stage(rid, idempotency_key="idem-wrong-restore-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-wrong-restore-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="failed",
        eval_id="eval_wrongrestore45",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    key = "idem-wrong-restore-rollback"
    approval_id = _action_approval(sqlite_session, svc, wo, rid, "rollback", key)

    def wrong_restore(versionset_id, _rollback_to, *, if_match, idempotency_key):
        q.call_log.append("rollback")
        return {
            "operation_id": "op_wrong_restore_45",
            "status": "succeeded",
            "kind": "rollback",
            "versionset_id": versionset_id,
            "result": {
                "revision": int(if_match) + 1,
                "status": "rolled_back",
                "restored_digest": "sha256:" + "c" * 64,
            },
        }

    monkeypatch.setattr(q, "rollback", wrong_restore)
    result = svc.rollback(rid, idempotency_key=key, approval_id=approval_id)

    assert result["status"] == "unknown"
    assert result["state"] == "UNKNOWN"


def test_rollback_terminal_failure_escalates_in_authoritative_state(sqlite_session, monkeypatch):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_rollback_fail", 46)
    svc.stage(rid, idempotency_key="idem-rb-fail-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-rb-fail-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="failed",
        eval_id="eval_rollbackfail46",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    key = "idem-rb-fail-rollback"
    approval_id = _action_approval(sqlite_session, svc, wo, rid, "rollback", key)

    def reject_rollback(*_args, **_kwargs):
        q.call_log.append("rollback")
        raise QualityAPIError("validation_failed", "rollback target unavailable", status_code=422)

    monkeypatch.setattr(q, "rollback", reject_rollback)
    result = svc.rollback(rid, idempotency_key=key, approval_id=approval_id)

    assert result["state"] == "FAILED_ESCALATED"
    assert result["manual_intervention_required"] is True


def test_reconcile_rejects_canary_percent_not_bound_to_grant(sqlite_session, monkeypatch):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_canary_reconcile", 47)
    svc.stage(rid, idempotency_key="idem-canary-reconcile-stage")
    q.unknown_ops = True
    result = _canary(sqlite_session, svc, wo, rid, "idem-canary-reconcile")
    assert result["state"] == "UNKNOWN"
    original_status = q.get_status

    def wrong_percent(versionset_id):
        status = original_status(versionset_id)
        return {**status, "canary": {"percent": 25}, "canary_percent": 25}

    monkeypatch.setattr(q, "get_status", wrong_percent)
    with pytest.raises(ReleaseServiceError) as exc:
        svc.reconcile(rid)

    assert exc.value.code == "target_mismatch"
    assert svc.get_release(rid)["state"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("action", "verification_status", "eval_id", "seed"),
    [
        ("promote", "passed", "eval_tamperpromote52", 52),
        ("rollback", "failed", "eval_tamperrollback53", 53),
    ],
)
def test_lifecycle_write_revalidates_post_canary_gate_after_action_approval(
    sqlite_session,
    action,
    verification_status,
    eval_id,
    seed,
):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, f"case_tamper_{action}", seed)
    svc.stage(rid, idempotency_key=f"idem-tamper-{action}-stage")
    _canary(sqlite_session, svc, wo, rid, f"idem-tamper-{action}-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status=verification_status,
        eval_id=eval_id,
    )
    svc.record_verification(
        rid,
        eval_id=eval_id,
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    key = f"idem-tamper-{action}-write"
    approval_id = _action_approval(sqlite_session, svc, wo, rid, action, key)
    row = sqlite_session.get(GateReportRecord, eval_id)
    row.report = {
        **row.report,
        "overall_status": "failed" if verification_status == "passed" else "passed",
    }
    sqlite_session.flush()
    calls_before = list(q.call_log)

    with pytest.raises(ReleaseServiceError) as exc:
        if action == "promote":
            svc.promote(rid, idempotency_key=key, approval_id=approval_id)
        else:
            svc.rollback(rid, idempotency_key=key, approval_id=approval_id)

    assert exc.value.code == "hash_mismatch"
    assert q.call_log == calls_before


def test_promote_fails_when_approved_active_baseline_has_been_replaced(sqlite_session):
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, "case_active_drift", 55)
    svc.stage(rid, idempotency_key="idem-active-drift-stage")
    _canary(sqlite_session, svc, wo, rid, "idem-active-drift-canary")
    verification = register_release_verification(
        svc,
        wo,
        q.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_activedrift55",
    )
    svc.record_verification(
        rid,
        eval_id=verification["eval_id"],
        report_hash=canonical_json_digest(verification, prefix=False),
    )
    key = "idem-active-drift-promote"
    approval_id = _action_approval(sqlite_session, svc, wo, rid, "promote", key)
    q._vs["vs_baseline0000000000000001"].status = "superseded"
    q.seed_versionset(
        "vs_replacement_active_55",
        status="active",
        revision=1,
        digest="sha256:" + "c" * 64,
    )

    with pytest.raises(ReleaseServiceError) as exc:
        svc.promote(rid, idempotency_key=key, approval_id=approval_id)

    assert exc.value.code == "quality_api_error"
    assert svc.get_release(rid)["state"] == "VERIFYING"
    assert q.get_versionset("vs_demo001fixedversionset01")["status"] == "canary"


@pytest.mark.parametrize("tamper", ["release_id", "kind", "approval_id"])
def test_reconcile_rejects_tampered_operation_grant_chain(sqlite_session, tamper):
    seed = {"release_id": 48, "kind": 49, "approval_id": 50}[tamper]
    svc, q = _svc(sqlite_session)
    rid, wo, _ = _full_release(sqlite_session, svc, q, f"case_reconcile_{tamper}", seed)
    svc.stage(rid, idempotency_key=f"idem-reconcile-{tamper}-stage")
    q.unknown_ops = True
    result = _canary(
        sqlite_session,
        svc,
        wo,
        rid,
        f"idem-reconcile-{tamper}-canary",
    )
    assert result["state"] == "UNKNOWN"
    aggregate = svc.store.get_aggregate("release", rid)
    operation = sqlite_session.get(
        release_service_module.ControllerOperation,
        aggregate.payload["unknown_op"],
    )
    if tamper == "release_id":
        operation.release_id = "rel_another"
    elif tamper == "kind":
        operation.kind = "promote"
    else:
        operation.approval_id = None
    sqlite_session.flush()

    with pytest.raises(ReleaseServiceError) as exc:
        svc.reconcile(rid)

    assert exc.value.code == "hash_mismatch"
    assert svc.get_release(rid)["state"] == "UNKNOWN"
