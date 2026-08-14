"""Deterministic attribution verification for immutable experiment artifacts.

The runner may collect provider outputs, but it does not own the verdict.  This
module validates the frozen protocol and both attribution contracts, recomputes
all counts/effects, and applies the repository-owned R1--R5 rules before the
control plane accepts an attribution decision.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Callable
import unicodedata
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator, FormatChecker
import yaml

from app.utils.jcs import canonical_json_digest


REPO_ROOT = Path(__file__).resolve().parents[3]
CELLS = ("C", "RP", "RK", "RM", "G")
SINGLE_FACTOR_ARMS = {"RP": "prompt", "RK": "kb", "RM": "model_params"}
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CELL_COMPONENTS = {
    "C": {"prompt_digest": "P1", "kb_manifest_digest": "K1", "model_digest": "M1"},
    "RP": {"prompt_digest": "P0", "kb_manifest_digest": "K1", "model_digest": "M1"},
    "RK": {"prompt_digest": "P1", "kb_manifest_digest": "K0", "model_digest": "M1"},
    "RM": {"prompt_digest": "P1", "kb_manifest_digest": "K1", "model_digest": "M0"},
    "G": {"prompt_digest": "P0", "kb_manifest_digest": "K0", "model_digest": "M0"},
}
Z_975 = 1.959964
_WS_RE = re.compile(r"\s+")


class AttributionValidationError(ValueError):
    pass


def _isolated_replay_artifact_path(parsed: Any) -> Path:
    """Resolve replay-only file/repo references without allowing repo traversal."""

    if parsed.scheme == "file" and parsed.path:
        return Path(unquote(parsed.path))
    if parsed.scheme == "repo" and parsed.path and not parsed.netloc:
        root = REPO_ROOT.resolve()
        path = (root / unquote(parsed.path).lstrip("/")).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AttributionValidationError(
                "trial repo output_ref escapes the repository root"
            ) from exc
        return path
    raise AttributionValidationError(
        "trial output_ref must be inline JSON or an isolated-replay file/repo artifact"
    )


def _normalise_probe(raw: dict[str, Any]) -> dict[str, Any]:
    expected = raw.get("expected_behavior") or {}
    tags = raw.get("tags") or {}
    return {
        "id": str(raw["id"]),
        "input": str(raw["input"]),
        "expected_behavior": {
            "description": str(expected.get("description", "")),
            "must_include": [str(value) for value in expected.get("must_include", [])],
            "must_not_include": [str(value) for value in expected.get("must_not_include", [])],
            **({"format": expected.get("format")} if expected.get("format") else {}),
            **(
                {"max_output_chars": expected.get("max_output_chars")}
                if expected.get("max_output_chars") is not None
                else {}
            ),
        },
        "tags": {
            **({"fault_layer": tags.get("fault_layer")} if tags.get("fault_layer") else {}),
            **({"topic": tags.get("topic")} if tags.get("topic") else {}),
        },
    }


def _probe_contract(digest: str) -> dict[str, dict[str, Any]]:
    """Resolve a frozen digest to a repository-owned deterministic probe oracle."""

    fixtures = Path(__file__).resolve().parents[3] / "contracts" / "fixtures"
    for path in sorted(fixtures.glob("probes-*.yaml")):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            probes = sorted(
                (_normalise_probe(item) for item in document.get("probes", [])),
                key=lambda item: item["id"],
            )
        except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError):
            continue
        candidate = canonical_json_digest({"probe_set": probes})
        if candidate == digest:
            return {probe["id"]: probe for probe in probes}
    raise AttributionValidationError(
        "probe_set_digest does not resolve to a repository-owned frozen probe contract"
    )


def _normalise_text(text: str) -> str:
    return _WS_RE.sub("", unicodedata.normalize("NFKC", text))


def _is_subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(character in iterator for character in needle)


def _contains_phrase(phrase: str, normalised_answer: str) -> bool:
    normalised_phrase = _normalise_text(phrase)
    return (
        not normalised_phrase
        or normalised_phrase in normalised_answer
        or _is_subsequence(normalised_phrase, normalised_answer)
    )


def _judge_probe(probe: dict[str, Any], answer: str) -> bool:
    """Re-run the repository-owned rule oracle; never trust runner recovery."""

    expected = probe["expected_behavior"]
    raw_answer = answer.strip()
    normalised_answer = _normalise_text(raw_answer)
    if expected.get("format") == "json":
        fenced = re.search(r"```(?:json)?\s*(.*?)```", raw_answer, flags=re.DOTALL)
        candidate = fenced.group(1).strip() if fenced else raw_answer
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            return False
        if isinstance(decoded, dict):
            keys = set(decoded)
        elif isinstance(decoded, list):
            keys = {
                key
                for item in decoded
                if isinstance(item, dict)
                for key in item
            }
        else:
            return False
        if any(value not in keys for value in expected.get("must_include", [])):
            return False
    elif any(
        not _contains_phrase(value, normalised_answer)
        for value in expected.get("must_include", [])
    ):
        return False
    if any(
        _normalise_text(value) and _normalise_text(value) in normalised_answer
        for value in expected.get("must_not_include", [])
    ):
        return False
    maximum = expected.get("max_output_chars")
    return maximum is None or len(raw_answer) <= int(maximum)


@dataclass(frozen=True)
class CellStats:
    recovery_rate: float
    n_trials: int
    control_pass_rate: float
    hidden_recovery_rate: float
    hidden_trials: int


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials <= 0 or successes < 0 or successes > trials:
        raise AttributionValidationError("invalid Wilson counts")
    p = successes / trials
    z2 = Z_975 * Z_975
    denominator = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denominator
    margin = (Z_975 / denominator) * math.sqrt(
        p * (1 - p) / trials + z2 / (4 * trials * trials)
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def newcombe_wilson_diff(
    p_arm: float,
    n_arm: int,
    p_control: float,
    n_control: int,
) -> tuple[float, float]:
    if n_arm <= 0 or n_control <= 0:
        raise AttributionValidationError("both arms require positive trial counts")
    arm_low, arm_high = wilson_interval(round(p_arm * n_arm), n_arm)
    ctl_low, ctl_high = wilson_interval(round(p_control * n_control), n_control)
    delta = p_arm - p_control
    lower = delta - math.sqrt((p_arm - arm_low) ** 2 + (ctl_high - p_control) ** 2)
    upper = delta + math.sqrt((arm_high - p_arm) ** 2 + (p_control - ctl_low) ** 2)
    return max(-1.0, lower), min(1.0, upper)


def _schema(name: str) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "contracts" / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(instance: dict[str, Any], name: str) -> None:
    validator = Draft202012Validator(_schema(name), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise AttributionValidationError(f"{name} invalid at {location}: {first.message}")


def _close(actual: Any, expected: float, label: str) -> None:
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        raise AttributionValidationError(f"{label} must be numeric")
    if abs(float(actual) - expected) > 0.0001:
        raise AttributionValidationError(
            f"{label} mismatch: declared={actual!r} recomputed={round(expected, 4)!r}"
        )


def _cell_stats(
    bundle: dict[str, Any],
    frozen: dict[str, Any],
    provider_log_resolver: Callable[[str], dict[str, Any]] | None,
) -> dict[str, CellStats]:
    probe_groups = {
        "discovery": list(frozen.get("discovery") or []),
        "hidden_confirmation": list(frozen.get("hidden_confirmation") or []),
        "unaffected_controls": list(frozen.get("unaffected_controls") or []),
    }
    all_ids = [probe_id for values in probe_groups.values() for probe_id in values]
    if len(all_ids) != len(set(all_ids)):
        raise AttributionValidationError("frozen probe groups must be disjoint and unique")
    repetitions = frozen.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions <= 0:
        raise AttributionValidationError("frozen repetitions must be a positive integer")
    expected_keys = {
        (probe_id, repetition)
        for probe_id in all_ids
        for repetition in range(1, repetitions + 1)
    }
    affected = set(probe_groups["discovery"] + probe_groups["hidden_confirmation"])
    hidden = set(probe_groups["hidden_confirmation"])
    controls = set(probe_groups["unaffected_controls"])
    versions = frozen.get("versions") or {}
    probe_oracle = _probe_contract(str(frozen.get("probe_set_digest") or ""))

    result: dict[str, CellStats] = {}
    for cell_name in CELLS:
        cell = bundle["cells"][cell_name]
        actual_versions = cell.get("versions") or {}
        expected_versions = {
            field: versions[version_key]
            for field, version_key in CELL_COMPONENTS[cell_name].items()
        }
        if actual_versions != expected_versions:
            raise AttributionValidationError(
                f"cell {cell_name} versions do not match the frozen component digests"
            )

        expected_versionset = (frozen.get("cell_versionsets") or {}).get(cell_name) or {}
        seen: dict[tuple[str, int], bool] = {}
        for trial in cell.get("results") or []:
            key = (trial.get("probe_id"), trial.get("repetition"))
            if key in seen:
                raise AttributionValidationError(f"cell {cell_name} has duplicate trial {key!r}")
            if key not in expected_keys:
                raise AttributionValidationError(f"cell {cell_name} has unexpected trial {key!r}")
            declared_recovered = trial.get("recovered")
            if not isinstance(declared_recovered, bool):
                raise AttributionValidationError(f"cell {cell_name} trial {key!r} is not boolean")
            recovered = _validate_output_artifact(
                trial,
                experiment_id=bundle.get("experiment_id"),
                case_id=bundle.get("case_id"),
                cell_name=cell_name,
                expected_versions=expected_versions,
                expected_versionset=expected_versionset,
                probe=probe_oracle[str(trial.get("probe_id"))],
                execution_profile=str(frozen.get("execution_profile") or ""),
                provider_log_resolver=provider_log_resolver,
            )
            if declared_recovered is not recovered:
                raise AttributionValidationError(
                    f"cell {cell_name} trial {key!r} recovery differs from the repository oracle"
                )
            seen[key] = recovered
        missing = expected_keys - set(seen)
        if missing:
            example = sorted(missing)[0]
            raise AttributionValidationError(
                f"cell {cell_name} is incomplete; missing {len(missing)} trials, first={example!r}"
            )

        affected_values = [value for (probe_id, _), value in seen.items() if probe_id in affected]
        hidden_values = [value for (probe_id, _), value in seen.items() if probe_id in hidden]
        control_values = [value for (probe_id, _), value in seen.items() if probe_id in controls]
        recovery_rate = sum(affected_values) / len(affected_values)
        control_pass_rate = sum(control_values) / len(control_values)
        hidden_recovery_rate = sum(hidden_values) / len(hidden_values)
        _close(cell.get("recovery_rate"), round(recovery_rate, 4), f"cells.{cell_name}.recovery_rate")
        _close(
            cell.get("control_pass_rate"),
            round(control_pass_rate, 4),
            f"cells.{cell_name}.control_pass_rate",
        )
        result[cell_name] = CellStats(
            recovery_rate=recovery_rate,
            n_trials=len(affected_values),
            control_pass_rate=control_pass_rate,
            hidden_recovery_rate=hidden_recovery_rate,
            hidden_trials=len(hidden_values),
        )
    return result


def validate_attribution_trial(
    *,
    experiment_id: str,
    case_id: str,
    frozen: dict[str, Any],
    cell_name: str,
    trial: dict[str, Any],
    provider_log_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate one immutable trial before it becomes resumable control-plane state.

    The final verdict still revalidates the complete evidence bundle.  This
    earlier check prevents a runner from checkpointing an unverified or
    differently-bound provider result and later treating it as completed work.
    """

    if cell_name not in CELLS:
        raise AttributionValidationError(f"unknown attribution cell {cell_name!r}")
    repetitions = frozen.get("repetitions")
    repetition = trial.get("repetition")
    if (
        isinstance(repetitions, bool)
        or not isinstance(repetitions, int)
        or repetitions <= 0
        or isinstance(repetition, bool)
        or not isinstance(repetition, int)
        or repetition < 1
        or repetition > repetitions
    ):
        raise AttributionValidationError("trial repetition is outside the frozen protocol")
    probe_id = trial.get("probe_id")
    expected_probe_ids = {
        *list(frozen.get("discovery") or []),
        *list(frozen.get("hidden_confirmation") or []),
        *list(frozen.get("unaffected_controls") or []),
    }
    if not isinstance(probe_id, str) or probe_id not in expected_probe_ids:
        raise AttributionValidationError("trial probe_id is outside the frozen protocol")
    if not isinstance(trial.get("recovered"), bool):
        raise AttributionValidationError("trial recovered must be boolean")
    versions = frozen.get("versions") or {}
    try:
        expected_versions = {
            field: versions[version_key]
            for field, version_key in CELL_COMPONENTS[cell_name].items()
        }
        probe = _probe_contract(str(frozen.get("probe_set_digest") or ""))[probe_id]
    except KeyError as exc:
        raise AttributionValidationError(
            "trial cannot be bound to the frozen VersionSet/probe contract"
        ) from exc
    return validate_trial_output_artifact(
        trial,
        experiment_id=experiment_id,
        case_id=case_id,
        cell_name=cell_name,
        expected_versions=expected_versions,
        expected_versionset=(frozen.get("cell_versionsets") or {}).get(cell_name) or {},
        probe=probe,
        execution_profile=str(frozen.get("execution_profile") or ""),
        provider_log_resolver=provider_log_resolver,
    )


