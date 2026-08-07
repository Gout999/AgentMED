"""测试共享 fixture：settings / probe_set / 录制样例 / 契约路径。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_harness.config import Settings
from eval_harness.probe_loader import load_probe_set

TESTS_ROOT = Path(__file__).resolve().parent
EVAL_ROOT = TESTS_ROOT.parent
SAMPLES = EVAL_ROOT / "samples"
REPO_ROOT = EVAL_ROOT.parent
CONTRACTS = REPO_ROOT / "contracts"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(llm_rpm_limit=60, experiment_repetitions=3)


@pytest.fixture(scope="session")
def probe_set():
    return load_probe_set(REPO_ROOT)


@pytest.fixture(scope="session")
def probe_samples() -> dict:
    return json.loads((SAMPLES / "b1_probe_responses.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def frozen_probe_set_digest() -> str:
    return "sha256:f51fbbee2810467c96658f93e4fc2b64b5b843b80e55bf5029f30fa26bb9dbf0"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def contracts_dir() -> Path:
    return CONTRACTS


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES
