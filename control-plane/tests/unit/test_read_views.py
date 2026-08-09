"""T8 read projection tests over SQLite with no external dependency."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.models.tables import Aggregate, Event, GateReportRecord, WorkOrder
from app.services.attribution import newcombe_wilson_diff
from app.services.release_service import ReleaseService
from app.services.trust_service import TrustService
from app.utils.jcs import canonical_json_digest, workorder_hash
from tests.conftest import (
    TEST_GATE_TOKEN,
    make_gate_report,
    make_workorder,
    register_workorder_with_lease,
)


def _create_mcp_tables(engine) -> None:
    """按 mcp-servers/001_init.sql 的 mcp_* 列形状建表（sqlite 子集，可被 JSON 列读回）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mcp_trust_ledger (
                  risk_class TEXT NOT NULL,
                  action_type TEXT NOT NULL,
                  epoch INTEGER NOT NULL,
                  successes INTEGER NOT NULL DEFAULT 0,
                  trials INTEGER NOT NULL DEFAULT 0,
                  autonomy_state TEXT NOT NULL DEFAULT 'MANUAL',
                  suspended_until TIMESTAMP,
                  pending_promotion_ref TEXT,
                  payload TEXT NOT NULL DEFAULT '{}',
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (risk_class, action_type, epoch)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mcp_audit (
                  audit_id TEXT PRIMARY KEY,
                  ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  actor TEXT NOT NULL,
                  action TEXT NOT NULL,
                  target TEXT NOT NULL,
                  params_digest TEXT NOT NULL,
                  result TEXT NOT NULL DEFAULT 'success',
                  error_code TEXT,
                  trace_id TEXT NOT NULL,
                  evidence_refs TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mcp_eval_runs (
                  eval_id TEXT PRIMARY KEY,
                  workorder_id TEXT NOT NULL,
                  suite_digest TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'queued',
                  report TEXT,
                  report_hash TEXT,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


# ------------------------------------------------------------------ 1. GET /v1/env


def test_env_returns_active_versionset_digest(app_client):
    client, quality = app_client
    quality.seed_versionset(
        "vs_baseline0000000001",
        status="active",
        revision=1,
        digest="sha256:8fc6a7ac413b640d759293053d3a414eb2c781a831d973c26794750f63dc47a1",
    )
    r = client.get("/v1/env")
    assert r.status_code == 200
    body = r.json()
    assert body["demo_app"]["versionset_id"] == "vs_baseline0000000001"
    assert body["demo_app"]["digest"].startswith("sha256:8fc6a7ac")
    assert body["demo_app"]["status"] == "active"
    assert body["demo_app"]["revision"] == 1


def test_env_unavailable_when_no_active_versionset(app_client):
    client, _ = app_client
    r = client.get("/v1/env")
    assert r.status_code == 200  # 降级为 200，不 5xx
    assert r.json() == {"demo_app": "unavailable"}


def test_env_unavailable_on_network_error(app_client):
    client, quality = app_client
    quality.fail_next = "network"
    r = client.get("/v1/env")
    assert r.status_code == 200
    assert r.json() == {"demo_app": "unavailable"}


@pytest.mark.parametrize(
    "malformed",
    [
        {"status": "active"},
        {
            "versionset_id": "vs_bad",
            "status": "active",
            "revision": 0,
            "digest": "not-a-digest",
        },
    ],
)
def test_env_unavailable_for_malformed_active_projection(app_client, malformed):
    client, quality = app_client
    quality.list_versionsets = lambda **_kwargs: {"items": [malformed]}  # type: ignore[method-assign]
    r = client.get("/v1/env")
    assert r.status_code == 200
    assert r.json() == {"demo_app": "unavailable"}


# ------------------------------------------------------------------ 2. GET /v1/trust/ledger


def test_trust_ledger_authoritative_empty_state(app_client):
    client, _ = app_client
    r = client.get("/v1/trust/ledger")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_ledger_ignores_disconnected_mcp_shadow_table(app_client, sqlite_engine):
    _create_mcp_tables(sqlite_engine)
    with sqlite_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO mcp_trust_ledger
                  (risk_class, action_type, epoch, successes, trials, autonomy_state)
                VALUES ('R1_REVERSIBLE_WRITE', 'shadow_only', 1, 99, 99, 'AUTO_ENABLED')
                """
            )
        )
    client, _ = app_client
    r = client.get("/v1/trust/ledger")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_ledger_computes_wilson_from_counts(app_client, sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO trust_ledger
                  (risk_class, action_type, epoch, successes, trials, autonomy_state, payload, updated_at)
                VALUES
                  ('R2_HIGH_IMPACT', 'release_outcome', 1, 3, 3, 'MANUAL', '{"promotion_eligible": false}', CURRENT_TIMESTAMP),
                  ('R0_READ', 'case_list', 1, 10, 10, 'ELIGIBLE', '{"promotion_eligible": true}', CURRENT_TIMESTAMP)
                """
            )
        )
    client, _ = app_client
    r = client.get("/v1/trust/ledger")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2

    triage = next(i for i in items if i["action_type"] == "release_outcome")
    assert triage["successes"] == 3 and triage["trials"] == 3
    # 3/3 → Wilson 双侧 95% 下界 ≈ 0.438494（contracts/wilson 事实源）
    assert abs(triage["LB"] - 0.438494) < 1e-5
    assert triage["promotion_eligible"] is False
    assert triage["autonomy_state"] == "MANUAL"

    case_list = next(i for i in items if i["action_type"] == "case_list")
    assert case_list["LB"] > 0.7  # 10/10 → 下界 ≈ 0.722


# ------------------------------------------------------------------ 3. GET /v1/trust/denials


def test_trust_denials_authoritative_empty_state(app_client):
    client, _ = app_client
    r = client.get("/v1/trust/denials")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_denials_ignores_mcp_shadow_audit(app_client, sqlite_engine):
    _create_mcp_tables(sqlite_engine)
    client, _ = app_client
    r = client.get("/v1/trust/denials")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_denials_lists_real_release_outcomes(app_client, sqlite_engine):
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=sqlite_engine)()
    try:
        service = TrustService(session)
        for index in range(3):
            service.record_outcome(
                source_event_id=f"evt_real_{index}",
                action_ref=f"rel_real_{index}",
                success=True,
                detail="operation-bound promote receipt",
            )
        session.commit()
    finally:
        session.close()
    client, _ = app_client
    r = client.get("/v1/trust/denials")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    d = items[0]
    assert d["action_type"] == "release_outcome"
    assert d["risk_class"] == "R2_HIGH_IMPACT"
    assert d["result"] == "denied"
    assert d["successes"] == 3 and d["trials"] == 3
    assert "0.4385" in d["reason"]


