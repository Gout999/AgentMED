"""SQLAlchemy 2.x 引擎与会话（demo_app 库；PG 唯一事实源）。"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _create_engine() -> object:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_schema() -> None:
    """仅供显式测试夹具使用；部署和应用启动必须通过 Alembic。"""
    from app import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(bind=engine)
