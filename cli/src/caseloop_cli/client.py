from __future__ import annotations

import ipaddress
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, TypeAlias
from urllib.parse import urlsplit

import httpx
import rfc8785
from pydantic import BaseModel, ValidationError

from ._generated.public_error_v1 import PublicErrorEnvelope
from ._generated.public_v1 import (
    CaseResponse,
    CaseTimelineResponse,
    EvidenceResponse,
    ServerCapabilitiesResponse,
    SignalSubmissionResponse,
)
from ._generated.public_v2 import (
    ApplicationGetResponse,
    ApplicationListResponse,
    ApplicationRegisterResponse,
    ComponentGetResponse,
    ComponentRegisterResponse,
    DependencyEdgeGetResponse,
    DependencyEdgeRecordResponse,
    EnvironmentGetResponse,
    EnvironmentRegisterResponse,
    V5ServerCapabilitiesResponse,
)
from ._generated.manifest_v2 import SystemManifestImportResponse
from ._generated.operation_manifest import (
    CliOperationManifestError,
    V2_CLI_OPERATIONS,
)
from .errors import CliError, ExitFamily


_WORKSPACE_ID = re.compile(r"^ws_[0-9A-Za-z]{8,64}$")
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_CASE_PATH = re.compile(r"^/api/v1/cases/(case_[0-9A-Za-z]{8,64})$")
_TIMELINE_PATH = re.compile(
    r"^/api/v1/cases/(case_[0-9A-Za-z]{8,64})/timeline$"
)
_EVIDENCE_PATH = re.compile(r"^/api/v1/evidence/(ter_[0-9A-Za-z]{8,64})$")
_MISSING_NO_TRACE = (
    "trace.input",
    "trace.output",
    "observations.model",
    "observations.tools",
)

# CLI semantic response-model adapters.  The C1 generated operation manifest
# owns the activated intent and structural wire selection; these local models
# add fail-closed semantic checks and may not override a generated rejection.
_V2_RESPONSE_MODELS: dict[str, SuccessModel] = {
    "capabilities.get": V5ServerCapabilitiesResponse,
    "applications.register": ApplicationRegisterResponse,
    "applications.get": ApplicationGetResponse,
    "applications.list": ApplicationListResponse,
    "environments.register": EnvironmentRegisterResponse,
    "environments.get": EnvironmentGetResponse,
    "system-components.register": ComponentRegisterResponse,
    "system-components.get": ComponentGetResponse,
    "dependency-edges.record": DependencyEdgeRecordResponse,
    "dependency-edges.get": DependencyEdgeGetResponse,
    "system-manifests.import": SystemManifestImportResponse,
}

# Strict id pattern per manifest path parameter.  Preserving the exact
# pre-cutover matchers keeps malformed ids on the unsupported path
# (CLIENT_OPERATION_UNSUPPORTED before any HTTP).
_V2_PATH_PARAM_ID_PATTERN: dict[str, str] = {
    "application_id": "app_[0-9A-Za-z]{8,64}",
    "environment_id": "env_[0-9A-Za-z]{8,64}",
    "component_id": "cmp_[0-9A-Za-z]{8,64}",
    "dependency_edge_id": "de_[0-9A-Za-z]{8,64}",
}

SuccessModel: TypeAlias = type[BaseModel]


@dataclass(frozen=True)
class _OperationSpec:
    name: str
    status_code: int
    response_model: SuccessModel
    resource_id: str | None = None


