"""权威审计（spec §7.6 / §11.4 重写）：权威源=DB，写失败即拒业务（503，不放行）。

zeroops audit.py（失败放行）显式废弃。本实现：
- 审计写入与业务同一事务；flush 失败 → AuditWriteError → 上层抛业务 503。
- audit.jsonl 仅为导出物，best-effort；导出失败不影响权威源。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from common.config import get_settings
from common.ids import new_audit_id, new_trace_id
from common.jcs import params_digest
from common.tables import AuditRow

logger = logging.getLogger(__name__)


class AuditWriteError(Exception):
    """审计写库失败 → 业务必须拒绝（503）。"""


class AuditService:
    def __init__(self, session: Session, settings: Any | None = None):
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
    ) -> AuditRow:
        if self.settings.audit_force_fail:
            raise AuditWriteError("AUDIT_FORCE_FAIL")

        row = AuditRow(
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
            self.session.add(row)
            self.session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.error("audit write failed action=%s target=%s", action, target)
            raise AuditWriteError(str(exc)) from exc

        # 导出物（失败不影响权威源；仅 best-effort）
        try:
            self._export_jsonl(row)
        except Exception:  # noqa: BLE001
            logger.warning("audit.jsonl export failed (non-fatal)", exc_info=True)

        return row

    def _export_jsonl(self, row: AuditRow) -> None:
        path = Path(self.settings.audit_jsonl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "audit_id": row.audit_id,
            "ts": row.ts.isoformat() if row.ts else None,
            "actor": row.actor,
            "action": row.action,
            "target": row.target,
            "params_digest": row.params_digest,
            "result": row.result,
            "error_code": row.error_code,
            "trace_id": row.trace_id,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def audit_uri_for(self, audit_id: str, kind: str = "mcp") -> str:
        """审计 URI（ApprovalGrant proof.ref 用）：audit://<kind>/<audit_id>。"""
        return f"audit://{kind}/{audit_id}"
