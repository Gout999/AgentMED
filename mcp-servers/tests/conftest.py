"""mcp-servers 单测共享 fixture（SQLite 内存，无外部依赖）。

DATABASE_URL 必须在任何 Settings 构造前设好；get_settings 为 lru_cache，
各测试用 Settings(database_url=...) 显式构造，避免 env 污染。

隔离策略：session 级建表一次；每测试开独立事务，结束回滚——测试间不泄漏状态。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("AUDIT_JSONL_PATH", "/tmp/agentmed-mcp-test-audit.jsonl")
os.environ.setdefault("AUDIT_FORCE_FAIL", "false")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from sqlalchemy.orm import Session  # noqa: E402

from common.config import Settings  # noqa: E402
from common.db import create_all, get_engine  # noqa: E402

MCP_SERVERS_ROOT = Path(__file__).resolve().parent.parent
WILSON_VECTORS = MCP_SERVERS_ROOT.parent / "contracts" / "wilson" / "wilson-vectors.json"
TEST_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def sqlite_engine():
    engine = get_engine(TEST_SQLITE_URL)  # StaticPool 单连接
    create_all(TEST_SQLITE_URL)
    return engine


@pytest.fixture()
def session(sqlite_engine):
    """每测试一个事务，结束回滚（SQLite 内存 DDL 亦随事务回滚）。"""
    conn = sqlite_engine.connect()
    trans = conn.begin()
    s = Session(bind=conn)
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


@pytest.fixture()
def settings():
    return Settings(database_url=TEST_SQLITE_URL, audit_jsonl_path="/tmp/agentmed-mcp-test-audit.jsonl")
