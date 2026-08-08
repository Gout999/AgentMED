"""Receipt-bearing notification adapters with explicit idempotency semantics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol


class NotificationDeliveryError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


class NotificationAdapter(Protocol):
    def deliver(
        self, *, outbox_id: str, payload: dict[str, Any], payload_digest: str
    ) -> dict[str, Any]: ...


class DisabledNotificationAdapter:
    """Production-safe default: no configured provider never becomes SENT."""

    def deliver(
        self, *, outbox_id: str, payload: dict[str, Any], payload_digest: str
    ) -> dict[str, Any]:
        raise NotificationDeliveryError(
            "notification_adapter_disabled",
            "notification provider is not configured",
            retryable=False,
        )


class FeishuMockAdapter:
    """Contract/replay-only Feishu adapter.

    It is intentionally named ``mock`` and must be selected explicitly. The
    adapter remembers outbox idempotency within the worker process and rejects
    key reuse with a different payload digest.
    """

    def __init__(self) -> None:
        self.receipts: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.fail_next: NotificationDeliveryError | None = None

    def deliver(
        self, *, outbox_id: str, payload: dict[str, Any], payload_digest: str
    ) -> dict[str, Any]:
        existing = self.receipts.get(outbox_id)
        if existing is not None:
            if existing.get("payload_digest") != payload_digest:
                raise NotificationDeliveryError(
                    "idempotency_conflict",
                    "notification outbox id was reused with a different payload",
                    retryable=False,
                )
            return dict(existing)
        if self.fail_next is not None:
            failure = self.fail_next
            self.fail_next = None
            raise failure
        channel = payload.get("channel")
        if not isinstance(channel, str) or not channel.startswith("feishu-mock"):
            raise NotificationDeliveryError(
                "unsupported_channel",
                "Feishu mock adapter only accepts explicitly marked feishu-mock channels",
                retryable=False,
            )
        self.calls.append(outbox_id)
        receipt = {
            "status": "sent",
            "provider": "feishu-mock",
            "provider_message_id": f"mock-msg-{outbox_id}",
            "thread_ref": payload.get("thread_ref"),
            "outbox_id": outbox_id,
            "payload_digest": payload_digest,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.receipts[outbox_id] = dict(receipt)
        return receipt
