"""mcp-servers 环境变量配置（D-001 默认值；口径与 control-plane 一致）。"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = Field(
        default="postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane",
        alias="DATABASE_URL",
    )
    casebase_database_url: str = Field(
        default="",  # 空 = 复用 database_url
        alias="CASEBASE_DATABASE_URL",
    )

    # 上游依赖（case-admin / release-admin 包装的 REST 面）
    control_plane_base_url: str = Field(default="http://127.0.0.1:8090", alias="CONTROL_PLANE_BASE_URL")
    control_plane_role_token: str = Field(default="", alias="CONTROL_PLANE_ROLE_TOKEN")
    gate_authority_token: str = Field(default="", alias="GATE_AUTHORITY_TOKEN")
    quality_api_base_url: str = Field(default="http://127.0.0.1:8080", alias="QUALITY_API_BASE_URL")
    quality_read_token: str = Field(default="conformance-read-token", alias="QUALITY_READ_TOKEN")
    # Required by every served MCP process. One process exposes exactly one
    # role projection; an empty value fails startup instead of exposing all tools.
    mcp_tool_profile: str = Field(default="", alias="MCP_TOOL_PROFILE")
    # The MCP listener is reachable from the local container network.  It must
    # accept tool traffic only when Higress supplies both the projection's
    # private upstream credential and the authenticated consumer identity.
    mcp_expected_consumer: str = Field(default="", alias="MCP_EXPECTED_CONSUMER")
    mcp_gateway_backend_token: str = Field(default="", alias="MCP_GATEWAY_BACKEND_TOKEN")
    # Canonical deterministic identity used on lease-bound controller writes.
    # Tool callers may not select another worker identity.
    mcp_worker_id: str = Field(default="", alias="MCP_WORKER_ID")

    # Gate executor: repository-owned allowlisted suites + persisted evidence.
    gate_evaluation_timeout_seconds: int = Field(default=300, alias="GATE_EVALUATION_TIMEOUT_SECONDS")
    gate_evidence_dir: str = Field(default="evidence/gate", alias="GATE_EVIDENCE_DIR")
    experiment_evidence_dir: str = Field(
        default="evidence/experiments", alias="EXPERIMENT_EVIDENCE_DIR"
    )
    experiment_heartbeat_interval_seconds: float = Field(
        default=15.0, alias="EXPERIMENT_HEARTBEAT_INTERVAL_SECONDS"
    )

    # 审批安全件（spec §5.2 / D-001 #10）
    approval_ttl_minutes: int = Field(default=30, alias="APPROVAL_TTL_MINUTES")
    # 冷却（D-001 Q8 / #7：SUSPENDED 冷却 24h）
    cooloff_hours: int = Field(default=24, alias="TRUST_COOLOFF_HOURS")
    # 晋升阈值（contracts/wilson 唯一事实源：0.9 固定）
    promotion_threshold: float = Field(default=0.9, alias="TRUST_PROMOTION_THRESHOLD")

    # 各 server 端口（PathRewrite uvicorn :8xxx）
    case_admin_port: int = Field(default=8001, alias="CASE_ADMIN_PORT")
    release_admin_port: int = Field(default=8002, alias="RELEASE_ADMIN_PORT")
    eval_runner_port: int = Field(default=8003, alias="EVAL_RUNNER_PORT")
    notification_port: int = Field(default=8004, alias="NOTIFICATION_PORT")
    casebase_port: int = Field(default=8005, alias="CASEBASE_PORT")
    host: str = Field(default="0.0.0.0", alias="MCP_HOST")

    # 审计（spec §7.6 / §11.4：权威源=DB，失败即拒）
    audit_jsonl_path: str = Field(default="./var/mcp-audit.jsonl", alias="AUDIT_JSONL_PATH")
    audit_force_fail: bool = Field(default=False, alias="AUDIT_FORCE_FAIL")  # 仅测试

    # 通知 mock（feishu-mock）：内存/SQLite 群消息日志
    notification_log_url: str = Field(default="", alias="NOTIFICATION_LOG_URL")  # 空=复用 database_url
    notification_room: str = Field(default="demo", alias="NOTIFICATION_DEFAULT_ROOM")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def resolved_casebase_url(self) -> str:
        return self.casebase_database_url or self.database_url

    @property
    def resolved_notification_url(self) -> str:
        return self.notification_log_url or self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
