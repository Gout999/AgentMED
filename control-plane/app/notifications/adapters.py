"""Receipt-bearing notification adapters with explicit idempotency semantics."""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import httpx


OFFICIAL_FEISHU_BASE_URL = "https://open.feishu.cn"


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


def _load_immutable_body(payload: dict[str, Any]) -> tuple[bytes, str]:
    """Load and verify the exact immutable body shared by all adapters."""

    body_ref = payload.get("body_ref")
    body_digest = payload.get("body_digest")
    parsed = urlparse(body_ref) if isinstance(body_ref, str) else None
    if parsed is None or parsed.scheme not in {"file", "data", "repo"}:
        raise NotificationDeliveryError(
            "body_unavailable",
            "notification delivery requires an immutable file://, repo://, or data:text/plain;base64 body",
            retryable=False,
        )
    if parsed.scheme == "data":
        header, separator, encoded = body_ref.partition(",")
        if separator != "," or header != "data:text/plain;base64":
            raise NotificationDeliveryError(
                "body_unavailable",
                "only data:text/plain;base64 notification bodies are accepted",
                retryable=False,
            )
        try:
            body = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise NotificationDeliveryError(
                "body_unavailable", "notification data body is invalid base64", retryable=False
            ) from exc
    else:
        if not parsed.path:
            raise NotificationDeliveryError(
                "body_unavailable", "notification file body path is empty", retryable=False
            )
        if parsed.scheme == "repo":
            repo_root = Path(__file__).resolve().parents[3]
            body_path = (repo_root / unquote(parsed.path).lstrip("/")).resolve()
            try:
                body_path.relative_to(repo_root)
            except ValueError as exc:
                raise NotificationDeliveryError(
                    "body_unavailable", "notification repo body escapes repository root", retryable=False
                ) from exc
        else:
            body_path = Path(unquote(parsed.path))
        try:
            if body_path.stat().st_size > 1_000_000:
                raise NotificationDeliveryError(
                    "body_too_large", "notification body exceeds 1 MB", retryable=False
                )
            body = body_path.read_bytes()
        except NotificationDeliveryError:
            raise
        except OSError as exc:
            raise NotificationDeliveryError(
                "body_unavailable", "notification body artifact is unavailable", retryable=False
            ) from exc
    if len(body) > 1_000_000:
        raise NotificationDeliveryError(
            "body_too_large", "notification body exceeds 1 MB", retryable=False
        )
    actual_body_digest = "sha256:" + hashlib.sha256(body).hexdigest()
    if actual_body_digest != body_digest:
        raise NotificationDeliveryError(
            "body_digest_mismatch",
            "notification body does not match its immutable digest",
            retryable=False,
        )
    return body, actual_body_digest


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
        _body, actual_body_digest = _load_immutable_body(payload)
        self.calls.append(outbox_id)
        receipt = {
            "status": "sent",
            "provider": "feishu-mock",
            "provider_message_id": f"mock-msg-{outbox_id}",
            "thread_ref": payload.get("thread_ref"),
            "outbox_id": outbox_id,
            "payload_digest": payload_digest,
            "body_digest": actual_body_digest,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.receipts[outbox_id] = dict(receipt)
        return receipt


class FeishuLiveAdapter:
    """Live Feishu reply adapter using tenant credentials and stable dedup UUIDs.

    The original complaint binding is
    ``thread_ref=feishu:<chat_id>:<message_id>``. The final segment is used as
    Feishu's reply target and is never replaced by a model-generated
    destination. Provider success is accepted only when Feishu returns
    ``code=0`` and a real response message id.
    """

    # Feishu's reply UUID deduplicates for one hour.  The dispatcher refuses an
    # ambiguous resend after this window instead of risking a second message.
    idempotency_window_seconds = 3600

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str = OFFICIAL_FEISHU_BASE_URL,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("Feishu app_id and app_secret are required")
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client()
        self.receipts: dict[str, dict[str, Any]] = {}
        self._tenant_token: str | None = None
        self._tenant_token_expires_at = 0.0

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
        channel = payload.get("channel")
        if (
            not isinstance(channel, str)
            or not channel.startswith("feishu:")
            or channel.startswith("feishu-mock")
        ):
            raise NotificationDeliveryError(
                "unsupported_channel",
                "Feishu live adapter only accepts explicitly marked feishu: channels",
                retryable=False,
            )
        thread_ref = payload.get("thread_ref")
        channel_parts = channel.split(":", 1)
        thread_parts = thread_ref.split(":", 2) if isinstance(thread_ref, str) else []
        if (
            len(channel_parts) != 2
            or not channel_parts[1]
            or len(thread_parts) != 3
            or thread_parts[0] != "feishu"
            or thread_parts[1] != channel_parts[1]
            or not thread_parts[2]
        ):
            raise NotificationDeliveryError(
                "invalid_thread_ref",
                "Feishu live reply requires thread_ref=feishu:<chat_id>:<message_id> matching channel",
                retryable=False,
            )
        message_id = thread_parts[2]
        body, actual_body_digest = _load_immutable_body(payload)
        try:
            text_body = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotificationDeliveryError(
                "body_invalid_utf8", "notification body must be UTF-8 text", retryable=False
            ) from exc
        token = self._tenant_access_token()
        dedup_uuid = "agentmed-" + hashlib.sha256(outbox_id.encode("utf-8")).hexdigest()[:32]
        try:
            response = self.client.post(
                f"{self.base_url}/open-apis/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "content": json.dumps({"text": text_body}, ensure_ascii=False),
                    "msg_type": "text",
                    "uuid": dedup_uuid,
                },
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise NotificationDeliveryError(
                "feishu_unavailable", str(exc), retryable=True
            ) from exc
        data = self._response_json(response, operation="reply")
        if response.status_code >= 400 or data.get("code") != 0:
            retryable = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
            # Feishu business errors commonly use HTTP 200. The delivery
            # outcome is then unknown, so the stable provider UUID makes a
            # conservative retry safe.
            if response.status_code < 400 and data.get("code") != 0:
                retryable = True
            raise NotificationDeliveryError(
                "feishu_reply_failed",
                f"Feishu reply failed status={response.status_code} code={data.get('code')}",
                retryable=retryable,
            )
        provider_message_id = (data.get("data") or {}).get("message_id")
        if not isinstance(provider_message_id, str) or not provider_message_id:
            raise NotificationDeliveryError(
                "feishu_receipt_invalid",
                "Feishu success response omitted message_id",
                retryable=True,
            )
        receipt = {
            "status": "sent",
            "provider": "feishu",
            "provider_origin": self.base_url,
            "provider_message_id": provider_message_id,
            "thread_ref": thread_ref,
            "outbox_id": outbox_id,
            "provider_idempotency_key": dedup_uuid,
            "payload_digest": payload_digest,
            "body_digest": actual_body_digest,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.receipts[outbox_id] = dict(receipt)
        return receipt

    def _tenant_access_token(self) -> str:
        import time

        now = time.monotonic()
        if self._tenant_token and now < self._tenant_token_expires_at:
            return self._tenant_token
        try:
            response = self.client.post(
                f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise NotificationDeliveryError(
                "feishu_auth_unavailable", str(exc), retryable=True
            ) from exc
        data = self._response_json(response, operation="auth")
        token = data.get("tenant_access_token")
        if (
            response.status_code >= 400
            or data.get("code") != 0
            or not isinstance(token, str)
            or not token
        ):
            retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
            raise NotificationDeliveryError(
                "feishu_auth_failed",
                f"Feishu auth failed status={response.status_code} code={data.get('code')}",
                retryable=retryable,
            )
        try:
            expires_in = max(60, int(data.get("expire", 7200)))
        except (TypeError, ValueError):
            expires_in = 7200
        self._tenant_token = token
        self._tenant_token_expires_at = now + max(30, expires_in - 60)
        return token

    def fetch_text_message(self, message_id: str) -> dict[str, Any]:
        """Fetch and normalize one exact original Feishu text message.

        Feishu's GET message API returns an items array.  We accept exactly one
        non-deleted text item whose identity equals the requested message id;
        every ambiguous/partial response fails closed.
        """

        if not isinstance(message_id, str) or not message_id.strip():
            raise NotificationDeliveryError(
                "invalid_thread_ref", "Feishu message_id is required", retryable=False
            )
        token = self._tenant_access_token()
        try:
            response = self.client.get(
                f"{self.base_url}/open-apis/im/v1/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            raise NotificationDeliveryError(
                "feishu_unavailable", str(exc), retryable=True
            ) from exc
        data = self._response_json(response, operation="message_get")
        items = (data.get("data") or {}).get("items")
        if response.status_code >= 400 or data.get("code") != 0:
            raise NotificationDeliveryError(
                "feishu_message_get_failed",
                f"Feishu message read failed status={response.status_code} code={data.get('code')}",
                retryable=(response.status_code in {408, 425, 429} or response.status_code >= 500),
            )
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise NotificationDeliveryError(
                "feishu_receipt_invalid",
                "Feishu message read must return exactly one item",
                retryable=False,
            )
        item = items[0]
        body = item.get("body") or {}
        try:
            content = json.loads(body.get("content", ""))
        except (TypeError, ValueError) as exc:
            raise NotificationDeliveryError(
                "feishu_receipt_invalid", "Feishu text body is invalid JSON", retryable=False
            ) from exc
        text = content.get("text") if isinstance(content, dict) else None
        sender = item.get("sender") or {}
        chat_id = item.get("chat_id")
        create_time = item.get("create_time")
        if (
            item.get("message_id") != message_id
            or item.get("msg_type") != "text"
            or item.get("deleted") is not False
            or not isinstance(chat_id, str)
            or not chat_id
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(sender, dict)
            or re.fullmatch(r"[1-9][0-9]{12}", str(create_time or "")) is None
        ):
            raise NotificationDeliveryError(
                "feishu_receipt_invalid",
                "Feishu message identity/type/content is incomplete or mismatched",
                retryable=False,
            )
        text_bytes = text.encode("utf-8")
        sender_digest = hashlib.sha256(
            json.dumps(sender, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return {
            "provider": "feishu",
            "provider_origin": self.base_url,
            "message_id": message_id,
            "channel": f"feishu:{chat_id}",
            "thread_ref": f"feishu:{chat_id}:{message_id}",
            "text": text,
            "text_digest": "sha256:" + hashlib.sha256(text_bytes).hexdigest(),
            "sender_ref": f"feishu-sender:{sender_digest[:24]}",
            "create_time": str(create_time),
        }

    @staticmethod
    def _response_json(response: httpx.Response, *, operation: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise NotificationDeliveryError(
                f"feishu_{operation}_invalid_response",
                "Feishu response was not JSON",
                retryable=True,
            ) from exc
        if not isinstance(data, dict):
            raise NotificationDeliveryError(
                f"feishu_{operation}_invalid_response",
                "Feishu response JSON was not an object",
                retryable=True,
            )
        return data