# ------------------------------------------------------------------ 4. GET /v1/cases/{id}/events


def test_case_events_ordered_and_carries_evidence_refs(app_client):
    client, _ = app_client
    resp = client.post("/v1/complaints", json={"source": "webhook", "text": "手机屏碎了", "external_id": "msg-ev-1"})
    assert resp.status_code == 200
    case_id = resp.json()["case_id"]

    r = client.get(f"/v1/cases/{case_id}/events")
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == case_id
    items = body["items"]
    assert len(items) >= 2
    seqs = [i["seq"] for i in items]
    assert seqs == sorted(seqs)
    assert items[0]["event_type"] == "complaint.received"
    # 事件 payload 里的证据引用字段被投影（complaint.received 带 text_ref）
    assert items[0]["evidence_refs"].get("text_ref") == f"inline:{case_id}"


def test_case_events_404_for_missing_case(app_client):
    client, _ = app_client
    r = client.get("/v1/cases/case_doesnotexist00000000000001/events")
    assert r.status_code == 404


# ------------------------------------------------------------------ 5. GET /v1/experiments/{id}?_view=full


_READ_VERSIONS = {
    "P0": "sha256:" + "1" * 64,
    "P1": "sha256:" + "2" * 64,
    "K0": "sha256:" + "3" * 64,
    "K1": "sha256:" + "3" * 64,
    "M0": "sha256:" + "4" * 64,
    "M1": "sha256:" + "4" * 64,
}
_READ_GOOD = {"versionset_id": "vs_goodread0001", "digest": "sha256:" + "5" * 64, "revision": 1}
_READ_BAD = {"versionset_id": "vs_badread00001", "digest": "sha256:" + "6" * 64, "revision": 1}
_READ_CELL_VERSIONSETS = {
    "C": _READ_BAD,
    "RP": _READ_GOOD,
    "RK": _READ_BAD,
    "RM": _READ_BAD,
    "G": _READ_GOOD,
}
_READ_DISCOVERY = ["cs-001", "cs-002", "cs-003"]
_READ_HIDDEN = ["cs-004", "cs-005"]
_READ_CONTROLS = ["cs-013", "cs-014", "cs-015", "cs-016"]
_READ_PROBE_DIGEST = "sha256:f51fbbee2810467c96658f93e4fc2b64b5b843b80e55bf5029f30fa26bb9dbf0"
_READ_SEED_REF = "seed://read-view/1"
_READ_RESPONSES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "eval-harness"
        / "samples"
        / "b1_probe_responses.json"
    ).read_text(encoding="utf-8")
)


