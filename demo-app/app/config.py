"""demo-app「小智客服」环境配置（env + .env 覆盖，密钥不入库）。"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # StepFun LLM（真实调用，无 mock）
    stepfun_api_key: str = ""
    stepfun_base_url: str = "https://api.stepfun.com/v1"
    stepfun_model: str = "step-3.7-flash"

    # 数据库（compose 内主机名 postgres；本机直跑用 127.0.0.1）
    database_url: str = "postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/demo_app"

    # Quality API 演示令牌（conformance 缺省值）
    caseloop_read_token: str = ""
    caseloop_write_token: str = ""
    release_controller_client_secret: str = ""
    quality_reader_client_secret: str = ""

    # 集中限速器（D-001：默认 8 RPM，留 2 余量给 AgentTeams worker）
    llm_rpm_limit: int = 8
    llm_retry_max_attempts: int = 5
    llm_retry_backoff_base_seconds: float = 1.0
    llm_retry_backoff_max_seconds: float = 30.0

    # 异步 operation TTL（Q1 裁决：24h）
    operation_ttl_hours: int = 24

    # OTel
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "demo-app-xiaozhi"

    # 服务监听
    host: str = "0.0.0.0"
    port: int = 8080

    # 检索参数
    retrieval_top_k: int = 3
    retrieval_min_score: float = 1.0

    @property
    def db_url_public(self) -> str:
        """给 README/日志展示时掩码密码（不打印密钥）。"""
        return self.database_url.split("@")[-1]


@lru_cache
def get_settings() -> Settings:
    return Settings()
