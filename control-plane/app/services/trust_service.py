"""Authoritative Trust Ledger consumer for real control-plane outcomes.

The ledger is append-only at the sample level. One terminal release action is
one sample regardless of how many probes appear in its evidence. Delivery
retries are idempotent by both source event and action reference.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Event, TrustLedger, TrustLedgerEntry
from app.services.audit import AuditService
from app.services.event_store import EventStore
from app.utils.ids import new_trust_entry_id


RISK_CLASS = "R2_HIGH_IMPACT"
ACTION_TYPE = "release_outcome"
TRUST_KEY = f"{ACTION_TYPE}:{RISK_CLASS}"
PROMOTION_THRESHOLD = 0.9
Z_TWO_SIDED_95 = 1.96

_REPO_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "contracts" / "schemas").is_dir()
    ),
    Path(__file__).resolve().parents[3],
)
_ENTRY_SCHEMA = json.loads(
    (_REPO_ROOT / "contracts" / "schemas" / "trust-ledger-entry.schema.json").read_text(
        encoding="utf-8"
    )
)
_ENTRY_VALIDATOR = Draft202012Validator(_ENTRY_SCHEMA, format_checker=FormatChecker())


class TrustServiceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("invalid Wilson counts")
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    z2 = Z_TWO_SIDED_95 * Z_TWO_SIDED_95
    denominator = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denominator
    margin = (Z_TWO_SIDED_95 / denominator) * (
        p * (1 - p) / trials + z2 / (4 * trials * trials)
    ) ** 0.5
    return (max(0.0, center - margin), min(1.0, center + margin))


class TrustService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.audit = AuditService(session, self.settings)
        self.store = EventStore(session)

    def consume_release_event(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Consume a real terminal/UNKNOWN release domain event exactly once."""

        event_type = envelope.get("domain_event_type")
        if event_type not in {"RELEASE_PROMOTED", "RELEASE_ROLLED_BACK", "RELEASE_UNKNOWN"}:
            raise TrustServiceError("unsupported_event", f"unsupported Trust event {event_type}")
        source_event_id = envelope.get("source_event_id")
        action_ref = envelope.get("aggregate_id")
        if not isinstance(source_event_id, str) or not source_event_id:
            raise TrustServiceError("invalid_envelope", "Trust event has no source_event_id")
        if not isinstance(action_ref, str) or not action_ref:
            raise TrustServiceError("invalid_envelope", "Trust event has no release action_ref")

        if event_type == "RELEASE_UNKNOWN":
            return self.block_unknown(
                source_event_id=source_event_id,
                action_ref=action_ref,
                detail="release result is UNKNOWN and requires reconciliation",
            )
        return self.record_outcome(
            source_event_id=source_event_id,
            action_ref=action_ref,
            success=event_type == "RELEASE_PROMOTED",
            detail=(
                "release promoted with an operation-bound receipt"
                if event_type == "RELEASE_PROMOTED"
                else "release rolled back after verification failure"
            ),
        )

    def record_outcome(
        self,
        *,
        source_event_id: str,
        action_ref: str,
        success: bool,
        detail: str,
    ) -> dict[str, Any]:
        existing = self.session.scalar(
            select(TrustLedgerEntry).where(
                TrustLedgerEntry.risk_class == RISK_CLASS,
                TrustLedgerEntry.action_type == ACTION_TYPE,
                TrustLedgerEntry.action_ref == action_ref,
            )
        )
        if existing is not None:
            if existing.outcome != ("success" if success else "failure"):
                raise TrustServiceError(
                    "idempotency_conflict",
                    f"release action {action_ref} already has a different Trust outcome",
                )
            # Different probe/result events for the same release action are
            # deliberately coalesced: one action is one sample.
            return self._receipt(existing, duplicate=True)

        row = self._locked_row()
        if row.autonomy_state == "BLOCKED_UNKNOWN":
            raise TrustServiceError(
                "blocked_unknown",
                "Trust state is BLOCKED_UNKNOWN; deterministic outcome cannot silently clear it",
            )

        before = row.autonomy_state
        new_trials = int(row.trials) + 1
        new_successes = int(row.successes) + (1 if success else 0)
        lower, upper = wilson_interval(new_successes, new_trials)
        eligible_by_evidence = lower > PROMOTION_THRESHOLD
        # Release is R2. It remains MANUAL even if statistical evidence later
        # crosses the threshold; 3/3 is also denied by the lower-bound rule.
        promotion = {
            "eligible": False,
            "decision": "denied",
            "threshold": PROMOTION_THRESHOLD,
        }
        now = datetime.now(timezone.utc)
        entry = {
            "schema_version": "0.1.0",
            "entry_id": new_trust_entry_id(),
            "trust_key": TRUST_KEY,
            "risk_class": RISK_CLASS,
            "action_type": ACTION_TYPE,
            "outcome": {
                "status": "success" if success else "failure",
                "action_ref": action_ref,
                "detail": detail,
            },
            "sample_rule": "one_action_one_sample",
            "evidence_epoch": int(row.epoch),
            "epoch_successes": new_successes,
            "epoch_trials": new_trials,
            "wilson": {
                "confidence": 0.95,
                "side": "two-sided",
                "z": Z_TWO_SIDED_95,
                "lower": lower,
                "upper": upper,
            },
            "autonomy_state_before": before,
            "autonomy_state_after": "MANUAL",
            "promotion": promotion,
            "recorded_at": now.isoformat(),
            "causation_id": source_event_id,
        }
        errors = sorted(_ENTRY_VALIDATOR.iter_errors(entry), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.path) or "$"
            raise TrustServiceError(
                "contract_violation", f"TrustLedgerEntry schema error at {path}: {first.message}"
            )

        # Audit first: forced/unavailable audit cannot mutate the ledger or emit
        # a Trust event. The enclosing dispatcher transaction is still the
        # atomic boundary for every subsequent write.
        self.audit.record(
            actor="controller:trust-ledger",
            action="trust.evidence_recorded",
            target=TRUST_KEY,
            params={
                "source_event_id": source_event_id,
                "action_ref": action_ref,
                "outcome": entry["outcome"]["status"],
                "epoch": row.epoch,
                "successes": new_successes,
                "trials": new_trials,
            },
            result="success",
            evidence_refs={"trust_entry_id": entry["entry_id"]},
        )
        denial_reason = (
            "lower_bound_below_threshold"
            if not eligible_by_evidence
            else "r2_requires_per_action_approval"
        )
        self.audit.record(
            actor="controller:trust-ledger",
            action="trust.promotion_denied",
            target=TRUST_KEY,
            params={
                "source_event_id": source_event_id,
                "wilson_lower": lower,
                "threshold": PROMOTION_THRESHOLD,
                "reason": denial_reason,
            },
            result="denied",
            evidence_refs={"trust_entry_id": entry["entry_id"]},
        )

        row.successes = new_successes
        row.trials = new_trials
        row.autonomy_state = "MANUAL"
        row.payload = {
            **(row.payload or {}),
            "wilson_lower": lower,
            "wilson_upper": upper,
            "promotion_eligible": False,
            "promotion_reason": denial_reason,
            "last_action_ref": action_ref,
            "last_source_event_id": source_event_id,
            "sample_rule": "one_action_one_sample",
        }
        row.updated_at = now
        persisted = TrustLedgerEntry(
            entry_id=entry["entry_id"],
            source_event_id=source_event_id,
            risk_class=RISK_CLASS,
            action_type=ACTION_TYPE,
            action_ref=action_ref,
            epoch=int(row.epoch),
            outcome=entry["outcome"]["status"],
            successes=new_successes,
            trials=new_trials,
            payload=entry,
            recorded_at=now,
        )
        self.session.add(persisted)

        trust_payload = {
            "action_type": ACTION_TYPE,
            "risk_class": RISK_CLASS,
            "outcome": entry["outcome"]["status"],
            "evidence_epoch": int(row.epoch),
            "epoch_successes": new_successes,
            "epoch_trials": new_trials,
            "wilson_lower": lower,
            "wilson_upper": upper,
            "action_ref": action_ref,
            "source_event_id": source_event_id,
        }
        evidence_event = self._append_trust_event(
            "trust.evidence_recorded",
            trust_payload,
            new_state="MANUAL",
            causation_id=source_event_id,
        )
        self._append_trust_event(
            "trust.promotion_denied",
            {
                "wilson_lower": lower,
                "threshold": PROMOTION_THRESHOLD,
                "reason": denial_reason,
                "action_ref": action_ref,
                "source_event_id": source_event_id,
            },
            new_state="MANUAL",
            causation_id=evidence_event.event_id,
        )
        self.session.flush()
        return self._receipt(persisted, duplicate=False)

    def block_unknown(
        self, *, source_event_id: str, action_ref: str, detail: str
    ) -> dict[str, Any]:
        row = self._locked_row()
        if (row.payload or {}).get("blocked_source_event_id") == source_event_id:
            return {
                "status": "consumed",
                "consumer": "trust-ledger",
                "source_event_id": source_event_id,
                "action_ref": action_ref,
                "autonomy_state": "BLOCKED_UNKNOWN",
                "duplicate": True,
            }
        self.audit.record(
            actor="controller:trust-ledger",
            action="trust.blocked_unknown",
            target=TRUST_KEY,
            params={"source_event_id": source_event_id, "action_ref": action_ref, "reason": detail},
            result="blocked",
        )
        row.autonomy_state = "BLOCKED_UNKNOWN"
        row.payload = {
            **(row.payload or {}),
            "blocked_source_event_id": source_event_id,
            "blocked_action_ref": action_ref,
            "blocked_reason": detail,
            "requires_human": True,
        }
        row.updated_at = datetime.now(timezone.utc)
        self._append_trust_event(
            "trust.blocked_unknown",
            {
                "reason": detail,
                "requires_human": True,
                "action_ref": action_ref,
                "source_event_id": source_event_id,
            },
            new_state="BLOCKED_UNKNOWN",
        )
        self.session.flush()
        return {
            "status": "consumed",
            "consumer": "trust-ledger",
            "source_event_id": source_event_id,
            "action_ref": action_ref,
            "autonomy_state": "BLOCKED_UNKNOWN",
            "duplicate": False,
        }

    def _locked_row(self) -> TrustLedger:
        row = self.session.scalar(
            select(TrustLedger)
            .where(
                TrustLedger.risk_class == RISK_CLASS,
                TrustLedger.action_type == ACTION_TYPE,
                TrustLedger.epoch == 1,
            )
            .with_for_update()
        )
        if row is None:
            row = TrustLedger(
                risk_class=RISK_CLASS,
                action_type=ACTION_TYPE,
                epoch=1,
                successes=0,
                trials=0,
                autonomy_state="MANUAL",
                payload={"sample_rule": "one_action_one_sample"},
            )
            self.session.add(row)
            self.session.flush()
        return row

    def _append_trust_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        new_state: str,
        causation_id: str | None = None,
    ) -> Event:
        aggregate = self.store.get_aggregate("trust", TRUST_KEY)
        return self.store.append_event(
            aggregate_type="trust",
            aggregate_id=TRUST_KEY,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id
            or str(payload.get("source_event_id") or "trust-ledger"),
            correlation_id=str(payload.get("action_ref") or TRUST_KEY),
            actor="controller:trust-ledger",
            expected_revision=aggregate.revision if aggregate is not None else None,
            new_state=new_state,
            merge_payload=payload,
        )

    @staticmethod
    def _receipt(entry: TrustLedgerEntry, *, duplicate: bool) -> dict[str, Any]:
        payload = entry.payload or {}
        return {
            "status": "consumed",
            "consumer": "trust-ledger",
            "entry_id": entry.entry_id,
            "source_event_id": entry.source_event_id,
            "action_ref": entry.action_ref,
            "outcome": entry.outcome,
            "epoch_successes": entry.successes,
            "epoch_trials": entry.trials,
            "wilson_lower": (payload.get("wilson") or {}).get("lower"),
            "promotion": payload.get("promotion"),
            "duplicate": duplicate,
        }
