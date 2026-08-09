"""T2 验收五场景（PG 真跑）。

前置：`docker compose -f deploy/compose.yaml up -d postgres`。
1. inbox 去重：重复 event_id 不重复立案
2. lease fencing 防脑裂：旧 fencing token 的写被拒
3. 灰度全链路：draft→canary→promote 与 draft→canary→rollback 两条状态流
4. nonce 重放拒绝
5. 审计写失败 → 写操作返回 503
"""
from datetime import datetime, timedelta, timezone
import hashlib
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.main import create_app
from app.models.tables import (
    Aggregate,
    Approval,
    Audit,
    Base,
    Event,
    Lease,
    TrustLedger,
    TrustLedgerEntry,
)
from app.quality.client import FakeQualityClient
from app.services.case_service import CaseService
from app.services.lease import LeaseService
from app.services.release_service import ReleaseService, ReleaseServiceError
from app.services.outbox_relay import OutboxDispatcher
from app.services.trust_service import ACTION_TYPE, RISK_CLASS, TrustService
from app.utils.jcs import canonical_json_digest

from tests.conftest import (
    TEST_DATABASE_URL,
    make_action_approval,
    make_approval,
    make_workorder,
    register_gate_for_workorder,
    register_release_verification,
    register_workorder_with_lease,
)

pytestmark = pytest.mark.integration


def _settings(**kw) -> Settings:
    base = dict(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
        require_mcp_role_tokens=False,
    )
    base.update(kw)
    return Settings(**base)


# ------------------------------------------------------------------ 场景 1：inbox 去重


def test_scenario_1_inbox_dedup_no_duplicate_filing(pg_session, pg_settings):
    svc = CaseService(pg_session, pg_settings)
    r1 = svc.ingest_complaint(source="webhook", text="手机屏碎了 13800138000", external_id="msg-1")
    pg_session.commit()

    r2 = svc.ingest_complaint(source="webhook", text="手机屏碎了 13800138000", external_id="msg-1")
    pg_session.commit()

    assert r2["duplicate"] is True
    assert r2["case_id"] == r1["case_id"]
    # 只立案一次
    cases = svc.list_cases()
    assert len(cases["items"]) == 1
    agg = svc.store.get_aggregate("case", r1["case_id"])
    assert agg is not None


# ------------------------------------------------------------------ 场景 2：lease fencing 防脑裂


