"""环境变量配置（D-001 默认值）。"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # 允许以字段名传参（测试/构造），env 仍走 alias
    )

    database_url: str = Field(
        default="postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane",
        alias="DATABASE_URL",
    )

    quality_api_base_url: str = Field(default="http://127.0.0.1:8080", alias="QUALITY_API_BASE_URL")
    quality_api_token: str = Field(default="", alias="QUALITY_API_TOKEN")
    control_plane_internal_token: str = Field(default="", alias="CONTROL_PLANE_TOKEN")
    approval_authority_token: str = Field(default="", alias="APPROVAL_AUTHORITY_TOKEN")

    lease_ttl_seconds: int = Field(default=60, alias="LEASE_TTL_SECONDS")
    complaint_dedup_window_hours: int = Field(default=24, alias="COMPLAINT_DEDUP_WINDOW_HOURS")
    approval_ttl_minutes: int = Field(default=30, alias="APPROVAL_TTL_MINUTES")

    # 灰度阶梯 5%→25%→100%；观察窗 MVP 2min（D-001）
    canary_steps: str = Field(default="5,25,100", alias="CANARY_STEPS")
    canary_observation_seconds: int = Field(default=120, alias="CANARY_OBSERVATION_SECONDS")
    operation_ttl_hours: int = Field(default=24, alias="OPERATION_TTL_HOURS")
    operation_poll_timeout_seconds: float = Field(default=5.0, alias="OPERATION_POLL_TIMEOUT_SECONDS")

    reconcile_backoff_initial_seconds: int = Field(default=5, alias="RECONCILE_BACKOFF_INITIAL_SECONDS")
    reconcile_backoff_max_seconds: int = Field(default=300, alias="RECONCILE_BACKOFF_MAX_SECONDS")

    outbox_relay_interval_seconds: float = Field(default=1.0, alias="OUTBOX_RELAY_INTERVAL_SECONDS")
    outbox_sink: str = Field(default="logging", alias="OUTBOX_SINK")

    control_plane_host: str = Field(default="0.0.0.0", alias="CONTROL_PLANE_HOST")
    control_plane_port: int = Field(default=8090, alias="CONTROL_PLANE_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    audit_jsonl_path: str = Field(default="./var/audit.jsonl", alias="AUDIT_JSONL_PATH")

    # 测试/注入：审计写失败开关（仅测试用）
    audit_force_fail: bool = Field(default=False, alias="AUDIT_FORCE_FAIL")

    @property
    def canary_step_list(self) -> List[int]:
        return [int(x.strip()) for x in self.canary_steps.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
