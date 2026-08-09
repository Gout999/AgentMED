"""Authoritative five-cell attribution tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.config import Settings
from app.services import attribution as attribution_service
from app.models.tables import Lease
from app.quality.client import FakeQualityClient
from app.services.attribution import newcombe_wilson_diff
from app.services.audit import AuditWriteError
from app.services.case_service import CaseService
from app.services.experiment_service import ExperimentService, ExperimentServiceError
from app.services.release_service import ReleaseService, ReleaseServiceError
from app.services.state_machines import IllegalTransition
from app.utils.jcs import canonical_json_digest


VERSIONS = {
    "P0": "sha256:" + "1" * 64,
    "P1": "sha256:" + "2" * 64,
    "K0": "sha256:" + "3" * 64,
    "K1": "sha256:" + "3" * 64,
    "M0": "sha256:" + "4" * 64,
    "M1": "sha256:" + "4" * 64,
}
GOOD_REF = {"versionset_id": "vs_good00000001", "digest": "sha256:" + "5" * 64, "revision": 1}
BAD_REF = {"versionset_id": "vs_bad000000001", "digest": "sha256:" + "6" * 64, "revision": 1}
CELL_VERSIONSETS = {"C": BAD_REF, "RP": GOOD_REF, "RK": BAD_REF, "RM": BAD_REF, "G": GOOD_REF}
DISCOVERY = ["cs-001", "cs-002", "cs-003"]
HIDDEN = ["cs-004", "cs-005"]
CONTROLS = ["cs-013", "cs-014", "cs-015", "cs-016"]
PROBE_DIGEST = "sha256:f51fbbee2810467c96658f93e4fc2b64b5b843b80e55bf5029f30fa26bb9dbf0"
SEED_REF = "seed://b1/20260807"
_RESPONSES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "eval-harness"
        / "samples"
        / "b1_probe_responses.json"
    ).read_text(encoding="utf-8")
)


def test_isolated_replay_repo_ref_is_root_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(attribution_service, "REPO_ROOT", tmp_path)
    expected = (tmp_path / "evidence" / "raw.json").resolve()

    assert attribution_service._isolated_replay_artifact_path(
        urlparse("repo:///evidence/raw.json")
    ) == expected
    with pytest.raises(
        attribution_service.AttributionValidationError,
        match="escapes the repository root",
    ):
        attribution_service._isolated_replay_artifact_path(
            urlparse("repo:///../outside.json")
        )
    with pytest.raises(attribution_service.AttributionValidationError):
        attribution_service._isolated_replay_artifact_path(
            urlparse("repo://remote/evidence/raw.json")
        )


def _services(session):
    settings = Settings(allow_isolated_replay_attribution=True)
    quality = FakeQualityClient()
    quality.seed_versionset(
        BAD_REF["versionset_id"],
        status="active",
        revision=BAD_REF["revision"],
        digest=BAD_REF["digest"],
        content={
            "prompt": {"digest": VERSIONS["P1"]},
            "kb_manifest": {"manifest_digest": VERSIONS["K1"]},
            "model": {"digest": VERSIONS["M1"]},
        },
    )
    quality.seed_versionset(
        GOOD_REF["versionset_id"],
        status="superseded",
        revision=GOOD_REF["revision"],
        digest=GOOD_REF["digest"],
        content={
            "prompt": {"digest": VERSIONS["P0"]},
            "kb_manifest": {"manifest_digest": VERSIONS["K0"]},
            "model": {"digest": VERSIONS["M0"]},
        },
    )
    cases = CaseService(session, settings)
    case_id = cases.ingest_complaint(
        source="webhook",
        text="refund policy complaint",
        external_id="b1-experiment-test",
    )["case_id"]
    lease = cases.claim(case_id, "eval-runner")
    return ExperimentService(session, settings, quality), cases, case_id, lease


def _freeze(
    svc: ExperimentService,
    experiment_id: str,
    *,
    execution_profile: str = "isolated-replay",
):
    return svc.freeze_protocol(
        experiment_id,
        execution_profile=execution_profile,
        probe_set_digest=PROBE_DIGEST,
        discovery=DISCOVERY,
        hidden_confirmation=HIDDEN,
        unaffected_controls=CONTROLS,
        repetitions=3,
        versions=VERSIONS,
        cell_versionsets=CELL_VERSIONSETS,
        random_seed_ref=SEED_REF,
        confidence=0.95,
    )


def _artifacts(experiment_id: str, case_id: str):
    recovered_cells = {"C": False, "RP": True, "RK": False, "RM": False, "G": True}
    component_map = {
        "C": ("P1", "K1", "M1"),
        "RP": ("P0", "K1", "M1"),
        "RK": ("P1", "K0", "M1"),
        "RM": ("P1", "K1", "M0"),
        "G": ("P0", "K0", "M0"),
    }
    cells = {}
    summaries = {}
    for arm, recovered in recovered_cells.items():
        p, k, m = component_map[arm]
        source_state = "baseline" if recovered else "b1_fault"
        results = []
        for probe_id in DISCOVERY + HIDDEN + CONTROLS:
            for repetition in range(1, 4):
                raw_output = {
                    "experiment_id": experiment_id,
                    "case_id": case_id,
                    "arm": arm,
                    "probe_id": probe_id,
                    "repetition": repetition,
                    "recovered": True if probe_id in CONTROLS else recovered,
                    "status": "recorded-replay",
                    "answer": _RESPONSES["states"][source_state][probe_id]["answer"],
                    "versionset_id": CELL_VERSIONSETS[arm]["versionset_id"],
                    "versionset_digest": CELL_VERSIONSETS[arm]["digest"],
                    "versionset_revision": CELL_VERSIONSETS[arm]["revision"],
                    "prompt_digest": VERSIONS[p],
                    "kb_manifest_digest": VERSIONS[k],
                    "model_digest": VERSIONS[m],
                }
                raw_path = Path("/tmp") / experiment_id / arm / f"{probe_id}-{repetition}.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(
                    json.dumps(raw_output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                results.append(
                    {
                        "probe_id": probe_id,
                        "repetition": repetition,
                        "recovered": True if probe_id in CONTROLS else recovered,
                        "output_ref": raw_path.resolve().as_uri(),
                        "output_digest": canonical_json_digest(raw_output),
                    }
                )
        rate = 1.0 if recovered else 0.0
        cells[arm] = {
            "versions": {
                "prompt_digest": VERSIONS[p],
                "kb_manifest_digest": VERSIONS[k],
                "model_digest": VERSIONS[m],
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
    positive_low, positive_high = newcombe_wilson_diff(1.0, 15, 0.0, 15)
    zero_low, zero_high = newcombe_wilson_diff(0.0, 15, 0.0, 15)
    effects = {
        "prompt": {
            "delta": 1.0,
            "ci95_lower": round(positive_low, 4),
            "ci95_upper": round(positive_high, 4),
            "significant": True,
        },
        "kb": {
            "delta": 0.0,
            "ci95_lower": round(zero_low, 4),
            "ci95_upper": round(zero_high, 4),
            "significant": False,
        },
        "model_params": {
            "delta": 0.0,
            "ci95_lower": round(zero_low, 4),
            "ci95_upper": round(zero_high, 4),
            "significant": False,
        },
        "method": "newcombe_wilson_diff",
    }
    now = datetime.now(timezone.utc).isoformat()
    bundle = {
        "schema_version": "0.1.0",
        "bundle_id": "eb_b1authoritative01",
        "experiment_id": experiment_id,
        "case_id": case_id,
        "protocol": {
            "matrix": "five_cell",
            "repetitions": 3,
            "random_arm_order": [f"{arm}@cs-001" for arm in ("RM", "C", "G", "RP", "RK")],
            "random_seed_ref": SEED_REF,
            "frozen_at": now,
            "confidence": 0.95,
        },
        "probe_set": {
            "probe_set_digest": PROBE_DIGEST,
            "discovery": DISCOVERY,
            "hidden_confirmation": HIDDEN,
            "unaffected_controls": CONTROLS,
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
        "report_id": "attr_b1authoritative01",
        "experiment_id": experiment_id,
        "case_id": case_id,
        "probe_set_digest": PROBE_DIGEST,
        "version_digests": VERSIONS,
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


def _running_experiment(session, *, execution_profile: str = "isolated-replay"):
    svc, cases, case_id, lease = _services(session)
    experiment_id = svc.create(case_id=case_id, hypothesis_layer="prompt")["experiment_id"]
    _freeze(svc, experiment_id, execution_profile=execution_profile)
    svc.start(
        experiment_id,
        runner_id="eval-runner",
        lease_id=lease["lease_id"],
        fencing_token=lease["fencing_token"],
    )
    return svc, cases, case_id, lease, experiment_id


def _bind_live_provider_logs(svc: ExperimentService, bundle: dict, report: dict) -> None:
    quality = svc.quality
    assert isinstance(quality, FakeQualityClient)
    for arm, cell in bundle["cells"].items():
        for trial in cell["results"]:
            path = Path(trial["output_ref"].removeprefix("file://"))
            raw = json.loads(path.read_text(encoding="utf-8"))
            request_id = f"req_{arm.lower()}_{trial['probe_id'].replace('-', '')}_{trial['repetition']}"
            trace_id = f"trace-{arm.lower()}-{trial['probe_id']}-{trial['repetition']}"
            raw.update({"status": "ok", "request_id": request_id, "trace_id": trace_id})
            encoded = json.dumps(
                raw,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            trial["output_ref"] = (
                "data:application/json;base64," + base64.b64encode(encoded).decode("ascii")
            )
            trial["output_digest"] = canonical_json_digest(raw)
            quality.seed_log(
                request_id,
                status="ok",
                trace_id=trace_id,
                versionset_id=raw["versionset_id"],
                prompt_digest=raw["prompt_digest"],
                kb_manifest_digest=raw["kb_manifest_digest"],
                model_digest=raw["model_digest"],
                answer_digest="sha256:" + hashlib.sha256(raw["answer"].encode("utf-8")).hexdigest(),
            )
    report["evidence_bundle_ref"]["digest"] = canonical_json_digest(bundle)


def _register_trials(
    svc: ExperimentService,
    experiment_id: str,
    lease: dict,
    bundle: dict,
    arms: tuple[str, ...] | None = None,
) -> None:
    for arm, cell in bundle["cells"].items():
        if arms is not None and arm not in arms:
            continue
        for trial in cell["results"]:
            result = svc.trial_completed(
                experiment_id,
                cell=arm,
                probe_id=trial["probe_id"],
                repetition=trial["repetition"],
                recovered=trial["recovered"],
                output_ref=trial["output_ref"],
                output_digest=trial["output_digest"],
                fencing_token=lease["fencing_token"],
            )
            assert result["duplicate"] is False


def _complete_cells(
    svc: ExperimentService,
    experiment_id: str,
    lease: dict,
    bundle: dict,
    order=("C", "RP", "RK", "RM", "G"),
) -> None:
    _register_trials(svc, experiment_id, lease, bundle)
    for index, arm in enumerate(order):
        svc.cell_completed(
            experiment_id,
            cell=arm,
            arm_order_index=index,
            recovery_rate=bundle["cells"][arm]["recovery_rate"],
            fencing_token=lease["fencing_token"],
        )


def test_experiment_lifecycle_recomputes_attributed_prompt(sqlite_session):
    svc, cases, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    _complete_cells(svc, experiment_id, lease, bundle, ("RM", "C", "G", "RP", "RK"))
    result = svc.verdict_computed(
        experiment_id,
        fencing_token=lease["fencing_token"],
        evidence_bundle=bundle,
        attribution_report=report,
    )
    assert result["state"] == "VERDICT_COMPUTED"
    assert result["payload"]["verdict"] == "ATTRIBUTED"
    assert result["payload"]["attributed_layer"] == "prompt"
    case = cases.get_case(case_id)
    assert case["state"] == "AWAITING_FIX"
    assert case["payload"]["fault_layer"] == "prompt"
    repair_lease = cases.claim(case_id, "repairer-1")
    assert repair_lease["state"] == "AWAITING_FIX"
    assert repair_lease["owner_id"] == "repairer-1"
    assert repair_lease["fencing_token"] != lease["fencing_token"]


def test_live_attribution_requires_and_accepts_exact_quality_provider_logs(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(
        sqlite_session,
        execution_profile="live",
    )
    bundle, report = _artifacts(experiment_id, case_id)
    _bind_live_provider_logs(svc, bundle, report)
    _complete_cells(svc, experiment_id, lease, bundle)
    result = svc.verdict_computed(
        experiment_id,
        fencing_token=lease["fencing_token"],
        evidence_bundle=bundle,
        attribution_report=report,
    )
    assert result["payload"]["verdict"] == "ATTRIBUTED"
    assert "get_log" in svc.quality.call_log


def test_trial_receipt_is_exactly_idempotent_and_provider_ids_cannot_be_reused(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(
        sqlite_session,
        execution_profile="live",
    )
    bundle, report = _artifacts(experiment_id, case_id)
    _bind_live_provider_logs(svc, bundle, report)
    first = bundle["cells"]["C"]["results"][0]
    request = {
        "cell": "C",
        "probe_id": first["probe_id"],
        "repetition": first["repetition"],
        "recovered": first["recovered"],
        "output_ref": first["output_ref"],
        "output_digest": first["output_digest"],
        "fencing_token": lease["fencing_token"],
    }

    created = svc.trial_completed(experiment_id, **request)
    duplicate = svc.trial_completed(experiment_id, **request)

    assert created["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert svc.list_completed_trials(experiment_id)["count"] == 1
    with pytest.raises(ExperimentServiceError) as conflict:
        svc.trial_completed(experiment_id, **{**request, "recovered": not first["recovered"]})
    assert conflict.value.code == "idempotency_conflict"

    header, encoded = first["output_ref"].split(",", 1)
    raw = json.loads(base64.b64decode(encoded).decode("utf-8"))
    raw["repetition"] = 2
    reused_ref = header + "," + base64.b64encode(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    with pytest.raises(ExperimentServiceError) as reused:
        svc.trial_completed(
            experiment_id,
            cell="C",
            probe_id=first["probe_id"],
            repetition=2,
            recovered=first["recovered"],
            output_ref=reused_ref,
            output_digest=canonical_json_digest(raw),
            fencing_token=lease["fencing_token"],
        )
    assert reused.value.code == "idempotency_conflict"
    assert "request_id was reused" in reused.value.message


def test_cell_completion_requires_all_trial_receipts(sqlite_session):
    svc, _, _case_id, lease, experiment_id = _running_experiment(sqlite_session)

    with pytest.raises(ExperimentServiceError) as exc:
        svc.cell_completed(
            experiment_id,
            cell="C",
            arm_order_index=0,
            recovery_rate=0.0,
            fencing_token=lease["fencing_token"],
        )

    assert exc.value.code == "incomplete_experiment"
    assert "immutable trial receipt" in exc.value.message


def test_live_attribution_rejects_recorded_replay_even_when_semantically_correct(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(
        sqlite_session,
        execution_profile="live",
    )
    bundle, report = _artifacts(experiment_id, case_id)
    trial = bundle["cells"]["C"]["results"][0]
    with pytest.raises(ExperimentServiceError) as exc:
        svc.trial_completed(
            experiment_id,
            cell="C",
            probe_id=trial["probe_id"],
            repetition=trial["repetition"],
            recovered=trial["recovered"],
            output_ref=trial["output_ref"],
            output_digest=trial["output_digest"],
            fencing_token=lease["fencing_token"],
        )
    assert exc.value.code == "validation_failed"
    assert "process-independent inline evidence" in exc.value.message


def test_isolated_replay_attribution_requires_explicit_sqlite_flag(sqlite_session):
    svc, _, case_id, _ = _services(sqlite_session)
    strict = ExperimentService(sqlite_session, Settings(), svc.quality)
    experiment_id = strict.create(case_id=case_id, hypothesis_layer="prompt")["experiment_id"]
    with pytest.raises(ExperimentServiceError) as exc:
        _freeze(strict, experiment_id, execution_profile="isolated-replay")
    assert exc.value.code == "validation_error"
    assert "explicit allow flag and SQLite" in exc.value.message


def test_missing_or_duplicate_cells_fail_closed(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    _register_trials(svc, experiment_id, lease, bundle, ("C",))
    svc.cell_completed(
        experiment_id,
        cell="C",
        arm_order_index=0,
        recovery_rate=0.0,
        fencing_token=lease["fencing_token"],
    )
    with pytest.raises(ExperimentServiceError) as duplicate:
        svc.cell_completed(
            experiment_id,
            cell="C",
            arm_order_index=1,
            recovery_rate=0.0,
            fencing_token=lease["fencing_token"],
        )
    assert duplicate.value.code == "idempotency_conflict"
    with pytest.raises(ExperimentServiceError) as incomplete:
        svc.verdict_computed(
            experiment_id,
            fencing_token=lease["fencing_token"],
            evidence_bundle=bundle,
            attribution_report=report,
        )
    assert incomplete.value.code == "incomplete_experiment"


def test_tampered_trial_or_caller_verdict_fails_closed(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    _complete_cells(svc, experiment_id, lease, bundle)
    bundle["cells"]["C"]["results"].pop()
    report["evidence_bundle_ref"]["digest"] = canonical_json_digest(bundle)
    with pytest.raises(ExperimentServiceError) as exc:
        svc.verdict_computed(
            experiment_id,
            fencing_token=lease["fencing_token"],
            evidence_bundle=bundle,
            attribution_report=report,
        )
    assert exc.value.code == "hash_mismatch"
    assert "trial set differs" in exc.value.message


def test_verdict_rejects_self_consistent_wrong_raw_versionset(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    trial = bundle["cells"]["RP"]["results"][0]
    raw_path = Path(trial["output_ref"].removeprefix("file://"))
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["versionset_id"] = BAD_REF["versionset_id"]
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    trial["output_digest"] = canonical_json_digest(raw)
    report["evidence_bundle_ref"]["digest"] = canonical_json_digest(bundle)

    with pytest.raises(ExperimentServiceError) as exc:
        _register_trials(svc, experiment_id, lease, bundle)
    assert exc.value.code == "validation_failed"
    assert "exact VersionSet identity mismatch" in exc.value.message


def test_verdict_rejects_runner_declared_recovery_that_disagrees_with_answer(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    trial = bundle["cells"]["C"]["results"][0]
    raw_path = Path(trial["output_ref"].removeprefix("file://"))
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["answer"] = _RESPONSES["states"]["baseline"][trial["probe_id"]]["answer"]
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    trial["output_digest"] = canonical_json_digest(raw)
    report["evidence_bundle_ref"]["digest"] = canonical_json_digest(bundle)

    with pytest.raises(ExperimentServiceError) as exc:
        _register_trials(svc, experiment_id, lease, bundle)
    assert exc.value.code == "validation_failed"
    assert "repository-owned probe oracle" in exc.value.message


def test_start_requires_exact_case_lease(sqlite_session):
    svc, _, case_id, lease = _services(sqlite_session)
    experiment_id = svc.create(case_id=case_id)["experiment_id"]
    _freeze(svc, experiment_id)
    with pytest.raises(ExperimentServiceError) as exc:
        svc.start(
            experiment_id,
            runner_id="eval-runner",
            lease_id=lease["lease_id"],
            fencing_token=lease["fencing_token"] + 1,
        )
    assert exc.value.code == "lease_lost"


def test_verdict_requires_current_fencing_token(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    _complete_cells(svc, experiment_id, lease, bundle)
    with pytest.raises(ExperimentServiceError) as exc:
        svc.verdict_computed(
            experiment_id,
            fencing_token=lease["fencing_token"] + 1,
            evidence_bundle=bundle,
            attribution_report=report,
        )
    assert exc.value.code == "lease_lost"


def test_freeze_rejects_unbound_or_non_digest_versions(sqlite_session):
    svc, _, case_id, _ = _services(sqlite_session)
    experiment_id = svc.create(case_id=case_id)["experiment_id"]
    with pytest.raises(ExperimentServiceError) as exc:
        svc.freeze_protocol(
            experiment_id,
            probe_set_digest=PROBE_DIGEST,
            discovery=DISCOVERY,
            hidden_confirmation=HIDDEN,
            unaffected_controls=CONTROLS,
            repetitions=3,
            versions={"P0": "v0"},
            cell_versionsets={},
            random_seed_ref=SEED_REF,
        )
    assert exc.value.code == "validation_error"


def test_freeze_rejects_versionset_identity_not_confirmed_by_quality(sqlite_session):
    svc, _, case_id, _ = _services(sqlite_session)
    experiment_id = svc.create(case_id=case_id)["experiment_id"]
    refs = {name: dict(ref) for name, ref in CELL_VERSIONSETS.items()}
    refs["RP"]["digest"] = "sha256:" + "0" * 64

    with pytest.raises(ExperimentServiceError) as exc:
        svc.freeze_protocol(
            experiment_id,
            probe_set_digest=PROBE_DIGEST,
            discovery=DISCOVERY,
            hidden_confirmation=HIDDEN,
            unaffected_controls=CONTROLS,
            repetitions=3,
            versions=VERSIONS,
            cell_versionsets=refs,
            random_seed_ref=SEED_REF,
        )

    assert exc.value.code == "hash_mismatch"


def test_verdict_rejects_frozen_versionset_revision_drift(sqlite_session):
    svc, _, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    _complete_cells(svc, experiment_id, lease, bundle)
    assert isinstance(svc.quality, FakeQualityClient)
    svc.quality._vs[GOOD_REF["versionset_id"]].revision = 2

    with pytest.raises(ExperimentServiceError) as exc:
        svc.verdict_computed(
            experiment_id,
            fencing_token=lease["fencing_token"],
            evidence_bundle=bundle,
            attribution_report=report,
        )

    assert exc.value.code == "hash_mismatch"


def test_escalate_full_factorial_requires_computed_confounded(sqlite_session):
    svc, _, case_id, _ = _services(sqlite_session)
    experiment_id = svc.create(case_id=case_id)["experiment_id"]
    with pytest.raises(IllegalTransition):
        svc.escalate_full_factorial(experiment_id, reason="confounded_control")


def test_runner_cancel_requires_exact_active_lease(sqlite_session):
    svc, cases, case_id, lease, experiment_id = _running_experiment(sqlite_session)

    cancelled = svc.cancel(
        experiment_id,
        reason="provider unavailable",
        runner_id="eval-runner",
        lease_id=lease["lease_id"],
        fencing_token=lease["fencing_token"],
    )

    assert cancelled["state"] == "CANCELLED"
    assert cancelled["duplicate"] is False
    case = cases.store.get_aggregate("case", case_id)
    assert case is not None and case.state == "ESCALATED"
    assert case.payload["escalated_experiment_id"] == experiment_id
    assert sqlite_session.get(Lease, case_id) is None
    assert cases.reclaim_if_expired(case_id) is None
    duplicate = svc.cancel(
        experiment_id,
        reason="provider unavailable",
        runner_id="eval-runner",
        lease_id=lease["lease_id"],
        fencing_token=lease["fencing_token"],
    )
    assert duplicate["state"] == "CANCELLED"
    assert duplicate["duplicate"] is True

    with pytest.raises(ExperimentServiceError) as conflict:
        svc.cancel(
            experiment_id,
            reason="different failure",
            runner_id="eval-runner",
            lease_id=lease["lease_id"],
            fencing_token=lease["fencing_token"],
        )
    assert conflict.value.code == "idempotency_conflict"


def test_runner_cancel_audit_failure_rolls_back_case_experiment_and_lease(sqlite_session):
    svc, cases, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    quality = svc.quality
    sqlite_session.commit()
    failing = ExperimentService(
        sqlite_session,
        Settings(audit_force_fail=True, allow_isolated_replay_attribution=True),
        quality,
    )

    with pytest.raises(AuditWriteError):
        failing.cancel(
            experiment_id,
            reason="provider unavailable",
            runner_id="eval-runner",
            lease_id=lease["lease_id"],
            fencing_token=lease["fencing_token"],
        )
    sqlite_session.rollback()

    experiment = svc.store.get_aggregate("experiment", experiment_id)
    case = cases.store.get_aggregate("case", case_id)
    persisted_lease = sqlite_session.get(Lease, case_id)
    assert experiment is not None and experiment.state == "RUNNING"
    assert case is not None and case.state == "ATTRIBUTING"
    assert persisted_lease is not None
    assert persisted_lease.lease_id == lease["lease_id"]


def test_expired_attribution_runner_is_requeued_with_frozen_progress(sqlite_session):
    svc, cases, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    _register_trials(svc, experiment_id, lease, bundle, ("C",))
    svc.cell_completed(
        experiment_id,
        cell="C",
        arm_order_index=0,
        recovery_rate=0.0,
        fencing_token=lease["fencing_token"],
    )
    persisted_lease = sqlite_session.get(Lease, case_id)
    assert persisted_lease is not None
    persisted_lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    sqlite_session.flush()

    reclaimed = cases.reclaim_if_expired(case_id)

    assert reclaimed is not None
    assert reclaimed["state"] == "DISPATCHED"
    assert reclaimed["experiment_state"] == "PROTOCOL_FROZEN"
    assert sqlite_session.get(Lease, case_id) is None
    experiment = svc.store.get_aggregate("experiment", experiment_id)
    assert experiment is not None
    assert experiment.payload["completed_cells"] == ["C"]
    replacement = cases.claim(case_id, "eval-runner-replacement")
    resumed = svc.start(
        experiment_id,
        runner_id="eval-runner-replacement",
        lease_id=replacement["lease_id"],
        fencing_token=replacement["fencing_token"],
    )
    assert resumed["state"] == "RUNNING"
    assert resumed["payload"]["completed_cells"] == ["C"]
    for trial in bundle["cells"]["C"]["results"]:
        retried_trial = svc.trial_completed(
            experiment_id,
            cell="C",
            probe_id=trial["probe_id"],
            repetition=trial["repetition"],
            recovered=trial["recovered"],
            output_ref=trial["output_ref"],
            output_digest=trial["output_digest"],
            fencing_token=replacement["fencing_token"],
        )
        assert retried_trial["duplicate"] is True
    retried_cell = svc.cell_completed(
        experiment_id,
        cell="C",
        arm_order_index=0,
        recovery_rate=0.0,
        fencing_token=replacement["fencing_token"],
    )
    assert retried_cell["duplicate"] is True
    _register_trials(svc, experiment_id, replacement, bundle, ("RP", "RK", "RM", "G"))
    for index, arm in enumerate(("RP", "RK", "RM", "G"), start=1):
        svc.cell_completed(
            experiment_id,
            cell=arm,
            arm_order_index=index,
            recovery_rate=bundle["cells"][arm]["recovery_rate"],
            fencing_token=replacement["fencing_token"],
        )
    verdict = svc.verdict_computed(
        experiment_id,
        fencing_token=replacement["fencing_token"],
        evidence_bundle=bundle,
        attribution_report=report,
    )
    assert verdict["state"] == "VERDICT_COMPUTED"
    case = cases.store.get_aggregate("case", case_id)
    assert case is not None and case.state == "AWAITING_FIX"
    assert sqlite_session.get(Lease, case_id) is None


def test_stale_runner_cannot_cancel_terminal_verdict_after_handoff(sqlite_session):
    svc, cases, case_id, lease, experiment_id = _running_experiment(sqlite_session)
    bundle, report = _artifacts(experiment_id, case_id)
    _complete_cells(svc, experiment_id, lease, bundle)
    svc.verdict_computed(
        experiment_id,
        fencing_token=lease["fencing_token"],
        evidence_bundle=bundle,
        attribution_report=report,
    )
    cases.claim(case_id, "repairer:after-verdict")

    with pytest.raises(ExperimentServiceError) as exc:
        svc.cancel(
            experiment_id,
            reason="late evaluator failure",
            runner_id="eval-runner",
            lease_id=lease["lease_id"],
            fencing_token=lease["fencing_token"],
        )

    assert exc.value.code == "lease_lost"
    assert svc.get(experiment_id)["state"] == "VERDICT_COMPUTED"


def _attributed_candidate_context(session):
    svc, _, case_id, lease, experiment_id = _running_experiment(session)
    bundle, report = _artifacts(experiment_id, case_id)
    _complete_cells(svc, experiment_id, lease, bundle)
    svc.verdict_computed(
        experiment_id,
        fencing_token=lease["fencing_token"],
        evidence_bundle=bundle,
        attribution_report=report,
    )
    bad_prompt_ref = {"prompt_id": "prompts/system.md", "version": "bad"}
    good_prompt_ref = {"prompt_id": "prompts/system.md", "version": "good"}
    bad_prompt = canonical_json_digest(bad_prompt_ref)
    good_prompt = canonical_json_digest(good_prompt_ref)
    kb_entry = {"kb_id": "customer-service", "entry_id": "policy", "version": "1.0.0"}
    normalized_entry = {**kb_entry, "digest": canonical_json_digest(kb_entry)}
    kb_digest = canonical_json_digest({"entries": [normalized_entry]})
    model_ref = {"provider": "recorded", "model": "athlete", "params": {"temperature": 0}}
    model_digest = canonical_json_digest(model_ref)
    bad_content = {
        "prompt": {**bad_prompt_ref, "digest": bad_prompt},
        "kb_manifest": {"entries": [normalized_entry], "manifest_digest": kb_digest},
        "model": {**model_ref, "digest": model_digest},
    }
    good_content = {
        "prompt": {**good_prompt_ref, "digest": good_prompt},
        "kb_manifest": dict(bad_content["kb_manifest"]),
        "model": dict(bad_content["model"]),
    }
    quality = FakeQualityClient()
    base = quality.seed_versionset(
        "vs_attributedbase01",
        status="active",
        revision=1,
        digest=canonical_json_digest(bad_content),
        content=bad_content,
    )
    proposal = {
        "case_id": case_id,
        "channel": "prompt",
        "attribution_report_digest": canonical_json_digest(report),
        "base_versionset_id": base["versionset_id"],
        "base_versionset_digest": base["digest"],
        "base_revision": base["revision"],
        "target_prompt_digest": good_prompt,
        "content": good_content,
    }
    repair_worker_id = "repairer:test-candidate"
    repair_lease = CaseService(session, Settings()).claim(case_id, repair_worker_id)
    return quality, proposal, {
        "worker_id": repair_worker_id,
        "fencing_token": repair_lease["fencing_token"],
    }


def test_candidate_is_controller_created_and_single_variable(sqlite_session):
    quality, proposal, lease_binding = _attributed_candidate_context(sqlite_session)
    releases = ReleaseService(sqlite_session, quality, Settings())
    result = releases.create_candidate(
        **proposal,
        **lease_binding,
        proposal_digest=canonical_json_digest(proposal),
        idempotency_key="candidate-authority-1",
    )
    assert result["status"] == "draft"
    assert result["case_id"] == proposal["case_id"]
    assert result["input_versions"]["prompt_digest"] != proposal["target_prompt_digest"]
    assert result["content"]["prompt"]["digest"] == proposal["target_prompt_digest"]
    assert quality.call_log == ["create_versionset"]

    tampered = {
        **proposal,
        "content": {
            **proposal["content"],
            "kb_manifest": {
                "entries": [{"kb_id": "other", "entry_id": "smuggled", "version": "1.0.0"}],
            },
        },
    }
    with pytest.raises(ReleaseServiceError) as exc:
        releases.create_candidate(
            **tampered,
            **lease_binding,
            proposal_digest=canonical_json_digest(tampered),
            idempotency_key="candidate-authority-2",
        )
    assert exc.value.code == "validation_failed"
    assert quality.call_log == ["create_versionset"]


def test_candidate_audit_failure_blocks_quality_write(sqlite_session):
    quality, proposal, lease_binding = _attributed_candidate_context(sqlite_session)
    releases = ReleaseService(sqlite_session, quality, Settings(audit_force_fail=True))
    with pytest.raises(AuditWriteError):
        releases.create_candidate(
            **proposal,
            **lease_binding,
            proposal_digest=canonical_json_digest(proposal),
            idempotency_key="candidate-audit-fail",
        )
    assert quality.call_log == []


def test_candidate_rejects_stale_repairer_fencing_token(sqlite_session):
    quality, proposal, lease_binding = _attributed_candidate_context(sqlite_session)
    lease = sqlite_session.get(Lease, proposal["case_id"])
    assert lease is not None
    lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    sqlite_session.flush()
    CaseService(sqlite_session, Settings()).claim(
        proposal["case_id"], "repairer:replacement"
    )

    with pytest.raises(ReleaseServiceError) as exc:
        ReleaseService(sqlite_session, quality, Settings()).create_candidate(
            **proposal,
            **lease_binding,
            proposal_digest=canonical_json_digest(proposal),
            idempotency_key="candidate-stale-lease",
        )

    assert exc.value.code == "lease_lost"
    assert quality.call_log == []


def test_candidate_retry_reconciles_durable_intent_after_post_quality_audit_crash(
    sqlite_session, monkeypatch
):
    quality, proposal, lease_binding = _attributed_candidate_context(sqlite_session)
    releases = ReleaseService(sqlite_session, quality, Settings())
    original_record = releases.audit.record

    def fail_completion(**kwargs):
        if kwargs.get("action") == "candidate.create":
            raise AuditWriteError("completion audit unavailable")
        return original_record(**kwargs)

    monkeypatch.setattr(releases.audit, "record", fail_completion)
    request = {
        **proposal,
        **lease_binding,
        "proposal_digest": canonical_json_digest(proposal),
        "idempotency_key": "candidate-durable-intent",
    }
    with pytest.raises(AuditWriteError):
        releases.create_candidate(**request)
    sqlite_session.rollback()

    intent = releases.store.get_aggregate(
        "candidate_creation", "candidate-durable-intent"
    )
    assert intent is not None and intent.state == "PENDING"
    assert quality.call_log == ["create_versionset"]

    old_lease = sqlite_session.get(Lease, proposal["case_id"])
    assert old_lease is not None
    old_lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    sqlite_session.commit()
    replacement = CaseService(sqlite_session, Settings()).claim(
        proposal["case_id"], "repairer:reconciler"
    )
    request.update(
        worker_id="repairer:reconciler",
        fencing_token=replacement["fencing_token"],
    )
    monkeypatch.setattr(releases.audit, "record", original_record)
    recovered = releases.create_candidate(**request)
    intent = releases.store.get_aggregate(
        "candidate_creation", "candidate-durable-intent"
    )
    assert intent is not None and intent.state == "COMPLETED"
    assert recovered["status"] == "draft"
    assert quality.call_log == ["create_versionset", "create_versionset"]

    duplicate = releases.create_candidate(**request)
    assert duplicate["duplicate"] is True
    assert duplicate["versionset_id"] == recovered["versionset_id"]
