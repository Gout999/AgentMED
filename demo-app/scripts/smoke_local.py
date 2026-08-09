"""本地冒烟：直连 compose 起的 PG（127.0.0.1:5432/demo_app）验证 Quality API 核心链路。

用法：STEPFUN_API_KEY=... python smoke_smoke.py
（无 Docker 依赖；演示 app 容器未起时也能验证服务层逻辑。）
"""
import os
import sys
import time
import uuid

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/demo_app",
)

from sqlalchemy.orm import Session

from app import faults, kb
from app.config import get_settings
from app.db import SessionLocal
from app.live_config import resolve_live_config
from app.models import Operation
from app.operations import execute_operation
from app.seeding import init_app
from app.versionset_service import (
    CASError,
    IdempotencyConflictError,
    apply_transition,
    build_status,
    create_operation,
    create_versionset,
    get_versionset,
    lifecycle_fingerprint,
    record_operation_idempotency,
    validate_cas,
    validate_transition,
)

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {extra}")


def run_lifecycle(db: Session, vs_id: str, action: str, body=None):
    vs = get_versionset(db, vs_id)
    validate_cas(vs, None, (body or {}).get("expected_revision"))
    validate_transition(vs, action)
    op = create_operation(db, vs, action, f"smoke-{uuid.uuid4()}", request=body or {})
    record_operation_idempotency(
        db,
        f"smoke-{uuid.uuid4()}",
        lifecycle_fingerprint(vs.versionset_id, action, body or {}),
        op,
    )
    db.commit()
    execute_operation(db, op.operation_id)
    return op


def main():
    init_app()
    db = SessionLocal()
    try:
        # 基线
        cfg = resolve_live_config(db)
        check("基线 active versionset 存在", cfg.versionset_id == "vs_baseline0000000001")
        check("基线 prompt digest 内容绑定", cfg.prompt.digest.startswith("sha256:"))
        check("基线 KB 条目 >= 30", len(cfg.entries) >= 30)

        # 创建 + 幂等
        content = {
            "prompt": {"prompt_id": "prompts/system.md", "version": "v-smoke-1", "digest": "sha256:" + "a" * 64},
            "kb_manifest": {"entries": [{"kb_id": "products", "entry_id": "x200", "version": "1.0.0", "digest": "sha256:" + "a" * 64}], "manifest_digest": "sha256:" + "a" * 64},
            "model": {"provider": "stepfun", "model": "step-3.7-flash", "params": {"temperature": 0.0}, "digest": "sha256:" + "a" * 64},
        }
        key = f"smoke-key-{uuid.uuid4()}"
        vs, created = create_versionset(db, content, key)
        check("创建成功 rev=1 draft", created and vs.status == "draft" and vs.revision == 1)
        vs2, created2 = create_versionset(db, content, key)
        check("幂等重放同一版本", not created2 and vs2.versionset_id == vs.versionset_id)
        try:
            create_versionset(db, {**content, "prompt": {**content["prompt"], "version": "v-smoke-2"}}, key)
            check("key 复用不同 body 应 422", False)
        except IdempotencyConflictError:
            check("key 复用不同 body 应 422", True)

        # CAS
        try:
            validate_cas(vs, None, None)
            check("缺前置应 412", False)
        except CASError as e:
            check("缺前置应 412", e.code == "precondition_failed")
        try:
            validate_cas(vs, '"999"', None)
            check("过期 revision 应 409", False)
        except CASError as e:
            check("过期 revision 应 409", e.code == "revision_conflict")

        # 全链路
        op = run_lifecycle(db, vs.versionset_id, "stage", {"expected_revision": 1})
        st = build_status(db, get_versionset(db, vs.versionset_id))
        check("stage -> staged", st["status"] == "staged")
        op = run_lifecycle(db, vs.versionset_id, "canary", {"expected_revision": 2, "percent": 10})
        st = build_status(db, get_versionset(db, vs.versionset_id))
        check("canary 10%", st["status"] == "canary" and st["canary"]["percent"] == 10)
        active_digest = get_versionset(db, cfg.versionset_id).digest
        op = run_lifecycle(
            db,
            vs.versionset_id,
            "promote",
            {"expected_revision": 3, "expected_active_digest": active_digest},
        )
        st = build_status(db, get_versionset(db, vs.versionset_id))
        check("promote -> active", st["status"] == "active" and st["is_active"])
        check("历史 >= 3", len(st["history"]) >= 3)

        # 非法迁移
        vs_ill, _ = create_versionset(db, {**content, "prompt": {**content["prompt"], "version": "v-smoke-ill"}}, f"smoke-key-{uuid.uuid4()}")
        try:
            validate_transition(get_versionset(db, vs_ill.versionset_id), "canary")
            check("draft->canary 非法迁移应 422", False)
        except Exception:
            check("draft->canary 非法迁移应 422", True)

        # B1 注入 digest 变化
        base_cfg = resolve_live_config(db)
        faults.inject_fault(db, "B1")
        b1_cfg = resolve_live_config(db)
        check("B1 注入后 prompt digest 改变", b1_cfg.prompt.digest != base_cfg.prompt.digest)
        check("B1 注入版本 v1.4.3", b1_cfg.prompt.version == "v1.4.3")
        faults.reset_faults(db)
        reset_cfg = resolve_live_config(db)
        check("reset 后恢复基线 digest", reset_cfg.prompt.digest == base_cfg.prompt.digest)

        # B2 注入 KB digest 变化
        faults.inject_fault(db, "B2")
        b2_cfg = resolve_live_config(db)
        check("B2 注入后 kb manifest digest 改变", b2_cfg.kb_manifest_digest != base_cfg.kb_manifest_digest)
        x200 = kb.find_entry(db, "products", "x200-earbuds")
        check("B2 后 X200 续航 8 小时", "8 小时" in x200.content)
        faults.reset_faults(db)
        x200 = kb.find_entry(db, "products", "x200-earbuds")
        check("B2 reset 后恢复 30 小时", "30 小时" in x200.content)

        print(f"\n== 冒烟结果: {PASS} passed, {FAIL} failed ==")
        sys.exit(1 if FAIL else 0)
    finally:
        db.close()


if __name__ == "__main__":
    main()