_ERROR_META: dict[str, tuple[int, bool]] = {
    "AUTHENTICATION_REQUIRED": (401, False),
    "TOKEN_INVALID": (401, False),
    "TOKEN_EXPIRED": (401, False),
    "TOKEN_NOT_YET_VALID": (401, False),
    "TOKEN_REVOKED": (401, False),
    "AUDIENCE_MISMATCH": (401, False),
    "ISSUER_MISMATCH": (401, False),
    "SIGNATURE_INVALID": (401, False),
    "SCOPE_FORBIDDEN": (403, False),
    "WORKSPACE_ACCESS_DENIED": (403, False),
    "REQUEST_INVALID": (400, False),
    "IDEMPOTENCY_KEY_REQUIRED": (400, False),
    "RESOURCE_NOT_FOUND": (404, False),
    "IDEMPOTENCY_CONFLICT": (409, False),
    "CATALOG_CONFLICT": (409, False),
    "CONTRACT_VERSION_UNSUPPORTED": (412, False),
    "CONTENT_TOO_LARGE": (413, False),
    "UNSUPPORTED_MEDIA_TYPE": (415, False),
    "VALIDATION_FAILED": (422, False),
    "RATE_LIMITED": (429, True),
    "DEPENDENCY_UNAVAILABLE": (503, True),
    "AUDIT_UNAVAILABLE": (503, True),
    "INTERNAL_ERROR": (500, False),
}


def _is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise CliError("API_URL_INVALID", ExitFamily.CONFIG) from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None and not 1 <= port <= 65_535
    ):
        raise CliError("API_URL_INVALID", ExitFamily.CONFIG)
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname.lower()):
        raise CliError("API_URL_INVALID", ExitFamily.CONFIG)
    return value.rstrip("/")


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    workspace_id: str
    token: str = field(repr=False)
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    client_version: str = "caseloop-cli/0.1.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", validate_base_url(self.base_url))
        if not _WORKSPACE_ID.fullmatch(self.workspace_id):
            raise CliError("WORKSPACE_ID_INVALID", ExitFamily.CONFIG)
        if not self.token or any(character.isspace() for character in self.token):
            raise CliError("CREDENTIAL_INVALID", ExitFamily.CONFIG)
        if self.max_attempts < 1 or self.max_attempts > 5:
            raise CliError("RETRY_CONFIGURATION_INVALID", ExitFamily.CONFIG)


def _exit_for_status(status: int) -> ExitFamily:
    if status in {401, 403}:
        return ExitFamily.AUTH
    if status == 404:
        return ExitFamily.NOT_FOUND
    if status == 409:
        return ExitFamily.CONFLICT
    if status in {400, 412, 413, 415, 422}:
        return ExitFamily.INPUT
    if status in _RETRYABLE_STATUS:
        return ExitFamily.TEMPORARY
    return ExitFamily.REMOTE


_PATH_TEMPLATE_PARAM = re.compile(r"^([^{}]*)\{([A-Za-z_][A-Za-z0-9_]*)\}([^{}]*)$")


def _compile_v2_path_specs() -> (
    tuple[tuple[re.Pattern[str], _OperationSpec, str | None], ...]
):
    """Compile the v2 (method, path) matchers from the C1 manifest.

    One matcher per activated intent; an intent without a hand-written
    response model or with an unrecognized path parameter fails closed at
    import time instead of degrading to stale matching.
    """
    compiled: list[tuple[re.Pattern[str], _OperationSpec, str | None]] = []
    for operation in V2_CLI_OPERATIONS:
        model = _V2_RESPONSE_MODELS.get(operation.intent)
        if model is None:
            raise CliOperationManifestError(
                f"v5.cli.response_model_unregistered: {operation.intent}"
            )
        spec = _OperationSpec(operation.intent, operation.status_code, model)
        resource_param: str | None = None
        if "{" in operation.path:
            match = _PATH_TEMPLATE_PARAM.fullmatch(operation.path)
            if match is None:
                raise CliOperationManifestError(
                    f"v5.cli.invalid_path_template: {operation.path}"
                )
            param = match.group(2)
            id_pattern = _V2_PATH_PARAM_ID_PATTERN.get(param)
            if id_pattern is None:
                raise CliOperationManifestError(
                    f"v5.cli.unknown_path_param: {operation.intent} {param}"
                )
            pattern = re.compile(
                "^"
                + re.escape(operation.method)
                + " "
                + re.escape(match.group(1))
                + f"(?P<resource_id>{id_pattern})"
                + re.escape(match.group(3))
                + "$"
            )
            resource_param = param
        else:
            pattern = re.compile(
                "^"
                + re.escape(operation.method)
                + " "
                + re.escape(operation.path)
                + "$"
            )
        compiled.append((pattern, spec, resource_param))
    return tuple(compiled)


