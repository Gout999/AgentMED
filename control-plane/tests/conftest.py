"""pytest 共享夹具。

- sqlite_session：unit 用（内存 SQLite，无外部依赖）
- pg_session / pg_engine：integration 用（compose 起 PG 后可用；不可达则 skip）
- app_client：FastAPI TestClient（SQLite 内存）
"""
from __future__ import annotations

import os
import base64
import hashlib
import json
from pathlib import Path
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
TEST_GATE_TOKEN = "test-gate-authority-token"


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
    target_versionset_id: str = "vs_demo001fixedversionset01",
    target_revision: int = 1,
    target_content: dict[str, Any] | None = None,
    dataset_id: str = "customer-service-regression",
    dataset_version: str = "1.0.0",
    policy_profile: str = "live",
    overall_status: str = "passed",
    eval_id: str | None = None,
) -> dict[str, Any]:
    workorder_suffix = hashlib.sha256(workorder_id.encode("utf-8")).hexdigest()[:16]
    eval_id = eval_id or f"eval_{workorder_suffix}"
    suffix = hashlib.sha256(eval_id.encode("utf-8")).hexdigest()[:16]
    component = "passed" if overall_status == "passed" else overall_status
    failed = 0 if component == "passed" else 1
    passed = 3 if component == "passed" else 0
    repo_root = Path(__file__).resolve().parents[2]
    sample = json.loads(
        (repo_root / "eval-harness" / "samples" / "b1_probe_responses.json").read_text(
            encoding="utf-8"
        )
    )
    answers = {
        probe_id: value["answer"] for probe_id, value in sample["states"]["baseline"].items()
    }
    probe_digest = sample["probe_set_digest"]
    content = target_content or {
        "prompt": {"digest": "sha256:" + "c" * 64},
        "kb_manifest": {"manifest_digest": "sha256:" + "d" * 64},
        "model": {"digest": "sha256:" + "e" * 64},
    }
    components = {
        "prompt_digest": (content.get("prompt") or {}).get("digest"),
        "kb_manifest_digest": (content.get("kb_manifest") or {}).get("manifest_digest"),
        "model_digest": (content.get("model") or {}).get("digest"),
    }

    def inline(payload: dict[str, Any]) -> dict[str, str]:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return {
            "uri": "data:application/json;base64," + base64.b64encode(raw).decode("ascii"),
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }

    contract_ref = inline({"exit_code": 0, "output": "3 passed in 0.01s\n"})
    replay_ref = inline({"exit_code": 0, "output": "16 passed in 0.01s\n"})
    responses = []
    judge_responses = []
    for probe_id, answer in answers.items():
        request_id = f"req-{suffix}-{probe_id}"
        responses.append(
            {
                "probe_id": probe_id,
                "request_id": request_id,
                "versionset_id": target_versionset_id,
                **components,
                "provider_status": "ok",
                "provider_origin": "https://api.stepfun.com/step_plan/v1",
                "trace_id": f"trace-{suffix}-{probe_id}",
                "answer": answer,
            }
        )
        raw_judge = json.dumps(
            {"score": 0.95 if component == "passed" else 0.0, "pass": component == "passed", "rationale": "fixture"},
            separators=(",", ":"),
        )
        judge_responses.append(
            {
                "probe_id": probe_id,
                "provider_request_id": f"judge-{suffix}-{probe_id}",
                "model_digest": "sha256:" + "5" * 64,
                "answer_digest": "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                "raw_response": raw_judge,
                "raw_response_digest": "sha256:"
                + hashlib.sha256(raw_judge.encode("utf-8")).hexdigest(),
                "parsed": {"score": 0.95 if component == "passed" else 0.0, "pass": component == "passed", "rationale": "fixture"},
            }
        )
    candidate_payload = (
        {
            "source": "recorded-replay",
            "versionset_digest": target_versionset_digest,
            "answers": answers,
        }
        if policy_profile == "isolated-replay"
        else {
            "target_versionset_id": target_versionset_id,
            "target_revision": target_revision,
            "target_versionset_digest": target_versionset_digest,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "dataset_digest": probe_digest,
            "responses": responses,
            "judge_responses": judge_responses,
        }
    )
    candidate_ref = inline(candidate_payload)
    artifacts = [contract_ref, replay_ref, candidate_ref]
    return {
        "schema_version": "0.2.0",
        "policy_profile": policy_profile,
        "report_id": f"gate_{suffix}",
        "eval_id": eval_id,
        "subject": {
            "target_versionset_digest": target_versionset_digest,
            "regression_suite_digest": "sha256:" + "3" * 64,
            "probe_set_digest": probe_digest,
        },
        "rule_track": {
            "status": component,
            "checks": [{"check_id": "rule-real", "status": "passed" if component == "passed" else "failed"}],
        },
        "judge_track": {
            "status": component,
            "judge_model_digest": "sha256:" + "5" * 64,
            "athlete_model_digest": components["model_digest"],
            "pass_threshold": 0.8,
            "scores": [
                {
                    "probe_id": probe_id,
                    "score": (
                        1.0
                        if policy_profile == "isolated-replay" and component == "passed"
                        else 0.95
                        if component == "passed"
                        else 0.0
                    ),
                    "pass": component == "passed",
                }
                for probe_id in answers
            ],
        },
        "deterministic_tests": {
            "status": component,
            "suites": [
                {
                    "suite": "contract-assets",
                    "kind": "contract",
                    "status": component,
                    "n_passed": 3 if component == "passed" else 0,
                    "n_failed": failed,
                    "report_ref": artifacts[0]["uri"],
                },
                {
                    "suite": "probe-replay",
                    "kind": "replay",
                    "status": component,
                    "n_passed": 16 if component == "passed" else 0,
                    "n_failed": failed,
                    "report_ref": artifacts[1]["uri"],
                },
            ],
        },
        "live_provider_e2e": (
            {
                "status": "skipped",
                "provider": "replay-not-live",
                "suites": [
                    {
                        "suite": "live-provider-e2e",
                        "status": "skipped",
                        "n_passed": 0,
                        "n_failed": 0,
                    }
                ],
            }
            if policy_profile == "isolated-replay"
            else {
                "status": component,
                "provider": "test-provider",
                "suites": [
                    {
                        "suite": "live-gate",
                        "status": component,
                        "n_passed": 16 if component == "passed" else 0,
                        "n_failed": failed,
                        "report_ref": artifacts[2]["uri"],
                    }
                ],
            }
        ),
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
        target_versionset_id=target_versionset_id,
        target_revision=target_revision,
        target_content=service.quality.get_versionset(target_versionset_id).get("content") or {},
        overall_status=overall_status,
    )
    if overall_status == "passed":
        candidate = json.loads(
            base64.b64decode(report["artifact_refs"][2]["uri"].split(",", 1)[1])
        )
        for item in candidate["responses"]:
            service.quality.seed_log(
                item["request_id"],
                status="ok",
                provider_origin=item["provider_origin"],
                trace_id=item["trace_id"],
                versionset_id=item["versionset_id"],
                prompt_digest=item["prompt_digest"],
                kb_manifest_digest=item["kb_manifest_digest"],
                model_digest=item["model_digest"],
                answer_digest="sha256:"
                + hashlib.sha256(item["answer"].encode("utf-8")).hexdigest(),
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


def register_workorder_with_lease(service: Any, workorder: dict[str, Any]) -> dict[str, Any]:
    """Test-only helper exercising the same mandatory lease path as HTTP/MCP."""

    worker_id = workorder["created_by"]
    lease = service.leases.claim(workorder["case_id"], worker_id)
    return service.register_workorder(
        workorder,
        worker_id=worker_id,
        fencing_token=lease.fencing_token,
    )


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
        target_versionset_id=remote_versionset["versionset_id"],
        target_revision=remote_versionset["revision"],
        target_content=(
            remote_versionset.get("content")
            or service.quality.get_versionset(remote_versionset["versionset_id"]).get("content")
            or {}
        ),
        dataset_id="canary-observation",
        dataset_version="1.0.0",
        overall_status=overall_status,
        eval_id=eval_id,
    )
    if overall_status == "passed":
        candidate = json.loads(
            base64.b64decode(report["artifact_refs"][2]["uri"].split(",", 1)[1])
        )
        for item in candidate["responses"]:
            service.quality.seed_log(
                item["request_id"],
                status="ok",
                provider_origin=item["provider_origin"],
                trace_id=item["trace_id"],
                versionset_id=item["versionset_id"],
                prompt_digest=item["prompt_digest"],
                kb_manifest_digest=item["kb_manifest_digest"],
                model_digest=item["model_digest"],
                answer_digest="sha256:"
                + hashlib.sha256(item["answer"].encode("utf-8")).hexdigest(),
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
        canary_observation_seconds=0,
        allow_isolated_replay_attribution=True,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
        gate_authority_token=TEST_GATE_TOKEN,
        require_mcp_role_tokens=False,
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
        canary_observation_seconds=0,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
        gate_authority_token=TEST_GATE_TOKEN,
        require_mcp_role_tokens=False,
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
        canary_observation_seconds=0,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
        gate_authority_token=TEST_GATE_TOKEN,
        require_mcp_role_tokens=False,
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
        canary_observation_seconds=0,
        audit_force_fail=audit_force_fail,
        control_plane_internal_token=TEST_CONTROL_TOKEN,
        approval_authority_token=TEST_APPROVAL_TOKEN,
        gate_authority_token=TEST_GATE_TOKEN,
        require_mcp_role_tokens=False,
    )
    q = quality or FakeQualityClient()
    app = create_app(settings=settings, quality_client=q, engine=eng, create_tables=True)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TEST_CONTROL_TOKEN}"
    client.__enter__()  # type: ignore[attr-defined]
    return client
