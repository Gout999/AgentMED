"""Experiment（归因对照实验）状态机服务（contracts/events/state-machines.yaml#experiment）。

Runner 也是领单 Worker；控制面验证 lease、冻结版本、cell 唯一性与完整归因产物。
CONFOUNDED → 强制 2³ 全因子（escalated_full_factorial 回 PROTOCOL_FROZEN）。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate, Lease
from app.quality.client import QualityAPIError, QualityClientProtocol
from app.services.attribution import (
    AttributionValidationError,
    CELL_COMPONENTS,
    validate_attribution_artifacts,
    validate_attribution_trial,
    validate_frozen_protocol,
)
from app.services.audit import AuditService
from app.services.event_store import EventStore
from app.services.lease import LeaseLost, LeaseService
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_experiment_id

VALID_CELLS = ("C", "RP", "RK", "RM", "G")
TRIAL_EVENT_TYPE = "experiment.trial_completed"


class ExperimentServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class ExperimentService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        quality: QualityClientProtocol | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.audit = AuditService(session, self.settings)
        self.leases = LeaseService(session, self.settings)
        self.quality = quality

    def create(
        self,
        *,
        case_id: str,
        hypothesis_layer: Optional[str] = None,
        protocol_version: str = "five_cell-v1",
    ) -> dict[str, Any]:
        if protocol_version != "five_cell-v1":
            raise ExperimentServiceError(
                "validation_failed",
                "Phase 1 accepts only the frozen five_cell-v1 protocol",
            )
        case = self.store.get_aggregate("case", case_id)
        if case is None:
            raise ExperimentServiceError("not_found", f"case {case_id} not found")
        if case.state != "DISPATCHED":
            raise ExperimentServiceError(
                "illegal_transition",
                f"experiment requires a DISPATCHED case; got {case.state}",
            )
        eid = new_experiment_id()
        requested = self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=eid,
            event_type="experiment.requested",
            payload={
                "case_id": case_id,
                "hypothesis_layer": hypothesis_layer,
                "protocol_version": protocol_version,
            },
            causation_id=self.store.list_events(case_id)[-1].event_id,
            correlation_id=case_id,
            actor="controller:experiment",
            machine="experiment",
            merge_payload={"case_id": case_id, "hypothesis_layer": hypothesis_layer},
        )
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="experiment.requested",
            payload={"experiment_id": eid, "protocol_version": protocol_version},
            causation_id=requested.event_id,
            correlation_id=case_id,
            actor="controller:experiment",
            expected_revision=case.revision,
            machine="case",
            merge_payload={"experiment_id": eid},
        )
        self.audit.record(
            actor="controller:experiment",
            action="experiment.requested",
            target=eid,
            params={"case_id": case_id, "protocol_version": protocol_version},
            result="success",
        )
        agg = self.store.get_aggregate("experiment", eid)
        return {"experiment_id": eid, "state": agg.state if agg else "REQUESTED", "revision": agg.revision if agg else 1}

    def freeze_protocol(
        self,
        experiment_id: str,
        *,
        execution_profile: str = "live",
        probe_set_digest: str,
        discovery: list[str],
        hidden_confirmation: list[str],
        unaffected_controls: list[str],
        repetitions: int,
        versions: dict[str, str],
        cell_versionsets: dict[str, dict[str, Any]],
        random_seed_ref: str,
        confidence: float = 0.95,
    ) -> dict[str, Any]:
        # S0-006 纵深防御：空探针集不得冻结（空实验静默冻结曾让归因师空轮询）。
        probe_sets = {
            "discovery": discovery,
            "hidden_confirmation": hidden_confirmation,
            "unaffected_controls": unaffected_controls,
        }
        for probe_key, probe_ids in probe_sets.items():
            if not probe_ids:
                raise ExperimentServiceError(
                    "validation_error",
                    f"{probe_key} 不能为空：冻结协议必须含非空探针集（发现/隐证/对照三分集齐全）",
                )
        payload = {
            "execution_profile": execution_profile,
            "probe_set_digest": probe_set_digest,
            "discovery": discovery,
            "hidden_confirmation": hidden_confirmation,
            "unaffected_controls": unaffected_controls,
            "repetitions": repetitions,
            "versions": versions,
            "cell_versionsets": cell_versionsets,
            "random_seed_ref": random_seed_ref,
            "confidence": confidence,
        }
        try:
            validate_frozen_protocol(payload)
        except AttributionValidationError as exc:
            raise ExperimentServiceError("validation_error", str(exc)) from exc
        self._validate_execution_profile(payload)
        self._verify_frozen_versionsets(payload)
        agg = self._require(experiment_id)
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.protocol_frozen",
            payload=payload,
            causation_id="experiment.requested",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor="controller:experiment",
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload=payload,
        )
        self.audit.record(
            actor="controller:experiment",
            action="experiment.protocol_frozen",
            target=experiment_id,
            params={"probe_set_digest": probe_set_digest, "repetitions": repetitions},
            result="success",
        )
        return self._view(self._require(experiment_id))

    def start(self, experiment_id: str, *, runner_id: str, lease_id: str, fencing_token: int) -> dict[str, Any]:
        agg = self._require(experiment_id)
        case_id = (agg.payload or {}).get("case_id")
        try:
            lease = self.leases.check_fencing(case_id, fencing_token)
        except LeaseLost as exc:
            raise ExperimentServiceError("lease_lost", str(exc)) from exc
        if lease.lease_id != lease_id or lease.owner_id != runner_id:
            raise ExperimentServiceError(
                "lease_lost",
                "experiment runner does not own the exact active Case lease",
            )
        case = self.store.get_aggregate_for_update("case", case_id)
        agg = self.store.get_aggregate_for_update("experiment", experiment_id)
        if agg is None or (agg.payload or {}).get("case_id") != case_id:
            raise ExperimentServiceError(
                "hash_mismatch", "experiment-to-Case binding changed while acquiring its lease"
            )
        if (
            case is None
            or case.state not in {"ATTRIBUTING", "DISPATCHED"}
            or (case.payload or {}).get("experiment_id") != experiment_id
            or (case.payload or {}).get("worker_id") != runner_id
            or (case.payload or {}).get("lease_id") != lease_id
            or int((case.payload or {}).get("fencing_token") or 0)
            != int(fencing_token)
        ):
            raise ExperimentServiceError(
                "lease_lost", "bound Case is not owned by the starting experiment runner"
            )
        started = self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.started",
            payload={"runner_id": runner_id, "lease_id": lease_id, "fencing_token": fencing_token},
            causation_id="experiment.protocol_frozen",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor=runner_id,
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={
                "runner_id": runner_id,
                "lease_id": lease_id,
                "fencing_token": fencing_token,
            },
        )
        if case.state == "DISPATCHED":
            self.store.append_event(
                aggregate_type="case",
                aggregate_id=case_id,
                event_type="experiment.resumed",
                payload={
                    "experiment_id": experiment_id,
                    "runner_id": runner_id,
                    "lease_id": lease_id,
                    "fencing_token": fencing_token,
                },
                causation_id=started.event_id,
                correlation_id=case_id,
                actor="controller:experiment",
                expected_revision=case.revision,
                machine="case",
            )
        self.audit.record(
            actor=runner_id,
            action="experiment.started",
            target=experiment_id,
            params={"fencing_token": fencing_token},
            result="success",
        )
        return self._view(self._require(experiment_id))

    def cell_completed(
        self,
        experiment_id: str,
        *,
        cell: str,
        arm_order_index: int,
        recovery_rate: float,
        fencing_token: int,
    ) -> dict[str, Any]:
        if cell not in VALID_CELLS:
            raise ExperimentServiceError("validation_failed", f"cell must be one of {VALID_CELLS}")
        if isinstance(arm_order_index, bool) or not isinstance(arm_order_index, int) or not 0 <= arm_order_index < 5:
            raise ExperimentServiceError("validation_failed", "arm_order_index must be an integer from 0 to 4")
        if isinstance(recovery_rate, bool) or not isinstance(recovery_rate, (int, float)) or not 0 <= recovery_rate <= 1:
            raise ExperimentServiceError("validation_failed", "recovery_rate must be between 0 and 1")
        agg = self._require(experiment_id)
        case_id = (agg.payload or {}).get("case_id")
        try:
            lease = self.leases.check_fencing(case_id, int(fencing_token))
        except (LeaseLost, TypeError, ValueError) as exc:
            raise ExperimentServiceError("lease_lost", str(exc)) from exc
        agg = self.store.get_aggregate_for_update("experiment", experiment_id)
        if agg is None or (agg.payload or {}).get("case_id") != case_id:
            raise ExperimentServiceError(
                "hash_mismatch", "experiment-to-Case binding changed while acquiring its lease"
            )
        if lease.owner_id != (agg.payload or {}).get("runner_id"):
            raise ExperimentServiceError("lease_lost", "experiment runner lease owner changed")
        trials = [
            event.payload
            for event in self.store.list_events(experiment_id)
            if event.event_type == TRIAL_EVENT_TYPE and event.payload.get("cell") == cell
        ]
        frozen = agg.payload or {}
        probe_ids = [
            *list(frozen.get("discovery") or []),
            *list(frozen.get("hidden_confirmation") or []),
            *list(frozen.get("unaffected_controls") or []),
        ]
        repetitions = int(frozen.get("repetitions") or 0)
        expected_trial_keys = {
            (probe_id, repetition)
            for probe_id in probe_ids
            for repetition in range(1, repetitions + 1)
        }
        actual_trial_keys = {
            (item.get("probe_id"), item.get("repetition")) for item in trials
        }
        if len(trials) != len(expected_trial_keys) or actual_trial_keys != expected_trial_keys:
            missing = expected_trial_keys - actual_trial_keys
            example = sorted(missing)[0] if missing else None
            raise ExperimentServiceError(
                "incomplete_experiment",
                f"cell {cell} requires every immutable trial receipt before completion"
                + (f"; first missing={example!r}" if example else ""),
            )
        affected = set(
            list(frozen.get("discovery") or [])
            + list(frozen.get("hidden_confirmation") or [])
        )
        affected_trials = [item for item in trials if item.get("probe_id") in affected]
        computed_recovery_rate = (
            sum(1 for item in affected_trials if item.get("recovered") is True)
            / len(affected_trials)
        )
        if abs(float(recovery_rate) - round(computed_recovery_rate, 4)) > 0.0001:
            raise ExperimentServiceError(
                "hash_mismatch",
                f"cell {cell} recovery_rate does not match immutable trial receipts",
            )
        completed = [
            event
            for event in self.store.list_events(experiment_id)
            if event.event_type == "experiment.cell_completed"
        ]
        requested_result = {
            "cell": cell,
            "arm_order_index": arm_order_index,
            "recovery_rate": recovery_rate,
        }
        existing_cell = next(
            (event for event in completed if event.payload.get("cell") == cell),
            None,
        )
        if existing_cell is not None:
            if existing_cell.payload == requested_result:
                return {**self._view(agg), "duplicate": True}
            raise ExperimentServiceError("idempotency_conflict", f"cell {cell} is already complete")
        if any(event.payload.get("arm_order_index") == arm_order_index for event in completed):
            raise ExperimentServiceError(
                "idempotency_conflict",
                f"arm_order_index {arm_order_index} is already assigned",
            )
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.cell_completed",
            payload=requested_result,
            causation_id="experiment.started",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor="runner",
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={"completed_cells": [*(agg.payload or {}).get("completed_cells", []), cell]},
        )
        self.audit.record(
            actor="runner",
            action="experiment.cell_completed",
            target=experiment_id,
            params={"cell": cell, "recovery_rate": recovery_rate},
            result="success",
        )
        return {**self._view(self._require(experiment_id)), "duplicate": False}

    def trial_completed(
        self,
        experiment_id: str,
        *,
        cell: str,
        probe_id: str,
        repetition: int,
        recovered: bool,
        output_ref: str,
        output_digest: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        """Checkpoint one provider trial under the exact active runner lease.

        The event is the resume authority.  A replacement runner can reuse an
        exact receipt after obtaining a new lease, but cannot replace it with a
        different answer, provider request, or recovery decision.
        """

        if cell not in VALID_CELLS:
            raise ExperimentServiceError("validation_failed", f"cell must be one of {VALID_CELLS}")
        if not isinstance(probe_id, str) or not probe_id:
            raise ExperimentServiceError("validation_failed", "probe_id is required")
        if isinstance(repetition, bool) or not isinstance(repetition, int):
            raise ExperimentServiceError("validation_failed", "repetition must be an integer")
        if not isinstance(recovered, bool):
            raise ExperimentServiceError("validation_failed", "recovered must be boolean")
        requested = {
            "cell": cell,
            "probe_id": probe_id,
            "repetition": repetition,
            "recovered": recovered,
            "output_ref": output_ref,
            "output_digest": output_digest,
        }
        initial = self._require(experiment_id)
        case_id = (initial.payload or {}).get("case_id")
        try:
            lease = self.leases.check_fencing(case_id, int(fencing_token))
        except (LeaseLost, TypeError, ValueError) as exc:
            raise ExperimentServiceError("lease_lost", str(exc)) from exc
        agg = self.store.get_aggregate_for_update("experiment", experiment_id)
        if agg is None or (agg.payload or {}).get("case_id") != case_id:
            raise ExperimentServiceError(
                "hash_mismatch", "experiment-to-Case binding changed while acquiring its lease"
            )
        runner_id = (agg.payload or {}).get("runner_id")
        if lease.owner_id != runner_id:
            raise ExperimentServiceError("lease_lost", "experiment runner lease owner changed")
        events = [
            event
            for event in self.store.list_events(experiment_id)
            if event.event_type == TRIAL_EVENT_TYPE
        ]
        existing = next(
            (
                event
                for event in events
                if event.payload.get("cell") == cell
                and event.payload.get("probe_id") == probe_id
                and event.payload.get("repetition") == repetition
            ),
            None,
        )
        if existing is not None:
            if all(existing.payload.get(key) == value for key, value in requested.items()):
                return {
                    "experiment_id": experiment_id,
                    "state": agg.state,
                    "revision": agg.revision,
                    "trial": requested,
                    "duplicate": True,
                }
            raise ExperimentServiceError(
                "idempotency_conflict",
                f"trial {(cell, probe_id, repetition)!r} is already complete",
            )
        frozen = agg.payload or {}
        self._validate_execution_profile(frozen)
        try:
            validated = validate_attribution_trial(
                experiment_id=experiment_id,
                case_id=case_id,
                frozen=frozen,
                cell_name=cell,
                trial=requested,
                provider_log_resolver=(
                    self.quality.get_log
                    if frozen.get("execution_profile") == "live" and self.quality is not None
                    else None
                ),
            )
        except AttributionValidationError as exc:
            raise ExperimentServiceError("validation_failed", str(exc)) from exc
        raw = validated["artifact"]
        request_id = raw.get("request_id")
        trace_id = raw.get("trace_id")
        if frozen.get("execution_profile") == "live":
            if any(event.payload.get("request_id") == request_id for event in events):
                raise ExperimentServiceError(
                    "idempotency_conflict", "live provider request_id was reused across trials"
                )
            if any(event.payload.get("trace_id") == trace_id for event in events):
                raise ExperimentServiceError(
                    "idempotency_conflict", "live provider trace_id was reused across trials"
                )
        event_payload = {
            **requested,
            "request_id": request_id,
            "trace_id": trace_id,
        }
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type=TRIAL_EVENT_TYPE,
            payload=event_payload,
            causation_id="experiment.started",
            correlation_id=case_id or experiment_id,
            actor=str(runner_id or "runner"),
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={
                "completed_trial_count": int(frozen.get("completed_trial_count") or 0) + 1,
            },
        )
        self.audit.record(
            actor=str(runner_id or "runner"),
            action=TRIAL_EVENT_TYPE,
            target=experiment_id,
            params={
                "cell": cell,
                "probe_id": probe_id,
                "repetition": repetition,
                "output_digest": output_digest,
                "request_id": request_id,
            },
            result="success",
        )
        current = self._require(experiment_id)
        return {
            "experiment_id": experiment_id,
            "state": current.state,
            "revision": current.revision,
            "trial": event_payload,
            "duplicate": False,
        }

    def verdict_computed(
        self,
        experiment_id: str,
        *,
        fencing_token: int,
        evidence_bundle: dict[str, Any],
        attribution_report: dict[str, Any],
    ) -> dict[str, Any]:
        agg = self._require(experiment_id)
        case_id = (agg.payload or {}).get("case_id")
        try:
            lease = self.leases.check_fencing(case_id, int(fencing_token))
        except (LeaseLost, TypeError, ValueError) as exc:
            raise ExperimentServiceError("lease_lost", str(exc)) from exc
        agg = self.store.get_aggregate_for_update("experiment", experiment_id)
        if agg is None or (agg.payload or {}).get("case_id") != case_id:
            raise ExperimentServiceError(
                "hash_mismatch", "experiment-to-Case binding changed while acquiring its lease"
            )
        if lease.owner_id != (agg.payload or {}).get("runner_id"):
            raise ExperimentServiceError("lease_lost", "experiment runner lease owner changed")
        completed = [
            event.payload
            for event in self.store.list_events(experiment_id)
            if event.event_type == "experiment.cell_completed"
        ]
        if len(completed) != len(VALID_CELLS) or {item.get("cell") for item in completed} != set(VALID_CELLS):
            raise ExperimentServiceError(
                "incomplete_experiment",
                "all five unique cell completion records are required before adjudication",
            )
        registered_trials = [
            event.payload
            for event in self.store.list_events(experiment_id)
            if event.event_type == TRIAL_EVENT_TYPE
        ]
        registered_by_key = {
            (item.get("cell"), item.get("probe_id"), item.get("repetition")): item
            for item in registered_trials
        }
        bundle_by_key: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        for cell_name in VALID_CELLS:
            for trial in (evidence_bundle.get("cells") or {}).get(cell_name, {}).get("results") or []:
                key = (cell_name, trial.get("probe_id"), trial.get("repetition"))
                bundle_by_key[key] = trial
        if set(registered_by_key) != set(bundle_by_key):
            raise ExperimentServiceError(
                "hash_mismatch", "evidence bundle trial set differs from immutable trial receipts"
            )
        bound_fields = ("probe_id", "repetition", "recovered", "output_ref", "output_digest")
        for key, registered in registered_by_key.items():
            supplied = bundle_by_key[key]
            if any(registered.get(field) != supplied.get(field) for field in bound_fields):
                raise ExperimentServiceError(
                    "hash_mismatch", f"evidence bundle trial {key!r} differs from its immutable receipt"
                )
        frozen = agg.payload or {}
        case_id = frozen.get("case_id")
        self._validate_execution_profile(frozen)
        self._verify_frozen_versionsets(frozen)
        try:
            computed = validate_attribution_artifacts(
                experiment_id=experiment_id,
                case_id=case_id,
                frozen=frozen,
                evidence_bundle=evidence_bundle,
                attribution_report=attribution_report,
                delta_min=self.settings.attribution_delta_min,
                provider_log_resolver=(
                    self.quality.get_log
                    if frozen.get("execution_profile") == "live" and self.quality is not None
                    else None
                ),
            )
        except AttributionValidationError as exc:
            raise ExperimentServiceError("validation_failed", str(exc)) from exc
        for item in completed:
            bundle_rate = evidence_bundle["cells"][item["cell"]]["recovery_rate"]
            if abs(float(item["recovery_rate"]) - float(bundle_rate)) > 0.0001:
                raise ExperimentServiceError(
                    "hash_mismatch",
                    f"cell {item['cell']} summary does not match immutable evidence",
                )
        evidence_bundle_ref = f"evidence://{computed['evidence_bundle_digest']}"
        report_ref = f"attribution://{computed['attribution_report_digest']}"
        event = self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.verdict_computed",
            payload={
                **computed,
                "evidence_bundle_ref": evidence_bundle_ref,
                "report_ref": report_ref,
                "evidence_bundle": evidence_bundle,
                "attribution_report": attribution_report,
            },
            causation_id="experiment.cell_completed",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor="controller:experiment",
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={
                "verdict": computed["verdict"],
                "attributed_layer": computed["attributed_layer"],
                "deltas": computed["deltas"],
                "evidence_bundle_digest": computed["evidence_bundle_digest"],
                "attribution_report_digest": computed["attribution_report_digest"],
                "evidence_bundle_ref": evidence_bundle_ref,
                "report_ref": report_ref,
            },
        )
        case = self.store.get_aggregate("case", case_id)
        if case is None or case.state != "ATTRIBUTING":
            raise ExperimentServiceError(
                "illegal_transition",
                f"bound Case must be ATTRIBUTING; got {case.state if case else 'missing'}",
            )
        if computed["verdict"] == "ATTRIBUTED":
            self.store.append_event(
                aggregate_type="case",
                aggregate_id=case_id,
                event_type="case.attribution_completed",
                payload={
                    "experiment_id": experiment_id,
                    "verdict": computed["verdict"],
                    "attributed_layer": computed["attributed_layer"],
                    "attribution_report_digest": computed["attribution_report_digest"],
                    "evidence_bundle_digest": computed["evidence_bundle_digest"],
                },
                causation_id=event.event_id,
                correlation_id=case_id,
                actor="controller:experiment",
                expected_revision=case.revision,
                machine="case",
                guard="verdict=ATTRIBUTED",
                merge_payload={
                    "attribution_verdict": computed["verdict"],
                    "fault_layer": computed["attributed_layer"],
                    "experiment_id": experiment_id,
                    "attribution_report_digest": computed["attribution_report_digest"],
                },
            )
        else:
            self.store.append_event(
                aggregate_type="case",
                aggregate_id=case_id,
                event_type="case.escalated",
                payload={
                    "experiment_id": experiment_id,
                    "verdict": computed["verdict"],
                    "reason_code": computed["reason_code"],
                },
                causation_id=event.event_id,
                correlation_id=case_id,
                actor="controller:experiment",
                expected_revision=case.revision,
                machine="case",
                merge_payload={"attribution_verdict": computed["verdict"]},
            )
        self.audit.record(
            actor="controller:experiment",
            action="experiment.verdict_computed",
            target=experiment_id,
            params={
                "verdict": computed["verdict"],
                "attributed_layer": computed["attributed_layer"],
                "evidence_bundle_digest": computed["evidence_bundle_digest"],
                "attribution_report_digest": computed["attribution_report_digest"],
            },
            result="success",
        )
        # The attribution runner no longer owns the Case after the authoritative
        # verdict.  Release the exact owner/token tuple in this transaction so
        # AWAITING_FIX can be claimed by a repairer without sharing a stale
        # evaluator lease.  Any mismatch fails the verdict transaction closed.
        try:
            self.leases.release(case_id, lease.owner_id, int(fencing_token))
        except LeaseLost as exc:  # pragma: no cover - row is locked above
            raise ExperimentServiceError("lease_lost", str(exc)) from exc
        self.audit.record(
            actor="controller:experiment",
            action="case.lease_released_after_attribution",
            target=case_id,
            params={
                "experiment_id": experiment_id,
                "owner_id": lease.owner_id,
                "fencing_token": int(fencing_token),
            },
            result="success",
        )
        return self._view(self._require(experiment_id))

    def _verify_frozen_versionsets(self, frozen: dict[str, Any]) -> None:
        """Bind every frozen cell to a real immutable Quality VersionSet."""

        if self.quality is None:
            raise ExperimentServiceError(
                "quality_api_error",
                "Quality read client is required to verify frozen VersionSets",
            )
        versions = frozen.get("versions") or {}
        refs = frozen.get("cell_versionsets") or {}
        cache: dict[str, dict[str, Any]] = {}
        for cell_name in VALID_CELLS:
            ref = refs.get(cell_name) or {}
            versionset_id = ref.get("versionset_id")
            try:
                remote = cache.get(versionset_id)
                if remote is None:
                    remote = self.quality.get_versionset(versionset_id)
                    cache[versionset_id] = remote
            except QualityAPIError as exc:
                raise ExperimentServiceError(
                    "quality_api_error",
                    f"cannot verify frozen VersionSet for {cell_name}: {exc}",
                ) from exc
            except Exception as exc:  # noqa: BLE001 - dependency failure is fail-closed
                raise ExperimentServiceError(
                    "quality_api_error",
                    f"cannot verify frozen VersionSet for {cell_name}: {exc}",
                ) from exc
            if (
                remote.get("versionset_id") != versionset_id
                or remote.get("digest") != ref.get("digest")
                or remote.get("revision") != ref.get("revision")
            ):
                raise ExperimentServiceError(
                    "hash_mismatch",
                    f"frozen VersionSet identity drifted for cell {cell_name}",
                )
            content = remote.get("content")
            if not isinstance(content, dict) or set(content) != {"prompt", "kb_manifest", "model"}:
                raise ExperimentServiceError(
                    "hash_mismatch",
                    f"frozen VersionSet content is incomplete for cell {cell_name}",
                )
            actual_components = {
                "prompt_digest": (content.get("prompt") or {}).get("digest"),
                "kb_manifest_digest": (content.get("kb_manifest") or {}).get("manifest_digest"),
                "model_digest": (content.get("model") or {}).get("digest"),
            }
            expected_components = {
                field: versions[version_key]
                for field, version_key in CELL_COMPONENTS[cell_name].items()
            }
            if actual_components != expected_components:
                raise ExperimentServiceError(
                    "hash_mismatch",
                    f"frozen VersionSet components do not match cell {cell_name}",
                )

    def _validate_execution_profile(self, frozen: dict[str, Any]) -> None:
        profile = frozen.get("execution_profile")
        if profile == "live":
            return
        if profile != "isolated-replay":
            raise ExperimentServiceError(
                "validation_error",
                "execution_profile must be live or isolated-replay",
            )
        dialect = self.session.get_bind().dialect.name
        if not self.settings.allow_isolated_replay_attribution or dialect != "sqlite":
            raise ExperimentServiceError(
                "validation_error",
                "isolated-replay attribution requires an explicit allow flag and SQLite",
            )

    def escalate_full_factorial(self, experiment_id: str, *, reason: str) -> dict[str, Any]:
        agg = self._require(experiment_id)
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.escalated_full_factorial",
            payload={"reason": reason, "from_verdict": "CONFOUNDED"},
            causation_id="experiment.verdict_computed",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor="controller:experiment",
            expected_revision=agg.revision,
            machine="experiment",
            guard="verdict=CONFOUNDED",
            merge_payload={"full_factorial": True, "reason": reason},
        )
        self.audit.record(
            actor="controller:experiment",
            action="experiment.escalated_full_factorial",
            target=experiment_id,
            params={"reason": reason},
            result="success",
        )
        return self._view(self._require(experiment_id))

    def cancel(
        self,
        experiment_id: str,
        *,
        reason: str,
        runner_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise ExperimentServiceError("validation_failed", "cancel reason is required")
        initial = self._require(experiment_id)
        case_id = (initial.payload or {}).get("case_id")
        if initial.state == "CANCELLED":
            return self._cancelled_replay(
                initial,
                reason=reason,
                runner_id=runner_id,
                lease_id=lease_id,
                fencing_token=fencing_token,
            )
        try:
            lease = self.leases.check_fencing(case_id, fencing_token)
        except LeaseLost as exc:
            # A concurrent exact cancel may have deleted the lease while this
            # request waited. Only the persisted exact tuple is idempotent.
            refreshed = self.store.get_aggregate_for_update("experiment", experiment_id)
            if refreshed is not None and refreshed.state == "CANCELLED":
                return self._cancelled_replay(
                    refreshed,
                    reason=reason,
                    runner_id=runner_id,
                    lease_id=lease_id,
                    fencing_token=fencing_token,
                )
            raise ExperimentServiceError("lease_lost", str(exc)) from exc
        if lease.lease_id != lease_id or lease.owner_id != runner_id:
            raise ExperimentServiceError(
                "lease_lost", "experiment cancel does not own the exact active Case lease"
            )
        case = self.store.get_aggregate_for_update("case", case_id)
        agg = self.store.get_aggregate_for_update("experiment", experiment_id)
        if agg is None:
            raise ExperimentServiceError("not_found", f"experiment {experiment_id} not found")
        payload = agg.payload or {}
        if (
            payload.get("case_id") != case_id
            or payload.get("runner_id") != runner_id
            or payload.get("lease_id") != lease_id
            or int(payload.get("fencing_token") or 0) != int(fencing_token)
        ):
            raise ExperimentServiceError(
                "lease_lost", "experiment runner binding changed before cancel"
            )
        if agg.state == "VERDICT_COMPUTED":
            raise ExperimentServiceError(
                "illegal_transition", "terminal verdict cannot be cancelled"
            )
        if agg.state == "CANCELLED":
            return self._cancelled_replay(
                agg,
                reason=reason,
                runner_id=runner_id,
                lease_id=lease_id,
                fencing_token=fencing_token,
            )
        if (
            case is None
            or case.state != "ATTRIBUTING"
            or (case.payload or {}).get("experiment_id") != experiment_id
            or (case.payload or {}).get("worker_id") != runner_id
            or (case.payload or {}).get("lease_id") != lease_id
            or int((case.payload or {}).get("fencing_token") or 0)
            != int(fencing_token)
        ):
            raise ExperimentServiceError(
                "lease_lost", "bound Case changed before experiment cancellation"
            )
        cancelled = self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.cancelled",
            payload={
                "reason": reason,
                "runner_id": runner_id,
                "lease_id": lease_id,
                "fencing_token": fencing_token,
            },
            causation_id="experiment.runner_failed",
            correlation_id=case_id or experiment_id,
            actor=runner_id,
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={
                "cancelled": True,
                "reason": reason,
                "cancelled_by_runner": runner_id,
                "cancelled_lease_id": lease_id,
                "cancelled_fencing_token": fencing_token,
            },
        )
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="case.escalated",
            payload={
                "reason": reason,
                "from_state": "ATTRIBUTING",
                "experiment_id": experiment_id,
                "runner_id": runner_id,
                "lease_id": lease_id,
                "fencing_token": fencing_token,
            },
            causation_id=cancelled.event_id,
            correlation_id=case_id,
            actor="controller:experiment",
            expected_revision=case.revision,
            machine="case",
            merge_payload={
                "escalation_reason": reason,
                "escalated_experiment_id": experiment_id,
                "worker_id": None,
                "lease_id": None,
                "fencing_token": None,
            },
        )
        self.session.delete(lease)
        self.audit.record(
            actor=runner_id,
            action="experiment.cancelled",
            target=experiment_id,
            params={"reason": reason, "lease_id": lease_id, "fencing_token": fencing_token},
            result="success",
        )
        self.audit.record(
            actor="controller:experiment",
            action="case.escalated_after_experiment_cancel",
            target=case_id,
            params={
                "experiment_id": experiment_id,
                "runner_id": runner_id,
                "lease_id": lease_id,
                "fencing_token": fencing_token,
            },
            result="success",
        )
        self.session.flush()
        return {**self._view(self._require(experiment_id)), "duplicate": False}

    def _cancelled_replay(
        self,
        aggregate: Aggregate,
        *,
        reason: str,
        runner_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        payload = aggregate.payload or {}
        expected = {
            "reason": payload.get("reason"),
            "runner_id": payload.get("cancelled_by_runner"),
            "lease_id": payload.get("cancelled_lease_id"),
            "fencing_token": int(payload.get("cancelled_fencing_token") or 0),
        }
        supplied = {
            "reason": reason,
            "runner_id": runner_id,
            "lease_id": lease_id,
            "fencing_token": int(fencing_token),
        }
        if expected != supplied:
            raise ExperimentServiceError(
                "idempotency_conflict",
                "cancelled experiment is bound to a different runner request",
            )
        case_id = payload.get("case_id")
        case = self.store.get_aggregate("case", case_id)
        if (
            case is None
            or case.state != "ESCALATED"
            or (case.payload or {}).get("escalated_experiment_id")
            != aggregate.aggregate_id
            or self.session.get(Lease, case_id) is not None
        ):
            raise ExperimentServiceError(
                "illegal_transition",
                "cancelled experiment closure is incomplete; Case/lease state is UNKNOWN",
            )
        return {**self._view(aggregate), "duplicate": True}

    def get(self, experiment_id: str) -> dict[str, Any]:
        return self._view(self._require(experiment_id))

    def list_completed_trials(self, experiment_id: str) -> dict[str, Any]:
        self._require(experiment_id)
        items = [
            event.payload
            for event in self.store.list_events(experiment_id)
            if event.event_type == TRIAL_EVENT_TYPE
        ]
        return {"experiment_id": experiment_id, "items": items, "count": len(items)}

    def list_experiments(self, *, state: Optional[str] = None, limit: int = 100, cursor: int = 0) -> dict[str, Any]:
        q = (
            select(Aggregate)
            .where(Aggregate.aggregate_type == "experiment")
            .order_by(Aggregate.aggregate_id)
        )
        if state:
            q = q.where(Aggregate.state == state)
        rows = list(self.session.scalars(q.offset(cursor).limit(limit)).all())
        return {
            "items": [self._view(r) for r in rows],
            "next_cursor": cursor + len(rows) if len(rows) == limit else None,
        }

    def _require(self, experiment_id: str) -> Aggregate:
        agg = self.store.get_aggregate("experiment", experiment_id)
        if agg is None:
            raise ExperimentServiceError("not_found", f"experiment {experiment_id} not found")
        return agg

    @staticmethod
    def _view(agg: Aggregate) -> dict[str, Any]:
        return {
            "experiment_id": agg.aggregate_id,
            "state": agg.state,
            "revision": agg.revision,
            "payload": agg.payload,
        }
