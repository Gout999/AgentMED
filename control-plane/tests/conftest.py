"""pytest 共享夹具。

- sqlite_session：unit 用（内存 SQLite，无外部依赖）
- pg_session / pg_engine：integration 用（compose 起 PG 后可用；不可达则 skip）
- app_client：FastAPI TestClient（SQLite 内存）
"""
from __future__ import annotations

import os
import hashlib
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models.tables import Base
from app.quality.client import FakeQualityClient
from app.utils.jcs import workorder_hash
from app.utils.jcs import canonical_json_digest

# S0-005：integration 测试必须落在 scratch 库，绝不默认指活库。
# pg_engine fixture 会 drop_all——默认值若指 control_plane 活库，一次 pytest 就清库（2026-08-08 实发事故）。
TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test",
)
TEST_CONTROL_TOKEN = "test-control-plane-token"
TEST_APPROVAL_TOKEN = "test-approval-authority-token"


def make_workorder(
    *,
    workorder_id: str,
    nonce: str,
    case_id: str,
    channel: str = "prompt",
    digest_seed: str = "a",
) -> dict[str, Any]:
    """构造合法 WorkOrder（ASCII-only，JCS 可哈希），hash 由规则重算。"""
    expiry = "2099-01-01T00:00:00+00:00"
    wo: dict[str, Any] = {
        "schema_version": "0.1.0",
        "workorder_id": workorder_id,
        "case_id": case_id,
        "channel": channel,
        "base_versionset_digest": f"sha256:{digest_seed * 64}",
        "target_versionset_digest": f"sha256:{'b' * 64}",
        "input_versions": {
            "prompt_digest": f"sha256:{'c' * 64}",
            "kb_manifest_digest": f"sha256:{'d' * 64}",
            "model_digest": f"sha256:{'e' * 64}",
        },
        "diff": {"format": "unified_diff", "content": "fix output format", "digest": f"sha256:{'f' * 64}"},
        "gate_report_ref": {"uri": "http://gate/1", "digest": f"sha256:{'g' * 64}"},
        "expiry": expiry,
        "nonce": nonce,
        "created_at": "2026-08-08T00:00:00+00:00",
        "created_by": "repairer-1",
        "hash_rule": "jcs-rfc8785+sha256",
    }
    wo["hash"] = workorder_hash(wo)
    return wo


def make_gate_report(
    workorder_id: str,
    *,
    target_versionset_digest: str = "sha256:" + "b" * 64,
    overall_status: str = "passed",
    eval_id: str | None = None,
) -> dict[str, Any]:
    workorder_suffix = hashlib.sha256(workorder_id.encode("utf-8")).hexdigest()[:16]
    eval_id = eval_id or f"eval_{workorder_suffix}"
    suffix = hashlib.sha256(eval_id.encode("utf-8")).hexdigest()[:16]
    component = "passed" if overall_status == "passed" else overall_status
    failed = 0 if component == "passed" else 1
    passed = 3 if component == "passed" else 0
    artifacts = [
        {"uri": f"file:///tmp/{eval_id}-contract.json", "digest": "sha256:" + "1" * 64},
        {"uri": f"file:///tmp/{eval_id}-replay.json", "digest": "sha256:" + "2" * 64},
        {"uri": f"file:///tmp/{eval_id}-candidate.json", "digest": "sha256:" + "7" * 64},
    ]
    return {
        "schema_version": "0.1.0",
        "report_id": f"gate_{suffix}",
        "eval_id": eval_id,
        "subject": {
            "target_versionset_digest": target_versionset_digest,
            "regression_suite_digest": "sha256:" + "3" * 64,
            "probe_set_digest": "sha256:" + "4" * 64,
        },
        "rule_track": {
            "status": component,
            "checks": [{"check_id": "rule-real", "status": "passed" if component == "passed" else "failed"}],
        },
        "judge_track": {
            "status": component,
            "judge_model_digest": "sha256:" + "5" * 64,
            "athlete_model_digest": "sha256:" + "6" * 64,
            "pass_threshold": 0.8,
            "scores": [
                {"probe_id": "cs-001", "score": 0.95 if component == "passed" else 0.0, "pass": component == "passed"}
            ],
        },
        "deterministic_tests": {
            "status": component,
            "suites": [
                {
                    "suite": "contract-assets",
                    "kind": "contract",
                    "status": component,
                    "n_passed": passed,
                    "n_failed": failed,
                    "report_ref": artifacts[0]["uri"],
                },
                {
                    "suite": "probe-replay",
                    "kind": "replay",
                    "status": component,
                    "n_passed": passed,
                    "n_failed": failed,
                    "report_ref": artifacts[1]["uri"],
                },
            ],
        },
        "live_provider_e2e": {
            "status": component,
            "provider": "test-provider",
            "suites": [
                {
                    "suite": "live-gate",
                    "status": component,
                    "n_passed": passed,
                    "n_failed": failed,
                    "report_ref": artifacts[2]["uri"],
                }
            ],
        },
        "overall_status": overall_status,
        "artifact_refs": artifacts,
        "created_at": "2026-08-08T00:00:00+00:00",
    }


