"""ApprovalGrant 校验（spec §5.2 / §11.1 重写；zeroops 静态 token 方案废弃）。

防掉包防重放四件套：
1. hash 绑定：发布前重算 presented WorkOrder hash，与 grant.workorder_hash 逐字节比对 → 不符 APPROVAL_MISMATCH。
2. nonce 一次性：PG 原子 UPDATE（WHERE nonce_consumed=false）消费；复用 → APPROVAL_REPLAYED。
3. expiry：now ≥ expiry → APPROVAL_EXPIRED；TTL 30min（D-001 #10）在 grant 时强制。
4. proof + audit URI：proof.method=server_recorded，proof.ref=audit://… 必须可回溯。

本模块为库，被各 server 与控制面复用；表见 mcp_approval_grants。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from common.audit import AuditService
from common.config import Settings, get_settings
from common.errors import (
    APPROVAL_EXPIRED,
    APPROVAL_MISMATCH,
    APPROVAL_REPLAYED,
    McpError,
    VALIDATION_FAILED,
)
from common.jcs import workorder_hash
from common.tables import ApprovalGrantRow


class ApprovalError(McpError):
    pass


def _parse_dt(value: str | _dt.datetime) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        value = str(value).replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


class ApprovalService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.audit = AuditService(session, self.settings)

    # ---------- grant 登记 ----------

    def grant(
        self,
        *,
        approval_id: str,
        workorder_id: str,
        workorder_hash: str,
        nonce: str,
        expiry: str,
        approver: dict[str, Any],
        decision: str,
        decided_at: str,
        proof: Optional[dict[str, Any]] = None,
        audit_uri: str = "",
    ) -> dict[str, Any]:
        """登记 ApprovalGrant（Q7 MVP：server_recorded proof + audit URI）。"""
        if not isinstance(approver, dict) or approver.get("type") != "human":
            raise McpError(VALIDATION_FAILED, "approver.type must be human")
        if decision not in ("approved", "rejected"):
            raise McpError(VALIDATION_FAILED, "decision must be approved|rejected")
        if len(workorder_hash) != 64 or any(c not in "0123456789abcdef" for c in workorder_hash.lower()):
            raise McpError(VALIDATION_FAILED, "workorder_hash must be 64 lowercase hex")

        now = _dt.datetime.now(_dt.timezone.utc)
        exp = _parse_dt(expiry)
        # TTL 强制：expiry 不得超过 now + 30min（D-001 #10）
        ttl = _dt.timedelta(minutes=self.settings.approval_ttl_minutes)
        if exp > now + ttl:
            raise McpError(VALIDATION_FAILED, f"expiry exceeds TTL {self.settings.approval_ttl_minutes}min")
        if exp <= now:
            raise McpError(APPROVAL_EXPIRED, "approval already expired at grant time")

        existing_nonce = self.session.scalar(
            select(ApprovalGrantRow).where(ApprovalGrantRow.nonce == nonce)
        )
        if existing_nonce is not None:
            raise McpError(
                APPROVAL_REPLAYED,
                f"nonce already used by approval {existing_nonce.approval_id}",
            )

        # Q7：server_recorded proof + audit URI（MVP；HMAC 列 Phase 3 硬化项）
        proof = proof or {
            "method": "server_recorded",
            "ref": audit_uri or f"audit://mcp/approval/{approval_id}",
        }
        if proof.get("method") != "server_recorded":
            raise McpError(VALIDATION_FAILED, "proof.method must be server_recorded (MVP)")
        audit_uri = audit_uri or str(proof.get("ref") or "")
        if not audit_uri.startswith("audit://"):
            raise McpError(VALIDATION_FAILED, "proof.ref must be an audit URI (audit://...)")

        row = ApprovalGrantRow(
            approval_id=approval_id,
            workorder_id=workorder_id,
            workorder_hash=workorder_hash.lower(),
            nonce=nonce,
            status="pending" if decision == "approved" else "rejected",
            decision=decision,
            approver=approver,
            expiry=exp,
            decided_at=_parse_dt(decided_at),
            nonce_consumed=False,
            proof=proof,
            audit_uri=audit_uri,
        )
        self.session.add(row)
        self.audit.record(
            actor=approver.get("identity", "human"),
            action="workorder.approve" if decision == "approved" else "workorder.reject",
            target=approval_id,
            params={"workorder_hash": workorder_hash, "decision": decision},
            result="success",
            evidence_refs={"proof": proof},
        )
        self.session.flush()
        return {
            "approval_id": approval_id,
            "status": row.status,
            "nonce_consumed": False,
            "proof": proof,
        }

    # ---------- 发布前校验（纯校验，不消费） ----------

    def validate_for_release(
        self,
        *,
        approval_id: str,
        presented_workorder: dict[str, Any],
    ) -> dict[str, Any]:
        """发布前校验：hash 绑定 + nonce 未消费 + expiry + proof。返回 verdict。"""
        row = self.session.get(ApprovalGrantRow, approval_id)
        if row is None:
            raise McpError(APPROVAL_MISMATCH, f"approval {approval_id} not found")

        if row.status == "consumed" or row.nonce_consumed:
            raise McpError(APPROVAL_REPLAYED, "approval nonce already consumed")
        if row.status == "rejected":
            raise McpError(APPROVAL_MISMATCH, "approval was rejected")

        now = _dt.datetime.now(_dt.timezone.utc)
        exp = row.expiry
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=_dt.timezone.utc)
        if exp <= now:
            self._mark_expired(row)
            raise McpError(APPROVAL_EXPIRED, "approval TTL exceeded")

        # 审批即批 hash：重算 presented WorkOrder hash 逐字节比对
        try:
            recomputed = workorder_hash(presented_workorder)
        except (ValueError, TypeError) as exc:
            raise McpError(VALIDATION_FAILED, f"hash compute failed: {exc}") from exc
        if recomputed != row.workorder_hash:
            raise McpError(
                APPROVAL_MISMATCH,
                f"workorder hash drift: declared={row.workorder_hash} recomputed={recomputed}",
            )

        # nonce 必须与 WorkOrder 原样一致（防伪造 grant）
        presented_nonce = presented_workorder.get("nonce", "")
        if presented_nonce != row.nonce:
            raise McpError(APPROVAL_MISMATCH, "presented nonce does not match approval nonce")

        # proof + audit URI
        proof = row.proof or {}
        if proof.get("method") != "server_recorded":
            raise McpError(APPROVAL_MISMATCH, "proof.method must be server_recorded")
        if not row.audit_uri.startswith("audit://"):
            raise McpError(APPROVAL_MISMATCH, "audit URI missing or invalid")

        return {
            "valid": True,
            "approval_id": approval_id,
            "workorder_hash": row.workorder_hash,
            "nonce": row.nonce,
            "expiry": row.expiry.isoformat(),
            "proof": proof,
            "audit_uri": row.audit_uri,
        }

    # ---------- nonce 原子消费（一次性） ----------

    def consume_nonce(self, *, approval_id: str, nonce: str) -> bool:
        """PG 原子消费：UPDATE ... WHERE nonce=:n AND nonce_consumed=false AND status='pending'。

        并发下仅一个事务 rowcount=1，其余 rowcount=0 → 复用即拒（APPROVAL_REPLAYED）。
        """
        now = _dt.datetime.now(_dt.timezone.utc)
        res = self.session.execute(
            update(ApprovalGrantRow)
            .where(ApprovalGrantRow.approval_id == approval_id)
            .where(ApprovalGrantRow.nonce == nonce)
            .where(ApprovalGrantRow.status == "pending")
            .where(ApprovalGrantRow.nonce_consumed.is_(False))
            .values(nonce_consumed=True, consumed_at=now, status="consumed")
        )
        self.session.flush()
        return bool(res.rowcount == 1)

    # ---------- 状态查询 ----------

    def get(self, approval_id: str) -> dict[str, Any]:
        row = self.session.get(ApprovalGrantRow, approval_id)
        if row is None:
            raise McpError(APPROVAL_MISMATCH, f"approval {approval_id} not found")
        now = _dt.datetime.now(_dt.timezone.utc)
        exp = row.expiry
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=_dt.timezone.utc)
        status = row.status
        if status == "pending" and exp <= now:
            status = "expired"
        return {
            "approval_id": row.approval_id,
            "workorder_id": row.workorder_id,
            "workorder_hash": row.workorder_hash,
            "nonce": row.nonce,
            "status": status,
            "decision": row.decision,
            "nonce_consumed": row.nonce_consumed,
            "expiry": row.expiry.isoformat(),
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "consumed_at": row.consumed_at.isoformat() if row.consumed_at else None,
            "approver": row.approver,
            "proof": row.proof,
            "audit_uri": row.audit_uri,
        }

    def _mark_expired(self, row: ApprovalGrantRow) -> None:
        if row.status == "pending":
            row.status = "expired"
            self.session.flush()
