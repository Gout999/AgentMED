"""Executable Attempt snapshot semantics for every frozen v4 state."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from v4_integrity import compute_record_digest, exact_record_binding, record_integrity_violations


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
SCHEMAS = V4_ROOT / "schemas"
VALID = V4_ROOT / "fixtures" / "valid"
INVALID = V4_ROOT / "fixtures" / "invalid"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = _json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Draft202012Validator(
        _json(SCHEMAS / "attempt.schema.json"),
        registry=Registry().with_resources(resources),
        format_checker=FormatChecker(),
    )


def _runtime_started(payload: dict[str, Any]) -> None:
    payload["runtime"]["runtime_session_id"] = "claude-session-stage0"
    payload["model"].update(
        {
            "resolved_provider": "zhipu",
            "resolved_model": "glm-5.2",
            "resolution_kind": "EXACT",
            "model_resolution_receipt_digest": "sha256:" + "44" * 32,
        }
    )
    payload["started_at"] = "2026-08-10T01:35:00Z"


def _advance(
    previous: dict[str, Any], state: str, authority_suffix: str
) -> dict[str, Any]:
    payload = copy.deepcopy(previous)
    payload["revision"] = previous["revision"] + 1
    payload["previous_snapshot"] = exact_record_binding("attempt", previous)
    payload["state"] = state
    payload["authority_receipt_id"] = "arec_" + authority_suffix
    payload["attempt_digest"] = compute_record_digest(payload, "attempt_digest")
    return payload


def _state_snapshots() -> dict[str, dict[str, Any]]:
    created = _json(VALID / "attempt-created.json")
    starting = _json(VALID / "attempt-starting.json")
    succeeded = _json(VALID / "attempt.json")

    running = _json(VALID / "attempt-running.json")
    output_recorded = _json(VALID / "attempt-output-recorded.json")

    cancel_requested = _advance(running, "CANCEL_REQUESTED", "cancelrequest01")

    failed = _advance(starting, "FAILED", "attemptfailed01")
    failed["failure"] = {
        "code": "RUNTIME_START_FAILED",
        "retryable": True,
        "reconciliation_required": False,
    }
    failed["completed_at"] = "2026-08-10T01:36:00Z"
    failed["attempt_digest"] = compute_record_digest(failed, "attempt_digest")

    timed_out = _advance(running, "TIMED_OUT", "attempttimeout1")
    timed_out["failure"] = {
        "code": "RUNTIME_TIMED_OUT",
        "retryable": True,
        "reconciliation_required": False,
    }
    timed_out["completed_at"] = "2026-08-10T02:35:00Z"
    timed_out["attempt_digest"] = compute_record_digest(timed_out, "attempt_digest")

    cancelled = _advance(created, "CANCELLED", "attemptcancel1")
    cancelled["completed_at"] = "2026-08-10T01:34:30Z"
    cancelled["attempt_digest"] = compute_record_digest(
        cancelled, "attempt_digest"
    )

    unknown = _advance(running, "UNKNOWN", "attemptunknown1")
    unknown["failure"] = {
        "code": "RUNTIME_OUTCOME_UNKNOWN",
        "retryable": False,
        "reconciliation_required": True,
    }
    unknown["attempt_digest"] = compute_record_digest(unknown, "attempt_digest")

    return {
        "CREATED": created,
        "STARTING": starting,
        "RUNNING": running,
        "OUTPUT_RECORDED": output_recorded,
        "CANCEL_REQUESTED": cancel_requested,
        "SUCCEEDED": succeeded,
        "FAILED": failed,
        "TIMED_OUT": timed_out,
        "CANCELLED": cancelled,
        "UNKNOWN": unknown,
    }


def test_every_frozen_attempt_state_has_an_honest_valid_snapshot() -> None:
    validator = _validator()
    snapshots = _state_snapshots()
    assert set(snapshots) == {
        "CREATED",
        "STARTING",
        "RUNNING",
        "OUTPUT_RECORDED",
        "CANCEL_REQUESTED",
        "SUCCEEDED",
        "FAILED",
        "TIMED_OUT",
        "CANCELLED",
        "UNKNOWN",
    }
    for state, payload in snapshots.items():
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
        assert not errors, f"{state}: {[error.message for error in errors]}"
        assert record_integrity_violations(payload, "attempt_digest") == ()


def test_created_and_starting_cannot_claim_runtime_or_model_resolution_too_early() -> None:
    validator = _validator()
    for fixture_name in ("attempt-created.json", "attempt-starting.json"):
        payload = _json(VALID / fixture_name)
        assert payload["runtime"]["runtime_session_id"] is None
        assert payload["model"]["resolved_provider"] is None
        assert payload["model"]["model_resolution_receipt_digest"] is None
        validator.validate(payload)

    mutation = _json(INVALID / "attempt-created-with-runtime-receipt.json")
    invalid = _json(VALID / mutation["base_fixture"])
    for item in mutation["mutations"]:
        target: Any = invalid
        parts = item["path"].split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = item["value"]
    assert list(validator.iter_errors(invalid))


def test_fallback_attempt_resolves_only_after_start_and_never_silently_switches() -> None:
    validator = _validator()
    fallback = _json(VALID / "attempt-created.json")
    fallback["attempt_kind"] = "FALLBACK"
    fallback["fallback_of_attempt_id"] = "att_prior0001"
    fallback["attempt_digest"] = compute_record_digest(fallback, "attempt_digest")
    validator.validate(fallback)
    fallback_created = copy.deepcopy(fallback)

    fallback["state"] = "RUNNING"
    fallback["revision"] = 2
    fallback["previous_snapshot"] = exact_record_binding(
        "attempt", fallback_created
    )
    fallback["authority_receipt_id"] = "arec_fallbackrun01"
    fallback["runtime_capability"] = _json(VALID / "attempt-running.json")[
        "runtime_capability"
    ]
    _runtime_started(fallback)
    fallback["model"]["resolution_kind"] = "FALLBACK"
    fallback["attempt_digest"] = compute_record_digest(fallback, "attempt_digest")
    validator.validate(fallback)

    fallback["model"]["resolution_kind"] = "EXACT"
    assert list(validator.iter_errors(fallback))
