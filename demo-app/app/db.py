"""SQLAlchemy 2.x 引擎与会话（demo_app 库；PG 唯一事实源）。"""
from __future__ import annotations

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _create_engine() -> object:
    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )

    # 每次建连确保 vector 扩展存在（幂等；caseloop 为 superuser）
    @event.listens_for(engine, "connect")
    def _ensure_vector(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        finally:
            cur.close()

    return engine


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
    """启动时建表（demo-app 不引入 alembic；表结构变更随代码演进）。"""
    from app import models  # noqa: F401  确保模型注册

    Base.metadata.create_all(bind=engine)
