"""V2 public header parsing for the /api/v2 catalog boundary.

Mirrors the frozen v1 ``auth_contract.PublicRequestHeaders`` rules, but the
contract version is fixed to ``2.0`` (``X-AgentMED-Contract-Version: 2.0``) and
mutations require ``X-AgentMED-Idempotency-Key`` per the V5 wire contract.
A missing or non-``2.0`` contract value is ``REQUEST_INVALID`` and never falls
back to v1.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated

from pydantic import Field, SecretStr, ValidationError

from .models import RequestId, WireModel
from .v5_models import SchemaVersion2
from .auth_contract import HeaderContractViolation


class PublicV2RequestHeaders(WireModel):
    """Validated v2 transport context before credential resolution."""

    bearer_token: SecretStr = Field(repr=False, exclude=True)
    requested_workspace_id: Annotated[str, Field(pattern=r"^ws_[0-9A-Za-z]{8,64}$")]
    contract_version: SchemaVersion2
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)] | None = None
    request_id: RequestId | None = None
    client_version: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @classmethod
    def from_headers(
        cls, headers: Mapping[str, str], *, mutation: bool = False
    ) -> "PublicV2RequestHeaders":
        normalized: dict[str, str] = {}
        for raw_name, value in headers.items():
            name = raw_name.lower()
            if name in normalized:
                raise HeaderContractViolation(
                    "REQUEST_INVALID", f"duplicate header: {raw_name.lower()}"
                )
            normalized[name] = value

        authorization = normalized.get("authorization")
        if authorization is None:
            raise HeaderContractViolation(
                "AUTHENTICATION_REQUIRED", "A bearer credential is required."
            )
        bearer = re.fullmatch(r"Bearer ([^\s]+)", authorization, flags=re.IGNORECASE)
        if bearer is None:
            raise HeaderContractViolation(
                "TOKEN_INVALID", "The bearer credential header is invalid."
            )

        workspace_id = normalized.get("x-agentmed-workspace-id")
        if workspace_id is None:
            raise HeaderContractViolation(
                "REQUEST_INVALID", "X-AgentMED-Workspace-ID is required."
            )
        contract_version = normalized.get("x-agentmed-contract-version")
        if contract_version != "2.0":
            # v2 missing or other value is REQUEST_INVALID; the URL major must
            # match the request header major and v2 never falls back to v1.
            raise HeaderContractViolation(
                "REQUEST_INVALID", "X-AgentMED-Contract-Version must be 2.0 for /api/v2."
            )

        idempotency_key = normalized.get("x-agentmed-idempotency-key")
        if mutation and idempotency_key is None:
            raise HeaderContractViolation(
                "IDEMPOTENCY_KEY_REQUIRED", "X-AgentMED-Idempotency-Key is required for mutations."
            )
        if "idempotency-key" in normalized:
            raise HeaderContractViolation(
                "REQUEST_INVALID", "v1 Idempotency-Key is not accepted on /api/v2."
            )

        try:
            return cls(
                bearer_token=SecretStr(bearer.group(1)),
                requested_workspace_id=workspace_id,
                contract_version=contract_version,
                idempotency_key=idempotency_key,
                request_id=normalized.get("x-request-id"),
                client_version=normalized.get("x-agentmed-client-version"),
            )
        except ValidationError as exc:
            fields = sorted({str(item["loc"][0]) for item in exc.errors()})
            raise HeaderContractViolation(
                "REQUEST_INVALID", f"Invalid public request header fields: {', '.join(fields)}."
            ) from None


__all__ = ["PublicV2RequestHeaders"]
