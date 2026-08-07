"""审计服务：权威源=DB；写失败即拒业务（503）。"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Audit
from app.utils.ids import new_audit_id, new_trace_id
from app.utils.jcs import params_digest

logger = logging.getLogger(__name__)


class AuditWriteError(Exception):
    """审计写库失败 → 业务必须拒绝。"""


class AuditService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def record(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        params: Any,
        result: str = "success",
        error_code: Optional[str] = None,
        trace_id: Optional[str] = None,
        evidence_refs: Optional[dict[str, Any]] = None,
    ) -> Audit:
        if self.settings.audit_force_fail:
            raise AuditWriteError("AUDIT_FORCE_FAIL")

        audit = Audit(
            audit_id=new_audit_id(),
            ts=datetime.now(timezone.utc),
            actor=actor,
            action=action,
            target=target,
            params_digest=params_digest(params if params is not None else {}),
            result=result,
            error_code=error_code,
            trace_id=trace_id or new_trace_id(),
            evidence_refs=evidence_refs,
        )
        try:
            self.session.add(audit)
            self.session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.error("audit write failed action=%s target=%s", action, target)
            raise AuditWriteError(str(exc)) from exc

        # 导出物（失败不影响权威源；仅 best-effort）
        try:
            self._export_jsonl(audit)
        except Exception:  # noqa: BLE001
            logger.warning("audit.jsonl export failed (non-fatal)", exc_info=True)

        return audit

    def _export_jsonl(self, audit: Audit) -> None:
        path = Path(self.settings.audit_jsonl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "audit_id": audit.audit_id,
            "ts": audit.ts.isoformat() if audit.ts else None,
            "actor": audit.actor,
            "action": audit.action,
            "target": audit.target,
            "params_digest": audit.params_digest,
            "result": audit.result,
            "error_code": audit.error_code,
            "trace_id": audit.trace_id,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