_V2_PATH_SPECS = _compile_v2_path_specs()

# Receipt resource kinds derived from the manifest ``command_target.resource``
# for the catalog mutations; the manifest import operation carries no
# command_target, and its receipt names the created system version set
# (hand-written, frozen, byte-identical to the pre-cutover literal).
_V2_RECEIPT_RESOURCE_KINDS: dict[str, str] = {
    operation.intent: operation.command_target_resource
    for operation in V2_CLI_OPERATIONS
    if operation.command_target_resource is not None
}
_V2_RECEIPT_RESOURCE_KINDS["system-manifests.import"] = "system_version_set"


def _operation_spec(method: str, path: str) -> _OperationSpec:
    normalized_method = method.upper()
    # v1 frozen surface (not part of the C1 activated-operation manifest).
    if normalized_method == "GET" and path == "/api/v1/capabilities":
        return _OperationSpec("capabilities.get", 200, ServerCapabilitiesResponse)
    if normalized_method == "POST" and path == "/api/v1/signals":
        return _OperationSpec("signals.submit", 201, SignalSubmissionResponse)
    if normalized_method == "GET":
        timeline = _TIMELINE_PATH.fullmatch(path)
        if timeline:
            return _OperationSpec(
                "cases.timeline", 200, CaseTimelineResponse, timeline.group(1)
            )
        case = _CASE_PATH.fullmatch(path)
        if case:
            return _OperationSpec("cases.get", 200, CaseResponse, case.group(1))
        evidence = _EVIDENCE_PATH.fullmatch(path)
        if evidence:
            return _OperationSpec(
                "evidence.get", 200, EvidenceResponse, evidence.group(1)
            )
    # v2 surface derived from the C1 activated-operation manifest.
    for pattern, spec, resource_param in _V2_PATH_SPECS:
        match = pattern.fullmatch(f"{normalized_method} {path}")
        if match is None:
            continue
        if resource_param is None:
            return spec
        return _OperationSpec(
            spec.name,
            spec.status_code,
            spec.response_model,
            match.group("resource_id"),
        )
    raise CliError("CLIENT_OPERATION_UNSUPPORTED", ExitFamily.PROTOCOL)


def _safe_remote_error(payload: dict[str, Any], code: str) -> dict[str, Any]:
    error = payload.get("error")
    assert isinstance(error, dict)
    return {
        "schema_version": "1.0",
        "error": {
            "code": code,
            "retryable": bool(error.get("retryable", False)),
            "request_id": error.get("request_id") if isinstance(error.get("request_id"), str) else None,
            "audit_ref": error.get("audit_ref") if isinstance(error.get("audit_ref"), str) else None,
        },
    }


def _contains_secret(value: object, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(
            _contains_secret(key, secret) or _contains_secret(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret(item, secret) for item in value)
    return False


def _canonical_digest(value: object) -> str:
    try:
        canonical = rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError):
        raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL) from None
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