def _new_running_experiment(client, quality) -> tuple[str, str, int]:
    quality.seed_versionset(
        _READ_BAD["versionset_id"],
        status="active",
        revision=_READ_BAD["revision"],
        digest=_READ_BAD["digest"],
        content={
            "prompt": {"digest": _READ_VERSIONS["P1"]},
            "kb_manifest": {"manifest_digest": _READ_VERSIONS["K1"]},
            "model": {"digest": _READ_VERSIONS["M1"]},
        },
    )
    quality.seed_versionset(
        _READ_GOOD["versionset_id"],
        status="superseded",
        revision=_READ_GOOD["revision"],
        digest=_READ_GOOD["digest"],
        content={
            "prompt": {"digest": _READ_VERSIONS["P0"]},
            "kb_manifest": {"manifest_digest": _READ_VERSIONS["K0"]},
            "model": {"digest": _READ_VERSIONS["M0"]},
        },
    )
    complaint = client.post(
        "/v1/complaints",
        json={"source": "webhook", "text": "read-view attribution complaint", "external_id": "read-view-exp"},
    )
    assert complaint.status_code == 200
    case_id = complaint.json()["case_id"]
    claim = client.post(f"/v1/cases/{case_id}/claim", json={"worker_id": "runner-1"})
    assert claim.status_code == 200
    lease = claim.json()
    resp = client.post("/v1/experiments", json={"case_id": case_id, "hypothesis_layer": "prompt"})
    assert resp.status_code == 200
    exp_id = resp.json()["experiment_id"]
    frozen = client.post(
        f"/v1/experiments/{exp_id}/protocol",
        json={
            "execution_profile": "isolated-replay",
            "probe_set_digest": _READ_PROBE_DIGEST,
            "discovery": _READ_DISCOVERY,
            "hidden_confirmation": _READ_HIDDEN,
            "unaffected_controls": _READ_CONTROLS,
            "repetitions": 3,
            "versions": _READ_VERSIONS,
            "cell_versionsets": _READ_CELL_VERSIONSETS,
            "random_seed_ref": _READ_SEED_REF,
            "confidence": 0.95,
        },
    )
    assert frozen.status_code == 200
    started = client.post(
        f"/v1/experiments/{exp_id}/start",
        json={
            "runner_id": "runner-1",
            "lease_id": lease["lease_id"],
            "fencing_token": lease["fencing_token"],
        },
    )
    assert started.status_code == 200
    return exp_id, case_id, lease["fencing_token"]