def register_gate_for_workorder(
    service: Any,
    workorder: dict[str, Any],
    *,
    target_versionset_id: str = "vs_demo001fixedversionset01",
    target_revision: int = 1,
    overall_status: str = "passed",
) -> dict[str, Any]:
    """Test-only helper: register an explicit GateReport and update the WorkOrder reference/hash."""
    report = make_gate_report(
        workorder["workorder_id"],
        target_versionset_digest=workorder["target_versionset_digest"],
        overall_status=overall_status,
    )
    report_hash = canonical_json_digest(report, prefix=False)
    workorder["gate_report_ref"] = {
        "uri": f"eval://{report['eval_id']}",
        "digest": f"sha256:{report_hash}",
    }
    workorder["hash"] = workorder_hash(workorder)
    service.gates.register_report(
        {
            "report": report,
            "report_hash": report_hash,
            "workorder_id": workorder["workorder_id"],
            "target_versionset_id": target_versionset_id,
            "target_revision": target_revision,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        }
    )
    return report


def register_release_verification(
    service: Any,
    workorder: dict[str, Any],
    remote_versionset: dict[str, Any],
    *,
    overall_status: str,
    eval_id: str,
) -> dict[str, Any]:
    """Register a distinct post-canary GateReport at the exact remote revision."""
    report = make_gate_report(
        workorder["workorder_id"],
        target_versionset_digest=remote_versionset["digest"],
        overall_status=overall_status,
        eval_id=eval_id,
    )
    service.gates.register_report(
        {
            "report": report,
            "report_hash": canonical_json_digest(report, prefix=False),
            "workorder_id": workorder["workorder_id"],
            "target_versionset_id": remote_versionset["versionset_id"],
            "target_revision": remote_versionset["revision"],
            "dataset_id": "canary-observation",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        }
    )
    return report


def make_approval(wo: dict[str, Any], approval_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "approval_id": approval_id,
        "workorder_hash": wo["hash"],
        "workorder_id": wo["workorder_id"],
        "nonce": wo["nonce"],
        "expiry": wo["expiry"],
        "approver": {"type": "human", "identity": "human-1"},
        "decision": "approved",
        "decided_at": "2026-08-08T00:00:00+00:00",
        "nonce_consumed": False,
    }


def make_action_approval(
    wo: dict[str, Any],
    *,
    approval_id: str,
    release_id: str,
    action: str,
    target_revision: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Construct an explicit human R2 grant bound to one release action."""

    return {
        "schema_version": "0.1.0",
        "approval_id": approval_id,
        "workorder_hash": wo["hash"],
        "workorder_id": wo["workorder_id"],
        "nonce": str(uuid.uuid5(uuid.NAMESPACE_URL, f"caseloop-action-grant:{approval_id}")),
        "expiry": wo["expiry"],
        "approver": {"type": "human", "identity": "human-1"},
        "decision": "approved",
        "decided_at": "2026-08-08T00:00:00+00:00",
        "nonce_consumed": False,
        "authorization": {
            "action": action,
            "release_id": release_id,
            "target_revision": target_revision,
            "params": params,
            "params_digest": canonical_json_digest(params),
        },
    }

# ------------------------------------------------------------------ sqlite（unit）


@pytest.fixture()
def sqlite_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def sqlite_session(sqlite_engine):
    S = sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False)
    s = S()
    yield s
    s.close()


@pytest.fixture()
def test_settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
    )


# ------------------------------------------------------------------ pg（integration）


def _pg_available() -> bool:
    try:
        eng = sa.create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 2})
        conn = eng.connect()
        conn.close()
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


def _new_pg_engine():
    return sa.create_engine(TEST_DATABASE_URL, poolclass=sa.pool.NullPool)


@pytest.fixture()
def pg_engine():
    if not _pg_available():
        pytest.skip("Postgres 不可达：先 docker compose -f deploy/compose.yaml up -d postgres")
    eng = _new_pg_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def pg_session(pg_engine):
    S = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    s = S()
    yield s
    s.close()


@pytest.fixture()
def pg_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
    )


@pytest.fixture()
def pg_client(pg_engine) -> tuple[TestClient, FakeQualityClient]:
    """PG 版 FastAPI client（integration 场景 1–4 的 HTTP 层）。"""
    quality = FakeQualityClient()
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
    )
    app = create_app(settings=settings, quality_client=quality, engine=pg_engine, create_tables=True)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TEST_CONTROL_TOKEN}"
        yield client, quality


# ------------------------------------------------------------------ FastAPI client


@pytest.fixture()
def app_client(sqlite_engine, test_settings) -> tuple[TestClient, FakeQualityClient]:
    quality = FakeQualityClient()
    app = create_app(
        settings=test_settings,
        quality_client=quality,
        engine=sqlite_engine,
        create_tables=True,
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TEST_CONTROL_TOKEN}"
        yield client, quality


def build_pg_app(*, audit_force_fail: bool = False, quality: FakeQualityClient | None = None) -> TestClient:
    """构建绑定 PG 的 FastAPI app（integration HTTP 测试用）。"""
    if not _pg_available():
        pytest.skip("Postgres 不可达：先 docker compose -f deploy/compose.yaml up -d postgres")
    eng = _new_pg_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
        audit_force_fail=audit_force_fail,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
    )
    q = quality or FakeQualityClient()
    app = create_app(settings=settings, quality_client=q, engine=eng, create_tables=True)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TEST_CONTROL_TOKEN}"
    client.__enter__()  # type: ignore[attr-defined]
    return client
