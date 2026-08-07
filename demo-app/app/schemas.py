"""请求体校验模型（对齐 contracts/quality-api/openapi.yaml schemas）。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------- VersionSet 写面

class PromptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_id: str
    version: str
    digest: Optional[str] = None


class KBManifestEntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kb_id: str
    entry_id: str
    version: str = "1.0.0"
    digest: Optional[str] = None


class KBManifestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[KBManifestEntryInput]
    manifest_digest: Optional[str] = None


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    digest: Optional[str] = None


class VersionSetContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: PromptInput
    kb_manifest: KBManifestInput
    model: ModelInput


class LifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Optional[int] = Field(default=None, ge=1)
    note: Optional[str] = None


class CanaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Optional[int] = Field(default=None, ge=1)
    percent: int = Field(ge=1, le=100)
    verification: Optional[dict[str, Any]] = None


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Optional[int] = Field(default=None, ge=1)
    rollback_to: str
    reason: Optional[str] = None


# ---------------------------------------------------------------- 用户面

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: Optional[str] = None
    user_ref: Optional[str] = None


class FeedbackPost(BaseModel):
    request_id: str = Field(min_length=4)
    rating: str
    comment: Optional[str] = Field(default=None, max_length=2000)
    user_ref: Optional[str] = None
    source: str = "in_app"

    @field_validator("rating")
    @classmethod
    def _rating_enum(cls, v: str) -> str:
        if v not in ("positive", "negative", "neutral"):
            raise ValueError("rating 必须是 positive|negative|neutral")
        return v


# ---------------------------------------------------------------- OAuth token

class TokenRequest(BaseModel):
    grant_type: str = "client_credentials"
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
