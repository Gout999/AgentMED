"""HTTP 客户端：退避重试 + 控制面 REST 错误 → MCP 统一错误码（spec §9.2）。

只读工具指数退避 ≤3 次；写工具不自动重试（调用方必须携带幂等键）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from common.errors import (
    DEPENDENCY_UNAVAILABLE,
    INTERNAL_ERROR,
    McpError,
    RATE_LIMITED,
    UPSTREAM_TIMEOUT,
)

logger = logging.getLogger(__name__)

# control-plane / demo-app REST error code → MCP error code
_CP_CODE_MAP = {
    "not_found": "NOT_FOUND",
    "validation_failed": "VALIDATION_FAILED",
    "pii_redaction_failed": "VALIDATION_FAILED",
    "illegal_transition": "STATE_CONFLICT",
    "revision_conflict": "STATE_CONFLICT",
    "lease_conflict": "LEASE_LOST",
    "lease_lost": "LEASE_LOST",
    "nonce_replay": "APPROVAL_REPLAYED",
    "hash_mismatch": "APPROVAL_MISMATCH",
    "approval_expired": "APPROVAL_EXPIRED",
    "quality_api_error": "DEPENDENCY_UNAVAILABLE",
    "audit_unavailable": "DEPENDENCY_UNAVAILABLE",
    "gate_not_passed": "GATE_FAILED",
}


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout: float = 10.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def _backoff(self, attempt: int) -> float:
        return min(0.5 * (2 ** attempt), 4.0)

    def get(self, path: str, params: Optional[dict[str, Any]] = None, *, retry: bool = True) -> dict[str, Any]:
        return self._request("GET", path, params=params, retry=retry)

    def post(self, path: str, json_body: Optional[dict[str, Any]] = None, *, retry: bool = False) -> dict[str, Any]:
        return self._request("POST", path, json_body=json_body, retry=retry)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        *,
        retry: bool,
    ) -> dict[str, Any]:
        attempts = self.max_retries if retry else 1
        last_err: Optional[McpError] = None
        for attempt in range(attempts):
            try:
                # trust_env=False：本机内部调用禁止走系统/环境代理
                # （httpx 默认 trust_env=True 会读 macOS 系统代理，localhost 被代理 502）
                resp = httpx.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json_body,
                    headers=self._headers(),
                    timeout=self.timeout,
                    trust_env=False,
                )
            except httpx.TimeoutException as exc:
                last_err = McpError(UPSTREAM_TIMEOUT, f"upstream timeout: {path}", retryable=True)
                logger.warning("upstream timeout attempt=%d path=%s", attempt + 1, path)
                time.sleep(self._backoff(attempt))
                continue
            except httpx.HTTPError as exc:
                last_err = McpError(DEPENDENCY_UNAVAILABLE, f"upstream unreachable: {exc}", retryable=True)
                logger.warning("upstream error attempt=%d path=%s err=%s", attempt + 1, path, exc)
                time.sleep(self._backoff(attempt))
                continue

            if resp.status_code == 429:
                last_err = McpError(RATE_LIMITED, "rate limited, back off", retryable=True)
                time.sleep(self._backoff(attempt))
                continue

            if resp.status_code >= 200 and resp.status_code < 300:
                return resp.json() if resp.content else {}

            # 4xx/5xx：解析错误体映射
            raise self._map_error(resp.status_code, resp.text)

        assert last_err is not None
        raise last_err

    @staticmethod
    def _map_error(status: int, body_text: str) -> McpError:
        """把 control-plane / Quality API 错误体映射为统一 MCP 错误。"""
        try:
            import json

            body = json.loads(body_text)
        except Exception:  # noqa: BLE001
            body = {}
        code = str((body.get("error") or body).get("code") or body.get("code") or "")
        message = str((body.get("error") or body).get("message") or body.get("message") or f"HTTP {status}")
        mcp_code = _CP_CODE_MAP.get(code, "VALIDATION_FAILED" if 400 <= status < 500 else "DEPENDENCY_UNAVAILABLE")
        retryable = status == 429 or status >= 500
        extra = {}
        if isinstance(body.get("error"), dict):
            extra = {k: v for k, v in body["error"].items() if k not in ("code", "message")}
        return McpError(mcp_code, message, retryable=retryable, extra=extra)
