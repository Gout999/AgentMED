"""Authenticated R2 application graph listing with authoritative revalidation."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, NoReturn

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.v5_tables import (
    AIApplication,
    DependencyEdge,
    Environment,
    SystemComponent,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.v5_models import (
    ApplicationListItem,
    ApplicationListResponse,
    ApplicationRecord,
    ComponentRecord,
    DependencyEdgeRecord,
    EnvironmentRecord,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.services.application_catalog import (
    ApplicationCatalogError,
    ApplicationCatalogService,
)
from app.utils.v4_integrity import canonical_digest, canonicalize


_CURSOR_PREFIX = "cur_"
_CURSOR_SIGNATURE_BYTES = hashlib.sha256().digest_size


class V5ApplicationListError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        workspace_id: str | None = None,
        audit_ref: str | None = None,
    ) -> None:
        self.code = code
        self.details: dict[str, object] = {}
        self.workspace_id = workspace_id
        self.audit_ref = audit_ref
        self.rollback_required = True
        super().__init__(code)


class V5ApplicationListReadDenial(V5ApplicationListError):
    """Audited zero-item denial safe for the HTTP boundary to commit."""

    def __init__(self, code: str, *, workspace_id: str, audit_ref: str) -> None:
        if code not in {
            "REQUEST_INVALID",
            "RESOURCE_NOT_FOUND",
            "SCOPE_FORBIDDEN",
            "VALIDATION_FAILED",
        }:
            raise ValueError("unsupported applications.list read denial")
        super().__init__(
            code,
            workspace_id=workspace_id,
            audit_ref=audit_ref,
        )
        self.rollback_required = False


class V5ApplicationListService:
    """List only graphs visible to the accepted public credential.

    Projection rows are never trusted directly.  Every lifecycle head is
    resolved through append-only history and every record is rebound to its
    authority receipt, source event, controller registration, and audit chain.
    """

    def __init__(
        self,
        session: Session,
        *,
        cursor_signing_key: str,
        audit_service: V4AuditService | None = None,
        clock=None,
    ) -> None:
        self.session = session
        self.cursor_key = cursor_signing_key.encode("utf-8")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit = audit_service or V4AuditService(session, clock=self.clock)
        self.catalog = ApplicationCatalogService(session, clock=self.clock)

    def list_applications(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        project_id: str | None,
        limit: int,
        cursor: str | None,
    ) -> ApplicationListResponse:
        if (
            "applications:read" not in principal.scopes
            or principal.requested_context.workspace_id != principal.workspace_id
            or principal.requested_context.required_scope != "applications:read"
        ):
            self._deny(
                principal=principal,
                request_id=request_id,
                code="SCOPE_FORBIDDEN",
                reason="scope",
            )
        if not self.cursor_key:
            raise V5ApplicationListError(
                "INTERNAL_ERROR", workspace_id=principal.workspace_id
            )
        if not 1 <= limit <= 100:
            self._deny(
                principal=principal,
                request_id=request_id,
                code="REQUEST_INVALID",
                reason="limit",
            )
        visible_projects = sorted(set(principal.project_ids))
        if project_id is None:
            self._deny(
                principal=principal,
                request_id=request_id,
                code="REQUEST_INVALID",
                reason="project_required",
            )
        if project_id not in visible_projects:
            self._deny(
                principal=principal,
                request_id=request_id,
                code="RESOURCE_NOT_FOUND",
                reason="project_visibility",
            )

        scope = self._cursor_scope(
            principal=principal,
            visible_projects=visible_projects,
            project_id=project_id,
            limit=limit,
        )
        after_id: str | None = None
        watermark_id: str | None
        if cursor is None:
            watermark_id = self._watermark(
                principal=principal,
                visible_projects=visible_projects,
                project_id=project_id,
            )
        else:
            try:
                decoded = self._decode_cursor(cursor)
            except ValueError:
                self._deny(
                    principal=principal,
                    request_id=request_id,
                    code="REQUEST_INVALID",
                    reason="cursor",
                )
            if decoded.get("scope") != scope:
                self._deny(
                    principal=principal,
                    request_id=request_id,
                    code="REQUEST_INVALID",
                    reason="cursor_scope",
                )
            watermark_id = decoded["watermark"]
            after_id = decoded["after"]

        rows = self._page_rows(
            principal=principal,
            visible_projects=visible_projects,
            project_id=project_id,
            watermark_id=watermark_id,
            after_id=after_id,
            limit=limit,
        )
        page = rows[:limit]
        has_more = len(rows) > limit
        try:
            items = [self._graph_item(row) for row in page]
        except (
            ApplicationCatalogError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise V5ApplicationListError(
                "INTERNAL_ERROR", workspace_id=principal.workspace_id
            ) from exc

        next_cursor = None
        if has_more and page and watermark_id is not None:
            next_cursor = self._encode_cursor(
                scope=scope,
                watermark=watermark_id,
                after=page[-1].application_id,
            )
        try:
            audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action="public.v5.applications.list",
                target="ai_application:list",
                params={
                    "request_id": request_id,
                    "project_id": project_id,
                    "limit": limit,
                    "cursor_present": cursor is not None,
                    "returned_application_ids": [
                        row.application_id for row in page
                    ],
                },
                result="success",
                trace_id=request_id,
            )
        except V4AuditUnavailable as exc:
            raise V5ApplicationListError(
                "AUDIT_UNAVAILABLE", workspace_id=principal.workspace_id
            ) from exc
        try:
            return ApplicationListResponse(
                schema_version="2.0",
                workspace_id=principal.workspace_id,
                request_id=request_id,
                audit_ref=audit.audit_ref,
                items=items,
                next_cursor=next_cursor,
            )
        except ValidationError as exc:
            raise V5ApplicationListError(
                "INTERNAL_ERROR",
                workspace_id=principal.workspace_id,
                audit_ref=audit.audit_ref,
            ) from exc

    def deny_invalid_query(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
    ) -> NoReturn:
        self._deny(
            principal=principal,
            request_id=request_id,
            code="VALIDATION_FAILED",
            reason="query_shape",
        )

    def _deny(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        code: str,
        reason: str,
    ) -> NoReturn:
        try:
            audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action="public.v5.applications.list",
                target="ai_application:list",
                params={
                    "request_id": request_id,
                    "denial_reason": reason,
                    "returned_items": 0,
                },
                result="denied",
                error_code=code,
                trace_id=request_id,
            )
        except V4AuditUnavailable as exc:
            raise V5ApplicationListError(
                "AUDIT_UNAVAILABLE", workspace_id=principal.workspace_id
            ) from exc
        raise V5ApplicationListReadDenial(
            code,
            workspace_id=principal.workspace_id,
            audit_ref=audit.audit_ref,
        )

    def _watermark(
        self,
        *,
        principal: AcceptedPrincipalContext,
        visible_projects: list[str],
        project_id: str,
    ) -> str | None:
        query = select(func.max(AIApplication.application_id)).where(
            AIApplication.workspace_id == principal.workspace_id,
            AIApplication.project_id.in_(visible_projects),
        )
        query = query.where(AIApplication.project_id == project_id)
        return self.session.scalar(query)

    def _page_rows(
        self,
        *,
        principal: AcceptedPrincipalContext,
        visible_projects: list[str],
        project_id: str,
        watermark_id: str | None,
        after_id: str | None,
        limit: int,
    ) -> list[AIApplication]:
        if watermark_id is None or not visible_projects:
            return []
        query = select(AIApplication).where(
            AIApplication.workspace_id == principal.workspace_id,
            AIApplication.project_id.in_(visible_projects),
            AIApplication.application_id <= watermark_id,
        )
        query = query.where(AIApplication.project_id == project_id)
        if after_id is not None:
            query = query.where(AIApplication.application_id > after_id)
        return list(
            self.session.scalars(
                query.order_by(AIApplication.application_id).limit(limit + 1)
            ).all()
        )

    def _graph_item(self, application: AIApplication) -> ApplicationListItem:
        application_record = self._lifecycle_record(
            row=application,
            kind="AI_APPLICATION",
            subject_id=application.application_id,
            model=ApplicationRecord,
        )
        environments = list(
            self.session.scalars(
                select(Environment)
                .where(
                    Environment.workspace_id == application.workspace_id,
                    Environment.application_id == application.application_id,
                )
                .order_by(Environment.environment_id)
            ).all()
        )
        components = list(
            self.session.scalars(
                select(SystemComponent)
                .where(
                    SystemComponent.workspace_id == application.workspace_id,
                    SystemComponent.application_id == application.application_id,
                )
                .order_by(SystemComponent.component_id)
            ).all()
        )
        edges = list(
            self.session.scalars(
                select(DependencyEdge)
                .where(
                    DependencyEdge.workspace_id == application.workspace_id,
                    DependencyEdge.application_id == application.application_id,
                )
                .order_by(DependencyEdge.edge_id)
            ).all()
        )
        return ApplicationListItem(
            application=application_record,
            environments=[
                self._plain_record(
                    row=row,
                    kind="ENVIRONMENT",
                    subject_id=row.environment_id,
                    subject_revision=row.revision,
                    model=EnvironmentRecord,
                )
                for row in environments
            ],
            system_components=[
                self._lifecycle_record(
                    row=row,
                    kind="SYSTEM_COMPONENT",
                    subject_id=row.component_id,
                    model=ComponentRecord,
                )
                for row in components
            ],
            dependency_edges=[
                self._plain_record(
                    row=row,
                    kind="DEPENDENCY_EDGE",
                    subject_id=row.edge_id,
                    subject_revision=1,
                    model=DependencyEdgeRecord,
                )
                for row in edges
            ],
        )

    def _lifecycle_record(self, *, row, kind: str, subject_id: str, model):
        if kind == "AI_APPLICATION":
            id_field = "application_id"
            scalar_fields = (
                "project_id",
                "slug",
                "display_name",
                "owner_principal_ids",
                "criticality",
                "data_classification",
                "governance_mode",
                "lifecycle_state",
            )
        else:
            id_field = "component_id"
            scalar_fields = (
                "application_id",
                "component_kind",
                "logical_name",
                "owner_principal_ids",
                "criticality",
                "data_classification",
                "permission_classification",
                "effect_classification",
                "dataset_role",
                "lifecycle_state",
            )
        envelope = self.catalog.verify_authoritative_record(
            row=row,
            subject_kind=kind,
            id_field=id_field,
            scalar_fields=scalar_fields,
            lifecycle_history=True,
        )
        return model.model_validate(envelope)

    def _plain_record(
        self,
        *,
        row,
        kind: str,
        subject_id: str,
        subject_revision: int | None,
        model,
    ):
        if kind == "ENVIRONMENT":
            id_field = "environment_id"
            scalar_fields = (
                "application_id",
                "logical_name",
                "risk_classification",
                "lifecycle_state",
            )
        else:
            id_field = "edge_id"
            scalar_fields = (
                "application_id",
                "from_component_id",
                "to_component_id",
                "relation",
                "required",
                "edge_digest",
            )
        envelope = self.catalog.verify_authoritative_record(
            row=row,
            subject_kind=kind,
            id_field=id_field,
            scalar_fields=scalar_fields,
            lifecycle_history=False,
        )
        return model.model_validate(envelope)

    def _cursor_scope(
        self,
        *,
        principal: AcceptedPrincipalContext,
        visible_projects: list[str],
        project_id: str,
        limit: int,
    ) -> str:
        return canonical_digest(
            {
                "workspace_id": principal.workspace_id,
                "principal_id": principal.principal_id,
                "visible_projects": visible_projects,
                "project_id": project_id,
                "limit": limit,
                "order": "application_id:asc",
            }
        )

    def _encode_cursor(self, *, scope: str, watermark: str, after: str) -> str:
        raw = canonicalize(
            {"version": 1, "scope": scope, "watermark": watermark, "after": after}
        )
        signature = hmac.new(self.cursor_key, raw, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode("ascii")
        token = _CURSOR_PREFIX + encoded
        if len(token) > 512:
            raise V5ApplicationListError("INTERNAL_ERROR")
        return token

    def _decode_cursor(self, cursor: str) -> dict[str, Any]:
        try:
            if not cursor.startswith(_CURSOR_PREFIX) or len(cursor) > 512:
                raise ValueError("cursor shape")
            encoded = cursor[len(_CURSOR_PREFIX) :]
            combined = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
            if len(combined) <= _CURSOR_SIGNATURE_BYTES:
                raise ValueError("cursor length")
            raw = combined[:-_CURSOR_SIGNATURE_BYTES]
            supplied = combined[-_CURSOR_SIGNATURE_BYTES:]
            expected = hmac.new(self.cursor_key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied, expected):
                raise ValueError("cursor signature")
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"version", "scope", "watermark", "after"}
                or payload["version"] != 1
                or not all(
                    isinstance(payload[field], str)
                    for field in ("scope", "watermark", "after")
                )
            ):
                raise ValueError("cursor payload")
            return payload
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid applications.list cursor") from exc


__all__ = [
    "V5ApplicationListError",
    "V5ApplicationListReadDenial",
    "V5ApplicationListService",
]
