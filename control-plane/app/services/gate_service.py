"""Authoritative GateReport persistence, integrity checks, and WorkOrder binding."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import GateReportRecord, WorkOrder
from app.services.audit import AuditService
from app.services.event_store import EventStore
from app.utils.jcs import canonical_json_digest


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_SCHEMA = json.loads(
    (_REPO_ROOT / "contracts" / "schemas" / "gate-report.schema.json").read_text(encoding="utf-8")
)
_GATE_VALIDATOR = Draft202012Validator(_GATE_SCHEMA, format_checker=FormatChecker())


class GateServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class GateService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.audit = AuditService(session, self.settings)

    def register_report(self, envelope: dict[str, Any]) -> dict[str, Any]:
        required = (
            "report",
            "workorder_id",
            "target_versionset_id",
            "target_revision",
            "dataset_id",
            "dataset_version",
            "evidence_digest",
        )
        missing = [key for key in required if key not in envelope]
        if missing:
            raise GateServiceError("validation_failed", f"gate envelope missing fields: {missing}")

        report = envelope["report"]
        if not isinstance(report, dict):
            raise GateServiceError("validation_failed", "report must be an object")
        schema_errors = sorted(_GATE_VALIDATOR.iter_errors(report), key=lambda err: list(err.path))
        if schema_errors:
            first = schema_errors[0]
            path = ".".join(str(part) for part in first.path) or "$"
            raise GateServiceError("validation_failed", f"GateReport schema error at {path}: {first.message}")

        eval_id = str(report["eval_id"])
        try:
            report_hash = canonical_json_digest(report, prefix=False)
        except (TypeError, ValueError) as exc:
            raise GateServiceError(
                "validation_failed", "GateReport contains non-canonical JSON values"
            ) from exc
        declared_hash = envelope.get("report_hash")
        if declared_hash is not None and declared_hash != report_hash:
            raise GateServiceError("hash_mismatch", "declared GateReport hash does not match report content")

        self._validate_report_conclusion(report)
        subject = report["subject"]
        evidence_digest = canonical_json_digest(report.get("artifact_refs") or [])
        if not report.get("artifact_refs"):
            raise GateServiceError("validation_failed", "GateReport artifact_refs must not be empty")
        if envelope["evidence_digest"] != evidence_digest:
            raise GateServiceError("hash_mismatch", "evidence digest does not match GateReport artifact manifest")
        if not _DIGEST_RE.fullmatch(evidence_digest):
            raise GateServiceError("validation_failed", "evidence_digest must be sha256:<64 hex>")
        artifact_uris = {item.get("uri") for item in report["artifact_refs"]}
        evidence_suites = [*(report["deterministic_tests"].get("suites") or [])]
        if report["live_provider_e2e"].get("status") in ("passed", "failed"):
            evidence_suites.extend(report["live_provider_e2e"].get("suites") or [])
        missing_suite_evidence = [
            item.get("suite")
            for item in evidence_suites
            if not item.get("report_ref") or item.get("report_ref") not in artifact_uris
        ]
        if missing_suite_evidence:
            raise GateServiceError(
                "validation_failed",
                f"GateReport suites are not bound to artifact_refs: {missing_suite_evidence}",
            )

        target_revision = envelope["target_revision"]
        if not isinstance(target_revision, int) or isinstance(target_revision, bool) or target_revision <= 0:
            raise GateServiceError("validation_failed", "target_revision must be a positive integer")
        for field in ("workorder_id", "target_versionset_id", "dataset_id", "dataset_version"):
            if not isinstance(envelope[field], str) or not envelope[field].strip():
                raise GateServiceError("validation_failed", f"{field} must be a non-empty string")

        dataset_digest = subject["probe_set_digest"]
        candidate_fields = {
            "workorder_id": envelope["workorder_id"],
            "target_versionset_id": envelope["target_versionset_id"],
            "target_versionset_digest": subject["target_versionset_digest"],
            "target_revision": target_revision,
            "dataset_id": envelope["dataset_id"],
            "dataset_version": envelope["dataset_version"],
            "dataset_digest": dataset_digest,
            "regression_suite_digest": subject["regression_suite_digest"],
            "evidence_digest": evidence_digest,
        }
        candidate_digest = canonical_json_digest(candidate_fields)
        if envelope.get("candidate_digest") not in (None, candidate_digest):
            raise GateServiceError("hash_mismatch", "candidate_digest does not match gate binding fields")

        existing = self.session.get(GateReportRecord, eval_id)
        if existing is not None:
            self._validate_stored_integrity(existing)
            if not self._same_registration(existing, report_hash, candidate_digest, envelope):
                raise GateServiceError("idempotency_conflict", f"eval_id {eval_id} already registered differently")
            return self._view(existing, duplicate=True)

        workorder = self.session.get(WorkOrder, envelope["workorder_id"])
        authorization_digest = (
            self._verification_authorization_digest(workorder) if workorder is not None else None
        )

        row = GateReportRecord(
            eval_id=eval_id,
            report_id=report["report_id"],
            workorder_id=envelope["workorder_id"],
            target_versionset_id=envelope["target_versionset_id"],
            target_versionset_digest=subject["target_versionset_digest"],
            target_revision=target_revision,
            dataset_id=envelope["dataset_id"],
            dataset_version=envelope["dataset_version"],
            dataset_digest=dataset_digest,
            evidence_digest=evidence_digest,
            candidate_digest=candidate_digest,
            report_hash=report_hash,
            authorization_digest=authorization_digest,
            overall_status=report["overall_status"],
            report=report,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self._record_eval_events(row)
        self.audit.record(
            actor="controller:gate",
            action="gate.report.register",
            target=eval_id,
            params={
                "workorder_id": row.workorder_id,
                "report_hash": report_hash,
                "candidate_digest": candidate_digest,
                "overall_status": row.overall_status,
                "authorization_digest": authorization_digest,
            },
            result="success",
            evidence_refs={"evidence_digest": evidence_digest},
        )
        self.session.flush()
        return self._view(row, duplicate=False)

    def bind_workorder(self, workorder_payload: dict[str, Any]) -> GateReportRecord:
        """Seal the final WorkOrder hash to the already immutable GateReport."""

        row = self._require_for_payload(workorder_payload, require_bound=False)
        workorder_hash = workorder_payload.get("hash", "")
        if not _HEX_RE.fullmatch(workorder_hash):
            raise GateServiceError("validation_failed", "WorkOrder hash must be 64 lowercase hex")
        binding_fields = {
            "eval_id": row.eval_id,
            "report_hash": row.report_hash,
            "candidate_digest": row.candidate_digest,
            "workorder_hash": workorder_hash,
            "target_versionset_id": row.target_versionset_id,
            "target_versionset_digest": row.target_versionset_digest,
            "target_revision": row.target_revision,
            "dataset_id": row.dataset_id,
            "dataset_version": row.dataset_version,
            "dataset_digest": row.dataset_digest,
            "evidence_digest": row.evidence_digest,
        }
        binding_digest = canonical_json_digest(binding_fields)
        if row.workorder_hash is not None:
            if row.workorder_hash != workorder_hash or row.binding_digest != binding_digest:
                raise GateServiceError("hash_mismatch", "GateReport is already bound to another WorkOrder hash")
            return row
        row.workorder_hash = workorder_hash
        row.binding_digest = binding_digest
        row.bound_at = datetime.now(timezone.utc)
        self.audit.record(
            actor="controller:gate",
            action="gate.workorder.bind",
            target=row.eval_id,
            params={"workorder_id": row.workorder_id, "workorder_hash": workorder_hash},
            result="success",
            evidence_refs={"binding_digest": binding_digest},
        )
        self.session.flush()
        return row

    def validate_for_workorder(self, workorder: WorkOrder | dict[str, Any]) -> GateReportRecord:
        payload = workorder.payload if isinstance(workorder, WorkOrder) else workorder
        return self._require_for_payload(payload, require_bound=True)

    def validate_for_release(
        self, workorder: WorkOrder, *, versionset_id: str, remote_versionset: dict[str, Any]
    ) -> GateReportRecord:
        row = self.validate_for_workorder(workorder)
        if row.target_versionset_id != versionset_id:
            raise GateServiceError(
                "target_mismatch",
                f"release versionset_id={versionset_id} does not match gate target {row.target_versionset_id}",
            )
        if remote_versionset.get("versionset_id") != versionset_id:
            raise GateServiceError("target_mismatch", "Quality API returned a different VersionSet id")
        if remote_versionset.get("digest") != row.target_versionset_digest:
            raise GateServiceError("target_mismatch", "target VersionSet digest drifted after gate")
        if remote_versionset.get("revision") != row.target_revision:
            raise GateServiceError(
                "revision_conflict",
                "target VersionSet revision drifted after gate",
                expected_revision=row.target_revision,
                actual_revision=remote_versionset.get("revision"),
            )
        return row

    def validate_release_verification(
        self,
        workorder: WorkOrder,
        *,
        eval_id: str,
        report_hash: str,
        remote_versionset: dict[str, Any],
    ) -> GateReportRecord:
        """Validate a post-canary GateReport against the exact remote revision.

        The pre-release GateReport remains bound to the immutable WorkOrder.  A
        canary verification is a separate, immutable report because stage/canary
        legitimately advance the VersionSet revision after approval.
        """

        # A valid canary report must never replace or weaken the original approval binding.
        self.validate_for_workorder(workorder)
        row = self.session.get(GateReportRecord, eval_id)
        if row is None:
            raise GateServiceError("gate_missing", f"verification GateReport {eval_id} is not registered")
        self._validate_stored_integrity(row)
        if report_hash != row.report_hash:
            raise GateServiceError("hash_mismatch", "verification GateReport hash mismatch")
        if row.workorder_id != workorder.workorder_id:
            raise GateServiceError("hash_mismatch", "verification GateReport belongs to another WorkOrder")
        expected_authorization = self._verification_authorization_digest(workorder)
        if row.authorization_digest != expected_authorization:
            raise GateServiceError(
                "hash_mismatch",
                "verification GateReport is not authorized by the final WorkOrder binding",
            )
        if row.target_versionset_id != remote_versionset.get("versionset_id"):
            raise GateServiceError("target_mismatch", "verification GateReport targets another VersionSet")
        if row.target_versionset_digest != remote_versionset.get("digest"):
            raise GateServiceError("target_mismatch", "verification GateReport target digest does not match remote")
        if row.target_revision != remote_versionset.get("revision"):
            raise GateServiceError(
                "revision_conflict",
                "verification GateReport target revision does not match remote",
                expected_revision=row.target_revision,
                actual_revision=remote_versionset.get("revision"),
            )
        if row.overall_status not in ("passed", "failed"):
            raise GateServiceError(
                "gate_failed",
                f"verification GateReport overall_status={row.overall_status}; result is not actionable",
            )
        return row

    def get(self, eval_id: str) -> dict[str, Any]:
        row = self.session.get(GateReportRecord, eval_id)
        if row is None:
            raise GateServiceError("not_found", f"GateReport {eval_id} not found")
        self._validate_stored_integrity(row)
        return self._view(row)

    def _require_for_payload(
        self, workorder_payload: dict[str, Any], *, require_bound: bool
    ) -> GateReportRecord:
        ref = workorder_payload.get("gate_report_ref")
        if not isinstance(ref, dict):
            raise GateServiceError("gate_missing", "WorkOrder GateReport reference is missing")
        uri = ref.get("uri")
        digest = ref.get("digest")
        if not isinstance(uri, str) or not uri.startswith("eval://"):
            raise GateServiceError("gate_missing", "GateReport uri must use eval://<eval_id>")
        eval_id = uri.removeprefix("eval://")
        row = self.session.get(GateReportRecord, eval_id)
        if row is None:
            raise GateServiceError("gate_missing", f"GateReport {eval_id} is not registered")
        self._validate_stored_integrity(row)
        if digest != f"sha256:{row.report_hash}":
            raise GateServiceError("hash_mismatch", "WorkOrder GateReport digest mismatch")
        if row.workorder_id != workorder_payload.get("workorder_id"):
            raise GateServiceError("hash_mismatch", "GateReport belongs to a different WorkOrder id")
        if row.target_versionset_digest != workorder_payload.get("target_versionset_digest"):
            raise GateServiceError("target_mismatch", "GateReport target digest does not match WorkOrder")
        self._validate_report_conclusion(row.report)
        if row.overall_status != "passed":
            raise GateServiceError(
                "gate_failed", f"GateReport overall_status={row.overall_status}; release requires passed"
            )
        if require_bound:
            workorder_hash = workorder_payload.get("hash")
            if row.workorder_hash != workorder_hash or not row.binding_digest:
                raise GateServiceError("hash_mismatch", "GateReport final WorkOrder hash binding mismatch")
            expected_binding = canonical_json_digest(
                {
                    "eval_id": row.eval_id,
                    "report_hash": row.report_hash,
                    "candidate_digest": row.candidate_digest,
                    "workorder_hash": workorder_hash,
                    "target_versionset_id": row.target_versionset_id,
                    "target_versionset_digest": row.target_versionset_digest,
                    "target_revision": row.target_revision,
                    "dataset_id": row.dataset_id,
                    "dataset_version": row.dataset_version,
                    "dataset_digest": row.dataset_digest,
                    "evidence_digest": row.evidence_digest,
                }
            )
            if expected_binding != row.binding_digest:
                raise GateServiceError("hash_mismatch", "GateReport binding digest mismatch")
        return row

    def _validate_stored_integrity(self, row: GateReportRecord) -> None:
        report = row.report or {}
        schema_errors = sorted(_GATE_VALIDATOR.iter_errors(report), key=lambda err: list(err.path))
        if schema_errors:
            raise GateServiceError("hash_mismatch", "persisted GateReport no longer matches its schema")
        if canonical_json_digest(report, prefix=False) != row.report_hash:
            raise GateServiceError("hash_mismatch", "persisted GateReport content hash mismatch")
        if report.get("eval_id") != row.eval_id or report.get("report_id") != row.report_id:
            raise GateServiceError("hash_mismatch", "persisted GateReport identity mismatch")
        if report.get("overall_status") != row.overall_status:
            raise GateServiceError("hash_mismatch", "persisted GateReport status projection mismatch")
        self._validate_report_conclusion(report)

        subject = report.get("subject") or {}
        if subject.get("target_versionset_digest") != row.target_versionset_digest:
            raise GateServiceError("hash_mismatch", "persisted GateReport target digest projection mismatch")
        if subject.get("probe_set_digest") != row.dataset_digest:
            raise GateServiceError("hash_mismatch", "persisted GateReport dataset digest projection mismatch")
        evidence_digest = canonical_json_digest(report.get("artifact_refs") or [])
        if evidence_digest != row.evidence_digest:
            raise GateServiceError("hash_mismatch", "persisted GateReport evidence digest mismatch")
        candidate_digest = canonical_json_digest(
            {
                "workorder_id": row.workorder_id,
                "target_versionset_id": row.target_versionset_id,
                "target_versionset_digest": row.target_versionset_digest,
                "target_revision": row.target_revision,
                "dataset_id": row.dataset_id,
                "dataset_version": row.dataset_version,
                "dataset_digest": row.dataset_digest,
                "regression_suite_digest": subject.get("regression_suite_digest"),
                "evidence_digest": row.evidence_digest,
            }
        )
        if candidate_digest != row.candidate_digest:
            raise GateServiceError("hash_mismatch", "persisted GateReport candidate digest mismatch")
        if row.authorization_digest is not None:
            workorder = self.session.get(WorkOrder, row.workorder_id)
            if workorder is None or row.authorization_digest != self._verification_authorization_digest(
                workorder
            ):
                raise GateServiceError(
                    "hash_mismatch",
                    "persisted verification authorization digest mismatch",
                )

    def _verification_authorization_digest(self, workorder: WorkOrder) -> str:
        """Bind a post-canary report to the approved immutable WorkOrder."""

        initial = self._require_for_payload(workorder.payload, require_bound=True)
        return canonical_json_digest(
            {
                "workorder_id": workorder.workorder_id,
                "workorder_hash": workorder.hash,
                "initial_eval_id": initial.eval_id,
                "initial_report_hash": initial.report_hash,
                "initial_binding_digest": initial.binding_digest,
                "target_versionset_id": initial.target_versionset_id,
                "target_versionset_digest": initial.target_versionset_digest,
            }
        )

    @staticmethod
    def _validate_report_conclusion(report: dict[str, Any]) -> None:
        rule = report.get("rule_track") or {}
        judge = report.get("judge_track") or {}
        deterministic = report.get("deterministic_tests") or {}
        live = report.get("live_provider_e2e") or {}
        statuses = [rule.get("status"), judge.get("status"), deterministic.get("status"), live.get("status")]
        if any(status in ("error", "skipped", None, "unknown", "inconclusive") for status in statuses):
            expected = "error"
        elif any(status == "failed" for status in statuses):
            expected = "failed"
        elif all(status == "passed" for status in statuses):
            expected = "passed"
        else:
            expected = "error"
        if report.get("overall_status") != expected:
            raise GateServiceError(
                "validation_failed",
                f"GateReport overall_status={report.get('overall_status')} inconsistent with tracks={statuses}",
            )

        checks = rule.get("checks") or []
        if rule.get("status") == "passed" and (not checks or any(item.get("status") != "passed" for item in checks)):
            raise GateServiceError("validation_failed", "passed rule track contains non-passed checks")
        scores = judge.get("scores") or []
        try:
            threshold = float(judge.get("pass_threshold", 0.0))
        except (TypeError, ValueError) as exc:
            raise GateServiceError("validation_failed", "judge pass_threshold is invalid") from exc
        if not math.isfinite(threshold):
            raise GateServiceError("validation_failed", "judge pass_threshold must be finite")
        for item in scores:
            try:
                score = float(item.get("score"))
            except (TypeError, ValueError) as exc:
                raise GateServiceError("validation_failed", "judge score is invalid") from exc
            if not math.isfinite(score):
                raise GateServiceError("validation_failed", "judge score must be finite")
        if judge.get("judge_model_digest") == judge.get("athlete_model_digest"):
            raise GateServiceError("validation_failed", "judge and athlete model digests must differ")
        if judge.get("status") == "passed" and (
            not scores
            or any(not item.get("pass") or float(item.get("score", 0.0)) < threshold for item in scores)
        ):
            raise GateServiceError("validation_failed", "passed judge track contains failing scores")

        suites = deterministic.get("suites") or []
        kinds = {item.get("kind") for item in suites}
        if not {"contract", "replay"}.issubset(kinds):
            raise GateServiceError("validation_failed", "deterministic tests require contract and replay suites")
        if deterministic.get("status") == "passed":
            if any(
                item.get("status") != "passed"
                or int(item.get("n_failed", 0)) != 0
                or int(item.get("n_passed", 0)) <= 0
                for item in suites
            ):
                raise GateServiceError("validation_failed", "passed deterministic track has empty/failing suites")

        live_suites = live.get("suites") or []
        if live.get("status") == "passed" and (
            not live_suites
            or any(
                item.get("status") != "passed"
                or int(item.get("n_failed", 0)) != 0
                or int(item.get("n_passed", 0)) <= 0
                for item in live_suites
            )
        ):
            raise GateServiceError("validation_failed", "passed live track has empty/failing suites")

    def _record_eval_events(self, row: GateReportRecord) -> None:
        base = {
            "workorder_id": row.workorder_id,
            "report_hash": row.report_hash,
            "candidate_digest": row.candidate_digest,
        }
        self.store.append_event(
            aggregate_type="eval",
            aggregate_id=row.eval_id,
            event_type="eval.requested",
            payload=base,
            correlation_id=row.workorder_id,
            actor="controller:gate",
            machine="eval",
            merge_payload=base,
        )
        self._append_eval(row.eval_id, "eval.started", base)
        self._append_eval(
            row.eval_id,
            "eval.rule_track_completed",
            {**base, "status": row.report["rule_track"]["status"]},
        )
        self._append_eval(
            row.eval_id,
            "eval.judge_track_completed",
            {**base, "status": row.report["judge_track"]["status"]},
        )
        if row.overall_status == "passed":
            self._append_eval(row.eval_id, "eval.passed", base)
        elif row.overall_status == "failed":
            self._append_eval(row.eval_id, "eval.failed", base)
        else:
            self._append_eval(row.eval_id, "eval.error", {**base, "retryable": False}, guard="retryable=false")

    def _append_eval(self, eval_id: str, event_type: str, payload: dict[str, Any], guard: str | None = None) -> None:
        agg = self.store.get_aggregate("eval", eval_id)
        if agg is None:
            raise GateServiceError("validation_failed", f"eval aggregate {eval_id} missing")
        self.store.append_event(
            aggregate_type="eval",
            aggregate_id=eval_id,
            event_type=event_type,
            payload=payload,
            correlation_id=payload.get("workorder_id", eval_id),
            actor="controller:gate",
            expected_revision=agg.revision,
            machine="eval",
            guard=guard,
            merge_payload=payload,
        )

    @staticmethod
    def _same_registration(
        row: GateReportRecord,
        report_hash: str,
        candidate_digest: str,
        envelope: dict[str, Any],
    ) -> bool:
        return (
            row.report_hash == report_hash
            and row.candidate_digest == candidate_digest
            and row.workorder_id == envelope["workorder_id"]
            and row.target_versionset_id == envelope["target_versionset_id"]
            and row.target_revision == envelope["target_revision"]
            and row.dataset_id == envelope["dataset_id"]
            and row.dataset_version == envelope["dataset_version"]
        )

    @staticmethod
    def _view(row: GateReportRecord, *, duplicate: bool = False) -> dict[str, Any]:
        return {
            "eval_id": row.eval_id,
            "report_id": row.report_id,
            "workorder_id": row.workorder_id,
            "workorder_hash": row.workorder_hash,
            "target_versionset_id": row.target_versionset_id,
            "target_versionset_digest": row.target_versionset_digest,
            "target_revision": row.target_revision,
            "dataset_id": row.dataset_id,
            "dataset_version": row.dataset_version,
            "dataset_digest": row.dataset_digest,
            "evidence_digest": row.evidence_digest,
            "candidate_digest": row.candidate_digest,
            "report_hash": row.report_hash,
            "binding_digest": row.binding_digest,
            "authorization_digest": row.authorization_digest,
            "overall_status": row.overall_status,
            "report": row.report,
            "duplicate": duplicate,
        }
