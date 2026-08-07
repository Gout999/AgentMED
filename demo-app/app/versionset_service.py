"""VersionSet CRUD + 生命周期状态机 + CAS + 幂等。

契约要点（contracts/quality-api/openapi.yaml）：
- 内容不可变：创建后 {prompt, kb_manifest, model} 不改，变更=新建 VersionSet。
- digest 服务端按 JCS+SHA-256 重算（客户端提交的 digest 仅作占位，被忽略）。
- 生命周期：draft→staged→canary→active（promote）；active→rolled_back（rollback）；
  promote 时原 active → superseded。
- 写面：If-Match 头 或 body expected_revision 二选一（都缺→412，不匹配→409）；
  Idempotency-Key 重放返回同一 operation/资源。
"""
from __future__ import annotations

import base64
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jcs, kb
from app.ids import new_operation_id, new_versionset_id
from app.models import (
    IdempotencyRecord,
    Operation,
    TransitionRecord,
    VersionSet,
)

# 各动作允许的当前状态（非法迁移 → 422 illegal_transition）
ALLOWED_FROM: dict[str, list[str]] = {
    "stage": ["draft"],
    "canary": ["staged", "canary"],
    "promote": ["staged", "canary"],
    "rollback": ["active"],
}


class CASError(Exception):
    """412 缺前置 / 409 冲突。"""

    def __init__(self, code: str, message: str, details: Optional[dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class IllegalTransitionError(Exception):
    """422 非法迁移。"""

    def __init__(self, message: str, *, current_status: str = "", attempted: str = ""):
        self.current_status = current_status
        self.attempted = attempted
        super().__init__(message)


class IdempotencyConflictError(Exception):
    """422 idempotency_key_conflict。"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 状态查询

def get_versionset(db: Session, vs_id: str) -> Optional[VersionSet]:
    return db.get(VersionSet, vs_id)


def get_active_versionset(db: Session) -> Optional[VersionSet]:
    return db.execute(
        select(VersionSet)
        .where(VersionSet.status == "active")
        .order_by(VersionSet.updated_at.desc(), VersionSet.created_at.desc())
    ).scalars().first()


def get_history(db: Session, vs_id: str) -> list[TransitionRecord]:
    return list(
        db.execute(
            select(TransitionRecord)
            .where(TransitionRecord.versionset_id == vs_id)
            .order_by(TransitionRecord.id.asc())
        ).scalars()
    )


def list_versionsets(
    db: Session, *, status: Optional[str] = None, limit: int = 50, cursor: Optional[str] = None
) -> tuple[list[VersionSet], Optional[str]]:
    q = select(VersionSet).order_by(VersionSet.created_at.desc(), VersionSet.versionset_id.desc())
    if status:
        q = q.where(VersionSet.status == status)
    # keyset 分页：cursor = base64url(created_at_iso|versionset_id)
    if cursor:
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            created_at_s, vs_id = raw.split("|", 1)
            q = q.where(
                (VersionSet.created_at < datetime.fromisoformat(created_at_s))
                | (
                    (VersionSet.created_at == datetime.fromisoformat(created_at_s))
                    & (VersionSet.versionset_id < vs_id)
                )
            )
        except Exception:  # noqa: BLE001 —— 非法 cursor 视为从头分页
            pass
    rows = db.execute(q.limit(limit + 1)).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = base64.urlsafe_b64encode(
            f"{last.created_at.isoformat()}|{last.versionset_id}".encode()
        ).decode()
    return list(page), next_cursor


def build_status(db: Session, vs: VersionSet) -> dict[str, Any]:
    history = get_history(db, vs.versionset_id)
    return {
        "versionset_id": vs.versionset_id,
        "revision": vs.revision,
        "status": vs.status,
        "is_active": vs.status == "active",
        **(
            {"canary": {"percent": vs.canary_percent, "started_at": vs.canary_started_at.isoformat()}}
            if vs.status == "canary" and vs.canary_percent is not None
            else {}
        ),
        "history": [
            {
                "from": t.from_status,
                "to": t.to_status,
                "at": t.at.isoformat(),
                "operation_id": t.operation_id,
                "actor": t.actor,
            }
            for t in history
        ],
    }


# ---------------------------------------------------------------- 创建

def _server_compute_content(db: Session, content_input: dict[str, Any]) -> dict[str, Any]:
    """服务端按 JCS+SHA-256 重算全部 digest（忽略客户端提交的 digest 占位）。"""
    from app import prompts_registry

    p = content_input.get("prompt", {})
    m = content_input.get("model", {})
    km = content_input.get("kb_manifest", {})
    kb_entries = km.get("entries", [])

    # prompt digest：注册表优先（内容绑定），否则按元数据（{prompt_id, version}）
    prompt_id = p.get("prompt_id", "prompts/system.md")
    prompt_version = p.get("version", "")
    if prompts_registry.get_prompt_version(db, prompt_id, prompt_version) is not None:
        _, reg_digest = prompts_registry.resolve_prompt(db, prompt_id, prompt_version)
    else:
        reg_digest = jcs.content_digest({"prompt_id": prompt_id, "version": prompt_version})
    prompt_obj = {
        "prompt_id": prompt_id,
        "version": prompt_version,
        "digest": reg_digest,
    }

    # KB manifest：逐条目 digest（已有条目内容绑定，未注册按元数据）
    manifest_entries = []
    for e in kb_entries:
        kb_id = e.get("kb_id", "")
        entry_id = e.get("entry_id", "")
        version = e.get("version", "1.0.0")
        row = kb.find_entry(db, kb_id, entry_id)
        if row is not None:
            d = row.digest
        else:
            d = jcs.content_digest({"kb_id": kb_id, "entry_id": entry_id, "version": version})
        manifest_entries.append({"kb_id": kb_id, "entry_id": entry_id, "version": version, "digest": d})
    manifest_digest = jcs.kb_manifest_digest(manifest_entries)
    kb_manifest_obj = {"entries": manifest_entries, "manifest_digest": manifest_digest}

    # model digest（params 在请求内，直接内容绑定）
    provider = m.get("provider", "stepfun")
    model_name = m.get("model", "step-3.7-flash")
    params = m.get("params", {"temperature": 0.0})
    model_digest = jcs.model_digest(provider, model_name, params)
    model_obj = {"provider": provider, "model": model_name, "params": params, "digest": model_digest}

    content = {"prompt": prompt_obj, "kb_manifest": kb_manifest_obj, "model": model_obj}
    content["digest"] = jcs.versionset_digest(prompt_obj, kb_manifest_obj, model_obj)
    return content


def _content_fingerprint(content_input: dict[str, Any]) -> str:
    """请求内容指纹（幂等判定用；忽略客户端 digest 占位以容忍等价重放）。"""
    p = content_input.get("prompt", {})
    m = content_input.get("model", {})
    km = content_input.get("kb_manifest", {})
    entries = [(e.get("kb_id", ""), e.get("entry_id", ""), e.get("version", "")) for e in km.get("entries", [])]
    return jcs.content_digest(
        {
            "prompt": (p.get("prompt_id", ""), p.get("version", "")),
            "kb_manifest": sorted(entries),
            "model": (m.get("provider", ""), m.get("model", ""), m.get("params", {})),
        }
    )


def create_versionset(
    db: Session, content_input: dict[str, Any], idempotency_key: str
) -> tuple[VersionSet, bool]:
    """创建（draft, revision=1）。幂等重放返回已有资源。返回 (vs, created)。"""
    fingerprint = _content_fingerprint(content_input)
    existing = db.get(IdempotencyRecord, idempotency_key)
    if existing is not None:
        if existing.resource_type == "versionset" and existing.fingerprint == fingerprint:
            vs = db.get(VersionSet, existing.resource_id)
            if vs is not None:
                return vs, False
        raise IdempotencyConflictError(
            "idempotency_key 复用但请求内容不同"
        )

    content = _server_compute_content(db, content_input)
    vs = VersionSet(
        versionset_id=new_versionset_id(),
        revision=1,
        status="draft",
        content=content,
        digest=content["digest"],
    )
    db.add(vs)
    db.add(
        IdempotencyRecord(
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            resource_type="versionset",
            resource_id=vs.versionset_id,
        )
    )
    db.commit()
    db.refresh(vs)
    return vs, True


# ---------------------------------------------------------------- CAS / 幂等

def parse_etag(if_match: str) -> Optional[int]:
    s = if_match.strip().strip('"')
    try:
        return int(s)
    except ValueError:
        return None


def validate_cas(vs: VersionSet, if_match: Optional[str], expected_revision: Optional[int]) -> None:
    """412/409 语义。If-Match 优先于 body.expected_revision。"""
    if if_match:
        rev = parse_etag(if_match)
        if rev is None:
            rev = expected_revision
        if rev is None:
            raise CASError(
                "precondition_failed",
                "If-Match 无法解析且无 expected_revision",
                {"subcode": "precondition_required"},
            )
        if rev != vs.revision:
            raise CASError(
                "revision_conflict",
                f"expected revision {rev}, current revision {vs.revision}",
                {"expected_revision": rev, "current_revision": vs.revision},
            )
        return
    if expected_revision is not None:
        if expected_revision != vs.revision:
            raise CASError(
                "revision_conflict",
                f"expected revision {expected_revision}, current revision {vs.revision}",
                {"expected_revision": expected_revision, "current_revision": vs.revision},
            )
        return
    raise CASError(
        "precondition_failed",
        "write requires If-Match header or expected_revision",
        {"subcode": "precondition_required"},
    )


def validate_transition(vs: VersionSet, action: str) -> None:
    if vs.status not in ALLOWED_FROM.get(action, []):
        raise IllegalTransitionError(
            f"illegal transition: cannot {action} from status {vs.status}",
            current_status=vs.status,
            attempted=action,
        )


def lifecycle_fingerprint(action: str, body: dict[str, Any]) -> str:
    return jcs.content_digest({"action": action, "body": body})


def resolve_idempotent_operation(
    db: Session, idempotency_key: str, fingerprint: str
) -> Optional[Operation]:
    rec = db.get(IdempotencyRecord, idempotency_key)
    if rec is None:
        return None
    if rec.resource_type != "operation" or rec.fingerprint != fingerprint:
        raise IdempotencyConflictError("idempotency_key 复用但请求内容不同")
    return db.get(Operation, rec.resource_id)


def record_operation_idempotency(
    db: Session, idempotency_key: str, fingerprint: str, operation: Operation
) -> None:
    db.add(
        IdempotencyRecord(
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            resource_type="operation",
            resource_id=operation.operation_id,
        )
    )


# ---------------------------------------------------------------- 异步 operation

def create_operation(
    db: Session,
    vs: VersionSet,
    action: str,
    idempotency_key: str,
    request: Optional[dict[str, Any]] = None,
) -> Operation:
    from datetime import timedelta

    from app.config import get_settings

    op = Operation(
        operation_id=new_operation_id(),
        kind=action,
        status="pending",
        idempotency_key=idempotency_key,
        versionset_id=vs.versionset_id,
        request=request or {},
        created_at=now_utc(),
        updated_at=now_utc(),
        expires_at=now_utc() + timedelta(hours=get_settings().operation_ttl_hours),
    )
    db.add(op)
    return op


def _record_transition(db: Session, vs: VersionSet, from_status: str, to_status: str, op: Operation) -> None:
    db.add(
        TransitionRecord(
            versionset_id=vs.versionset_id,
            from_status=from_status,
            to_status=to_status,
            at=now_utc(),
            operation_id=op.operation_id,
            actor="release-controller",
        )
    )


def _resolve_rollback_target(
    db: Session, vs: VersionSet, rollback_to: str
) -> VersionSet:
    """rollback_to: "previous"（上一个 active）或完整 digest。"""
    if rollback_to == "previous":
        # 上一个 active = 最近一次被 promote 顶掉的 superseded 版本
        target = db.execute(
            select(VersionSet)
            .where(VersionSet.status == "superseded")
            .order_by(VersionSet.updated_at.desc(), VersionSet.created_at.desc())
        ).scalars().first()
        if target is None:
            # 回退：查历史里 rollback 前曾 active 的版本
            raise IllegalTransitionError("no previous active versionset to rollback to")
        return target
    # 按 digest 匹配
    target = db.execute(
        select(VersionSet).where(VersionSet.digest == rollback_to)
    ).scalars().first()
    if target is None:
        raise IllegalTransitionError("rollback_to digest 未匹配任何已知 VersionSet")
    return target


def apply_transition(db: Session, vs: VersionSet, action: str, op: Operation) -> None:
    """执行生命周期迁移（execute_operation 的同步核心；假设前置校验已过）。"""
    from_status = vs.status

    if action == "stage":
        vs.status = "staged"
        _record_transition(db, vs, from_status, "staged", op)
    elif action == "canary":
        percent = int((op.request or {}).get("percent", 5))
        if vs.status != "canary":
            vs.canary_started_at = now_utc()
        vs.status = "canary"
        vs.canary_percent = percent
        _record_transition(db, vs, from_status, "canary", op)
    elif action == "promote":
        prev_active = get_active_versionset(db)
        if prev_active is not None and prev_active.versionset_id != vs.versionset_id:
            prev_active.status = "superseded"
            prev_active.revision += 1
            _record_transition(db, prev_active, "active", "superseded", op)
        vs.status = "active"
        vs.canary_percent = 100
        _record_transition(db, vs, from_status, "active", op)
    elif action == "rollback":
        rollback_to = (op.request or {}).get("rollback_to", "previous")
        target = _resolve_rollback_target(db, vs, rollback_to)
        target_from = target.status
        vs.status = "rolled_back"
        vs.canary_percent = 0
        _record_transition(db, vs, from_status, "rolled_back", op)
        target.status = "active"
        target.canary_percent = 100
        target.revision += 1
        _record_transition(db, target, target_from, "active", op)
    else:
        raise IllegalTransitionError(f"unknown action: {action}")

    vs.revision += 1
    db.commit()
    db.refresh(vs)