def _read_view_artifacts(experiment_id: str, case_id: str, output_root: Path) -> tuple[dict, dict]:
    recovered_cells = {"C": False, "RP": True, "RK": False, "RM": False, "G": True}
    component_map = {
        "C": ("P1", "K1", "M1"),
        "RP": ("P0", "K1", "M1"),
        "RK": ("P1", "K0", "M1"),
        "RM": ("P1", "K1", "M0"),
        "G": ("P0", "K0", "M0"),
    }
    cells: dict[str, dict] = {}
    summaries: dict[str, dict] = {}
    for arm, recovered in recovered_cells.items():
        prompt, kb, model = component_map[arm]
        source_state = "baseline" if recovered else "b1_fault"
        results: list[dict] = []
        for probe_id in _READ_DISCOVERY + _READ_HIDDEN + _READ_CONTROLS:
            for repetition in range(1, 4):
                trial_recovered = True if probe_id in _READ_CONTROLS else recovered
                raw_output = {
                    "experiment_id": experiment_id,
                    "case_id": case_id,
                    "arm": arm,
                    "probe_id": probe_id,
                    "repetition": repetition,
                    "recovered": trial_recovered,
                    "status": "recorded-replay",
                    "answer": _READ_RESPONSES["states"][source_state][probe_id]["answer"],
                    "versionset_id": _READ_CELL_VERSIONSETS[arm]["versionset_id"],
                    "versionset_digest": _READ_CELL_VERSIONSETS[arm]["digest"],
                    "versionset_revision": _READ_CELL_VERSIONSETS[arm]["revision"],
                    "prompt_digest": _READ_VERSIONS[prompt],
                    "kb_manifest_digest": _READ_VERSIONS[kb],
                    "model_digest": _READ_VERSIONS[model],
                }
                output_path = output_root / arm / f"{probe_id}-{repetition}.json"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(raw_output, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                results.append(
                    {
                        "probe_id": probe_id,
                        "repetition": repetition,
                        "recovered": trial_recovered,
                        "output_ref": output_path.resolve().as_uri(),
                        "output_digest": canonical_json_digest(raw_output),
                    }
                )
        rate = 1.0 if recovered else 0.0
        cells[arm] = {
            "versions": {
                "prompt_digest": _READ_VERSIONS[prompt],
                "kb_manifest_digest": _READ_VERSIONS[kb],
                "model_digest": _READ_VERSIONS[model],
            },
            "results": results,
            "recovery_rate": rate,
            "control_pass_rate": 1.0,
        }
        summaries[arm] = {
            "recovery_rate": rate,
            "n_probes": 5,
            "n_trials": 15,
            "control_pass_rate": 1.0,
        }
    positive = newcombe_wilson_diff(1.0, 15, 0.0, 15)
    zero = newcombe_wilson_diff(0.0, 15, 0.0, 15)
    effects = {
        "prompt": {
            "delta": 1.0,
            "ci95_lower": round(positive[0], 4),
            "ci95_upper": round(positive[1], 4),
            "significant": True,
        },
        "kb": {
            "delta": 0.0,
            "ci95_lower": round(zero[0], 4),
            "ci95_upper": round(zero[1], 4),
            "significant": False,
        },
        "model_params": {
            "delta": 0.0,
            "ci95_lower": round(zero[0], 4),
            "ci95_upper": round(zero[1], 4),
            "significant": False,
        },
        "method": "newcombe_wilson_diff",
    }
    now = datetime.now(timezone.utc).isoformat()
    bundle = {
        "schema_version": "0.1.0",
        "bundle_id": "eb_readview000001",
        "experiment_id": experiment_id,
        "case_id": case_id,
        "protocol": {
            "matrix": "five_cell",
            "repetitions": 3,
            "random_arm_order": [f"{arm}@cs-001" for arm in ("RM", "C", "G", "RP", "RK")],
            "random_seed_ref": _READ_SEED_REF,
            "frozen_at": now,
            "confidence": 0.95,
        },
        "probe_set": {
            "probe_set_digest": _READ_PROBE_DIGEST,
            "discovery": _READ_DISCOVERY,
            "hidden_confirmation": _READ_HIDDEN,
            "unaffected_controls": _READ_CONTROLS,
        },
        "cells": cells,
        "effects": effects,
        "verdict": {
            "decision": "ATTRIBUTED",
            "attributed_layer": "prompt",
            "rationale": "only RP recovered and hidden probes reproduced",
            "hidden_confirmation_reproduced": True,
        },
        "created_at": now,
    }
    report = {
        "schema_version": "0.1.0",
        "report_id": "attr_readview0001",
        "experiment_id": experiment_id,
        "case_id": case_id,
        "probe_set_digest": _READ_PROBE_DIGEST,
        "version_digests": _READ_VERSIONS,
        "cells": summaries,
        "deltas": {
            layer: {
                "estimate": effect["delta"],
                "ci95_lower": effect["ci95_lower"],
                "ci95_upper": effect["ci95_upper"],
            }
            for layer, effect in effects.items()
            if layer != "method"
        },
        "verdict": {
            "decision": "ATTRIBUTED",
            "attributed_layer": "prompt",
            "interaction_detected": False,
            "full_factorial_required": False,
            "rationale": "only RP recovered and hidden probes reproduced",
        },
        "evidence_bundle_ref": {
            "uri": f"file:///tmp/{experiment_id}/evidence-bundle.json",
            "digest": canonical_json_digest(bundle),
        },
        "generated_at": now,
    }
    report["deltas"]["method"] = "newcombe_wilson_diff"
    return bundle, report


def _post_trial_receipts(client, experiment_id: str, fencing_token: int, bundle: dict, arms) -> None:
    for arm in arms:
        for trial in bundle["cells"][arm]["results"]:
            receipt = client.post(
                f"/v1/experiments/{experiment_id}/trials",
                json={
                    "cell": arm,
                    "probe_id": trial["probe_id"],
                    "repetition": trial["repetition"],
                    "recovered": trial["recovered"],
                    "output_ref": trial["output_ref"],
                    "output_digest": trial["output_digest"],
                    "fencing_token": fencing_token,
                },
            )
            assert receipt.status_code == 200, receipt.text


def _seed_live_gate_evidence(quality, report: dict) -> None:
    candidate = json.loads(
        base64.b64decode(report["artifact_refs"][2]["uri"].split(",", 1)[1])
    )
    first = candidate["responses"][0]
    quality.seed_versionset(
        candidate["target_versionset_id"],
        status="draft",
        revision=candidate["target_revision"],
        digest=report["subject"]["target_versionset_digest"],
        content={
            "prompt": {"digest": first["prompt_digest"]},
            "kb_manifest": {"manifest_digest": first["kb_manifest_digest"]},
            "model": {"digest": first["model_digest"]},
        },
    )
    for item in candidate["responses"]:
        quality.seed_log(
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


def test_experiment_full_view_returns_cells_before_verdict(app_client, tmp_path):
    client, quality = app_client
    exp_id, case_id, fencing_token = _new_running_experiment(client, quality)
    bundle, _ = _read_view_artifacts(exp_id, case_id, tmp_path / "partial-read-view")
    _post_trial_receipts(client, exp_id, fencing_token, bundle, ("C", "RP"))
    first = client.post(
        f"/v1/experiments/{exp_id}/cells",
        json={"cell": "C", "arm_order_index": 0, "recovery_rate": 0.0, "fencing_token": fencing_token},
    )
    second = client.post(
        f"/v1/experiments/{exp_id}/cells",
        json={"cell": "RP", "arm_order_index": 1, "recovery_rate": 1.0, "fencing_token": fencing_token},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    r = client.get(f"/v1/experiments/{exp_id}", params={"_view": "full"})
    assert r.status_code == 200
    body = r.json()
    assert body["experiment_id"] == exp_id
    assert [c["cell"] for c in body["cells"]] == ["C", "RP"]
    assert [c["recovery_rate"] for c in body["cells"]] == [0.0, 1.0]
    assert body["deltas"] is None
    assert body["verdict"] is None
    assert body["attributed_layer"] is None
    assert body["confidence_intervals"] is None


def test_experiment_full_view_with_verdict_projection(app_client, tmp_path):
    client, quality = app_client
    exp_id, case_id, fencing_token = _new_running_experiment(client, quality)
    bundle, report = _read_view_artifacts(exp_id, case_id, tmp_path / "read-view")
    _post_trial_receipts(client, exp_id, fencing_token, bundle, ("C", "RP", "RK", "RM", "G"))
    for idx, cell in enumerate(("C", "RP", "RK", "RM", "G")):
        completed = client.post(
            f"/v1/experiments/{exp_id}/cells",
            json={
                "cell": cell,
                "arm_order_index": idx,
                "recovery_rate": bundle["cells"][cell]["recovery_rate"],
                "fencing_token": fencing_token,
            },
        )
        assert completed.status_code == 200, completed.text
    verdict = client.post(
        f"/v1/experiments/{exp_id}/verdict",
        json={
            "fencing_token": fencing_token,
            "evidence_bundle": bundle,
            "attribution_report": report,
        },
    )
    assert verdict.status_code == 200, verdict.text

    r = client.get(f"/v1/experiments/{exp_id}", params={"_view": "full"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "ATTRIBUTED"
    assert body["attributed_layer"] == "prompt"
    assert body["deltas"] == {"prompt": 1.0, "kb": 0.0, "model_params": 0.0}
    assert body["evidence_bundle_ref"].startswith("evidence://sha256:")
    assert body["report_ref"].startswith("attribution://sha256:")
    assert len(body["cells"]) == 5
    assert body["confidence_intervals"] is None  # 无数据字段 → null 不报错


def test_experiment_full_view_404_for_missing(app_client):
    client, _ = app_client
    r = client.get("/v1/experiments/exp_doesnotexist00000000001", params={"_view": "full"})
    assert r.status_code == 404


def test_experiment_default_view_unchanged(app_client):
    """不带 _view 时仍返回原聚合视图（回归防护）。"""
    client, quality = app_client
    exp_id, _, _ = _new_running_experiment(client, quality)
    r = client.get(f"/v1/experiments/{exp_id}")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"experiment_id", "state", "revision", "payload"}
    assert "cells" not in body


# ------------------------------------------------------------------ 6. GET /v1/workorders


def _new_awaiting_approval_changeset(client, quality, seed: int = 1) -> tuple[str, dict]:
    workorder_id = f"wo_{seed:012d}"
    wo = make_workorder(
        workorder_id=workorder_id,
        nonce=f"00000000-0000-0000-0000-{seed:012d}",
        case_id="case_1234567890abcdef",
    )
    report = make_gate_report(workorder_id, eval_id=f"eval_readview{seed:012d}")
    report_hash = canonical_json_digest(report, prefix=False)
    wo["gate_report_ref"] = {
        "uri": f"eval://{report['eval_id']}",
        "digest": f"sha256:{report_hash}",
    }
    wo["hash"] = workorder_hash(wo)
    _seed_live_gate_evidence(quality, report)

    gate = client.post(
        "/v1/gate-reports",
        headers={"Authorization": f"Bearer {TEST_GATE_TOKEN}"},
        json={
            "report": report,
            "report_hash": report_hash,
            "workorder_id": workorder_id,
            "target_versionset_id": "vs_demo001fixedversionset01",
            "target_revision": 1,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        },
    )
    assert gate.status_code == 200, gate.text
    # Seed the read projection through the same mandatory lease path used by
    # external registration.
    with client.app.state.session_factory() as session:
        service = ReleaseService(
            session, client.app.state.quality_client, client.app.state.settings
        )
        registered = register_workorder_with_lease(service, wo)
        session.commit()
    assert registered["workorder_id"] == workorder_id
    cs_id = f"cs_{workorder_id}"
    attached = client.post(
        f"/v1/changesets/{cs_id}/gate",
        json={"eval_id": report["eval_id"], "report_hash": report_hash},
    )
    assert attached.status_code == 200, attached.text
    requested = client.post(
        f"/v1/changesets/{cs_id}/approval-request",
        json={
            "workorder_hash": wo["hash"],
            "nonce": wo["nonce"],
            "expiry": "2099-01-01T00:00:00+00:00",
            "channel": "feishu",
        },
    )
    assert requested.status_code == 200, requested.text
    return cs_id, wo


def test_workorders_empty(app_client):
    client, _ = app_client
    r = client.get("/v1/workorders")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_workorders_list_immutable_rows_with_changeset_lifecycle_metadata(app_client):
    client, quality = app_client
    cs_id, expected = _new_awaiting_approval_changeset(client, quality)

    r = client.get("/v1/workorders")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    wo = items[0]
    assert wo["changeset_id"] == cs_id
    assert wo["workorder_id"] == "wo_000000000001"
    assert wo["hash"] == expected["hash"]
    assert wo["freeze_at"] == "2099-01-01T00:00:00+00:00"  # approval_requested 的 expiry
    assert wo["requester"] == "repairer-1"  # changeset.drafted 事件 author_agent
    assert wo["channel"] == "prompt"
    assert wo["nonce"] == expected["nonce"]
    assert wo["state"] == "AWAITING_APPROVAL"
    assert "payload" not in wo
    assert wo["projection_warning"] is None
    assert wo["workorder_integrity_status"] == "verified"
    assert wo["gate_integrity_status"] == "verified"
    assert wo["gate_target_revision"] == 1
    assert wo["gate_target_versionset_id"] == "vs_demo001fixedversionset01"
    assert wo["gate_binding_digest"].startswith("sha256:")


def test_workorders_do_not_trust_tampered_changeset_hash(app_client, sqlite_session):
    client, quality = app_client
    cs_id, expected = _new_awaiting_approval_changeset(client, quality)
    changeset = sqlite_session.get(
        Aggregate,
        {"aggregate_type": "changeset", "aggregate_id": cs_id},
    )
    assert changeset is not None
    changeset.payload = {**(changeset.payload or {}), "workorder_hash": "0" * 64}
    sqlite_session.commit()

    item = client.get("/v1/workorders").json()["items"][0]
    assert item["hash"] == expected["hash"]
    assert item["projection_warning"] == "changeset_hash_mismatch"


def test_workorders_hide_tampered_immutable_payload(app_client, sqlite_session):
    client, quality = app_client
    _, expected = _new_awaiting_approval_changeset(client, quality)
    row = sqlite_session.get(WorkOrder, expected["workorder_id"])
    assert row is not None
    row.payload = {**row.payload, "created_by": "attacker"}
    sqlite_session.commit()

    item = client.get("/v1/workorders").json()["items"][0]
    assert item["workorder_integrity_status"] == "integrity_error"
    assert item["workorder_integrity_error"] == "workorder_hash_mismatch"
    assert item["gate_integrity_status"] == "integrity_error"
    assert "payload" not in item
    assert item["nonce"] is None
    assert item["target_versionset_digest"] is None


def test_workorders_fail_closed_when_projection_columns_are_tampered(app_client, sqlite_session):
    client, quality = app_client
    _, expected = _new_awaiting_approval_changeset(client, quality)
    row = sqlite_session.get(WorkOrder, expected["workorder_id"])
    assert row is not None
    row.case_id = "case_attacker"
    row.channel = "model"
    sqlite_session.commit()

    item = client.get("/v1/workorders").json()["items"][0]
    assert item["workorder_integrity_status"] == "integrity_error"
    assert item["workorder_integrity_error"] == "workorder_projection_mismatch"
    assert item["case_id"] is None
    assert item["channel"] == "UNKNOWN"
    assert item["state"] == "UNKNOWN"
    assert "payload" not in item


def test_workorders_multi_changeset(app_client):
    client, quality = app_client
    _new_awaiting_approval_changeset(client, quality, seed=1)
    _new_awaiting_approval_changeset(client, quality, seed=2)
    r = client.get("/v1/workorders")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


# ------------------------------------------------------------------ 7. GET /v1/gates


def test_gates_authoritative_empty_state_without_mcp_tables(app_client):
    client, _ = app_client
    r = client.get("/v1/gates")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_gates_empty_state(app_client, sqlite_engine):
    _create_mcp_tables(sqlite_engine)
    client, _ = app_client
    r = client.get("/v1/gates")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_gates_projects_dual_track_report(app_client, sqlite_engine):
    del sqlite_engine  # authoritative gates no longer read the MCP projection table
    client, quality = app_client
    report = make_gate_report("wo_000000000001", eval_id="eval_readviewgate1")
    _seed_live_gate_evidence(quality, report)
    report_hash = canonical_json_digest(report, prefix=False)
    registered = client.post(
        "/v1/gate-reports",
        headers={"Authorization": f"Bearer {TEST_GATE_TOKEN}"},
        json={
            "report": report,
            "report_hash": report_hash,
            "workorder_id": "wo_000000000001",
            "target_versionset_id": "vs_demo001fixedversionset01",
            "target_revision": 1,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        },
    )
    assert registered.status_code == 200, registered.text
    r = client.get("/v1/gates")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    g = items[0]
    assert g["eval_id"] == "eval_readviewgate1"
    assert g["report_id"] == report["report_id"]
    assert g["rule_track"] == "passed"
    assert g["judge_track"] == "passed"
    assert g["deterministic_tests"] == "passed"
    assert g["live_provider_e2e"] == "passed"
    assert g["verdict"] == "passed"
    assert g["report_hash"] == report_hash
    assert g["status"] == "unbound"
    assert g["binding_status"] == "UNBOUND"
    assert g["binding_error"] is None

    evidence = client.get("/v1/evidence").json()["items"]
    gate_item = next(item for item in evidence if item["source_type"] == "gate")
    assert gate_item["kind"] == "gate_report_binding"
    assert gate_item["binding_status"] == "UNKNOWN"
    assert gate_item["integrity_error"] == "gate_unbound"
    assert not any(item["kind"].startswith("artifact_ref_") for item in evidence)


def test_gates_marks_workorder_bound_report_verified(app_client):
    client, quality = app_client
    _, expected = _new_awaiting_approval_changeset(client, quality)

    item = client.get("/v1/gates").json()["items"][0]
    assert item["workorder_id"] == expected["workorder_id"]
    assert item["status"] == "completed"
    assert item["binding_status"] == "VERIFIED"
    assert item["binding_error"] is None
    assert item["verdict"] == "passed"


def test_gates_fail_closed_when_binding_digest_is_tampered(app_client, sqlite_session):
    client, quality = app_client
    _changeset_id, expected = _new_awaiting_approval_changeset(client, quality)
    eval_id = expected["gate_report_ref"]["uri"].removeprefix("eval://")
    row = sqlite_session.get(GateReportRecord, eval_id)
    assert row is not None
    row.binding_digest = "sha256:" + "0" * 64
    sqlite_session.commit()

    item = client.get("/v1/gates").json()["items"][0]
    assert item["status"] == "integrity_error"
    assert item["binding_status"] == "UNKNOWN"
    assert item["binding_error"] == "hash_mismatch"
    assert item["verdict"] == "error"
    assert "passed" not in item.values()


def test_gates_fail_closed_when_persisted_report_is_tampered(app_client, sqlite_session):
    client, quality = app_client
    report = make_gate_report("wo_000000000002", eval_id="eval_readviewtampered1")
    _seed_live_gate_evidence(quality, report)
    report_hash = canonical_json_digest(report, prefix=False)
    registered = client.post(
        "/v1/gate-reports",
        headers={"Authorization": f"Bearer {TEST_GATE_TOKEN}"},
        json={
            "report": report,
            "report_hash": report_hash,
            "workorder_id": "wo_000000000002",
            "target_versionset_id": "vs_demo001fixedversionset01",
            "target_revision": 1,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        },
    )
    assert registered.status_code == 200, registered.text

    row = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert row is not None
    row.report = {**row.report, "overall_status": "failed"}
    sqlite_session.commit()

    response = client.get("/v1/gates")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["eval_id"] == report["eval_id"]
    assert item["status"] == "integrity_error"
    assert item["integrity_error"] == "hash_mismatch"
    assert item["verdict"] == "error"
    assert item["rule_track"] == "error"
    assert "passed" not in item.values()


# ------------------------------------------------------------------ 8. GET /v1/evidence


def test_evidence_projects_recorded_refs_without_claiming_artifact_verified(app_client):
    client, _ = app_client
    complaint = client.post(
        "/v1/complaints",
        json={"source": "webhook", "text": "evidence projection", "external_id": "evidence-1"},
    )
    assert complaint.status_code == 200
    case_id = complaint.json()["case_id"]

    response = client.get("/v1/evidence", params={"case_id": case_id})
    assert response.status_code == 200
    body = response.json()
    assert body["artifact_store"] == "unavailable"
    assert body["warning"] == "artifact_content_unavailable"
    text_ref = next(item for item in body["items"] if item["kind"] == "text_ref")
    assert text_ref["case_id"] == case_id
    assert text_ref["reference"] == f"inline:{case_id}"
    assert text_ref["binding_status"] == "REFERENCE_RECORDED"
    assert text_ref["artifact_status"] == "UNKNOWN"
    assert not any(item["kind"] == "complainant_ref" for item in body["items"])
    assert not any(item.get("artifact_status") == "VERIFIED" for item in body["items"])
    assert all("value" not in item for item in body["items"])


def test_evidence_includes_bound_workorder_and_gate_refs(app_client):
    client, quality = app_client
    _, expected = _new_awaiting_approval_changeset(client, quality)
    response = client.get("/v1/evidence", params={"case_id": expected["case_id"]})
    assert response.status_code == 200
    items = response.json()["items"]
    gate_ref = next(
        item
        for item in items
        if item["source_type"] == "workorder" and item["kind"] == "gate_report_ref"
    )
    assert gate_ref["reference"].startswith("eval://")
    assert gate_ref["digest"].startswith("sha256:")
    assert gate_ref["binding_status"] == "BOUND"
    assert gate_ref["artifact_status"] == "UNKNOWN"
    gate_digest = next(
        item
        for item in items
        if item["source_type"] == "gate" and item["kind"] == "evidence_digest"
    )
    assert gate_digest["case_id"] == expected["case_id"]
    assert gate_digest["integrity_status"] == "recorded"
    assert all("value" not in item for item in items)
    assert "fix output format" not in str(items)


def test_evidence_rejects_malformed_digest_claim(app_client, sqlite_session):
    client, _ = app_client
    complaint = client.post(
        "/v1/complaints",
        json={"source": "webhook", "text": "digest check", "external_id": "bad-digest-1"},
    )
    case_id = complaint.json()["case_id"]
    event = sqlite_session.scalar(
        select(Event).where(
            Event.aggregate_id == case_id,
            Event.event_type == "complaint.received",
        )
    )
    assert event is not None
    event.payload = {**event.payload, "evidence_digest": "not-a-sha256-digest"}
    sqlite_session.commit()

    items = client.get("/v1/evidence", params={"case_id": case_id}).json()["items"]
    digest = next(item for item in items if item["kind"] == "evidence_digest")
    assert digest["digest"] is None
    assert digest["binding_status"] == "UNKNOWN"
    assert digest["integrity_status"] == "invalid_digest"


def test_evidence_fail_closed_for_tampered_gate_report(app_client, sqlite_session):
    client, quality = app_client
    _, expected = _new_awaiting_approval_changeset(client, quality)
    eval_id = expected["gate_report_ref"]["uri"].removeprefix("eval://")
    row = sqlite_session.get(GateReportRecord, eval_id)
    assert row is not None
    row.report = {**row.report, "overall_status": "failed"}
    sqlite_session.commit()

    items = client.get("/v1/evidence").json()["items"]
    gate_items = [item for item in items if item["source_type"] == "gate"]
    assert len(gate_items) == 1
    assert gate_items[0]["kind"] == "gate_report_integrity"
    assert gate_items[0]["case_id"] == expected["case_id"]
    assert gate_items[0]["binding_status"] == "UNKNOWN"
    assert gate_items[0]["integrity_status"] == "source_integrity_error"
    assert gate_items[0]["integrity_error"] == "hash_mismatch"
    assert not any(item["kind"].startswith("artifact_ref_") for item in gate_items)

    workorder_gate = next(
        item
        for item in items
        if item["source_type"] == "workorder" and item["kind"] == "gate_report_ref"
    )
    assert workorder_gate["binding_status"] == "UNKNOWN"
    assert workorder_gate["integrity_status"] == "source_integrity_error"
