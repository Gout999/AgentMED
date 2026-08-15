"""Atomic Release-to-Case-to-notification closure coordinator."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import ReleaseClosure
from app.services.case_service import CaseService, CaseServiceError
from app.services.notification_service import NotificationService, NotificationServiceError


class CaseClosureServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class CaseClosureService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def resolve_and_queue(
        self,
        *,
        release_id: str,
        channel: str,
        thread_ref: str,
        body_ref: str,
        body_digest: str,
    ) -> dict[str, Any]:
        """Resolve from a real terminal Release event and queue the original reply.

        Both projections share the caller's transaction.  Any notification or
        audit failure rolls the Case resolution back as well.
        """

        closure = self.session.scalar(
            select(ReleaseClosure)
            .where(ReleaseClosure.release_id == release_id)
            .with_for_update()
        )
        if closure is None:
            raise CaseClosureServiceError(
                "closure_missing",
                "notification requires the immutable ReleaseClosure configured before promote",
            )
        supplied = {
            "channel": channel,
            "thread_ref": thread_ref,
            "body_ref": body_ref,
            "body_digest": body_digest,
        }
        if any(getattr(closure, key) != value for key, value in supplied.items()):
            raise CaseClosureServiceError(
                "hash_mismatch",
                "notification body/origin differs from the immutable ReleaseClosure",
            )
        if closure.status not in {"configured", "queued"}:
            raise CaseClosureServiceError(
                "illegal_transition",
                f"ReleaseClosure state {closure.status} cannot queue a notification",
            )

        notification_id = "notif_" + hashlib.sha256(
            f"agentmed-release-notification:{release_id}".encode("utf-8")
        ).hexdigest()[:24]
        try:
            resolved = CaseService(self.session, self.settings).resolve_from_release(
                release_id=release_id
            )
            queued = NotificationService(self.session, self.settings).queue(
                case_id=resolved["case_id"],
                release_id=release_id,
                causation_id=resolved["event_id"],
                channel=channel,
                thread_ref=thread_ref,
                body_ref=body_ref,
                body_digest=body_digest,
                notification_id=notification_id,
            )
        except (CaseServiceError, NotificationServiceError) as exc:
            raise CaseClosureServiceError(exc.code, exc.message, **exc.extra) from exc
        closure.status = "queued"
        closure.notification_id = queued["notification_id"]
        closure.queued_at = datetime.now(timezone.utc)
        return {
            "release_id": release_id,
            "case": resolved,
            "notification": queued,
        }
