"""Quality API v2 客户端（按 contracts/quality-api/openapi.yaml）。

写面：stage / canary / promote / rollback（If-Match + Idempotency-Key + 异步 operation）。
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

import httpx

from app.utils.jcs import jcs_subset, sha256_hex


class QualityAPIError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 0, details: Optional[dict] = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(f"{code}: {message}")


class QualityClientProtocol(Protocol):
    def get_versionset(self, versionset_id: str) -> dict[str, Any]: ...
    def get_status(self, versionset_id: str) -> dict[str, Any]: ...
    def list_versionsets(
        self, *, status: Optional[str] = None, limit: int = 50
    ) -> dict[str, Any]: ...
    def stage(
        self, versionset_id: str, *, if_match: str, idempotency_key: str, expected_revision: Optional[int] = None
    ) -> dict[str, Any]: ...
    def canary(
        self,
        versionset_id: str,
        percent: int,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]: ...
    def promote(
        self,
        versionset_id: str,
        expected_active_digest: str,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]: ...
    def rollback(
        self,
        versionset_id: str,
        rollback_to: str,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]: ...
    def get_operation(self, operation_id: str) -> dict[str, Any]: ...


class QualityAPIClient:
    """HTTP 客户端（生产调 demo-app）。"""

    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self, *, if_match: Optional[str] = None, idempotency_key: Optional[str] = None) -> dict[str, str]:
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if if_match is not None:
            h["If-Match"] = if_match if if_match.startswith('"') else f'"{if_match}"'
        if idempotency_key is not None:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise QualityAPIError("network_error", str(exc), status_code=0) from exc

        if resp.status_code == 410:
            raise QualityAPIError("operation_expired", resp.text, status_code=410)
        if resp.status_code == 409:
            body = _safe_json(resp)
            raise QualityAPIError(
                "revision_conflict",
                body.get("error", {}).get("message", "revision conflict"),
                status_code=409,
                details=body.get("error", {}).get("details"),
            )
        if resp.status_code == 412:
            raise QualityAPIError("precondition_failed", resp.text, status_code=412)
        if resp.status_code >= 400:
            body = _safe_json(resp)
            code = body.get("error", {}).get("code", "http_error")
            msg = body.get("error", {}).get("message", resp.text)
            raise QualityAPIError(code, msg, status_code=resp.status_code, details=body.get("error", {}).get("details"))
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def get_versionset(self, versionset_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/versionsets/{versionset_id}", headers=self._headers())

    def list_versionsets(self, *, status: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        return self._request("GET", "/v2/versionsets", params=params, headers=self._headers())

    def get_status(self, versionset_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/versionsets/{versionset_id}/status", headers=self._headers())

    def stage(
        self,
        versionset_id: str,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return self._request(
            "POST",
            f"/v2/versionsets/{versionset_id}/stage",
            headers=self._headers(if_match=if_match, idempotency_key=idempotency_key),
            json=body or None,
        )

    def canary(
        self,
        versionset_id: str,
        percent: int,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"percent": percent}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return self._request(
            "POST",
            f"/v2/versionsets/{versionset_id}/canary",
            headers=self._headers(if_match=if_match, idempotency_key=idempotency_key),
            json=body,
        )

    def promote(
        self,
        versionset_id: str,
        expected_active_digest: str,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"expected_active_digest": expected_active_digest}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return self._request(
            "POST",
            f"/v2/versionsets/{versionset_id}/promote",
            headers=self._headers(if_match=if_match, idempotency_key=idempotency_key),
            json=body or None,
        )

    def rollback(
        self,
        versionset_id: str,
        rollback_to: str,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"rollback_to": rollback_to}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return self._request(
            "POST",
            f"/v2/versionsets/{versionset_id}/rollback",
            headers=self._headers(if_match=if_match, idempotency_key=idempotency_key),
            json=body,
        )

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/operations/{operation_id}", headers=self._headers())


def _safe_json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {}


# ---------- Fake（integration 无 demo-app 时使用） ----------


@dataclass
class _VS:
    versionset_id: str
    status: str = "draft"
    revision: int = 1
    digest: str = ""
    canary_percent: int = 0
    content: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Op:
    operation_id: str
    status: str = "succeeded"
    versionset_id: str = ""
    kind: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=24)
    )


class FakeQualityClient:
    """内存版 Quality API，行为对齐 openapi 写面语义（供控制面 integration）。"""

    def __init__(
        self,
        *,
        fail_next: Optional[str] = None,
        unknown_ops: bool = False,
        defer_effects: bool = False,
    ):
        self._vs: dict[str, _VS] = {}
        self._ops: dict[str, _Op] = {}
        self._idem: dict[str, str] = {}  # idempotency_key → operation_id
        self.fail_next = fail_next  # network | timeout | 410
        self.unknown_ops = unknown_ops
        self.defer_effects = defer_effects
        self.call_log: list[str] = []

    def seed_versionset(
        self,
        versionset_id: str = "vs_demo001fixedversionset01",
        status: str = "draft",
        revision: int = 1,
        digest: str = "sha256:" + "a" * 64,
    ) -> dict[str, Any]:
        self._vs[versionset_id] = _VS(
            versionset_id=versionset_id, status=status, revision=revision, digest=digest
        )
        return self.get_versionset(versionset_id)

    def get_versionset(self, versionset_id: str) -> dict[str, Any]:
        vs = self._require(versionset_id)
        return {
            "versionset_id": vs.versionset_id,
            "status": vs.status,
            "revision": vs.revision,
            "digest": vs.digest,
            "canary_percent": vs.canary_percent,
        }

    def get_status(self, versionset_id: str) -> dict[str, Any]:
        vs = self._require(versionset_id)
        result = {
            **self.get_versionset(versionset_id),
            "is_active": vs.status == "active",
            "history": [dict(item) for item in vs.history],
        }
        if vs.status == "canary":
            result["canary"] = {"percent": vs.canary_percent}
        return result

    def list_versionsets(self, *, status: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
        if self.fail_next in ("network", "timeout"):
            reason = self.fail_next
            self.fail_next = None
            raise QualityAPIError("network_error", f"simulated {reason}", status_code=0)
        rows = [self.get_versionset(vs_id) for vs_id in self._vs]
        if status:
            rows = [r for r in rows if r["status"] == status]
        rows.sort(key=lambda r: r["versionset_id"])
        return {"items": rows[:limit], "next_cursor": None}

    def stage(self, versionset_id: str, *, if_match: str, idempotency_key: str, expected_revision: Optional[int] = None) -> dict[str, Any]:
        return self._lifecycle(versionset_id, "stage", if_match, idempotency_key, expected_revision)

    def canary(
        self,
        versionset_id: str,
        percent: int,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._lifecycle(
            versionset_id, "canary", if_match, idempotency_key, expected_revision, percent=percent
        )

    def promote(
        self,
        versionset_id: str,
        expected_active_digest: str,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._lifecycle(
            versionset_id,
            "promote",
            if_match,
            idempotency_key,
            expected_revision,
            expected_active_digest=expected_active_digest,
        )

    def rollback(
        self,
        versionset_id: str,
        rollback_to: str,
        *,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._lifecycle(
            versionset_id, "rollback", if_match, idempotency_key, expected_revision, rollback_to=rollback_to
        )

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        if self.fail_next == "410":
            self.fail_next = None
            raise QualityAPIError("operation_expired", "purged", status_code=410)
        op = self._ops.get(operation_id)
        if op is None:
            raise QualityAPIError("not_found", "operation not found", status_code=404)
        now = datetime.now(timezone.utc)
        if op.expires_at < now:
            raise QualityAPIError("operation_expired", "purged", status_code=410)
        return {
            "operation_id": op.operation_id,
            "status": op.status,
            "versionset_id": op.versionset_id,
            "kind": op.kind,
            "result": op.result,
        }

    def _lifecycle(
        self,
        versionset_id: str,
        kind: str,
        if_match: str,
        idempotency_key: str,
        expected_revision: Optional[int],
        **extra: Any,
    ) -> dict[str, Any]:
        self.call_log.append(kind)
        if self.fail_next == "network":
            self.fail_next = None
            raise QualityAPIError("network_error", "simulated network failure", status_code=0)
        if self.fail_next == "timeout":
            self.fail_next = None
            raise QualityAPIError("network_error", "timeout", status_code=0)

        if idempotency_key in self._idem:
            op_id = self._idem[idempotency_key]
            existing = self._ops[op_id]
            replay_revision = self._parse_match(if_match, expected_revision)
            if (
                existing.versionset_id != versionset_id
                or existing.kind != kind
                or existing.request != {"expected_revision": replay_revision, **extra}
            ):
                raise QualityAPIError(
                    "validation_failed",
                    "idempotency_key reused with different lifecycle parameters",
                    422,
                    details={"subcode": "idempotency_key_conflict"},
                )
            return self.get_operation(op_id)

        vs = self._require(versionset_id)
        rev = self._parse_match(if_match, expected_revision)
        if rev is None:
            raise QualityAPIError("precondition_failed", "If-Match or expected_revision required", 412)
        if rev != vs.revision:
            raise QualityAPIError(
                "revision_conflict",
                f"expected {rev}, current {vs.revision}",
                409,
                details={"expected_revision": rev, "current_revision": vs.revision},
            )

        op_id = f"op_{uuid.uuid4().hex[:16]}"
        op = _Op(
            operation_id=op_id,
            status="pending" if (self.unknown_ops or self.defer_effects) else "succeeded",
            versionset_id=versionset_id,
            kind=kind,
            request={"expected_revision": rev, **extra},
        )
        if not self.defer_effects:
            op.result = self._apply_transition(vs, kind, op_id, extra)
            op.applied = True
        self._ops[op_id] = op
        self._idem[idempotency_key] = op_id
        return self.get_operation(op_id)

    def _apply_transition(
        self,
        vs: _VS,
        kind: str,
        operation_id: str,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply one fake transition and record the same receipt identity as Quality API."""

        from_status = vs.status
        restored_digest: Optional[str] = None
        if kind == "stage":
            if vs.status != "draft":
                raise QualityAPIError("validation_failed", f"cannot stage from {vs.status}", 422)
            vs.status = "staged"
        elif kind == "canary":
            if vs.status not in ("staged", "canary"):
                raise QualityAPIError("validation_failed", f"cannot canary from {vs.status}", 422)
            vs.status = "canary"
            vs.canary_percent = int(extra.get("percent", 5))
        elif kind == "promote":
            if vs.status not in ("canary", "staged"):
                raise QualityAPIError("validation_failed", f"cannot promote from {vs.status}", 422)
            active_rows = [
                other
                for other in self._vs.values()
                if other.versionset_id != vs.versionset_id and other.status == "active"
            ]
            active = active_rows[0] if len(active_rows) == 1 else None
            expected_active_digest = extra.get("expected_active_digest")
            if active is None or active.digest != expected_active_digest:
                raise QualityAPIError(
                    "revision_conflict",
                    "active VersionSet changed after promote approval",
                    409,
                    details={
                        "expected_active_digest": expected_active_digest,
                        "current_active_digest": active.digest if active is not None else None,
                        "current_active_count": len(active_rows),
                    },
                )
            for other in self._vs.values():
                if other.versionset_id != vs.versionset_id and other.status == "active":
                    other_from = other.status
                    other.status = "superseded"
                    other.revision += 1
                    other.history.append(
                        {"from": other_from, "to": "superseded", "operation_id": operation_id}
                    )
            vs.status = "active"
            vs.canary_percent = 100
        elif kind == "rollback":
            if vs.status not in ("canary", "active"):
                raise QualityAPIError("validation_failed", f"cannot rollback from {vs.status}", 422)
            rollback_to = extra.get("rollback_to", "previous")
            if rollback_to == "previous":
                desired_status = "active" if vs.status == "canary" else "superseded"
                target = next(
                    (
                        other
                        for other in reversed(list(self._vs.values()))
                        if other.versionset_id != vs.versionset_id and other.status == desired_status
                    ),
                    None,
                )
            else:
                target = next(
                    (
                        other
                        for other in self._vs.values()
                        if other.versionset_id != vs.versionset_id and other.digest == rollback_to
                    ),
                    None,
                )
            if target is None:
                raise QualityAPIError(
                    "validation_failed",
                    "approved rollback target digest is unavailable",
                    422,
                )
            vs.status = "rolled_back"
            vs.canary_percent = 0
            if target.status != "active":
                target.status = "active"
                target.revision += 1
            target.canary_percent = 100
            restored_digest = target.digest

        vs.revision += 1
        vs.history.append(
            {"from": from_status, "to": vs.status, "operation_id": operation_id}
        )
        return {
            "revision": vs.revision,
            "status": vs.status,
            "canary_percent": vs.canary_percent,
            **({"restored_digest": restored_digest} if restored_digest else {}),
        }

    def complete_pending(self, operation_id: str, status: str = "succeeded") -> None:
        op = self._ops[operation_id]
        if status == "succeeded" and not op.applied:
            vs = self._require(op.versionset_id)
            expected_revision = op.request.get("expected_revision")
            if vs.revision != expected_revision:
                raise QualityAPIError(
                    "revision_conflict",
                    f"expected {expected_revision}, current {vs.revision}",
                    409,
                )
            extra = {key: value for key, value in op.request.items() if key != "expected_revision"}
            op.result = self._apply_transition(vs, op.kind, op.operation_id, extra)
            op.applied = True
        op.status = status

    def _require(self, versionset_id: str) -> _VS:
        if versionset_id not in self._vs:
            raise QualityAPIError("not_found", f"versionset {versionset_id} not found", 404)
        return self._vs[versionset_id]

    @staticmethod
    def _parse_match(if_match: str, expected_revision: Optional[int]) -> Optional[int]:
        if if_match:
            s = if_match.strip().strip('"')
            try:
                return int(s)
            except ValueError:
                return expected_revision
        return expected_revision