def test_scenario_2_lease_fencing_rejects_stale_token(pg_session, pg_settings):
    svc = CaseService(pg_session, pg_settings)
    case_id = svc.ingest_complaint(source="webhook", text="问题", external_id="msg-2")["case_id"]
    pg_session.commit()

    c1 = svc.claim(case_id, "worker-a")
    stale_token = c1["fencing_token"]
    pg_session.commit()

    # 强制过期 → 另一 worker 重新领单（新 token）
    row = pg_session.get(Lease, case_id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    pg_session.commit()

    svc.reclaim_if_expired(case_id)
    c2 = svc.claim(case_id, "worker-b")
    pg_session.commit()
    assert c2["fencing_token"] != stale_token

    # 旧 token 的写被拒（防脑裂）
    from app.services.case_service import CaseServiceError

    with pytest.raises(CaseServiceError) as exc:
        svc.transition(
            case_id,
            "case.attribution_completed",
            {"verdict": "ATTRIBUTED"},
            fencing_token=stale_token,
            guard="verdict=ATTRIBUTED",
        )
    assert exc.value.code == "lease_lost"
    pg_session.rollback()


def test_heartbeat_commit_wins_over_expiry_watchdog_reclaim(pg_engine, pg_settings):
    """A watchdog must refresh the lease after waiting for heartbeat's row lock."""

    factory = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False, future=True)
    with factory() as session:
        service = CaseService(session, pg_settings)
        case_id = service.ingest_complaint(
            source="webhook", text="heartbeat race", external_id="msg-heartbeat-race"
        )["case_id"]
        session.commit()
        claim = service.claim(case_id, "worker-heartbeat")
        session.commit()
        lease = session.get(Lease, case_id)
        assert lease is not None
        old_expiry = datetime.now(timezone.utc) + timedelta(seconds=1)
        lease.expires_at = old_expiry
        session.commit()

    heartbeat_locked = threading.Event()
    release_heartbeat = threading.Event()
    watchdog_started = threading.Event()
    results: list[object] = []
    errors: list[BaseException] = []

    def heartbeat() -> None:
        try:
            with factory() as session:
                service = CaseService(session, pg_settings)
                service.heartbeat(
                    case_id, "worker-heartbeat", claim["fencing_token"]
                )
                session.flush()
                heartbeat_locked.set()
                assert release_heartbeat.wait(timeout=5)
                session.commit()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
            heartbeat_locked.set()

    def watchdog() -> None:
        try:
            watchdog_started.set()
            with factory() as session:
                results.append(CaseService(session, pg_settings).reclaim_if_expired(case_id))
                session.commit()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    heartbeat_thread = threading.Thread(target=heartbeat)
    heartbeat_thread.start()
    assert heartbeat_locked.wait(timeout=5)
    while datetime.now(timezone.utc) <= old_expiry:
        time.sleep(0.01)
    watchdog_thread = threading.Thread(target=watchdog)
    watchdog_thread.start()
    assert watchdog_started.wait(timeout=2)
    time.sleep(0.1)
    release_heartbeat.set()
    heartbeat_thread.join(timeout=5)
    watchdog_thread.join(timeout=5)

    assert not errors
    assert results == [None]
    with factory() as session:
        aggregate = session.get(
            Aggregate, {"aggregate_type": "case", "aggregate_id": case_id}
        )
        lease = session.get(Lease, case_id)
        assert aggregate is not None and aggregate.state == "DISPATCHED"
        assert lease is not None and lease.expires_at > datetime.now(timezone.utc)


def test_concurrent_candidate_completion_is_exactly_once(pg_engine, pg_settings):
    """Two idempotent Quality returns produce one candidate and one completion."""

    factory = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False, future=True)
    case_id = "case_candidate_completion_race"
    attribution_report_digest = canonical_json_digest(
        {"case_id": case_id, "verdict": "ATTRIBUTED", "fault_layer": "prompt"}
    )
    bad_prompt = {"prompt_id": "prompts/system.md", "version": "bad"}
    good_prompt = {"prompt_id": "prompts/system.md", "version": "good"}
    bad_prompt_digest = canonical_json_digest(bad_prompt)
    good_prompt_digest = canonical_json_digest(good_prompt)
    kb_entry = {"kb_id": "customer-service", "entry_id": "policy", "version": "1.0.0"}
    normalized_entry = {**kb_entry, "digest": canonical_json_digest(kb_entry)}
    kb_manifest = {
        "entries": [normalized_entry],
        "manifest_digest": canonical_json_digest({"entries": [normalized_entry]}),
    }
    model = {"provider": "recorded", "model": "athlete", "params": {"temperature": 0}}
    model = {**model, "digest": canonical_json_digest(model)}
    base_content = {
        "prompt": {**bad_prompt, "digest": bad_prompt_digest},
        "kb_manifest": kb_manifest,
        "model": model,
    }
    candidate_content = {
        "prompt": {**good_prompt, "digest": good_prompt_digest},
        "kb_manifest": dict(kb_manifest),
        "model": dict(model),
    }

    with factory() as session:
        session.add(
            Aggregate(
                aggregate_type="case",
                aggregate_id=case_id,
                state="AWAITING_FIX",
                payload={
                    "fault_layer": "prompt",
                    "attribution_verdict": "ATTRIBUTED",
                    "attribution_report_digest": attribution_report_digest,
                },
                revision=1,
            )
        )
        lease = LeaseService(session, pg_settings).claim(case_id, "repairer:concurrent")
        lease_binding = {
            "worker_id": lease.owner_id,
            "fencing_token": lease.fencing_token,
        }
        session.commit()

    quality_barrier = threading.Barrier(2)
    quality_lock = threading.Lock()

    class CoordinatedQuality(FakeQualityClient):
        def create_versionset(self, content, *, idempotency_key):
            quality_barrier.wait(timeout=5)
            with quality_lock:
                return super().create_versionset(
                    content, idempotency_key=idempotency_key
                )

    quality = CoordinatedQuality()
    base = quality.seed_versionset(
        "vs_concurrentcandidatebase01",
        status="active",
        revision=1,
        digest=canonical_json_digest(base_content),
        content=base_content,
    )
    proposal = {
        "case_id": case_id,
        "channel": "prompt",
        "attribution_report_digest": attribution_report_digest,
        "base_versionset_id": base["versionset_id"],
        "base_versionset_digest": base["digest"],
        "base_revision": base["revision"],
        "target_prompt_digest": good_prompt_digest,
        "content": candidate_content,
    }
    request = {
        **proposal,
        **lease_binding,
        "proposal_digest": canonical_json_digest(proposal),
        "idempotency_key": "candidate-concurrent-completion",
    }
    results: list[dict] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def create() -> None:
        try:
            with factory() as session:
                result = ReleaseService(session, quality, pg_settings).create_candidate(
                    **request
                )
        except BaseException as exc:  # pragma: no cover - surfaced below
            with result_lock:
                errors.append(exc)
        else:
            with result_lock:
                results.append(result)

    threads = [threading.Thread(target=create), threading.Thread(target=create)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert len(results) == 2
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert results[0]["versionset_id"] == results[1]["versionset_id"]
    with factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.aggregate_type == "candidate_creation",
                Event.aggregate_id == "candidate-concurrent-completion",
                Event.event_type == "candidate.creation_completed",
            )
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(Audit)
            .where(Audit.action == "candidate.create", Audit.result == "success")
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(Aggregate)
            .where(Aggregate.aggregate_type == "candidate")
        ) == 1


