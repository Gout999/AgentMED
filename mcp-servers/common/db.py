"""SQLAlchemy 引擎/会话工厂。支持 PG（生产）与 SQLite（单测内存）。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from common.config import get_settings
from common import tables  # noqa: F401  确保模型注册

_engines: dict[str, Engine] = {}
_sessions: dict[str, sessionmaker] = {}


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if url == "sqlite://" or url.endswith(":memory:"):
            # 内存库共享单连接，保证跨会话可见同一份表/数据（单测）
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    if url not in _engines:
        _engines[url] = _make_engine(url)
    return _engines[url]


def get_session_factory(url: str | None = None) -> sessionmaker:
    url = url or get_settings().database_url
    if url not in _sessions:
        _sessions[url] = sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)
    return _sessions[url]


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """事务上下文：提交成功 commit，异常 rollback。"""
    factory = get_session_factory(url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def new_session(url: str | None = None) -> Iterator[Session]:
    """非自动提交会话（调用方控制 commit/rollback）。"""
    factory = get_session_factory(url)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def create_all(url: str | None = None) -> None:
    """建表（测试/开发用；生产走 migrations/001_init.sql）。"""
    engine = get_engine(url)
    tables.Base.metadata.create_all(engine)


def dispose_engines() -> None:
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()
    _sessions.clear()
