"""CaseLoop control-plane FastAPI 入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text as sql_text

from app import __version__
from app.api import (
    cases,
    changesets,
    evidence_export,
    experiments,
    gates,
    notifications,
    public_v4,
    public_v5,
    read_views,
    releases,
)
from app.config import Settings, get_settings, validate_public_authority_config
from app.api.deps import validate_authority_config
from app.db import get_engine, get_session_factory
from app.models import Base
from app.quality.client import FakeQualityClient, QualityAPIClient
from app.services.event_store import CASConflict
from app.services.state_machines import IllegalTransition

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _expected_database_heads() -> frozenset[str]:
    """Resolve the migration heads shipped in this checkout/container image."""

    service_root = Path(__file__).resolve().parents[1]
    config = AlembicConfig(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "alembic"))
    heads = frozenset(ScriptDirectory.from_config(config).get_heads())
    if not heads:
        raise RuntimeError("no Alembic migration head is available")
    return heads


def _readiness_checks(app: FastAPI) -> dict[str, str]:
    checks = {
        "database": "unavailable",
        "migration": "unknown",
        "public_auth": "misconfigured",
    }
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        try:
            with engine.connect() as connection:
                connection.execute(sql_text("SELECT 1")).scalar_one()
                current_heads = frozenset(
                    MigrationContext.configure(connection).get_current_heads()
                )
            checks["database"] = "ok"
            checks["migration"] = (
                "current"
                if current_heads == _expected_database_heads()
                else "mismatch"
            )
        except Exception as exc:  # noqa: BLE001 - readiness must fail closed
            logger.warning(
                "control-plane database readiness failed error_type=%s",
                type(exc).__name__,
            )

    settings = getattr(app.state, "settings", None)
    try:
        if not isinstance(settings, Settings):
            raise ValueError("settings unavailable")
        validate_public_authority_config(settings)
        checks["public_auth"] = "configured"
    except (TypeError, ValueError):
        pass
    return checks


def create_app(
    settings: Settings | None = None,
    *,
    quality_client: Any = None,
    notification_adapter: Any = None,
    engine: Any = None,
    create_tables: bool = False,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
        if settings.require_mcp_role_tokens:
            try:
                validate_authority_config(settings, require_all_role_tokens=True)
            except Exception as exc:
                raise RuntimeError(
                    "control-plane role authority preflight failed"
                ) from exc
        eng = engine or get_engine(settings.database_url)
        app.state.engine = eng
        app.state.session_factory = get_session_factory(eng)
        app.state.settings = settings
        if quality_client is not None:
            app.state.quality_client = quality_client
        else:
            app.state.quality_client = QualityAPIClient(
                settings.quality_api_base_url, settings.quality_api_token
            )
        if notification_adapter is not None:
            app.state.notification_adapter = notification_adapter
        if create_tables:
            Base.metadata.create_all(bind=eng)
        logger.info("control-plane up version=%s", __version__)
        yield

    app = FastAPI(
        title="CaseLoop Control Plane",
        version=__version__,
        description="确定性控制面：Case/Release Controller（LLM 不是状态与权限权威源）",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.env_cache = {"ts": 0.0, "payload": None}  # GET /v1/env 5s 缓存
    if quality_client is not None:
        app.state.quality_client = quality_client
    if notification_adapter is not None:
        app.state.notification_adapter = notification_adapter

    app.include_router(cases.router)
    app.include_router(experiments.router)
    app.include_router(gates.router)
    app.include_router(changesets.router)
    app.include_router(releases.router)
    app.include_router(notifications.router)
    app.include_router(read_views.router)
    app.include_router(evidence_export.router)
    app.include_router(public_v4.router)
    app.include_router(public_v5.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", response_model=None)
    def readyz(request: Request) -> Any:
        checks = _readiness_checks(request.app)
        ready = checks == {
            "database": "ok",
            "migration": "current",
            "public_auth": "configured",
        }
        payload: dict[str, Any] = {
            "status": "ready" if ready else "not_ready",
            "version": __version__,
            "checks": checks,
        }
        if not ready:
            return JSONResponse(status_code=503, content=payload)
        return payload

    # 状态机非法迁移 / CAS 冲突 → 结构化错误（避免裸 500）
    @app.exception_handler(IllegalTransition)
    async def _illegal_transition_handler(_req: Request, exc: IllegalTransition) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "illegal_transition",
                "message": str(exc),
                "current_state": exc.from_state,
                "event": exc.event,
            },
        )

    @app.exception_handler(CASConflict)
    async def _cas_conflict_handler(_req: Request, exc: CASConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "code": "revision_conflict",
                "message": str(exc),
                "expected_revision": exc.expected,
                "actual_revision": exc.actual,
            },
        )

    return app


# uvicorn app.main:app
app = create_app()
