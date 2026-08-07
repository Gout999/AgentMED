"""Experiment（归因对照实验）状态机服务（contracts/events/state-machines.yaml#experiment）。

Runner 也是领单 Worker；cell 结果按 (cell, probe, repetition) 幂等键去重（由调用方保证）。
CONFOUNDED → 强制 2³ 全因子（escalated_full_factorial 回 PROTOCOL_FROZEN）。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_experiment_id, new_trace_id

VALID_CELLS = ("C", "RP", "RK", "RM", "G")
VALID_VERDICTS = ("ATTRIBUTED", "INCONCLUSIVE", "CONFOUNDED")


class ExperimentServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class ExperimentService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.audit = AuditService(session, self.settings)

    def create(
        self,
        *,
        case_id: str,
        hypothesis_layer: Optional[str] = None,
        protocol_version: str = "five_cell-v1",
    ) -> dict[str, Any]:
        eid = new_experiment_id()
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=eid,
            event_type="experiment.requested",
            payload={
                "case_id": case_id,
                "hypothesis_layer": hypothesis_layer,
                "protocol_version": protocol_version,
            },
            causation_id="case.opened",
            correlation_id=case_id,
            actor="controller:experiment",
            machine="experiment",
            merge_payload={"case_id": case_id, "hypothesis_layer": hypothesis_layer},
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
        probe_set_digest: str,
        discovery: list[str],
        hidden_confirmation: list[str],
        unaffected_controls: list[str],
        repetitions: int,
        versions: dict[str, str],
        random_seed_ref: str,
    ) -> dict[str, Any]:
        agg = self._require(experiment_id)
        payload = {
            "probe_set_digest": probe_set_digest,
            "discovery": discovery,
            "hidden_confirmation": hidden_confirmation,
            "unaffected_controls": unaffected_controls,
            "repetitions": repetitions,
            "versions": versions,
            "random_seed_ref": random_seed_ref,
        }
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
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.started",
            payload={"runner_id": runner_id, "lease_id": lease_id, "fencing_token": fencing_token},
            causation_id="experiment.protocol_frozen",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor=runner_id,
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={"runner_id": runner_id, "fencing_token": fencing_token},
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
        fencing_token: Optional[int] = None,
    ) -> dict[str, Any]:
        if cell not in VALID_CELLS:
            raise ExperimentServiceError("validation_failed", f"cell must be one of {VALID_CELLS}")
        agg = self._require(experiment_id)
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.cell_completed",
            payload={"cell": cell, "arm_order_index": arm_order_index, "recovery_rate": recovery_rate},
            causation_id="experiment.started",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor="runner",
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={"cell_progress": cell},
        )
        self.audit.record(
            actor="runner",
            action="experiment.cell_completed",
            target=experiment_id,
            params={"cell": cell, "recovery_rate": recovery_rate},
            result="success",
        )
        return self._view(self._require(experiment_id))

    def verdict_computed(
        self,
        experiment_id: str,
        *,
        verdict: str,
        deltas: dict[str, float],
        evidence_bundle_ref: str,
        report_ref: str,
        attributed_layer: Optional[str] = None,
    ) -> dict[str, Any]:
        if verdict not in VALID_VERDICTS:
            raise ExperimentServiceError("validation_failed", f"verdict must be one of {VALID_VERDICTS}")
        agg = self._require(experiment_id)
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.verdict_computed",
            payload={
                "verdict": verdict,
                "attributed_layer": attributed_layer,
                "deltas": deltas,
                "evidence_bundle_ref": evidence_bundle_ref,
                "report_ref": report_ref,
            },
            causation_id="experiment.cell_completed",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor="controller:experiment",
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={"verdict": verdict, "report_ref": report_ref},
        )
        self.audit.record(
            actor="controller:experiment",
            action="experiment.verdict_computed",
            target=experiment_id,
            params={"verdict": verdict},
            result="success",
        )
        return self._view(self._require(experiment_id))

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

    def cancel(self, experiment_id: str, *, reason: str) -> dict[str, Any]:
        agg = self._require(experiment_id)
        self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.cancelled",
            payload={"reason": reason},
            causation_id="manual",
            correlation_id=(agg.payload or {}).get("case_id") or experiment_id,
            actor="controller:experiment",
            expected_revision=agg.revision,
            machine="experiment",
            merge_payload={"cancelled": True, "reason": reason},
        )
        self.audit.record(
            actor="controller:experiment",
            action="experiment.cancelled",
            target=experiment_id,
            params={"reason": reason},
            result="success",
        )
        return self._view(self._require(experiment_id))

    def get(self, experiment_id: str) -> dict[str, Any]:
        return self._view(self._require(experiment_id))

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