def validate_trial_output_artifact(
    trial: dict[str, Any],
    *,
    experiment_id: str,
    case_id: str,
    cell_name: str,
    expected_versions: dict[str, str],
    expected_versionset: dict[str, Any],
    probe: dict[str, Any],
    execution_profile: str,
    provider_log_resolver: Callable[[str], dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return the validated raw artifact and deterministic recovery decision."""

    recovered = _validate_output_artifact(
        trial,
        experiment_id=experiment_id,
        case_id=case_id,
        cell_name=cell_name,
        expected_versions=expected_versions,
        expected_versionset=expected_versionset,
        probe=probe,
        execution_profile=execution_profile,
        provider_log_resolver=provider_log_resolver,
        _return_artifact=True,
    )
    assert isinstance(recovered, tuple)  # private helper contract
    return {"recovered": recovered[0], "artifact": recovered[1]}


def _validate_output_artifact(
    trial: dict[str, Any],
    *,
    experiment_id: str,
    case_id: str,
    cell_name: str,
    expected_versions: dict[str, str],
    expected_versionset: dict[str, Any],
    probe: dict[str, Any],
    execution_profile: str,
    provider_log_resolver: Callable[[str], dict[str, Any]] | None,
    _return_artifact: bool = False,
) -> bool | tuple[bool, dict[str, Any]]:
    output_ref = trial.get("output_ref")
    output_digest = trial.get("output_digest")
    if not isinstance(output_ref, str) or not output_ref:
        raise AttributionValidationError("every trial requires a persisted output_ref")
    if not isinstance(output_digest, str) or DIGEST_RE.fullmatch(output_digest) is None:
        raise AttributionValidationError("every trial requires a canonical output_digest")
    parsed = urlparse(output_ref)
    try:
        if parsed.scheme == "data":
            header, separator, encoded = output_ref.partition(",")
            if separator != "," or header != "data:application/json;base64":
                raise AttributionValidationError(
                    "trial data output_ref must be data:application/json;base64"
                )
            # Reject oversized data URIs before Base64 allocates the decoded
            # payload. Four encoded bytes represent at most three raw bytes.
            if len(encoded) > ((2_000_000 + 2) // 3) * 4:
                raise AttributionValidationError("trial output artifact exceeds 2 MB")
            artifact_bytes = base64.b64decode(encoded, validate=True)
        elif parsed.scheme in {"file", "repo"} and parsed.path:
            if execution_profile != "isolated-replay":
                raise AttributionValidationError(
                    "live trial output_ref must be process-independent inline evidence"
                )
            path = _isolated_replay_artifact_path(parsed)
            if path.stat().st_size > 2_000_000:
                raise AttributionValidationError("trial output artifact exceeds 2 MB")
            artifact_bytes = path.read_bytes()
        else:
            raise AttributionValidationError(
                "trial output_ref must be inline JSON or an isolated-replay file/repo artifact"
            )
        if len(artifact_bytes) > 2_000_000:
            raise AttributionValidationError("trial output artifact exceeds 2 MB")
        raw = json.loads(artifact_bytes.decode("utf-8"))
    except AttributionValidationError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, binascii.Error) as exc:
        raise AttributionValidationError("trial output artifact is unavailable or invalid") from exc
    if canonical_json_digest(raw) != output_digest:
        raise AttributionValidationError("trial output artifact digest mismatch")
    expected_identity = {
        "experiment_id": experiment_id,
        "case_id": case_id,
        "arm": cell_name,
        "probe_id": trial.get("probe_id"),
        "repetition": trial.get("repetition"),
        "recovered": trial.get("recovered"),
    }
    if any(raw.get(key) != value for key, value in expected_identity.items()):
        raise AttributionValidationError("trial output artifact identity/recovery mismatch")
    actual_versions = {
        "prompt_digest": raw.get("prompt_digest"),
        "kb_manifest_digest": raw.get("kb_manifest_digest"),
        "model_digest": raw.get("model_digest"),
    }
    if actual_versions != expected_versions:
        raise AttributionValidationError("trial output artifact VersionSet components mismatch")
    actual_versionset = {
        "versionset_id": raw.get("versionset_id"),
        "digest": raw.get("versionset_digest"),
        "revision": raw.get("versionset_revision"),
    }
    if actual_versionset != expected_versionset:
        raise AttributionValidationError("trial output artifact exact VersionSet identity mismatch")
    answer = raw.get("answer")
    if not isinstance(answer, str):
        raise AttributionValidationError("trial output artifact has no answer for deterministic replay")
    if execution_profile == "isolated-replay":
        if raw.get("status") != "recorded-replay":
            raise AttributionValidationError(
                "isolated-replay attribution requires status=recorded-replay"
            )
    elif execution_profile == "live":
        if raw.get("status") != "ok":
            raise AttributionValidationError("live attribution requires provider status=ok")
        request_id = raw.get("request_id")
        trace_id = raw.get("trace_id")
        if not isinstance(request_id, str) or not request_id or not isinstance(trace_id, str) or not trace_id:
            raise AttributionValidationError("live attribution requires request_id and trace_id")
        if provider_log_resolver is None:
            raise AttributionValidationError("live attribution requires a Quality provider-log resolver")
        try:
            provider_log = provider_log_resolver(request_id)
        except Exception as exc:  # noqa: BLE001 - evidence dependency failure is fail-closed
            raise AttributionValidationError(
                f"Quality provider log unavailable for request_id={request_id}"
            ) from exc
        answer_digest = "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest()
        expected_log = {
            "request_id": request_id,
            "status": "ok",
            "trace_id": trace_id,
            "versionset_id": expected_versionset.get("versionset_id"),
            "prompt_digest": expected_versions.get("prompt_digest"),
            "kb_manifest_digest": expected_versions.get("kb_manifest_digest"),
            "model_digest": expected_versions.get("model_digest"),
            "answer_digest": answer_digest,
        }
        if any(provider_log.get(key) != value for key, value in expected_log.items()):
            raise AttributionValidationError(
                "live attribution output does not match the authoritative Quality provider log"
            )
    else:
        raise AttributionValidationError("unknown attribution execution_profile")
    recovered = _judge_probe(probe, answer)
    if raw.get("recovered") is not recovered:
        raise AttributionValidationError(
            "trial output artifact recovery differs from the repository-owned probe oracle"
        )
    if _return_artifact:
        return recovered, raw
    return recovered


def _effect(arm: str, stats: dict[str, CellStats], delta_min: float) -> dict[str, Any]:
    arm_stats = stats[arm]
    control = stats["C"]
    lower, upper = newcombe_wilson_diff(
        arm_stats.recovery_rate,
        arm_stats.n_trials,
        control.recovery_rate,
        control.n_trials,
    )
    hidden_lower, _ = newcombe_wilson_diff(
        arm_stats.hidden_recovery_rate,
        arm_stats.hidden_trials,
        control.hidden_recovery_rate,
        control.hidden_trials,
    )
    hidden_delta = arm_stats.hidden_recovery_rate - control.hidden_recovery_rate
    return {
        "delta": arm_stats.recovery_rate - control.recovery_rate,
        "ci95_lower": lower,
        "ci95_upper": upper,
        "significant": lower > delta_min,
        "hidden_delta": hidden_delta,
        "hidden_ci95_lower": hidden_lower,
        "hidden_reproduced": hidden_delta > 0 and hidden_lower > 0,
    }


def _adjudicate(stats: dict[str, CellStats], delta_min: float) -> dict[str, Any]:
    effects = {arm: _effect(arm, stats, delta_min) for arm in SINGLE_FACTOR_ARMS}
    g_effect = _effect("G", stats, delta_min)
    for arm, cell in stats.items():
        if cell.control_pass_rate < 1.0:
            return {
                "decision": "INCONCLUSIVE",
                "attributed_layer": None,
                "interaction_detected": False,
                "full_factorial_required": False,
                "hidden_confirmation_reproduced": False,
                "reason_code": "ENV_UNTRUSTED",
                "rationale": f"R1: {arm} unaffected controls failed",
                "effects": effects,
            }
    if not g_effect["significant"]:
        return {
            "decision": "INCONCLUSIVE",
            "attributed_layer": None,
            "interaction_detected": False,
            "full_factorial_required": False,
            "hidden_confirmation_reproduced": False,
            "reason_code": "BASELINE_NOT_RESTORED",
            "rationale": "R2: known-good baseline did not recover",
            "effects": effects,
        }
    significant = [arm for arm, effect in effects.items() if effect["significant"]]
    if len(significant) != 1:
        return {
            "decision": "CONFOUNDED",
            "attributed_layer": None,
            "interaction_detected": True,
            "full_factorial_required": True,
            "hidden_confirmation_reproduced": False,
            "reason_code": "INTERACTION_UNRESOLVED",
            "rationale": "R3: zero or multiple single-factor arms recovered",
            "effects": effects,
        }
    arm = significant[0]
    effect = effects[arm]
    if effect["hidden_reproduced"]:
        return {
            "decision": "ATTRIBUTED",
            "attributed_layer": SINGLE_FACTOR_ARMS[arm],
            "interaction_detected": False,
            "full_factorial_required": False,
            "hidden_confirmation_reproduced": True,
            "reason_code": None,
            "rationale": f"R4: only {arm} recovered and hidden confirmation reproduced",
            "effects": effects,
        }
    return {
        "decision": "INCONCLUSIVE",
        "attributed_layer": None,
        "interaction_detected": False,
        "full_factorial_required": False,
        "hidden_confirmation_reproduced": False,
        "reason_code": "CONFIRMATION_MISMATCH",
        "rationale": f"R5: {arm} recovery did not reproduce on hidden probes",
        "effects": effects,
    }


def validate_attribution_artifacts(
    *,
    experiment_id: str,
    case_id: str,
    frozen: dict[str, Any],
    evidence_bundle: dict[str, Any],
    attribution_report: dict[str, Any],
    delta_min: float,
    provider_log_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate, recompute and bind one attribution report to its raw evidence."""

    _validate_schema(evidence_bundle, "evidence-bundle.schema.json")
    _validate_schema(attribution_report, "attribution-report.schema.json")
    if evidence_bundle.get("experiment_id") != experiment_id or attribution_report.get("experiment_id") != experiment_id:
        raise AttributionValidationError("artifact experiment_id does not match the aggregate")
    if evidence_bundle.get("case_id") != case_id or attribution_report.get("case_id") != case_id:
        raise AttributionValidationError("artifact case_id does not match the frozen experiment")
    if evidence_bundle.get("probe_set") != {
        "probe_set_digest": frozen.get("probe_set_digest"),
        "discovery": frozen.get("discovery"),
        "hidden_confirmation": frozen.get("hidden_confirmation"),
        "unaffected_controls": frozen.get("unaffected_controls"),
    }:
        raise AttributionValidationError("evidence probe set differs from the frozen protocol")
    protocol = evidence_bundle.get("protocol") or {}
    if (
        protocol.get("matrix") != "five_cell"
        or protocol.get("repetitions") != frozen.get("repetitions")
        or protocol.get("confidence") != 0.95
        or protocol.get("random_seed_ref") != frozen.get("random_seed_ref")
    ):
        raise AttributionValidationError("evidence protocol differs from the frozen protocol")
    order = protocol.get("random_arm_order") or []
    if len(order) < len(CELLS) or set(str(item).split("@", 1)[0] for item in order) != set(CELLS):
        raise AttributionValidationError("random_arm_order must record every five-cell arm")

    versions = frozen.get("versions") or {}
    if attribution_report.get("version_digests") != versions:
        raise AttributionValidationError("report version digests differ from the frozen protocol")
    if attribution_report.get("probe_set_digest") != frozen.get("probe_set_digest"):
        raise AttributionValidationError("report probe_set_digest differs from the frozen protocol")

    stats = _cell_stats(evidence_bundle, frozen, provider_log_resolver)
    computed = _adjudicate(stats, delta_min)
    bundle_effects = evidence_bundle.get("effects") or {}
    report_deltas = attribution_report.get("deltas") or {}
    for arm, layer in SINGLE_FACTOR_ARMS.items():
        effect = computed["effects"][arm]
        declared_effect = bundle_effects.get(layer) or {}
        _close(declared_effect.get("delta"), round(effect["delta"], 4), f"effects.{layer}.delta")
        _close(
            declared_effect.get("ci95_lower"),
            round(effect["ci95_lower"], 4),
            f"effects.{layer}.ci95_lower",
        )
        _close(
            declared_effect.get("ci95_upper"),
            round(effect["ci95_upper"], 4),
            f"effects.{layer}.ci95_upper",
        )
        if declared_effect.get("significant") is not effect["significant"]:
            raise AttributionValidationError(f"effects.{layer}.significant mismatch")
        declared_delta = report_deltas.get(layer) or {}
        _close(declared_delta.get("estimate"), round(effect["delta"], 4), f"deltas.{layer}.estimate")
        _close(
            declared_delta.get("ci95_lower"),
            round(effect["ci95_lower"], 4),
            f"deltas.{layer}.ci95_lower",
        )
        _close(
            declared_delta.get("ci95_upper"),
            round(effect["ci95_upper"], 4),
            f"deltas.{layer}.ci95_upper",
        )
    if bundle_effects.get("method") != "newcombe_wilson_diff" or report_deltas.get("method") != "newcombe_wilson_diff":
        raise AttributionValidationError("effect method must be newcombe_wilson_diff")

    evidence_digest = canonical_json_digest(evidence_bundle)
    evidence_ref = attribution_report.get("evidence_bundle_ref") or {}
    if evidence_ref.get("digest") != evidence_digest:
        raise AttributionValidationError("AttributionReport evidence_bundle_ref digest mismatch")
    bundle_verdict = evidence_bundle.get("verdict") or {}
    report_verdict = attribution_report.get("verdict") or {}
    expected_pairs = {
        "decision": computed["decision"],
        "attributed_layer": computed["attributed_layer"],
    }
    for key, expected in expected_pairs.items():
        if bundle_verdict.get(key) != expected or report_verdict.get(key) != expected:
            raise AttributionValidationError(f"artifact verdict {key} does not match deterministic adjudication")
    if bundle_verdict.get("hidden_confirmation_reproduced") is not computed["hidden_confirmation_reproduced"]:
        raise AttributionValidationError("hidden confirmation verdict mismatch")
    if report_verdict.get("interaction_detected") is not computed["interaction_detected"]:
        raise AttributionValidationError("interaction verdict mismatch")
    if report_verdict.get("full_factorial_required", False) is not computed["full_factorial_required"]:
        raise AttributionValidationError("full-factorial verdict mismatch")

    for cell_name, cell_stats in stats.items():
        summary = (attribution_report.get("cells") or {}).get(cell_name) or {}
        _close(summary.get("recovery_rate"), round(cell_stats.recovery_rate, 4), f"report.cells.{cell_name}.recovery_rate")
        _close(summary.get("control_pass_rate"), round(cell_stats.control_pass_rate, 4), f"report.cells.{cell_name}.control_pass_rate")
        if summary.get("n_probes") != len(frozen["discovery"] + frozen["hidden_confirmation"]):
            raise AttributionValidationError(f"report.cells.{cell_name}.n_probes mismatch")
        if summary.get("n_trials") != cell_stats.n_trials:
            raise AttributionValidationError(f"report.cells.{cell_name}.n_trials mismatch")

    report_digest = canonical_json_digest(attribution_report)
    return {
        "verdict": computed["decision"],
        "attributed_layer": computed["attributed_layer"],
        "deltas": {
            layer: round(computed["effects"][arm]["delta"], 4)
            for arm, layer in SINGLE_FACTOR_ARMS.items()
        },
        "reason_code": computed["reason_code"],
        "rationale": computed["rationale"],
        "evidence_bundle_digest": evidence_digest,
        "attribution_report_digest": report_digest,
    }


def validate_frozen_protocol(payload: dict[str, Any]) -> None:
    if payload.get("execution_profile") not in {"live", "isolated-replay"}:
        raise AttributionValidationError("execution_profile must be live or isolated-replay")
    required_digest_keys = {"P0", "P1", "K0", "K1", "M0", "M1"}
    versions = payload.get("versions")
    if not isinstance(versions, dict) or set(versions) != required_digest_keys:
        raise AttributionValidationError("versions must contain exactly P0/P1/K0/K1/M0/M1")
    if any(not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None for value in versions.values()):
        raise AttributionValidationError("all frozen component versions must be sha256 digests")
    cell_versionsets = payload.get("cell_versionsets")
    if not isinstance(cell_versionsets, dict) or set(cell_versionsets) != set(CELLS):
        raise AttributionValidationError("cell_versionsets must bind exactly C/RP/RK/RM/G")
    for cell_name, ref in cell_versionsets.items():
        if not isinstance(ref, dict):
            raise AttributionValidationError(f"cell_versionsets.{cell_name} must be an object")
        if (
            not isinstance(ref.get("versionset_id"), str)
            or not (
                ref["versionset_id"].startswith("vs_")
                or ref["versionset_id"].startswith("vset_")
            )
        ):
            raise AttributionValidationError(f"cell_versionsets.{cell_name}.versionset_id is invalid")
        if not isinstance(ref.get("digest"), str) or DIGEST_RE.fullmatch(ref["digest"]) is None:
            raise AttributionValidationError(f"cell_versionsets.{cell_name}.digest is invalid")
        revision = ref.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise AttributionValidationError(f"cell_versionsets.{cell_name}.revision is invalid")
    probe_digest = payload.get("probe_set_digest")
    if not isinstance(probe_digest, str) or DIGEST_RE.fullmatch(probe_digest) is None:
        raise AttributionValidationError("probe_set_digest must be a sha256 digest")
    probe_oracle = _probe_contract(probe_digest)
    selected = [
        probe_id
        for key in ("discovery", "hidden_confirmation", "unaffected_controls")
        for probe_id in (payload.get(key) or [])
    ]
    if not selected or len(selected) != len(set(selected)) or any(
        probe_id not in probe_oracle for probe_id in selected
    ):
        raise AttributionValidationError(
            "frozen probe groups must be non-empty, disjoint, and owned by the frozen probe contract"
        )
    if payload.get("confidence") != 0.95:
        raise AttributionValidationError("confidence must be 0.95")