@pytest.mark.parametrize("operation", ["inject", "recover"])
def test_concurrent_demo_fault_completion_is_exactly_once(
    pg_engine, pg_settings, operation
):
    """Idempotent provider receipts yield one controller completion and audit."""

    factory = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False, future=True)
    settings = pg_settings.model_copy(update={"allow_demo_fault_injection": True})
    provider_barrier = threading.Barrier(2)
    provider_lock = threading.Lock()

    class CoordinatedQuality(FakeQualityClient):
        def inject_fault(self, *args, **kwargs):
            if operation == "inject":
                provider_barrier.wait(timeout=5)
            with provider_lock:
                return super().inject_fault(*args, **kwargs)

        def recover_fault(self, *args, **kwargs):
            if operation == "recover":
                provider_barrier.wait(timeout=5)
            with provider_lock:
                return super().recover_fault(*args, **kwargs)

    quality = CoordinatedQuality()
    good_id = "vs_pgdemobaseline000001"
    fault_id = "vs_pgdemofault000000001"
    quarantine_id = "vs_pgdemoquarantine0001"
    quality.seed_versionset(
        good_id, status="active", revision=1, digest="sha256:" + "a" * 64
    )
    quality.seed_versionset(
        fault_id, status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    if operation == "recover":
        # Establish the exact precondition without consuming the coordinated
        # controller idempotency key under test.
        quality.inject_fault(
            "B1",
            expected_active_versionset_id=good_id,
            fault_versionset_id=fault_id,
        )
        quality.seed_versionset(
            quarantine_id,
            status="canary",
            revision=3,
            digest="sha256:" + "c" * 64,
        )

    results: list[dict] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()
    key = f"demo-{operation}-concurrent"

    def invoke() -> None:
        try:
            with factory() as session:
                service = ReleaseService(session, quality, settings)
                if operation == "inject":
                    result = service.inject_demo_fault(
                        fault_id="B1",
                        expected_active_versionset_id=good_id,
                        fault_versionset_id=fault_id,
                        idempotency_key=key,
                    )
                else:
                    result = service.recover_demo_fault(
                        fault_id="B1",
                        expected_active_fault_versionset_id=fault_id,
                        restore_versionset_id=good_id,
                        quarantine_versionset_id=quarantine_id,
                        idempotency_key=key,
                    )
        except BaseException as exc:  # pragma: no cover - surfaced below
            with result_lock:
                errors.append(exc)
        else:
            with result_lock:
                results.append(result)

    threads = [threading.Thread(target=invoke), threading.Thread(target=invoke)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert sorted(result["duplicate"] for result in results) == [False, True]
    aggregate_type = f"demo_fault_{'injection' if operation == 'inject' else 'recovery'}"
    completion_event = (
        "demo_fault.inject_completed" if operation == "inject" else "demo_fault.recovered"
    )
    success_action = (
        "demo_fault.B1.injected" if operation == "inject" else "demo_fault.B1.recovered"
    )
    with factory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.aggregate_type == aggregate_type,
                Event.aggregate_id == key,
                Event.event_type == completion_event,
            )
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(Audit)
            .where(Audit.action == success_action, Audit.result == "success")
        ) == 1


