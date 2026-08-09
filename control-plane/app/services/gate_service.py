"""Authoritative GateReport persistence, integrity checks, and WorkOrder binding."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Event, GateReportRecord, WorkOrder
from app.quality.client import QualityAPIError, QualityClientProtocol
from app.services.attribution import _judge_probe, _probe_contract
from app.services.audit import AuditService
from app.services.event_store import EventStore
from app.utils.jcs import canonical_json_digest


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_OFFICIAL_STEPFUN_BASE_URL = "https://api.stepfun.com/step_plan/v1"
_REPO_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "contracts" / "schemas").is_dir()
    ),
    Path(__file__).resolve().parents[3],
)
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
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        *,
        quality: QualityClientProtocol | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.audit = AuditService(session, self.settings)
        self.quality = quality

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

        artifacts = self._load_artifacts(report)
        if report.get("overall_status") == "passed":
            self._validate_passing_evidence(report, envelope, artifacts)

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
        if workorder is not None:
            # A post-canary report is registered after the immutable WorkOrder
            # exists.  Bind it immediately to the same exact hash instead of
            # leaving a second, release-authorizing gate partially bound.
            row.workorder_hash = workorder.hash
            row.binding_digest = self._binding_digest(row, workorder.hash)
            row.bound_at = datetime.now(timezone.utc)
        correlation_id = envelope.get("correlation_id", row.workorder_id)
        if not isinstance(correlation_id, str) or not correlation_id:
            raise GateServiceError("validation_failed", "gate correlation_id must be a non-empty string")
        self.session.add(row)
        terminal_event = self._record_eval_events(row, correlation_id=correlation_id)
        if row.workorder_hash is not None:
            self._record_bound_event(
                row,
                correlation_id=correlation_id,
                causation_id=terminal_event.event_id,
            )
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

        row = self._require_for_payload(
            workorder_payload,
            require_bound=False,
            require_passed=False,
        )
        workorder_hash = workorder_payload.get("hash", "")
        if not _HEX_RE.fullmatch(workorder_hash):
            raise GateServiceError("validation_failed", "WorkOrder hash must be 64 lowercase hex")
        binding_digest = self._binding_digest(row, workorder_hash)
        if row.workorder_hash is not None:
            if row.workorder_hash != workorder_hash or row.binding_digest != binding_digest:
                raise GateServiceError("hash_mismatch", "GateReport is already bound to another WorkOrder hash")
            self._record_bound_event(
                row,
                correlation_id=str(workorder_payload.get("case_id") or row.workorder_id),
            )
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
        self._record_bound_event(
            row,
            correlation_id=str(workorder_payload.get("case_id") or row.workorder_id),
        )
        self.session.flush()
        return row

    def validate_for_workorder(self, workorder: WorkOrder | dict[str, Any]) -> GateReportRecord:
        payload = workorder.payload if isinstance(workorder, WorkOrder) else workorder
        return self._require_for_payload(payload, require_bound=True, require_passed=True)

    def validate_bound_workorder(
        self, workorder: WorkOrder | dict[str, Any]
    ) -> GateReportRecord:
        """Validate immutable binding without treating a failed gate as approval authority."""

        payload = workorder.payload if isinstance(workorder, WorkOrder) else workorder
        return self._require_for_payload(payload, require_bound=True, require_passed=False)

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
        if row.overall_status not in ("passed", "failed", "error"):
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
        self,
        workorder_payload: dict[str, Any],
        *,
        require_bound: bool,
        require_passed: bool = True,
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
        if require_passed and row.overall_status != "passed":
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
        if row.workorder_hash is None:
            if row.binding_digest is not None or row.bound_at is not None:
                raise GateServiceError(
                    "hash_mismatch", "persisted GateReport has an incomplete WorkOrder binding"
                )
        else:
            if not _HEX_RE.fullmatch(row.workorder_hash):
                raise GateServiceError(
                    "hash_mismatch", "persisted GateReport WorkOrder hash is invalid"
                )
            if row.binding_digest != self._binding_digest(row, row.workorder_hash):
                raise GateServiceError(
                    "hash_mismatch", "persisted GateReport binding digest mismatch"
                )
            workorder = self.session.get(WorkOrder, row.workorder_id)
            if workorder is None or workorder.hash != row.workorder_hash:
                raise GateServiceError(
                    "hash_mismatch", "persisted GateReport WorkOrder projection mismatch"
                )

    @staticmethod
    def _binding_digest(row: GateReportRecord, workorder_hash: str) -> str:
        return canonical_json_digest(
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

    def _validate_report_conclusion(self, report: dict[str, Any]) -> None:
        rule = report.get("rule_track") or {}
        judge = report.get("judge_track") or {}
        deterministic = report.get("deterministic_tests") or {}
        live = report.get("live_provider_e2e") or {}
        profile = report.get("policy_profile")
        if profile not in ("live", "isolated-replay"):
            raise GateServiceError("validation_failed", f"unsupported gate policy profile {profile!r}")
        if profile != self.settings.gate_policy_profile:
            raise GateServiceError(
                "validation_failed",
                "GateReport policy_profile does not match the controller policy",
            )
        authoritative_statuses = [rule.get("status"), judge.get("status"), deterministic.get("status")]
        if profile == "isolated-replay":
            if not self.settings.allow_isolated_replay_gate:
                raise GateServiceError(
                    "validation_failed",
                    "isolated-replay Gate policy is disabled",
                )
            if not self.settings.database_url.startswith("sqlite"):
                raise GateServiceError(
                    "validation_failed",
                    "isolated-replay Gate policy requires an isolated SQLite controller",
                )
            if live.get("status") != "skipped" or live.get("provider") not in (
                "replay-not-live",
                "external-blocked",
            ):
                raise GateServiceError(
                    "validation_failed",
                    "isolated-replay Gate must explicitly mark live-provider E2E as skipped",
                )
            statuses = authoritative_statuses
        else:
            statuses = [*authoritative_statuses, live.get("status")]
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
                f"GateReport overall_status={report.get('overall_status')} inconsistent with "
                f"profile={profile} tracks={statuses}",
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

    def _load_artifacts(self, report: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Load and hash every artifact; live evidence must cross processes inline."""

        profile = report.get("policy_profile")
        loaded: dict[str, dict[str, Any]] = {}
        for ref in report.get("artifact_refs") or []:
            uri = str(ref.get("uri") or "")
            parsed = urlparse(uri)
            try:
                if parsed.scheme == "data":
                    header, separator, encoded = uri.partition(",")
                    if separator != "," or header != "data:application/json;base64":
                        raise GateServiceError(
                            "validation_failed",
                            "gate inline artifacts must be data:application/json;base64",
                        )
                    if len(encoded) > 2_700_000:
                        raise GateServiceError("validation_failed", "gate artifact exceeds 2 MB")
                    payload = base64.b64decode(encoded, validate=True)
                elif profile == "isolated-replay" and parsed.scheme in {"file", "repo"}:
                    if parsed.scheme == "repo":
                        path = (_REPO_ROOT / unquote(parsed.path).lstrip("/")).resolve()
                    else:
                        path = Path(unquote(parsed.path)).resolve()
                    if _REPO_ROOT not in path.parents:
                        raise GateServiceError(
                            "validation_failed", "isolated gate artifact escapes the repository"
                        )
                    if path.stat().st_size > 2_000_000:
                        raise GateServiceError("validation_failed", "gate artifact exceeds 2 MB")
                    payload = path.read_bytes()
                else:
                    raise GateServiceError(
                        "validation_failed",
                        "live gate artifacts must use process-independent inline JSON evidence",
                    )
                if len(payload) > 2_000_000:
                    raise GateServiceError("validation_failed", "gate artifact exceeds 2 MB")
                digest = "sha256:" + hashlib.sha256(payload).hexdigest()
                if digest != ref.get("digest"):
                    raise GateServiceError("hash_mismatch", f"gate artifact digest mismatch: {uri}")
                value = json.loads(payload.decode("utf-8"))
            except GateServiceError:
                raise
            except (OSError, UnicodeDecodeError, ValueError, binascii.Error) as exc:
                raise GateServiceError(
                    "validation_failed", f"gate artifact is unavailable or invalid: {uri}"
                ) from exc
            if not isinstance(value, dict):
                raise GateServiceError("validation_failed", "gate artifacts must be JSON objects")
            loaded[uri] = value
        return loaded

    @staticmethod
    def _pytest_counts(output: str) -> tuple[int, int]:
        passed = sum(int(value) for value in re.findall(r"(\d+)\s+passed\b", output))
        failed = sum(
            int(value)
            for word in ("failed", "error", "errors")
            for value in re.findall(rf"(\d+)\s+{word}\b", output)
        )
        return passed, failed

    def _suite_semantics(self, kind: str, artifact: dict[str, Any]) -> tuple[str, int, int]:
        if "returncode" in artifact:
            passed = artifact.get("n_passed")
            failed = artifact.get("n_failed")
            code = artifact.get("returncode")
            timed_out = artifact.get("timed_out")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (passed, failed, code)):
                raise GateServiceError("validation_failed", "gate suite counters are invalid")
            if timed_out is True:
                return "error", passed, max(1, failed)
            if code == 0 and passed > 0 and failed == 0:
                return "passed", passed, failed
            return ("failed" if code != 0 and failed > 0 else "error"), passed, max(failed, 1)
        if "exit_code" in artifact:
            code = artifact.get("exit_code")
            output = artifact.get("output")
            if isinstance(code, bool) or not isinstance(code, int) or not isinstance(output, str):
                raise GateServiceError("validation_failed", "gate command report is invalid")
            passed, failed = self._pytest_counts(output)
            if code == 0 and passed > 0 and failed == 0:
                return "passed", passed, failed
            return ("failed" if code != 0 and failed > 0 else "error"), passed, max(failed, 1)
        if kind == "contract" and isinstance(artifact.get("checks"), int):
            checks = artifact["checks"]
            errors = artifact.get("errors")
            if checks <= 0 or not isinstance(errors, list):
                raise GateServiceError("validation_failed", "contract artifact is empty or invalid")
            failed = len(errors)
            return ("passed" if failed == 0 else "failed"), checks - min(checks, failed), failed
        if kind == "replay" and isinstance(artifact.get("checks"), dict):
            checks = artifact["checks"]
            if not checks or any(not isinstance(value, dict) for value in checks.values()):
                raise GateServiceError("validation_failed", "replay artifact is empty or invalid")
            failed = sum(value.get("passed") is not True for value in checks.values())
            return ("passed" if failed == 0 else "failed"), len(checks) - failed, failed
        raise GateServiceError("validation_failed", f"unsupported {kind} gate suite artifact")

    def _validate_passing_evidence(
        self,
        report: dict[str, Any],
        envelope: dict[str, Any],
        artifacts: dict[str, dict[str, Any]],
    ) -> None:
        """Recompute every deterministic pass and bind live provider receipts."""

        for suite in report["deterministic_tests"]["suites"]:
            artifact = artifacts.get(suite.get("report_ref"))
            if artifact is None:
                raise GateServiceError("validation_failed", "gate suite artifact is missing")
            actual = self._suite_semantics(str(suite.get("kind")), artifact)
            declared = (suite.get("status"), suite.get("n_passed"), suite.get("n_failed"))
            if actual != declared:
                raise GateServiceError(
                    "validation_failed", "gate suite summary differs from the executed report"
                )

        deterministic_refs = {
            suite.get("report_ref") for suite in report["deterministic_tests"]["suites"]
        }
        live_refs = {
            suite.get("report_ref") for suite in report["live_provider_e2e"].get("suites") or []
            if suite.get("report_ref")
        }
        candidate_refs = live_refs or (set(artifacts) - deterministic_refs)
        if len(candidate_refs) != 1:
            raise GateServiceError(
                "validation_failed", "GateReport must bind exactly one candidate-answer artifact"
            )
        candidate = artifacts.get(next(iter(candidate_refs)))
        if candidate is None:
            raise GateServiceError("validation_failed", "candidate-answer artifact is missing")

        probe_digest = report["subject"]["probe_set_digest"]
        try:
            probes = _probe_contract(probe_digest)
        except ValueError as exc:
            raise GateServiceError("validation_failed", str(exc)) from exc
        profile = report["policy_profile"]
        if profile == "isolated-replay":
            answers = candidate.get("answers")
            if (
                candidate.get("source") != "recorded-replay"
                or candidate.get("versionset_digest") != report["subject"]["target_versionset_digest"]
                or not isinstance(answers, dict)
                or set(answers) != set(probes)
            ):
                raise GateServiceError(
                    "validation_failed", "isolated candidate evidence is not frozen to the report"
                )
        else:
            if self.quality is None:
                raise GateServiceError("validation_failed", "live gate requires Quality verification")
            try:
                target = self.quality.get_versionset(envelope["target_versionset_id"])
            except QualityAPIError as exc:
                raise GateServiceError("validation_failed", "live gate target is unavailable") from exc
            if (
                target.get("versionset_id") != envelope["target_versionset_id"]
                or target.get("digest") != report["subject"]["target_versionset_digest"]
                or target.get("revision") != envelope["target_revision"]
            ):
                raise GateServiceError("target_mismatch", "live gate target identity drifted")
            expected_components = {
                "prompt_digest": ((target.get("content") or {}).get("prompt") or {}).get("digest"),
                "kb_manifest_digest": ((target.get("content") or {}).get("kb_manifest") or {}).get("manifest_digest"),
                "model_digest": ((target.get("content") or {}).get("model") or {}).get("digest"),
            }
            responses = candidate.get("responses")
            if (
                candidate.get("target_versionset_id") != envelope["target_versionset_id"]
                or candidate.get("target_revision") != envelope["target_revision"]
                or candidate.get("target_versionset_digest") != report["subject"]["target_versionset_digest"]
                or candidate.get("dataset_id") != envelope["dataset_id"]
                or candidate.get("dataset_version") != envelope["dataset_version"]
                or candidate.get("dataset_digest") != probe_digest
                or not isinstance(responses, list)
            ):
                raise GateServiceError("validation_failed", "live candidate evidence binding is invalid")
            indexed = {item.get("probe_id"): item for item in responses if isinstance(item, dict)}
            if len(indexed) != len(responses) or set(indexed) != set(probes):
                raise GateServiceError("validation_failed", "live candidate probe coverage is incomplete")
            request_ids = [item.get("request_id") for item in responses]
            trace_ids = [item.get("trace_id") for item in responses]
            if (
                any(not isinstance(value, str) or not value for value in request_ids)
                or len(set(request_ids)) != len(probes)
                or any(not isinstance(value, str) or not value for value in trace_ids)
                or len(set(trace_ids)) != len(probes)
            ):
                raise GateServiceError(
                    "validation_failed",
                    "live candidate probes require unique request_id and trace_id receipts",
                )
            answers = {}
            for probe_id, item in indexed.items():
                answer = item.get("answer")
                request_id = item.get("request_id")
                trace_id = item.get("trace_id")
                actual_components = {key: item.get(key) for key in expected_components}
                if (
                    not isinstance(answer, str)
                    or not isinstance(request_id, str)
                    or not request_id
                    or not isinstance(trace_id, str)
                    or not trace_id
                    or item.get("provider_status") != "ok"
                    or item.get("provider_origin") != _OFFICIAL_STEPFUN_BASE_URL
                    or item.get("versionset_id") != envelope["target_versionset_id"]
                    or actual_components != expected_components
                ):
                    raise GateServiceError("validation_failed", f"live probe {probe_id} binding is invalid")
                try:
                    provider_log = self.quality.get_log(request_id)
                except QualityAPIError as exc:
                    raise GateServiceError(
                        "validation_failed", f"Quality provider log missing for {probe_id}"
                    ) from exc
                expected_log = {
                    "request_id": request_id,
                    "status": "ok",
                    "provider_origin": _OFFICIAL_STEPFUN_BASE_URL,
                    "trace_id": trace_id,
                    "versionset_id": envelope["target_versionset_id"],
                    **expected_components,
                    "answer_digest": "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                }
                if any(provider_log.get(key) != value for key, value in expected_log.items()):
                    raise GateServiceError(
                        "validation_failed", f"live probe {probe_id} differs from Quality provider log"
                    )
                answers[probe_id] = answer
            self._validate_judge_evidence(report, candidate, answers)

        oracle = {probe_id: _judge_probe(probe, answers[probe_id]) for probe_id, probe in probes.items()}
        if not all(oracle.values()):
            raise GateServiceError("validation_failed", "passed rule track contains failing raw answers")
        score_ids = {item.get("probe_id") for item in report["judge_track"]["scores"]}
        if score_ids != set(probes):
            raise GateServiceError("validation_failed", "judge scores do not cover the frozen probe set")
        if profile == "isolated-replay" and any(
            item.get("pass") is not oracle[item.get("probe_id")]
            or float(item.get("score", -1)) != (1.0 if oracle[item.get("probe_id")] else 0.0)
            for item in report["judge_track"]["scores"]
        ):
            raise GateServiceError("validation_failed", "recorded judge scores differ from rule replay")
        if profile == "live":
            suite = report["live_provider_e2e"]["suites"][0]
            if (suite.get("status"), suite.get("n_passed"), suite.get("n_failed")) != (
                "passed",
                len(probes),
                0,
            ):
                raise GateServiceError("validation_failed", "live suite counts differ from raw answers")

    def _validate_judge_evidence(
        self,
        report: dict[str, Any],
        candidate: dict[str, Any],
        answers: dict[str, str],
    ) -> None:
        evidence = candidate.get("judge_responses")
        if not isinstance(evidence, list):
            raise GateServiceError("validation_failed", "live judge response evidence is missing")
        indexed = {item.get("probe_id"): item for item in evidence if isinstance(item, dict)}
        score_rows = report["judge_track"]["scores"]
        scores = {item.get("probe_id"): item for item in score_rows if isinstance(item, dict)}
        if (
            len(indexed) != len(evidence)
            or set(indexed) != set(answers)
            or len(scores) != len(score_rows)
            or set(scores) != set(answers)
        ):
            raise GateServiceError("validation_failed", "live judge evidence coverage is incomplete")
        provider_request_ids = [item.get("provider_request_id") for item in evidence]
        if (
            any(
                not isinstance(provider_request_id, str) or not provider_request_id
                for provider_request_id in provider_request_ids
            )
            or len(set(provider_request_ids)) != len(answers)
        ):
            raise GateServiceError(
                "validation_failed",
                "live judge probes require unique provider_request_id receipts",
            )
        for probe_id, item in indexed.items():
            raw = item.get("raw_response")
            parsed = item.get("parsed")
            if (
                not isinstance(raw, str)
                or not isinstance(parsed, dict)
                or item.get("model_digest") != report["judge_track"]["judge_model_digest"]
                or item.get("answer_digest")
                != "sha256:" + hashlib.sha256(answers[probe_id].encode("utf-8")).hexdigest()
                or item.get("raw_response_digest")
                != "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
            ):
                raise GateServiceError("validation_failed", f"judge evidence {probe_id} binding is invalid")
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            try:
                decoded = json.loads(match.group(0)) if match else None
                decoded_score = float(decoded.get("score")) if isinstance(decoded, dict) else None
            except (ValueError, TypeError, json.JSONDecodeError):
                decoded = None
                decoded_score = None
            declared = scores[probe_id]
            if (
                not isinstance(decoded, dict)
                or not isinstance(decoded.get("pass"), bool)
                or decoded_score is None
                or not math.isfinite(decoded_score)
                or parsed.get("pass") is not decoded.get("pass")
                or float(parsed.get("score", -1)) != max(0.0, min(1.0, decoded_score))
                or declared.get("pass") is not parsed.get("pass")
                or float(declared.get("score", -1)) != float(parsed.get("score", -2))
            ):
                raise GateServiceError("validation_failed", f"judge evidence {probe_id} was not parsed faithfully")

    def _record_eval_events(self, row: GateReportRecord, *, correlation_id: str) -> Event:
        subject = row.report["subject"]
        base = {
            "workorder_id": row.workorder_id,
            "report_hash": row.report_hash,
            "candidate_digest": row.candidate_digest,
            "overall_status": row.overall_status,
            "evidence_digest": row.evidence_digest,
            "target_revision": row.target_revision,
        }
        requested_event = self.store.append_event(
            aggregate_type="eval",
            aggregate_id=row.eval_id,
            event_type="eval.requested",
            payload={
                **base,
                "changeset_id": f"cs_{row.workorder_id}",
                "target_versionset_digest": row.target_versionset_digest,
                "regression_suite_digest": subject["regression_suite_digest"],
                "probe_set_digest": row.dataset_digest,
                "trigger": "regression" if row.authorization_digest else "gate",
            },
            correlation_id=correlation_id,
            actor="controller:gate",
            machine="eval",
            merge_payload=base,
        )
        rule_checks = row.report["rule_track"]["checks"]
        received_event = self._append_eval(
            row.eval_id,
            "eval.report_received",
            {
                **base,
                "execution_mode": "completed_report_import",
                "rule_status": row.report["rule_track"]["status"],
                "judge_status": row.report["judge_track"]["status"],
                "n_rule_passed": sum(item.get("status") == "passed" for item in rule_checks),
                "n_rule_failed": sum(item.get("status") == "failed" for item in rule_checks),
                "judge_model_digest": row.report["judge_track"]["judge_model_digest"],
                "athlete_model_digest": row.report["judge_track"]["athlete_model_digest"],
                "target_versionset_digest": subject["target_versionset_digest"],
                "regression_suite_digest": subject["regression_suite_digest"],
                "probe_set_digest": subject["probe_set_digest"],
            },
            causation_id=requested_event.event_id,
            correlation_id=correlation_id,
        )
        report_ref = f"eval://{row.eval_id}"
        completed = {
            **base,
            "report_ref": report_ref,
            "report_digest": f"sha256:{row.report_hash}",
            "report_received_event_id": received_event.event_id,
        }
        if row.overall_status == "passed":
            terminal_event = self._append_eval(
                row.eval_id,
                "eval.passed",
                completed,
                causation_id=received_event.event_id,
                correlation_id=correlation_id,
            )
        elif row.overall_status == "failed":
            terminal_event = self._append_eval(
                row.eval_id,
                "eval.failed",
                {
                    **completed,
                    "failing_checks": self._failing_check_refs(row.report),
                },
                causation_id=received_event.event_id,
                correlation_id=correlation_id,
            )
        else:
            terminal_event = self._append_eval(
                row.eval_id,
                "eval.error",
                {
                    **completed,
                    "error": "one or more gate tracks ended in an infrastructure or indeterminate state",
                    "retryable": False,
                },
                guard="retryable=false",
                causation_id=received_event.event_id,
                correlation_id=correlation_id,
            )
        return terminal_event

    def _record_bound_event(
        self,
        row: GateReportRecord,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Event:
        """Publish GATE_COMPLETED only after the final WorkOrder hash is sealed."""

        if not row.workorder_hash or not row.binding_digest or row.bound_at is None:
            raise GateServiceError(
                "hash_mismatch", "cannot complete an unbound GateReport"
            )
        payload = {
            "workorder_id": row.workorder_id,
            "workorder_hash": row.workorder_hash,
            "eval_id": row.eval_id,
            "report_hash": row.report_hash,
            "report_ref": f"eval://{row.eval_id}",
            "report_digest": f"sha256:{row.report_hash}",
            "candidate_digest": row.candidate_digest,
            "binding_digest": row.binding_digest,
            "overall_status": row.overall_status,
            "evidence_digest": row.evidence_digest,
            "target_versionset_id": row.target_versionset_id,
            "target_versionset_digest": row.target_versionset_digest,
            "target_revision": row.target_revision,
            "dataset_id": row.dataset_id,
            "dataset_version": row.dataset_version,
            "dataset_digest": row.dataset_digest,
            "bound_at": row.bound_at.isoformat(),
        }
        events = self.store.list_events(row.eval_id)
        existing = next((event for event in events if event.event_type == "eval.bound"), None)
        if existing is not None:
            if existing.payload != payload:
                raise GateServiceError(
                    "hash_mismatch", "persisted Gate completion binding changed"
                )
            return existing
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.event_type in {"eval.passed", "eval.failed", "eval.error"}
            ),
            None,
        )
        if terminal is None:
            raise GateServiceError(
                "validation_failed", "Gate terminal event is missing before binding"
            )
        aggregate = self.store.get_aggregate("eval", row.eval_id)
        if aggregate is None:
            raise GateServiceError("validation_failed", "Gate aggregate is missing")
        return self.store.append_event(
            aggregate_type="eval",
            aggregate_id=row.eval_id,
            event_type="eval.bound",
            payload=payload,
            causation_id=causation_id or terminal.event_id,
            correlation_id=correlation_id,
            actor="controller:gate",
            expected_revision=aggregate.revision,
            merge_payload={
                "workorder_hash": row.workorder_hash,
                "binding_digest": row.binding_digest,
                "bound_at": row.bound_at.isoformat(),
            },
        )

    def _append_eval(
        self,
        eval_id: str,
        event_type: str,
        payload: dict[str, Any],
        guard: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        agg = self.store.get_aggregate("eval", eval_id)
        if agg is None:
            raise GateServiceError("validation_failed", f"eval aggregate {eval_id} missing")
        return self.store.append_event(
            aggregate_type="eval",
            aggregate_id=eval_id,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id or "none",
            correlation_id=correlation_id or payload.get("workorder_id", eval_id),
            actor="controller:gate",
            expected_revision=agg.revision,
            machine="eval",
            guard=guard,
            merge_payload=payload,
        )

    @staticmethod
    def _failing_check_refs(report: dict[str, Any]) -> list[str]:
        """Return stable, non-empty failure identities for eval.failed."""

        failures: list[str] = []
        rule = report.get("rule_track") or {}
        for check in rule.get("checks") or []:
            if check.get("status") != "passed":
                failures.append(f"rule:{check.get('check_id') or 'unknown'}")
        if rule.get("status") != "passed" and not any(item.startswith("rule:") for item in failures):
            failures.append("rule:track")

        judge = report.get("judge_track") or {}
        threshold = float(judge.get("pass_threshold", 0.0))
        for score in judge.get("scores") or []:
            if not score.get("pass") or float(score.get("score", 0.0)) < threshold:
                failures.append(f"judge:{score.get('probe_id') or 'unknown'}")
        if judge.get("status") != "passed" and not any(
            item.startswith("judge:") for item in failures
        ):
            failures.append("judge:track")

        for section_name in ("deterministic_tests", "live_provider_e2e"):
            section = report.get(section_name) or {}
            for suite in section.get("suites") or []:
                if suite.get("status") != "passed" or int(suite.get("n_failed", 0)) > 0:
                    failures.append(
                        f"{section_name}:{suite.get('suite') or 'unknown'}"
                    )
            if section.get("status") != "passed" and not any(
                item.startswith(f"{section_name}:") for item in failures
            ):
                failures.append(f"{section_name}:track")

        return sorted(set(failures)) or ["gate:overall_status_failed"]

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
