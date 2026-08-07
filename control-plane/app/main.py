"""CaseLoop control-plane FastAPI 入口。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import __version__
from app.api import cases, releases
from app.config import Settings, get_settings
from app.db import get_engine, get_session_factory
from app.models.tables import Base
from app.quality.client import FakeQualityClient, QualityAPIClient

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    quality_client: Any = None,
    engine: Any = None,
    create_tables: bool = False,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
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
    if quality_client is not None:
        app.state.quality_client = quality_client

    app.include_router(cases.router)
    app.include_router(releases.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


# uvicorn app.main:app
app = create_app()