# ------------------------------------------------------------------ 场景 3：灰度全链路


def _new_release(
    svc: ReleaseService, session, case_id: str, seed: int, quality: FakeQualityClient
) -> tuple[str, dict]:
    wo = make_workorder(workorder_id=f"wo_{seed:012d}", nonce=f"00000000-0000-0000-0000-{seed:012d}", case_id=case_id)
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    session.commit()
    ap = make_approval(wo, f"ap_{seed}")
    svc.grant_approval(ap)
    session.commit()
    rel = svc.start_release(
        workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01"
    )
    complaint = next(
        event for event in svc.store.list_events(case_id) if event.event_type == "complaint.received"
    )
    svc.configure_closure(
        rel["release_id"],
        channel=complaint.payload["channel"],
        thread_ref=complaint.payload["thread_ref"],
        body_ref=f"file:///tmp/caseloop-integration-reply-{seed}.txt",
        body_digest="sha256:" + "d" * 64,
    )
    session.commit()
    return rel["release_id"], wo


def _action_approval(session, svc, wo, release_id, action, key, *, reason="manual"):
    suffix = hashlib.sha256(f"{release_id}:{action}:{key}".encode()).hexdigest()[:16]
    approval_id = f"ap_itg_{suffix}"
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
    session.commit()
    return approval_id


