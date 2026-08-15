"""环境变量配置（D-001 默认值）。"""
from __future__ import annotations

import json
import secrets
from functools import lru_cache
from typing import List

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,  # 允许以字段名传参（测试/构造），env 仍走 alias
    )

    # BaseSettings resolves DATABASE_URL case-insensitively from this field
    # name. Avoid an explicit alias here: Pydantic otherwise lets the env alias
    # overwrite a programmatic database_url used to isolate replay/test stores.
    database_url: str = "postgresql+psycopg://agentmed:agentmed@127.0.0.1:5432/control_plane"

    quality_api_base_url: str = Field(default="http://127.0.0.1:8088", alias="QUALITY_API_BASE_URL")
    quality_api_token: str = Field(default="", alias="QUALITY_API_TOKEN")
    control_plane_internal_token: str = Field(default="", alias="CONTROL_PLANE_TOKEN")
    control_plane_role_tokens_json: str = Field(
        default="{}",
        alias="CONTROL_PLANE_ROLE_TOKENS_JSON",
    )
    require_mcp_role_tokens: bool = Field(
        default=True,
        alias="REQUIRE_MCP_ROLE_TOKENS",
    )
    approval_authority_token: str = Field(default="", alias="APPROVAL_AUTHORITY_TOKEN")
    gate_authority_token: str = Field(default="", alias="GATE_AUTHORITY_TOKEN")

    # Public v4 credentials are an independent authority namespace.  Neither
    # secret may fall back to or reuse an internal controller/role token.
    public_credential_hash_pepper: SecretStr = Field(
        default=SecretStr(""), alias="PUBLIC_CREDENTIAL_HASH_PEPPER"
    )
    public_cursor_signing_key: SecretStr = Field(
        default=SecretStr(""), alias="PUBLIC_CURSOR_SIGNING_KEY"
    )
    public_auth_issuer: str = Field(
        default="https://auth.agentmed.dev", alias="PUBLIC_AUTH_ISSUER"
    )

    lease_ttl_seconds: int = Field(default=60, alias="LEASE_TTL_SECONDS")
    complaint_dedup_window_hours: int = Field(default=24, alias="COMPLAINT_DEDUP_WINDOW_HOURS")
    approval_ttl_minutes: int = Field(default=30, alias="APPROVAL_TTL_MINUTES")
    attribution_delta_min: float = Field(default=0.2, alias="ATTRIBUTION_DELTA_MIN")
    allow_isolated_replay_attribution: bool = Field(
        default=False,
        alias="ALLOW_ISOLATED_REPLAY_ATTRIBUTION",
    )
    gate_policy_profile: str = Field(default="live", alias="GATE_POLICY_PROFILE")
    allow_isolated_replay_gate: bool = Field(
        default=False,
        alias="ALLOW_ISOLATED_REPLAY_GATE",
    )
    allow_demo_fault_injection: bool = Field(
        default=False,
        alias="ALLOW_DEMO_FAULT_INJECTION",
    )

    # 灰度阶梯 5%→25%→100%；观察窗 MVP 2min（D-001）
    canary_steps: str = Field(default="5,25,100", alias="CANARY_STEPS")
    canary_observation_seconds: int = Field(default=120, alias="CANARY_OBSERVATION_SECONDS")
    operation_ttl_hours: int = Field(default=24, alias="OPERATION_TTL_HOURS")
    operation_poll_timeout_seconds: float = Field(default=5.0, alias="OPERATION_POLL_TIMEOUT_SECONDS")

    reconcile_backoff_initial_seconds: int = Field(default=5, alias="RECONCILE_BACKOFF_INITIAL_SECONDS")
    reconcile_backoff_max_seconds: int = Field(default=300, alias="RECONCILE_BACKOFF_MAX_SECONDS")

    outbox_relay_interval_seconds: float = Field(default=1.0, alias="OUTBOX_RELAY_INTERVAL_SECONDS")
    outbox_claim_ttl_seconds: int = Field(default=30, alias="OUTBOX_CLAIM_TTL_SECONDS")
    outbox_max_attempts: int = Field(default=5, alias="OUTBOX_MAX_ATTEMPTS")
    outbox_retry_initial_seconds: int = Field(default=2, alias="OUTBOX_RETRY_INITIAL_SECONDS")
    outbox_retry_max_seconds: int = Field(default=300, alias="OUTBOX_RETRY_MAX_SECONDS")
    notification_adapter: str = Field(default="disabled", alias="NOTIFICATION_ADAPTER")
    feishu_app_id: str = Field(default="", alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", alias="FEISHU_APP_SECRET")
    feishu_base_url: str = Field(
        default="https://open.feishu.cn", alias="FEISHU_BASE_URL"
    )
    feishu_timeout_seconds: float = Field(default=10.0, alias="FEISHU_TIMEOUT_SECONDS")

    control_plane_host: str = Field(default="0.0.0.0", alias="CONTROL_PLANE_HOST")
    control_plane_port: int = Field(default=8090, alias="CONTROL_PLANE_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    audit_jsonl_path: str = Field(default="./var/audit.jsonl", alias="AUDIT_JSONL_PATH")

    # 测试/注入：审计写失败开关（仅测试用）
    audit_force_fail: bool = Field(default=False, alias="AUDIT_FORCE_FAIL")

    @property
    def canary_step_list(self) -> List[int]:
        return [int(x.strip()) for x in self.canary_steps.split(",") if x.strip()]


def _secret_text(value: object) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value or "")


def validate_public_authority_config(settings: Settings) -> None:
    """Fail closed unless the public authority secrets are independent.

    Public credential hashing and cursor signing are separate from all internal
    controller and worker credentials.  Readiness uses this shared preflight so
    a process cannot be advertised as ready while every public V4/V5 request
    would fail at its authentication boundary.
    """

    pepper = _secret_text(settings.public_credential_hash_pepper)
    cursor_key = _secret_text(settings.public_cursor_signing_key)
    if not pepper or not cursor_key:
        raise ValueError("public authority secrets are not configured")

    try:
        role_tokens = json.loads(settings.control_plane_role_tokens_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("control-plane role token map is invalid") from exc
    if not isinstance(role_tokens, dict) or any(
        not isinstance(token, str) for token in role_tokens.values()
    ):
        raise ValueError("control-plane role token map is invalid")

    internal_peers = [
        settings.control_plane_internal_token,
        settings.approval_authority_token,
        settings.gate_authority_token,
        *(token for token in role_tokens.values() if token),
    ]
    if secrets.compare_digest(pepper, cursor_key) or any(
        peer
        and (
            secrets.compare_digest(pepper, peer)
            or secrets.compare_digest(cursor_key, peer)
        )
        for peer in internal_peers
    ):
        raise ValueError("public authority secrets must be independent")


@lru_cache
def get_settings() -> Settings:
    return Settings()