class PublicApiClient:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self._config = config
        self._transport = transport
        self._sleep = sleep
        self._uuid_factory = uuid_factory

    def request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | None = None,
        body: bytes | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
        api_major: int = 1,
    ) -> dict[str, Any]:
        spec = _operation_spec(method, path)
        stable_request_id = request_id or f"req_{self._uuid_factory().hex}"
        if api_major == 2:
            contract_version = "2.0"
            idempotency_header = "X-CaseLoop-Idempotency-Key"
        else:
            contract_version = "1.0"
            idempotency_header = "Idempotency-Key"
        headers = {
            "Authorization": f"Bearer {self._config.token}",
            "X-CaseLoop-Workspace-ID": self._config.workspace_id,
            "X-CaseLoop-Contract-Version": contract_version,
            "X-CaseLoop-Client-Version": self._config.client_version,
            "X-Request-ID": stable_request_id,
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers[idempotency_header] = idempotency_key

        with httpx.Client(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        ) as client:
            response: httpx.Response | None = None
            for attempt in range(self._config.max_attempts):
                try:
                    response = client.request(method, path, params=params, content=body, headers=headers)
                except httpx.TransportError:
                    if attempt + 1 == self._config.max_attempts:
                        raise CliError("NETWORK_UNAVAILABLE", ExitFamily.TEMPORARY) from None
                    self._sleep(min(0.1 * (2**attempt), 1.0))
                    continue
                if response.status_code in _RETRYABLE_STATUS and attempt + 1 < self._config.max_attempts:
                    self._sleep(min(0.1 * (2**attempt), 1.0))
                    continue
                break

        assert response is not None
        if 300 <= response.status_code < 400:
            raise CliError("REMOTE_REDIRECT_REFUSED", ExitFamily.PROTOCOL)
        if response.headers.get("x-caseloop-contract-version") != contract_version:
            raise CliError("REMOTE_CONTRACT_INVALID", ExitFamily.PROTOCOL)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL) from None
        if not isinstance(payload, dict):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        if _contains_secret(payload, self._config.token):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        if 200 <= response.status_code < 300:
            if response.status_code != spec.status_code:
                raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
            try:
                success = spec.response_model.model_validate(payload)
            except ValidationError:
                raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL) from None
            self._validate_success_binding(
                spec,
                success,
                stable_request_id=stable_request_id,
                body=body,
                idempotency_key=idempotency_key,
                raw_payload=payload,
                params=params,
            )
            return payload
        try:
            public_error = PublicErrorEnvelope.model_validate(payload)
        except ValidationError:
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        code = public_error.error.code
        expected_error = _ERROR_META.get(code)
        if (
            expected_error is None
            or expected_error != (response.status_code, public_error.error.retryable)
            or public_error.error.request_id != stable_request_id
            or (
                public_error.workspace_resolved
                and public_error.workspace_id != self._config.workspace_id
            )
        ):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        raise CliError(
            code,
            _exit_for_status(response.status_code),
            payload=_safe_remote_error(payload, code),
        )

    def _validate_success_binding(
        self,
        spec: _OperationSpec,
        success: BaseModel,
        *,
        stable_request_id: str,
        body: bytes | None,
        idempotency_key: str | None,
        raw_payload: dict[str, Any],
        params: list[tuple[str, str]] | None,
    ) -> None:
        if getattr(success, "workspace_id", None) != self._config.workspace_id:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        response_request_id = getattr(success, "request_id", None)
        is_replayed_signal = (
            isinstance(success, SignalSubmissionResponse)
            and success.idempotency.replayed is True
        )
        is_replayed_v5 = (
            isinstance(
                success,
                (
                    ApplicationRegisterResponse,
                    EnvironmentRegisterResponse,
                    ComponentRegisterResponse,
                    DependencyEdgeRecordResponse,
                    SystemManifestImportResponse,
                ),
            )
            and success.idempotency.replayed is True
        )
        if not is_replayed_signal and not is_replayed_v5 and response_request_id != stable_request_id:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        if isinstance(success, ApplicationGetResponse):
            if success.application.application_id != spec.resource_id:
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return
        if isinstance(success, ApplicationListResponse):
            requested_projects = [
                value for name, value in (params or []) if name == "project_id"
            ]
            if len(requested_projects) != 1 or any(
                item.application.project_id != requested_projects[0]
                for item in success.items
            ):
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return
        if isinstance(success, ApplicationRegisterResponse):
            self._validate_v5_mutation_binding(
                spec, success, body, idempotency_key, raw_payload,
                resource_id=success.application.application_id,
                stable_request_id=stable_request_id,
            )
            return
        if isinstance(success, EnvironmentGetResponse):
            if success.environment.environment_id != spec.resource_id:
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return
        if isinstance(success, EnvironmentRegisterResponse):
            self._validate_v5_mutation_binding(
                spec, success, body, idempotency_key, raw_payload,
                resource_id=success.environment.environment_id,
                stable_request_id=stable_request_id,
            )
            return
        if isinstance(success, ComponentGetResponse):
            if success.component.component_id != spec.resource_id:
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return
        if isinstance(success, ComponentRegisterResponse):
            self._validate_v5_mutation_binding(
                spec, success, body, idempotency_key, raw_payload,
                resource_id=success.component.component_id,
                stable_request_id=stable_request_id,
            )
            return
        if isinstance(success, DependencyEdgeGetResponse):
            if success.edge.edge_id != spec.resource_id:
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return
        if isinstance(success, SystemManifestImportResponse):
            self._validate_v5_mutation_binding(
                spec, success, body, idempotency_key, raw_payload,
                resource_id=success.system_version_set.system_version_set_id,
                stable_request_id=stable_request_id,
            )
            return
        if isinstance(success, DependencyEdgeRecordResponse):
            self._validate_v5_mutation_binding(
                spec, success, body, idempotency_key, raw_payload,
                resource_id=success.edge.edge_id,
                stable_request_id=stable_request_id,
            )
            return
        if isinstance(success, CaseResponse):
            if success.data.case_id != spec.resource_id:
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return
        if isinstance(success, CaseTimelineResponse):
            if success.data.case_id != spec.resource_id:
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return
        if isinstance(success, EvidenceResponse):
            self._validate_evidence_binding(spec, success, raw_payload)
            return
        if not isinstance(success, SignalSubmissionResponse):
            return
        if body is None or idempotency_key is None:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        try:
            request_payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL) from None
        if not isinstance(request_payload, dict) or request_payload.get("run_locator") is not None:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        receipt = success.idempotency.receipt
        actual = (
            receipt.workspace_id,
            receipt.request_id,
            receipt.audit_ref,
            receipt.intent,
            receipt.idempotency_key,
            receipt.resource.kind,
            receipt.resource.id,
            receipt.operation_id,
            receipt.status,
            success.signal.source_event_id,
            success.evidence.agent_run_ref_id,
            success.evidence.status,
            success.case.status,
            success.case.correlation_status,
            success.case.triage_status,
            tuple(success.evidence.missing_fields),
            tuple(success.missing_fields),
            success.next_action.code,
            success.next_action.command,
            success.next_action.href,
        )
        expected = (
            self._config.workspace_id,
            success.request_id,
            success.audit_ref,
            "signals.submit",
            idempotency_key,
            "signal",
            success.signal.signal_id,
            None,
            "COMPLETED",
            request_payload.get("source_event_id"),
            None,
            "UNKNOWN",
            "OPEN",
            "NEEDS_CORRELATION",
            "UNTRIAGED",
            _MISSING_NO_TRACE,
            _MISSING_NO_TRACE,
            "CORRELATE_TRACE",
            None,
            None,
        )
        if actual != expected:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        raw_idempotency = raw_payload.get("idempotency")
        if not isinstance(raw_idempotency, dict):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        raw_receipt = raw_idempotency.get("receipt")
        if not isinstance(raw_receipt, dict):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        response_without_idempotency = dict(raw_payload)
        response_without_idempotency.pop("idempotency", None)
        receipt_without_self_digest = dict(raw_receipt)
        receipt_without_self_digest.pop("receipt_digest", None)
        if (
            receipt.request_fingerprint != _canonical_digest(request_payload)
            or receipt.response_digest
            != _canonical_digest(response_without_idempotency)
            or receipt.receipt_digest
            != _canonical_digest(receipt_without_self_digest)
        ):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)

    def _validate_v5_mutation_binding(
        self,
        spec: _OperationSpec,
        success: BaseModel,
        body: bytes | None,
        idempotency_key: str | None,
        raw_payload: dict[str, Any],
        *,
        resource_id: str,
        stable_request_id: str,
    ) -> None:
        if body is None or idempotency_key is None:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        try:
            request_payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL) from None
        if not isinstance(request_payload, dict):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        receipt = success.idempotency.receipt
        raw_idempotency = raw_payload.get("idempotency")
        if not isinstance(raw_idempotency, dict):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        raw_receipt = raw_idempotency.get("receipt")
        if not isinstance(raw_receipt, dict):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        response_without_idempotency = dict(raw_payload)
        response_without_idempotency.pop("idempotency", None)
        receipt_without_self_digest = dict(raw_receipt)
        receipt_without_self_digest.pop("receipt_digest", None)
        replayed = success.idempotency.replayed is True
        if isinstance(success, SystemManifestImportResponse):
            expected_principal = success.bootstrap_attestation.attester_principal_id
        else:
            # Catalog records are sealed by the registered authority controller,
            # while the idempotency receipt names the authenticated caller.  The
            # opaque CLI credential does not expose a separately trusted caller
            # principal, so only manifest import has an in-response exact actor
            # binding (the bootstrap attester).
            expected_principal = None
        if (
            receipt.request_fingerprint != _canonical_digest(request_payload)
            or receipt.response_digest
            != _canonical_digest(response_without_idempotency)
            or receipt.receipt_digest
            != _canonical_digest(receipt_without_self_digest)
            or receipt.workspace_id != self._config.workspace_id
            or receipt.audit_ref != success.audit_ref
            or receipt.intent != spec.name
            or receipt.idempotency_key != idempotency_key
            or receipt.request_id != success.request_id
            or (not replayed and receipt.request_id != stable_request_id)
            or (
                expected_principal is not None
                and receipt.principal_id != expected_principal
            )
            or receipt.resource.kind != _V2_RECEIPT_RESOURCE_KINDS.get(spec.name)
            or receipt.resource.id != resource_id
            or receipt.status != "COMPLETED"
            or receipt.operation_id is not None
        ):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)

    def _validate_evidence_binding(
        self,
        spec: _OperationSpec,
        success: EvidenceResponse,
        raw_payload: dict[str, Any],
    ) -> None:
        raw_data = raw_payload.get("data")
        raw_receipt = raw_data.get("receipt") if isinstance(raw_data, dict) else None
        if not isinstance(raw_data, dict) or not isinstance(raw_receipt, dict):
            raise CliError("REMOTE_PROTOCOL_ERROR", ExitFamily.PROTOCOL)
        receipt_without_self_digest = dict(raw_receipt)
        receipt_without_self_digest.pop("receipt_digest", None)
        computed_digest = _canonical_digest(receipt_without_self_digest)
        receipt = success.data.receipt
        if (
            receipt.receipt_id != spec.resource_id
            or receipt.workspace_id != self._config.workspace_id
            or success.data.receipt_digest != receipt.receipt_digest
            or raw_data.get("receipt_digest") != raw_receipt.get("receipt_digest")
            or receipt.receipt_digest != computed_digest
        ):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)

        results = {item.name: item.status for item in receipt.field_results}
        if set(results) != set(receipt.requested_fields):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        if (receipt.agent_run_ref_id is None) != (
            receipt.agent_run_ref_digest is None
        ):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)

        statuses = set(results.values())
        if receipt.collection_mode == "NO_LOCATOR":
            if (
                set(receipt.requested_fields) != set(_MISSING_NO_TRACE)
                or statuses != {"MISSING"}
                or receipt.query is not None
                or receipt.agent_run_ref_id is not None
                or receipt.agent_run_ref_digest is not None
                or receipt.completeness != "UNKNOWN"
                or receipt.artifact_ref is not None
                or receipt.source_payload_digest is not None
                or receipt.deep_link is not None
                or receipt.failure is None
                or receipt.failure.code != "NO_TRACE_LOCATOR"
                or receipt.failure.retryable is not False
            ):
                raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
            return

        if receipt.query is None:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        if receipt.completeness == "COMPLETE":
            valid_completeness = (
                statuses == {"OBSERVED"}
                and receipt.artifact_ref is not None
                and receipt.failure is None
            )
        elif receipt.completeness == "PARTIAL":
            valid_completeness = (
                statuses == {"OBSERVED", "MISSING"}
                and receipt.artifact_ref is not None
                and receipt.failure is None
            )
        else:
            valid_completeness = "MISSING" in statuses and receipt.failure is not None
        if not valid_completeness:
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
