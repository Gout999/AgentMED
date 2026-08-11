"""C5 rollback drill: V5 入口禁用路径构造验证（v5-architecture-convergence.md#C5）。

当前控制面**没有运行时 kill-switch**：`app.main.create_app` 固定 include
``public_v5.router``，不存在按配置关闭 V5 面的开关。本 drill 通过**构造**
验证「禁用 V5 面」的行为契约——monkeypatch 掉 ``app.main.public_v5``
（替换为空 router），构造一个不含任何 v5 路由的 app，并断言：

1. V3/V4 字节不变：同一 seed 下 ``GET /api/v1/capabilities``（capabilities
   v1 只读 smoke）的成功响应与未认证 401 错误信封，在 baseline app（含
   v5 router）与禁用版 app 之间 masked-canonical 字节一致；
2. v5 outbox 行不产生：禁用版上 v5 读/写路径均 404，请求在路由层即终止，
   v5 写 handler 不可达，``outbox``/``events`` 表保持零行；
3. schema/head 不变：alembic 脚本链 head 仍为 ``012``（drill 只读校验脚本
   head，不运行任何迁移；011/012 的 preflight / 零部分 schema 变更语义由
   ``tests/unit/test_migrations.py`` 钉住，恢复路径见
   ``docs/plans/v5-migration-recovery.md``）；
4. 错误信封正常：v5 路径返回标准 404 JSON 信封（``{"detail": "Not Found"}``）
   而非 500；v1 认证失败返回公共错误信封（401）。

对照性断言 ``test_disabled_construction_actually_removes_v5_surface`` 保证
drill 的构造确实删除了 v5 面（否则 404 断言是空谈）。

本 drill 不修改任何迁移、路由或模型代码：禁用只发生在测试构造层面。
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import Base
from app.models.v4_tables import PublicCredential, PublicPrincipal
from app.public_api.credential_resolver import digest_public_subject, hash_opaque_bearer
from app.utils.v4_integrity import canonical_digest

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_VERSIONS = REPO_ROOT / "control-plane" / "alembic" / "versions"

WORKSPACE = "ws_01J0000000000001"
PROJECT = "proj_01J0000000000001"
PRINCIPAL_ID = "prn_01J000000000000A"
SUBJECT = "catalog-admin-01J0000000000001"
ISSUER = "https://auth.caseloop.dev"
AUDIENCES = ["caseloop-public-api"]
RAW_TOKEN = "drill-c5-token-0123456789-abcdef"
PEPPER = "drill-c5-pepper"
CURSOR_KEY = "drill-c5-cursor"
SCOPES = ["capabilities:read"]
REQUEST_ID = "req_01J000000000000A"

ALEMBIC_HEAD = "012"


def _claims(workspace_id: str, project_ids: list[str], scopes: list[str]) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": workspace_id,
            "project_ids": project_ids,
            "environment_ids": [],
            "scopes": scopes,
        }
    )


def _seed_public_auth(engine: sa.Engine) -> None:
    """Seed one capabilities:read principal + credential (mirrors the C4 drill)."""
    now = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        session.add(
            PublicPrincipal(
                principal_id=PRINCIPAL_ID,
                workspace_id=WORKSPACE,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(SUBJECT),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=list(SCOPES),
                trust_roles=["integrator"],
                claims_digest=_claims(WORKSPACE, [PROJECT], SCOPES),
                revoked_at=None,
            )
        )
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000A",
                workspace_id=WORKSPACE,
                principal_id=PRINCIPAL_ID,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(RAW_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "b" * 64,
                claims_digest=_claims(WORKSPACE, [PROJECT], SCOPES),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=list(SCOPES),
                state="ACTIVE",
                issued_at=now,
                not_before=now,
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
                revoked_at=None,
            )
        )
        session.commit()


def _fresh_engine() -> sa.Engine:
    engine = sa.create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    _seed_public_auth(engine)
    return engine


def _settings() -> Settings:
    return Settings(
        database_url="sqlite://",
        public_credential_hash_pepper=SecretStr(PEPPER),
        public_cursor_signing_key=SecretStr(CURSOR_KEY),
        require_mcp_role_tokens=False,
    )


def _build_app(engine: sa.Engine, *, include_v5: bool, monkeypatch: pytest.MonkeyPatch):
    """Build the control-plane app; ``include_v5=False`` constructs the
    V5-disabled surface (monkeypatched empty router in place of public_v5).

    No kill-switch exists today: the disabled path is a construction-level
    simulation of the C5 rollback action "disable affected V5 entry points".
    """
    if not include_v5:
        import app.main as main_module

        monkeypatch.setattr(
            main_module,
            "public_v5",
            types.SimpleNamespace(router=APIRouter()),
        )
    return create_app(settings=_settings(), engine=engine, create_tables=True)


def _v1_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {RAW_TOKEN}",
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": "1.0",
        "X-Request-ID": REQUEST_ID,
    }


def _masked_canonical(value: Any) -> bytes:
    """Deterministic byte identity, masking per-request fresh fields.

    ``audit_ref`` (one audit row per request) and ``generated_at`` (server
    clock) are request-bound by design; masking them does not weaken the
    invariant — they are still present and shaped identically on both sides.
    """

    def mask(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: "<fresh>" if key in {"audit_ref", "generated_at"} else mask(value)
                for key, value in item.items()
            }
        if isinstance(item, list):
            return [mask(child) for child in item]
        return item

    return json.dumps(
        mask(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# 1. V3/V4 bytes unchanged when the V5 surface is disabled
# ---------------------------------------------------------------------------


def test_v1_capabilities_bytes_identical_with_v5_surface_disabled(monkeypatch) -> None:
    with TestClient(_build_app(_fresh_engine(), include_v5=True, monkeypatch=monkeypatch)) as baseline:
        with TestClient(
            _build_app(_fresh_engine(), include_v5=False, monkeypatch=monkeypatch)
        ) as disabled:
            response_a = baseline.get("/api/v1/capabilities", headers=_v1_headers())
            response_b = disabled.get("/api/v1/capabilities", headers=_v1_headers())

    assert response_a.status_code == 200
    assert response_b.status_code == response_a.status_code
    assert _masked_canonical(response_b.json()) == _masked_canonical(response_a.json())

    # The v1 surface advertises exactly the intents authorized for the seeded
    # capabilities:read principal — with or without the v5 router — and no v5
    # intent leaks into the compatibility surface.
    enabled_a = {
        item["name"]
        for item in response_a.json()["data"]["enabled_intents"]
    }
    enabled_b = {
        item["name"]
        for item in response_b.json()["data"]["enabled_intents"]
    }
    assert enabled_a == enabled_b == {"capabilities.get"}
    assert "applications.register" not in enabled_b


def test_v1_error_envelope_bytes_identical_with_v5_surface_disabled(monkeypatch) -> None:
    with TestClient(_build_app(_fresh_engine(), include_v5=True, monkeypatch=monkeypatch)) as baseline:
        with TestClient(
            _build_app(_fresh_engine(), include_v5=False, monkeypatch=monkeypatch)
        ) as disabled:
            unauth_a = baseline.get("/api/v1/capabilities", headers={"X-Request-ID": REQUEST_ID})
            unauth_b = disabled.get("/api/v1/capabilities", headers={"X-Request-ID": REQUEST_ID})

    assert unauth_a.status_code == 401
    assert unauth_b.status_code == unauth_a.status_code
    assert _masked_canonical(unauth_b.json()) == _masked_canonical(unauth_a.json())
    # The public error envelope is the v1 contract shape (code, not a bare 500).
    assert unauth_b.json()["error"]["code"]


# ---------------------------------------------------------------------------
# 2. Disabled construction really removes the V5 surface (non-vacuous)
# ---------------------------------------------------------------------------


def test_disabled_construction_actually_removes_v5_surface(monkeypatch) -> None:
    with TestClient(_build_app(_fresh_engine(), include_v5=True, monkeypatch=monkeypatch)) as baseline:
        with TestClient(
            _build_app(_fresh_engine(), include_v5=False, monkeypatch=monkeypatch)
        ) as disabled:
            # Baseline: the v5 route exists and requires authentication (401,
            # never 404) — the route table serves.
            route_probe = baseline.get("/api/v2/capabilities")
            assert route_probe.status_code == 401
            assert disabled.get("/api/v2/capabilities").status_code == 404


# ---------------------------------------------------------------------------
# 3. V5 write path unreachable: no outbox/events rows, clean 404 envelope
# ---------------------------------------------------------------------------


def test_v5_write_path_unreachable_produces_no_outbox_rows(monkeypatch) -> None:
    engine = _fresh_engine()
    with TestClient(_build_app(engine, include_v5=False, monkeypatch=monkeypatch)) as disabled:
        # Read path: 404 with the standard FastAPI JSON envelope, not a 500.
        read = disabled.get("/api/v2/capabilities", headers=_v1_headers())
        assert read.status_code == 404
        assert read.json() == {"detail": "Not Found"}

        # Write path: 404 before any handler runs — the v5 write path is
        # unreachable by construction.
        write = disabled.post(
            "/api/v2/applications",
            headers=_v1_headers(),
            json={"name": "x", "criticality": "P1"},
        )
        assert write.status_code == 404
        assert write.json() == {"detail": "Not Found"}
        assert write.status_code != 500

    with Session(engine) as session:
        assert session.execute(sa.text("SELECT COUNT(*) FROM outbox")).scalar_one() == 0
        assert session.execute(sa.text("SELECT COUNT(*) FROM events")).scalar_one() == 0


# ---------------------------------------------------------------------------
# 4. Schema/head unchanged: alembic head stays 012 (read-only script check)
# ---------------------------------------------------------------------------


def test_alembic_head_unchanged_at_012() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "control-plane" / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "control-plane" / "alembic"))
    script = ScriptDirectory.from_config(config)

    # The migration chain is untouched by the drill (and by the rollback
    # action): head remains 011 -> 012, and no migration file is deleted.
    assert script.get_heads() == [ALEMBIC_HEAD]
    assert script.get_revision("012").down_revision == "011"
    assert (ALEMBIC_VERSIONS / "011_v5_lifecycle_authority_foundation.py").is_file()
    assert (ALEMBIC_VERSIONS / "012_v5_event_envelope.py").is_file()
