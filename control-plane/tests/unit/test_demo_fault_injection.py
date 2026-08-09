"""Controlled B1 injection is authoritative, idempotent, and fail closed."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.api.evidence_export import (
    _audit_matches_case_evidence,
    _event_matches_case_evidence,
    _related_demo_fault_operations,
)
from app.models.tables import Aggregate, Audit, Event
from app.quality.client import FakeQualityClient
from app.services.audit import AuditWriteError
from app.services.release_service import ReleaseService, ReleaseServiceError


GOOD_ID = "vs_baseline0000000001"
BAD_ID = "vs_b1fault000000000001"
CANDIDATE_ID = "vs_b1candidate00000001"


def _quality() -> FakeQualityClient:
    quality = FakeQualityClient()
    quality.seed_versionset(GOOD_ID, status="active", digest="sha256:" + "a" * 64)
    quality.seed_versionset(BAD_ID, status="draft", digest="sha256:" + "b" * 64)
    return quality


def test_demo_fault_injection_is_disabled_by_default(sqlite_session):
    quality = _quality()
    service = ReleaseService(sqlite_session, quality, Settings(database_url="sqlite:///:memory:"))
    with pytest.raises(ReleaseServiceError) as exc:
        service.inject_demo_fault(
            fault_id="B1",
            expected_active_versionset_id=GOOD_ID,
            fault_versionset_id=BAD_ID,
            idempotency_key="inject-disabled-1",
        )
    assert exc.value.code == "validation_failed"
    assert "inject_fault" not in quality.call_log


def test_demo_fault_injection_binds_exact_receipt_and_retry(sqlite_session):
    quality = _quality()
    settings = Settings(database_url="sqlite:///:memory:", allow_demo_fault_injection=True)
    service = ReleaseService(sqlite_session, quality, settings)
    receipt = service.inject_demo_fault(
        fault_id="B1",
        expected_active_versionset_id=GOOD_ID,
        fault_versionset_id=BAD_ID,
        idempotency_key="inject-exact-1",
    )
    assert receipt["previous_versionset_id"] == GOOD_ID
    assert receipt["fault_versionset_id"] == BAD_ID
    assert receipt["duplicate"] is False
    assert receipt["provider_duplicate"] is False
    assert quality.get_versionset(GOOD_ID)["status"] == "superseded"
    assert quality.get_versionset(BAD_ID)["status"] == "active"

    duplicate = service.inject_demo_fault(
        fault_id="B1",
        expected_active_versionset_id=GOOD_ID,
        fault_versionset_id=BAD_ID,
        idempotency_key="inject-exact-1",
    )
    assert duplicate["duplicate"] is True
    assert duplicate["provider_duplicate"] is False
    assert quality.call_log.count("inject_fault") == 1
    audits = list(sqlite_session.query(Audit).filter(Audit.action.like("demo_fault.B1.inject%")))
    assert {row.target for row in audits} == {"inject-exact-1"}

    with pytest.raises(ReleaseServiceError) as exc:
        service.inject_demo_fault(
            fault_id="B1",
            expected_active_versionset_id=GOOD_ID,
            fault_versionset_id="vs_other_fault_target",
            idempotency_key="inject-exact-1",
        )
    assert exc.value.code == "idempotency_conflict"


def test_demo_fault_new_controller_operation_can_resume_active_provider_fault(
    sqlite_session,
):
    quality = _quality()
    settings = Settings(database_url="sqlite:///:memory:", allow_demo_fault_injection=True)
    service = ReleaseService(sqlite_session, quality, settings)

    first = service.inject_demo_fault(
        fault_id="B1",
        expected_active_versionset_id=GOOD_ID,
        fault_versionset_id=BAD_ID,
        idempotency_key="inject-first-live-run",
    )
    resumed = service.inject_demo_fault(
        fault_id="B1",
        expected_active_versionset_id=GOOD_ID,
        fault_versionset_id=BAD_ID,
        idempotency_key="inject-resumed-live-run",
    )

    assert first["duplicate"] is False
    assert first["provider_duplicate"] is False
    assert resumed["duplicate"] is False
    assert resumed["provider_duplicate"] is True
    assert resumed["previous_versionset_id"] == GOOD_ID
    assert resumed["fault_versionset_id"] == BAD_ID
    assert quality.get_versionset(GOOD_ID)["status"] == "superseded"
    assert quality.get_versionset(BAD_ID)["status"] == "active"
    assert quality.call_log.count("inject_fault") == 2
    audits = list(sqlite_session.query(Audit).filter(Audit.action.like("demo_fault.B1.inject%")))
    assert {row.target for row in audits} == {
        "inject-first-live-run",
        "inject-resumed-live-run",
    }


def test_demo_fault_recovery_restores_exact_pair_and_is_idempotent(sqlite_session):
    quality = _quality()
    settings = Settings(database_url="sqlite:///:memory:", allow_demo_fault_injection=True)
    service = ReleaseService(sqlite_session, quality, settings)
    service.inject_demo_fault(
        fault_id="B1",
        expected_active_versionset_id=GOOD_ID,
        fault_versionset_id=BAD_ID,
        idempotency_key="inject-for-recovery",
    )
    quality.seed_versionset(
        CANDIDATE_ID,
        status="canary",
        revision=3,
        digest="sha256:" + "c" * 64,
    )

    receipt = service.recover_demo_fault(
        fault_id="B1",
        expected_active_fault_versionset_id=BAD_ID,
        restore_versionset_id=GOOD_ID,
        quarantine_versionset_id=CANDIDATE_ID,
        idempotency_key="recover-exact-1",
    )
    assert receipt["restored_versionset_id"] == GOOD_ID
    assert receipt["fault_versionset_id"] == BAD_ID
    assert quality.get_versionset(GOOD_ID)["status"] == "active"
    assert quality.get_versionset(BAD_ID)["status"] == "draft"
    assert receipt["quarantined_versionset_id"] == CANDIDATE_ID
    assert quality.get_versionset(CANDIDATE_ID)["status"] == "rolled_back"

    duplicate = service.recover_demo_fault(
        fault_id="B1",
        expected_active_fault_versionset_id=BAD_ID,
        restore_versionset_id=GOOD_ID,
        quarantine_versionset_id=CANDIDATE_ID,
        idempotency_key="recover-exact-1",
    )
    assert duplicate["duplicate"] is True
    assert duplicate["provider_duplicate"] is False
    assert quality.call_log.count("recover_fault") == 1


def test_demo_fault_recovery_audit_failure_prevents_quality_mutation(sqlite_session):
    quality = _quality()
    healthy = ReleaseService(
        sqlite_session,
        quality,
        Settings(database_url="sqlite:///:memory:", allow_demo_fault_injection=True),
    )
    healthy.inject_demo_fault(
        fault_id="B1",
        expected_active_versionset_id=GOOD_ID,
        fault_versionset_id=BAD_ID,
        idempotency_key="inject-before-recovery-audit",
    )
    failing = ReleaseService(
        sqlite_session,
        quality,
        Settings(
            database_url="sqlite:///:memory:",
            allow_demo_fault_injection=True,
            audit_force_fail=True,
        ),
    )
    with pytest.raises(AuditWriteError):
        failing.recover_demo_fault(
            fault_id="B1",
            expected_active_fault_versionset_id=BAD_ID,
            restore_versionset_id=GOOD_ID,
            idempotency_key="recover-audit-fail",
        )
    assert "recover_fault" not in quality.call_log
    assert quality.get_versionset(BAD_ID)["status"] == "active"


def test_demo_fault_audit_failure_prevents_quality_mutation(sqlite_session):
    quality = _quality()
    settings = Settings(
        database_url="sqlite:///:memory:",
        allow_demo_fault_injection=True,
        audit_force_fail=True,
    )
    service = ReleaseService(sqlite_session, quality, settings)
    with pytest.raises(AuditWriteError):
        service.inject_demo_fault(
            fault_id="B1",
            expected_active_versionset_id=GOOD_ID,
            fault_versionset_id=BAD_ID,
            idempotency_key="inject-audit-fail",
        )
    assert "inject_fault" not in quality.call_log
    assert quality.get_versionset(GOOD_ID)["status"] == "active"
    assert quality.get_versionset(BAD_ID)["status"] == "draft"


def test_demo_fault_invalid_provider_activation_time_fails_closed(
    sqlite_session, monkeypatch
):
    quality = _quality()
    original = quality.inject_fault

    def invalid_timestamp(*args, **kwargs):
        receipt = original(*args, **kwargs)
        return {**receipt, "injected_at": "not-a-timestamp"}

    monkeypatch.setattr(quality, "inject_fault", invalid_timestamp)
    service = ReleaseService(
        sqlite_session,
        quality,
        Settings(database_url="sqlite:///:memory:", allow_demo_fault_injection=True),
    )

    with pytest.raises(ReleaseServiceError) as exc:
        service.inject_demo_fault(
            fault_id="B1",
            expected_active_versionset_id=GOOD_ID,
            fault_versionset_id=BAD_ID,
            idempotency_key="inject-invalid-time",
        )

    assert exc.value.code == "quality_api_error"
    aggregate = sqlite_session.get(Aggregate, ("demo_fault_injection", "inject-invalid-time"))
    assert aggregate is not None and aggregate.state == "UNKNOWN"


def test_b1_evidence_export_selects_only_case_bound_fault_lifecycle(sqlite_session):
    sqlite_session.add_all(
        [
            Aggregate(
                aggregate_type="case",
                aggregate_id="case_b1evidence0001",
                state="OPEN",
                revision=1,
                payload={"demo_fault_injection_id": "inject-b1-evidence"},
            ),
            Aggregate(
                aggregate_type="demo_fault_injection",
                aggregate_id="inject-b1-evidence",
                state="COMPLETED",
                revision=2,
                payload={
                    "fault_versionset_id": BAD_ID,
                    "expected_active_versionset_id": GOOD_ID,
                    "receipt": {
                        "fault_versionset_id": BAD_ID,
                        "previous_versionset_id": GOOD_ID,
                    },
                },
            ),
            Aggregate(
                aggregate_type="demo_fault_injection",
                aggregate_id="inject-other-case",
                state="COMPLETED",
                revision=2,
                payload={"fault_versionset_id": "vs_unrelated0000000001"},
            ),
            Aggregate(
                aggregate_type="demo_fault_injection",
                aggregate_id="inject-same-pair-older-run",
                state="COMPLETED",
                revision=2,
                payload={
                    "fault_versionset_id": BAD_ID,
                    "expected_active_versionset_id": GOOD_ID,
                },
            ),
        ]
    )
    sqlite_session.flush()

    rows = _related_demo_fault_operations(sqlite_session, "case_b1evidence0001")

    assert [(row.aggregate_type, row.aggregate_id) for row in rows] == [
        ("demo_fault_injection", "inject-b1-evidence")
    ]


def test_b1_evidence_filters_same_pair_historical_events_and_audits_by_operation_id():
    identities = {"case_b1evidence0001", "inject-current-run"}
    current_event = Event(
        event_id="evt_current_injection",
        aggregate_type="demo_fault_injection",
        aggregate_id="inject-current-run",
        seq=1,
        event_type="demo_fault.inject_started",
        payload={"fault_versionset_id": BAD_ID},
        causation_id="inject-current-run",
        correlation_id=BAD_ID,
        actor="controller:release",
    )
    old_event = Event(
        event_id="evt_old_injection",
        aggregate_type="demo_fault_injection",
        aggregate_id="inject-old-run",
        seq=1,
        event_type="demo_fault.inject_started",
        payload={"fault_versionset_id": BAD_ID},
        causation_id="inject-old-run",
        correlation_id=BAD_ID,
        actor="controller:release",
    )
    current_audit = Audit(
        audit_id="audit_current_injection",
        actor="controller:release",
        action="demo_fault.B1.injected",
        target="inject-current-run",
        params_digest="sha256:" + "1" * 64,
        result="success",
        trace_id="trace-current",
    )
    old_audit = Audit(
        audit_id="audit_old_injection",
        actor="controller:release",
        action="demo_fault.B1.injected",
        target="inject-old-run",
        params_digest="sha256:" + "2" * 64,
        result="success",
        trace_id="trace-old",
    )

    assert _event_matches_case_evidence(
        current_event, case_id="case_b1evidence0001", identities=identities
    )
    assert not _event_matches_case_evidence(
        old_event, case_id="case_b1evidence0001", identities=identities
    )
    assert _audit_matches_case_evidence(current_audit, identities=identities)
    assert not _audit_matches_case_evidence(old_audit, identities=identities)
