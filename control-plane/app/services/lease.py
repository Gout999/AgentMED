"""Worker 领单租约 + fencing token（D-001: 60s 心跳续租）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import FencingCounter, Lease
from app.utils.ids import new_lease_id


class LeaseLost(Exception):
    """租约丢失或 fencing token 过期。"""


class LeaseConflict(Exception):
    """资源已被他人持有且未过期。"""


class LeaseService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def _next_fencing_token(self) -> int:
        """全局单调递增 fencing token。"""
        row = self.session.get(FencingCounter, 1)
        if row is None:
            row = FencingCounter(id=1, next_token=1)
            self.session.add(row)
            self.session.flush()
        token = int(row.next_token)
        row.next_token = token + 1
        self.session.flush()
        return token

    def claim(self, resource_id: str, owner_id: str) -> Lease:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self.settings.lease_ttl_seconds)
        existing = self.session.get(Lease, resource_id)

        if existing is not None:
            exp = existing.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now and existing.owner_id != owner_id:
                raise LeaseConflict(
                    f"resource {resource_id} held by {existing.owner_id} until {existing.expires_at}"
                )
            # 过期回收或同 owner 重领：发新 fencing token
            existing.owner_id = owner_id
            existing.fencing_token = self._next_fencing_token()
            existing.lease_id = new_lease_id()
            existing.acquired_at = now
            existing.expires_at = expires
            self.session.flush()
            return existing

        lease = Lease(
            resource_id=resource_id,
            owner_id=owner_id,
            fencing_token=self._next_fencing_token(),
            lease_id=new_lease_id(),
            acquired_at=now,
            expires_at=expires,
        )
        self.session.add(lease)
        self.session.flush()
        return lease

    def heartbeat(self, resource_id: str, owner_id: str, fencing_token: int) -> Lease:
        lease = self._require_active(resource_id, owner_id, fencing_token)
        now = datetime.now(timezone.utc)
        lease.expires_at = now + timedelta(seconds=self.settings.lease_ttl_seconds)
        self.session.flush()
        return lease

    def release(self, resource_id: str, owner_id: str, fencing_token: int) -> None:
        lease = self._require_active(resource_id, owner_id, fencing_token)
        self.session.delete(lease)
        self.session.flush()

    def check_fencing(self, resource_id: str, fencing_token: int) -> Lease:
        """校验 fencing token 仍为最新且未过期；否则 LEASE_LOST。"""
        lease = self.session.get(Lease, resource_id)
        if lease is None:
            raise LeaseLost(f"no lease for {resource_id}")
        now = datetime.now(timezone.utc)
        exp = lease.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            raise LeaseLost(f"lease expired for {resource_id}")
        if int(lease.fencing_token) != int(fencing_token):
            raise LeaseLost(
                f"stale fencing token for {resource_id}: got {fencing_token}, current {lease.fencing_token}"
            )
        return lease

    def is_expired(self, resource_id: str) -> bool:
        lease = self.session.get(Lease, resource_id)
        if lease is None:
            return True
        now = datetime.now(timezone.utc)
        exp = lease.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp <= now

    def reclaim_expired(self, resource_id: str) -> Optional[Lease]:
        """返回过期 lease（若存在），不删除——claim 时会覆盖。"""
        lease = self.session.get(Lease, resource_id)
        if lease is None:
            return None
        if self.is_expired(resource_id):
            return lease
        return None

    def _require_active(self, resource_id: str, owner_id: str, fencing_token: int) -> Lease:
        lease = self.check_fencing(resource_id, fencing_token)
        if lease.owner_id != owner_id:
            raise LeaseLost(f"owner mismatch: {owner_id} vs {lease.owner_id}")
        return lease
