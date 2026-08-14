"""Case Controller REST API。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    get_app_settings,
    get_db_session,
    require_internal_write,
    require_principal_worker,
)
from app.config import Settings
from app.services.audit import AuditWriteError
from app.services.b1_fixture import B1FixtureError, load_b1_complaint_fixture
from app.services.case_service import CaseService, CaseServiceError
from app.notifications.adapters import (
    OFFICIAL_FEISHU_BASE_URL,
    FeishuLiveAdapter,
    NotificationDeliveryError,
)
from app.services.outbox_relay import OutboxDispatcher, notification_adapter_from_settings
from app.models.tables import Aggregate

router = APIRouter(tags=["cases"])


class ComplaintIn(BaseModel):
    source: str = Field(..., description="webhook | poll")
    text: str
    external_id: Optional[str] = None
    channel: str = "feishu-mock:default:"
    thread_ref: Optional[str] = None
    complainant_ref: str = "anon"
    attachments: list[str] = Field(default_factory=list)
    app_ref: str = "demo-app"
    title: Optional[str] = None
    auto_open: bool = True


class ClaimIn(BaseModel):
    worker_id: str


class HeartbeatIn(BaseModel):
    worker_id: str
    fencing_token: int


class LeaseCheckIn(BaseModel):
    worker_id: str
    fencing_token: int


class SuggestionIn(BaseModel):
    worker_id: str
    fencing_token: int
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class FeishuComplaintIn(BaseModel):
    app_ref: str = "demo-app:b1-live"
    title: str = "B1 live prompt regression"
    auto_open: bool = True
    demo_fault_injection_id: Optional[str] = Field(default=None, min_length=8, max_length=128)


class TransitionIn(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_revision: Optional[int] = None
    fencing_token: Optional[int] = None
    actor: str = "system"
    guard: Optional[str] = None


class ReopenIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


def _feishu_live_adapter(request: Request, settings: Settings) -> FeishuLiveAdapter:
    adapter = getattr(request.app.state, "notification_adapter", None)
    if adapter is None:
        adapter = notification_adapter_from_settings(settings)
        request.app.state.notification_adapter = adapter
    if not isinstance(adapter, FeishuLiveAdapter):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "feishu_live_not_configured",
                "message": "Feishu live inbound adapter is not configured",
            },
        )
    return adapter


@router.get("/v1/inbox/feishu/messages/{message_id}")
def get_feishu_inbound_message(
    message_id: str,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    """Fetch the exact original Feishu text through the credentialed adapter.

    The orchestration client never receives Feishu credentials; it receives a
    normalized message identity/content digest from this deterministic boundary.
    """

    adapter = _feishu_live_adapter(request, settings)
    try:
        return adapter.fetch_text_message(message_id)
    except NotificationDeliveryError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.post("/v1/inbox/feishu/messages/{message_id}/complaint")
def file_feishu_complaint(
    message_id: str,
    body: FeishuComplaintIn,
    request: Request,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    """Fetch and file one Feishu message atomically at the control boundary."""

    injection_receipt: dict[str, Any] | None = None
    if body.demo_fault_injection_id is not None:
        injection = session.get(
            Aggregate,
            ("demo_fault_injection", body.demo_fault_injection_id),
        )
        payload = injection.payload if injection is not None else {}
        receipt = (payload or {}).get("receipt") or {}
        if (
            injection is None
            or injection.state != "COMPLETED"
            or (payload or {}).get("fault_id") != "B1"
            or receipt.get("fault_id") != "B1"
            or receipt.get("fault_versionset_id")
            != (payload or {}).get("fault_versionset_id")
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "demo_fault_injection_binding_invalid",
                    "message": "complaint must reference one completed B1 injection intent",
                },
            )
        injection_receipt = receipt

    adapter = _feishu_live_adapter(request, settings)
    try:
        inbound = adapter.fetch_text_message(message_id)
        text = inbound.get("text")
        expected_digest = (
            "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
            if isinstance(text, str)
            else None
        )
        if (
            inbound.get("provider") != "feishu"
            or inbound.get("provider_origin") != adapter.base_url
            or inbound.get("message_id") != message_id
            or inbound.get("thread_ref")
            != f"{inbound.get('channel')}:{message_id}"
            or inbound.get("text_digest") != expected_digest
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "feishu_message_binding_invalid",
                    "message": "Feishu adapter response is not bound to the requested message",
                },
            )
        if injection_receipt is not None:
            if inbound.get("provider_origin") != OFFICIAL_FEISHU_BASE_URL:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "b1_feishu_provider_origin_invalid",
                        "message": "live B1 evidence requires the official Feishu API origin",
                    },
                )
            try:
                fixture = load_b1_complaint_fixture()
            except B1FixtureError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"code": "b1_fixture_unavailable", "message": str(exc)},
                ) from exc
            if inbound.get("text") != fixture.text or inbound.get("text_digest") != fixture.text_digest:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "b1_complaint_fixture_mismatch",
                        "message": "Feishu complaint does not match the repository-owned B1 fixture",
                    },
                )
            try:
                create_time = str(inbound.get("create_time") or "")
                if re.fullmatch(r"[1-9][0-9]{12}", create_time) is None:
                    raise ValueError("create_time is not a millisecond epoch")
                provider_created_at = datetime.fromtimestamp(
                    int(create_time) / 1000,
                    timezone.utc,
                )
                injected_at = datetime.fromisoformat(
                    str(injection_receipt.get("injected_at") or "").replace("Z", "+00:00")
                )
                if injected_at.tzinfo is None:
                    raise ValueError("injected_at is timezone-naive")
            except (OverflowError, OSError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "b1_complaint_chronology_invalid",
                        "message": "B1 injection/message timestamps are invalid",
                    },
                ) from exc
            if provider_created_at <= injected_at.astimezone(timezone.utc):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "b1_complaint_chronology_invalid",
                        "message": "Feishu complaint must be created after the bound B1 injection",
                    },
                )
        complaint = CaseService(session, settings).ingest_complaint(
            source="webhook",
            text=inbound["text"],
            external_id=inbound["message_id"],
            channel=inbound["channel"],
            thread_ref=inbound["thread_ref"],
            complainant_ref=inbound["sender_ref"],
            attachments=[f"feishu-text-digest:{inbound['text_digest']}"],
            app_ref=body.app_ref,
            title=body.title,
            auto_open=body.auto_open,
            demo_fault_injection_id=body.demo_fault_injection_id,
            provider_origin=inbound["provider_origin"],
            provider_create_time=inbound["create_time"],
            source_text_digest=inbound["text_digest"],
        )
    except NotificationDeliveryError as exc:
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except AuditWriteError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "audit_unavailable", "message": str(exc)},
        ) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return {
        **complaint,
        "inbound": {
            key: inbound[key]
            for key in (
                "provider",
                "provider_origin",
                "message_id",
                "channel",
                "thread_ref",
                "text_digest",
                "sender_ref",
                "create_time",
            )
        },
        "demo_fault_injection_id": body.demo_fault_injection_id,
    }


def _raise(exc: CaseServiceError) -> None:
    status = {
        "not_found": 404,
        "validation_failed": 422,
        "pii_redaction_failed": 422,
        "illegal_transition": 422,
        "revision_conflict": 409,
        "lease_conflict": 409,
        "lease_lost": 409,
        "idempotency_conflict": 409,
        "forbidden_transition": 403,
    }.get(exc.code, 400)
    body: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message, **exc.extra}}
    if exc.code == "illegal_transition" and "current_state" in exc.extra:
        body["error"]["current_state"] = exc.extra["current_state"]
    raise HTTPException(status_code=status, detail=body["error"])


@router.post("/v1/complaints")
def post_complaint(
    body: ComplaintIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    try:
        result = svc.ingest_complaint(
            source=body.source,
            text=body.text,
            external_id=body.external_id,
            channel=body.channel,
            thread_ref=body.thread_ref,
            complainant_ref=body.complainant_ref,
            attachments=body.attachments,
            app_ref=body.app_ref,
            title=body.title,
            auto_open=body.auto_open,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return result


@router.get("/v1/cases")
def list_cases(
    state: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    return svc.list_cases(state=state, limit=limit, cursor=cursor)


@router.get("/v1/cases/{case_id}")
def get_case(
    case_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    try:
        return svc.get_case(case_id)
    except CaseServiceError as exc:
        _raise(exc)
    return {}  # pragma: no cover


@router.post("/v1/cases/{case_id}/claim")
def claim_case(
    case_id: str,
    body: ClaimIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    require_principal_worker(_authority, body.worker_id)
    svc = CaseService(session, settings)
    try:
        return svc.claim(case_id, body.worker_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/cases/{case_id}/heartbeat")
def heartbeat_case(
    case_id: str,
    body: HeartbeatIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    require_principal_worker(_authority, body.worker_id)
    svc = CaseService(session, settings)
    try:
        return svc.heartbeat(case_id, body.worker_id, body.fencing_token)
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/cases/{case_id}/lease-check")
def check_case_lease(
    case_id: str,
    body: LeaseCheckIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    """Authorize a worker write without extending or mutating the lease."""

    require_principal_worker(_authority, body.worker_id)
    svc = CaseService(session, settings)
    try:
        return svc.validate_active_lease(case_id, body.worker_id, body.fencing_token)
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/cases/{case_id}/suggestions")
def submit_case_suggestion(
    case_id: str,
    body: SuggestionIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    """Validate the active lease and persist the suggestion in one transaction."""

    require_principal_worker(_authority, body.worker_id)
    svc = CaseService(session, settings)
    try:
        return svc.submit_suggestion(
            case_id=case_id,
            worker_id=body.worker_id,
            fencing_token=body.fencing_token,
            idempotency_key=body.idempotency_key,
            kind=body.kind,
            payload=body.payload,
            evidence_refs=body.evidence_refs,
        )
    except AuditWriteError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "audit_unavailable", "message": str(exc)},
        ) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/cases/{case_id}/reclaim")
def reclaim_case(
    case_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    """lease 过期回收（看门狗/人工触发）。"""
    svc = CaseService(session, settings)
    try:
        result = svc.reclaim_if_expired(case_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    if result is None:
        raise HTTPException(status_code=409, detail={"code": "not_expired", "message": "lease not expired or not dispatched"})
    return result


@router.post("/v1/cases/{case_id}/reopen")
def reopen_case(
    case_id: str,
    body: ReopenIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    """人工结论落库：quality-officer 复核 escalated 案例后重开重派。

    重开只把案例送回 OPEN（清除 escalation 投影），派单仍走 claim
    （新 lease + 新 fencing token），不在此处隐式派单。
    """
    svc = CaseService(session, settings)
    try:
        return svc.reopen(case_id, reason=body.reason, actor=_authority)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/cases/{case_id}/transitions")
def transition_case(
    case_id: str,
    body: TransitionIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    if _authority.startswith("mcp:") and body.event_type != "case.escalated":
        raise HTTPException(
            status_code=403,
            detail={"code": "forbidden", "message": "MCP transition authority is limited to case.escalated"},
        )
    svc = CaseService(session, settings)
    try:
        return svc.transition(
            case_id,
            body.event_type,
            body.payload,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            actor=_authority if _authority.startswith("mcp:") else body.actor,
            guard=body.guard,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/outbox/relay")
def relay_outbox(
    request: Request,
    limit: int = 50,
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "dispatcher_unavailable", "message": "session factory is unavailable"},
        )
    adapter = getattr(request.app.state, "notification_adapter", None)
    return OutboxDispatcher(
        factory,
        settings,
        notification_adapter=adapter,
        worker_id="api:outbox-dispatcher",
    ).dispatch_batch(limit=min(max(limit, 1), 500))
