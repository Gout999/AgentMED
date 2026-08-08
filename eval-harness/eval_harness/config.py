"""运行时配置（环境变量 + .env）。密钥永不入库；.env* 已被仓库 gitignore。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 加载工作目录/仓库根的 .env（若存在）
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path.cwd() / ".env")
# live 测试凭证来源（主控约定：~/Documents/kimi/workspace/ACL-team/.env）
load_dotenv(Path.home() / "Documents/kimi/workspace/ACL-team/.env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Quality API
    quality_api_base_url: str = field(
        default_factory=lambda: _env("CASELOOP_QUALITY_API_BASE_URL", "http://127.0.0.1:8080")
    )
    read_token: str = field(default_factory=lambda: _env("CASELOOP_READ_TOKEN", "conformance-read-token"))
    write_token: str = field(default_factory=lambda: _env("CASELOOP_WRITE_TOKEN", "conformance-write-token"))
    quality_api_timeout_seconds: float = field(
        default_factory=lambda: float(_env("CASELOOP_QUALITY_API_TIMEOUT_SECONDS", "95"))
    )

    # StepFun（运动员模型）
    stepfun_api_key: str = field(default_factory=lambda: _env("STEPFUN_API_KEY"))
    stepfun_base_url: str = field(default_factory=lambda: _env("STEPFUN_BASE_URL", "https://api.stepfun.com/v1"))
    stepfun_model: str = field(default_factory=lambda: _env("STEPFUN_MODEL", "step-3.7-flash"))

    # 裁判模型（必须 ≠ 运动员模型；缺省空 → 裁判轨 live 标 UNAVAILABLE）
    judge_model: str = field(default_factory=lambda: _env("JUDGE_MODEL"))

    # 集中限速（D-001：默认 8 RPM，留 2 余量给 AgentTeams worker）
    llm_rpm_limit: int = field(default_factory=lambda: int(_env("LLM_RPM_LIMIT", "8")))

    # 实验协议（D-001 裁决）
    experiment_repetitions: int = field(default_factory=lambda: int(_env("EXPERIMENT_REPETITIONS", "5")))
    experiment_delta_min: float = field(default_factory=lambda: float(_env("EXPERIMENT_DELTA_MIN", "0.2")))
    experiment_max_supplements: int = field(default_factory=lambda: int(_env("EXPERIMENT_MAX_SUPPLEMENTS", "2")))
    experiment_confidence: float = field(default_factory=lambda: float(_env("EXPERIMENT_CONFIDENCE", "0.95")))

    # 门禁
    gate_judge_pass_threshold: float = field(default_factory=lambda: float(_env("GATE_JUDGE_PASS_THRESHOLD", "0.8")))
    provider_timeout_seconds: float = field(
        default_factory=lambda: float(_env("GATE_PROVIDER_TIMEOUT_SECONDS", "90"))
    )

    # 仓库根（用于定位 contracts/ 与 demo-app/ 只读参考）
    repo_root: Path = _REPO_ROOT

    @property
    def has_stepfun_key(self) -> bool:
        return bool(self.stepfun_api_key)


def get_settings() -> Settings:
    return Settings()