def test_scenario_3_gray_release_promote_and_rollback(pg_session, pg_settings):
    quality = FakeQualityClient()
    quality.seed_versionset(
        "vs_baseline0000000000000001", status="active", revision=1, digest="sha256:" + "a" * 64
    )
    quality.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    svc = ReleaseService(pg_session, quality, pg_settings)

    case_svc = CaseService(pg_session, pg_settings)
    case_id = case_svc.ingest_complaint(source="webhook", text="问题", external_id="msg-3")["case_id"]
    pg_session.commit()

    # ---- promote 全链路：draft → stage → canary → promote
    rid1, wo1 = _new_release(svc, pg_session, case_id, 11, quality)
    st = svc.stage(rid1, idempotency_key="itg-stage-1")
    pg_session.commit()
    assert st["state"] == "STAGING"
    ca = svc.canary(
        rid1,
        idempotency_key="itg-canary-1",
        approval_id=_action_approval(
            pg_session, svc, wo1, rid1, "canary", "itg-canary-1"
        ),
    )
    pg_session.commit()
    assert ca["state"] == "CANARYING"
    verification1 = register_release_verification(
        svc,
        wo1,
        quality.get_versionset("vs_demo001fixedversionset01"),
        overall_status="passed",
        eval_id="eval_itgcanarypass11",
    )
    svc.record_verification(
        rid1,
        eval_id=verification1["eval_id"],
        report_hash=canonical_json_digest(verification1, prefix=False),
    )
    pg_session.commit()
    pr = svc.promote(
        rid1,
        idempotency_key="itg-promote-1",
        approval_id=_action_approval(
            pg_session, svc, wo1, rid1, "promote", "itg-promote-1"
        ),
    )
    pg_session.commit()
    assert pr["state"] == "COMPLETED"
    assert quality.get_versionset("vs_demo001fixedversionset01")["status"] == "active"

    # ---- rollback 全链路：draft → stage → canary → rollback
    quality.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )  # 重置远端
    quality.seed_versionset(
        "vs_baseline0000000000000001", status="active", revision=1, digest="sha256:" + "a" * 64
    )
    rid2, wo2 = _new_release(svc, pg_session, case_id, 12, quality)
    svc.stage(rid2, idempotency_key="itg-rb-stage-1")
    pg_session.commit()
    svc.canary(
        rid2,
        idempotency_key="itg-rb-canary-1",
        approval_id=_action_approval(
            pg_session, svc, wo2, rid2, "canary", "itg-rb-canary-1"
        ),
    )
    pg_session.commit()
    verification2 = register_release_verification(
        svc,
        wo2,
        quality.get_versionset("vs_demo001fixedversionset01"),
        overall_status="failed",
        eval_id="eval_itgcanaryfail12",
    )
    svc.record_verification(
        rid2,
        eval_id=verification2["eval_id"],
        report_hash=canonical_json_digest(verification2, prefix=False),
    )
    pg_session.commit()
    rb = svc.rollback(
        rid2,
        idempotency_key="itg-rb-rollback-1",
        approval_id=_action_approval(
            pg_session, svc, wo2, rid2, "rollback", "itg-rb-rollback-1"
        ),
    )
    pg_session.commit()
    assert rb["state"] == "ROLLED_BACK"
    assert quality.get_versionset("vs_demo001fixedversionset01")["status"] == "rolled_back"

    # Trust is fed only by the real terminal release events via the durable
    # dispatcher. Probe count inside either action cannot inflate the samples.
    factory = sessionmaker(bind=pg_session.get_bind(), autoflush=False, autocommit=False)

    class TrustOnlyConsumer:
        def consume(self, session, row):
            if row.event_type not in {
                "RELEASE_PROMOTED",
                "RELEASE_ROLLED_BACK",
                "RELEASE_UNKNOWN",
            }:
                return {
                    "status": "consumed",
                    "consumer": "trust-only-test",
                    "domain_event_type": row.event_type,
                    "source_event_id": row.source_event_id,
                }
            return TrustService(session, pg_settings).consume_release_event(row.payload or {})

    dispatched = OutboxDispatcher(
        factory,
        pg_settings,
        domain_consumer=TrustOnlyConsumer(),
        worker_id="integration:release-trust",
    ).dispatch_batch(limit=100)
    assert dispatched["dead"] == 0 and dispatched["blocked"] == 0
    pg_session.expire_all()
    ledger = pg_session.get(
        TrustLedger,
        {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
    )
    assert ledger is not None
    assert ledger.successes == 1 and ledger.trials == 2
    outcomes = list(
        pg_session.scalars(select(TrustLedgerEntry.outcome).order_by(TrustLedgerEntry.outcome))
    )
    assert outcomes == ["failure", "success"]


# ------------------------------------------------------------------ 场景 4：nonce 重放拒绝


def test_scenario_4_nonce_replay_rejected(pg_session, pg_settings):
    quality = FakeQualityClient()
    quality.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    svc = ReleaseService(pg_session, quality, pg_settings)

    case_svc = CaseService(pg_session, pg_settings)
    case_id = case_svc.ingest_complaint(source="webhook", text="问题", external_id="msg-4")["case_id"]
    pg_session.commit()

    wo = make_workorder(workorder_id="wo_000000000021", nonce="00000000-0000-0000-0000-000000000021", case_id=case_id)
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    pg_session.commit()
    ap = make_approval(wo, "ap_000000000021")
    svc.grant_approval(ap)
    pg_session.commit()

    svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    pg_session.commit()

    # nonce 已消费 → 重放拒绝
    with pytest.raises(ReleaseServiceError) as exc:
        svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    assert exc.value.code == "nonce_replay"
    pg_session.rollback()


def test_scenario_4_concurrent_nonce_consumption_creates_one_release(
    pg_engine, pg_session, pg_settings
):
    """PostgreSQL row lock makes nonce consumption atomic across requests."""

    quality = FakeQualityClient()
    quality.seed_versionset(
        "vs_demo001fixedversionset01",
        status="draft",
        revision=1,
        digest="sha256:" + "b" * 64,
    )
    setup = ReleaseService(pg_session, quality, pg_settings)
    wo = make_workorder(
        workorder_id="wo_000000000022",
        nonce="00000000-0000-0000-0000-000000000022",
        case_id="case_concurrent_nonce",
    )
    register_gate_for_workorder(setup, wo)
    register_workorder_with_lease(setup, wo)
    setup.grant_approval(make_approval(wo, "ap_000000000022"))
    pg_session.commit()

    factory = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def worker() -> None:
        session = factory()
        try:
            barrier.wait(timeout=5)
            service = ReleaseService(session, quality, pg_settings)
            service.start_release(
                workorder_id=wo["workorder_id"],
                approval_id="ap_000000000022",
                versionset_id="vs_demo001fixedversionset01",
            )
            session.commit()
            outcome = "success"
        except ReleaseServiceError as exc:
            session.rollback()
            outcome = exc.code
        finally:
            session.close()
        with outcome_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["nonce_replay", "success"]
    count = pg_session.scalar(
        select(func.count()).select_from(Aggregate).where(Aggregate.aggregate_type == "release")
    )
    assert count == 1


# ------------------------------------------------------------------ 补充：UNKNOWN→reconcile 退避


def test_scenario_6_unknown_reconcile(pg_session, pg_settings):
    """写操作结果不可考 → UNKNOWN；reconcile 以 GET /status 对账收敛（含指数退避循环）。"""
    quality = FakeQualityClient()
    quality.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    svc = ReleaseService(pg_session, quality, pg_settings)

    case_svc = CaseService(pg_session, pg_settings)
    case_id = case_svc.ingest_complaint(source="webhook", text="问题", external_id="msg-6")["case_id"]
    pg_session.commit()

    wo = make_workorder(workorder_id="wo_000000000031", nonce="00000000-0000-0000-0000-000000000031", case_id=case_id)
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    pg_session.commit()
    ap = make_approval(wo, "ap_000000000031")
    svc.grant_approval(ap)
    pg_session.commit()
    rid = svc.start_release(
        workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01"
    )["release_id"]
    pg_session.commit()

    svc.stage(rid, idempotency_key="itg-unk-stage-1")
    pg_session.commit()
    # canary 进入 pending → 轮询超时 → UNKNOWN（远端实际已生效）
    quality.unknown_ops = True
    unk = svc.canary(
        rid,
        idempotency_key="itg-unk-canary-1",
        approval_id=_action_approval(
            pg_session, svc, wo, rid, "canary", "itg-unk-canary-1"
        ),
    )
    pg_session.commit()
    assert unk["state"] == "UNKNOWN"
    assert unk["status"] == "unknown"

    rc = svc.reconcile_loop(rid, max_attempts=3)
    assert rc["state"] == "CANARYING"
    assert rc["remote_status"] == "canary"
    pg_session.commit()


# ------------------------------------------------------------------ 场景 5：审计写失败 → 503


def test_scenario_5_audit_failure_returns_503(pg_engine):
    """审计写失败 → 写操作返回 503 且业务拒绝（无 case 落库）。"""
    settings = _settings(audit_force_fail=True)
    app = create_app(settings=settings, quality_client=FakeQualityClient(), engine=pg_engine, create_tables=True)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/complaints",
            json={"source": "webhook", "text": "审计失败不应立案", "external_id": "msg-5"},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["code"] == "audit_unavailable"

        # 业务被拒绝：无 case、无 inbox 残留
        cases = client.get("/v1/cases").json()
        assert cases["items"] == []
