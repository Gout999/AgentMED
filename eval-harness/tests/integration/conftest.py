"""集成测试共享 fixture：live 环境探测（demo-app 可达 + StepFun key），不可用即 skip。"""
from __future__ import annotations

from pathlib import Path

import pytest
import requests
import yaml

from eval_harness.config import Settings
from eval_harness.probe_loader import load_probe_set, frozen_digest

TESTS_ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = TESTS_ROOT.parent
REPO_ROOT = EVAL_ROOT.parent
B1_FIXTURE = REPO_ROOT / "contracts" / "fixtures" / "b1-prompt-regression.yaml"


@pytest.fixture(scope="session")
def live_settings():
    s = Settings()
    if not s.has_stepfun_key:
        pytest.skip("STEPFUN_API_KEY 未配置（live 测试需要真实 StepFun 调用）")
    try:
        r = requests.get(f"{s.quality_api_base_url}/health", timeout=5)
        healthy = r.status_code == 200
    except Exception:
        healthy = False
    if not healthy:
        pytest.skip(f"demo-app 未就绪: {s.quality_api_base_url}")
    return s


@pytest.fixture(scope="session")
def live_probe_set(live_settings):
    ps = load_probe_set(REPO_ROOT)
    return ps


@pytest.fixture(scope="session")
def b1_protocol_reps(live_settings) -> int:
    """B1 fixture 冻结的 repetitions（3）；可用 EXPERIMENT_REPETITIONS 覆盖。"""
    import os
    if os.environ.get("EXPERIMENT_REPETITIONS"):
        return int(os.environ["EXPERIMENT_REPETITIONS"])
    raw = yaml.safe_load(B1_FIXTURE.read_text(encoding="utf-8"))
    return int(raw["experiment_protocol"]["repetitions"])
