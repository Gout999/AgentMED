"""FastAPI 依赖。"""
from __future__ import annotations

from collections.abc import Generator
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.quality.client import FakeQualityClient, QualityAPIClient, QualityClientProtocol


def get_db_session(request: Request) -> Generator[Session, None, None]:
    factory = getattr(request.app.state, "session_factory", None) or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def get_quality_client(request: Request) -> QualityClientProtocol:
    client = getattr(request.app.state, "quality_client", None)
    if client is not None:
        return client
    settings = get_app_settings(request)
    return QualityAPIClient(settings.quality_api_base_url, settings.quality_api_token)
