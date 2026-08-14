"""V5-1C read-only Issue source connection (GitHub Issue / manual snapshot).

The issue text is data, never an instruction or acceptance truth.  This module
normalizes a fetched issue snapshot into a canonical, digest-bound record,
annotates edited/deleted source state, and flags prompt-injection markers so
downstream code (case binding, acceptance proposal) never treats the issue
body as an instruction source.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.v5_tables import IssueSourceSnapshot
from app.utils.ids import new_issue_snapshot_id
from app.utils.v4_integrity import V4IntegrityError, canonical_digest

# Instruction-like markers that make an issue body suspect as a prompt-injection
# carrier.  Detection only annotates the snapshot; it never changes behavior on
# its own, and issue text is never executed as an instruction anywhere.
_INSTRUCTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "you are now",
    "system prompt",
    "system instruction",
    "override your instructions",
    "ignore the instructions above",
    "as an ai assistant",
    "do not follow",
)
_NON_TEXT_ATTACHMENT_MARKERS = (
    "data:",
    "attachment",
    "file://",
    "exec(",
    "subprocess",
    "os.system",
    "curl ",
    "wget ",
)


class IssueSourceError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        details: dict[str, object] | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        self.workspace_id = workspace_id
        self.rollback_required = True
        super().__init__(code)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _nested_text(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            _nested_text(child, out)
    elif isinstance(value, list):
        for child in value:
            _nested_text(child, out)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def normalize_issue_snapshot(
    payload: dict[str, Any],
    *,
    source_kind: str,
    source_url: str | None,
    external_repo: str | None,
    external_issue_number: int | None,
    fetched_at: datetime,
    edited_flag: bool | None = None,
    deleted_flag: bool | None = None,
) -> dict[str, Any]:
    """Validate and normalize a raw issue snapshot into canonical data.

    Returns the canonical snapshot record (schema-major-2) with digests.  The
    raw issue text is preserved verbatim under ``snapshot_payload`` for audit,
    but ``title``/``body``/``attachments`` remain pure data: nothing here is
    ever treated as an instruction.
    """

    if source_kind not in {"github_issue", "manual"}:
        raise IssueSourceError("VALIDATION_FAILED", details={"reason": "SOURCE_KIND"})
    if source_kind == "github_issue":
        if (
            not isinstance(source_url, str)
            or not source_url.startswith(("https://", "http://"))
            or len(source_url) > 1024
        ):
            raise IssueSourceError("VALIDATION_FAILED", details={"reason": "SOURCE_URL"})
        if not isinstance(external_repo, str) or not 1 <= len(external_repo) <= 256:
            raise IssueSourceError(
                "VALIDATION_FAILED", details={"reason": "EXTERNAL_REPO"}
            )
        if not isinstance(external_issue_number, int) or isinstance(
            external_issue_number, bool
        ) or external_issue_number < 1:
            raise IssueSourceError(
                "VALIDATION_FAILED", details={"reason": "EXTERNAL_ISSUE_NUMBER"}
            )
    else:
        if source_url is not None and (
            not isinstance(source_url, str) or not 1 <= len(source_url) <= 1024
        ):
            raise IssueSourceError("VALIDATION_FAILED", details={"reason": "SOURCE_URL"})
        if external_repo is not None or external_issue_number is not None:
            raise IssueSourceError(
                "VALIDATION_FAILED", details={"reason": "MANUAL_SOURCE_IDENTITY"}
            )
    explicit_flag_invalid = (
        edited_flag is not None and type(edited_flag) is not bool
    ) or (deleted_flag is not None and type(deleted_flag) is not bool)
    payload_flag_invalid = (
        "edited_flag" in payload and type(payload["edited_flag"]) is not bool
    ) or ("deleted_flag" in payload and type(payload["deleted_flag"]) is not bool)
    if explicit_flag_invalid or payload_flag_invalid:
        raise IssueSourceError(
            "VALIDATION_FAILED", details={"reason": "SOURCE_STATE_FLAGS"}
        )
    title = payload.get("title")
    body = payload.get("body")
    if not isinstance(title, str) or not 1 <= len(title) <= 512:
        raise IssueSourceError("VALIDATION_FAILED", details={"reason": "ISSUE_TITLE"})
    if body is not None and not isinstance(body, str):
        raise IssueSourceError("VALIDATION_FAILED", details={"reason": "ISSUE_BODY"})

    collected: list[str] = []
    _nested_text(payload, collected)
    text_blob = "\n".join(collected)
    instruction_markers_detected = _contains_any(text_blob, _INSTRUCTION_MARKERS)
    non_text_attachment_detected = _contains_any(text_blob, _NON_TEXT_ATTACHMENT_MARKERS)

    state = payload.get("state")
    canonical_edited_flag = bool(
        edited_flag if edited_flag is not None else payload.get("edited_flag", False)
    )
    canonical_deleted_flag = bool(
        deleted_flag
        if deleted_flag is not None
        else payload.get("deleted_flag", False)
    ) or bool(state in ("deleted", "DELETED"))
    body_text = body if isinstance(body, str) else ""
    # The summary a signal/case can carry is a bounded human-readable title; the
    # body stays data-only and is never treated as an instruction.
    canonical: dict[str, Any] = {
        "schema_version": "2.0",
        "source_kind": source_kind,
        "source_url": source_url,
        "external_repo": external_repo,
        "external_issue_number": external_issue_number,
        "title": title,
        "body": body_text,
        "state": state if isinstance(state, str) else None,
        "attachments": (
            payload.get("attachments")
            if isinstance(payload.get("attachments"), list)
            else []
        ),
        "edited_flag": canonical_edited_flag,
        "deleted_flag": canonical_deleted_flag,
        "instruction_markers_detected": instruction_markers_detected,
        "non_text_attachment_detected": non_text_attachment_detected,
        "fetched_at": _wire_time(fetched_at),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/snapshot_digest)",
        "snapshot_digest": "",
    }
    try:
        digest = canonical_digest(canonical)
    except V4IntegrityError as exc:
        raise IssueSourceError("VALIDATION_FAILED", details={"reason": "DIGEST"}) from exc
    canonical["snapshot_digest"] = digest
    return canonical


class IssueSourceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_snapshot(
        self,
        *,
        workspace_id: str,
        case_id: str,
        canonical_snapshot: dict[str, Any],
        recorded_by_principal: str,
        fetched_at: datetime,
    ) -> IssueSourceSnapshot:
        """Persist one normalized snapshot (caller owns commit/rollback)."""

        stored_digest = canonical_snapshot.get("snapshot_digest")
        if not isinstance(stored_digest, str) or not stored_digest.startswith("sha256:"):
            raise IssueSourceError("INTERNAL_ERROR", workspace_id=workspace_id)
        existing = self.session.scalar(
            select(IssueSourceSnapshot).where(
                IssueSourceSnapshot.workspace_id == workspace_id,
                IssueSourceSnapshot.case_id == case_id,
                IssueSourceSnapshot.snapshot_digest == stored_digest,
            )
        )
        if existing is not None:
            # Replaying the same exact snapshot for the same Case is idempotent.
            # A later provider version has a different digest and is appended as
            # a separate immutable snapshot instead of rewriting history.
            return existing
        row = IssueSourceSnapshot(
            issue_snapshot_id=new_issue_snapshot_id(),
            workspace_id=workspace_id,
            case_id=case_id,
            source_kind=canonical_snapshot["source_kind"],
            source_url=canonical_snapshot["source_url"],
            external_repo=canonical_snapshot["external_repo"],
            external_issue_number=canonical_snapshot["external_issue_number"],
            snapshot_payload=canonical_snapshot,
            snapshot_digest=stored_digest,
            edited_flag=bool(canonical_snapshot["edited_flag"]),
            deleted_flag=bool(canonical_snapshot["deleted_flag"]),
            instruction_markers_detected=bool(
                canonical_snapshot["instruction_markers_detected"]
            ),
            fetched_at=_as_utc(fetched_at),
            recorded_by_principal=recorded_by_principal,
        )
        try:
            # The database carries the same (workspace, case, digest) unique
            # key.  A savepoint turns a concurrent duplicate into a safe replay
            # without aborting the caller-owned binding transaction.
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            existing = self.session.scalar(
                select(IssueSourceSnapshot).where(
                    IssueSourceSnapshot.workspace_id == workspace_id,
                    IssueSourceSnapshot.case_id == case_id,
                    IssueSourceSnapshot.snapshot_digest == stored_digest,
                )
            )
            if existing is not None:
                return existing
            raise IssueSourceError(
                "CATALOG_CONFLICT",
                details={"reason": "ISSUE_SNAPSHOT_IDENTITY_CONFLICT"},
                workspace_id=workspace_id,
            ) from exc
        return row


__all__ = [
    "IssueSourceError",
    "IssueSourceService",
    "normalize_issue_snapshot",
]
