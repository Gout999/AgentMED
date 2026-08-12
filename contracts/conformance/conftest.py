"""conformance suite 共享 fixture 与路径常量。

BASE_URL 从环境变量读：CASELOOP_QUALITY_API_BASE_URL（默认 http://127.0.0.1:8080）。
令牌：CASELOOP_READ_TOKEN / CASELOOP_WRITE_TOKEN（演示环境缺省占位值；
真实部署由 Higress 凭证托管注入）。写面令牌按契约仅 Release Controller 持有，
conformance 持有它只是为了扮演 Release Controller 做契约级验证。

依赖守卫：本套件必须按 `contracts/conformance/requirements.txt` 安装。缺少
`rfc3339-validator` 时 jsonschema 的 `FormatChecker()` 会静默不注册
`date-time`，整套 schema 断言照样全绿但少验一整类约束——这正是 AGENTS.md
禁止的假绿。与 `v4_integrity.py` 对 `rfc8785` 的处理一致：缺钉版依赖是
hard conformance failure，不降级、不跳过。
"""
import os
import pathlib

import pytest
import requests
from jsonschema import FormatChecker

if "date-time" not in FormatChecker().checkers:  # pragma: no cover - fatal
    raise RuntimeError(
        "CaseLoop conformance requires pinned dependency rfc3339-validator==0.1.4; "
        "without it jsonschema silently skips every date-time format assertion. "
        "Install contracts/conformance/requirements.txt into the interpreter used "
        "to run this suite."
    )

CONTRACTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMAS_DIR = CONTRACTS_ROOT / "schemas"
FIXTURES_DIR = CONTRACTS_ROOT / "fixtures"
SAMPLES_DIR = FIXTURES_DIR / "samples"
WILSON_VECTORS = CONTRACTS_ROOT / "wilson" / "wilson-vectors.json"
EVENTS_YAML = CONTRACTS_ROOT / "events" / "events.yaml"
STATE_MACHINES_YAML = CONTRACTS_ROOT / "events" / "state-machines.yaml"
OPENAPI_YAML = CONTRACTS_ROOT / "quality-api" / "openapi.yaml"

BASE_URL = os.environ.get("CASELOOP_QUALITY_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
READ_TOKEN = os.environ.get("CASELOOP_READ_TOKEN", "conformance-read-token")
WRITE_TOKEN = os.environ.get("CASELOOP_WRITE_TOKEN", "conformance-write-token")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def read_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {READ_TOKEN}"})
    return s


@pytest.fixture(scope="session")
def write_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {WRITE_TOKEN}"})
    return s
