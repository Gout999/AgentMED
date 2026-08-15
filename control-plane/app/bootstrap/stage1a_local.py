"""Fail-closed local bootstrap for the Stage 1A public vertical slice.

The future CLI creates and safely stages the opaque bearer before invoking
this module.  The raw bearer and raw jti enter only in the JSON document on
stdin; only HMAC/SHA-256 digests reach PostgreSQL and neither secret is ever
returned.  This module deliberately has no schema creation path: Alembic must
already be at one head containing revision 007.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sys
from typing import Any, Literal, TextIO

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.models import Audit
from app.models.v4_tables import (
    ControllerRegistration,
    PublicCredential,
    PublicPrincipal,
    SourceConnection,
)
from app.public_api.credential_resolver import (
    digest_public_subject,
    hash_opaque_bearer,
)
from app.services.authority import (
    AuthorityError,
    build_controller_registration_record,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.utils.ids import new_transaction_id
from app.utils.v4_integrity import V4IntegrityError, canonical_digest


_WORKSPACE_ID = r"^ws_[0-9A-Za-z]{8,64}$"
_PROJECT_ID = r"^proj_[0-9A-Za-z]{8,64}$"
_ENVIRONMENT_ID = r"^env_[0-9A-Za-z]{8,64}$"
_SOURCE_ID = r"^src_[0-9A-Za-z]{8,64}$"
_PRINCIPAL_ID = r"^prn_[0-9A-Za-z]{8,64}$"
_CREDENTIAL_ID = r"^cred_[0-9A-Za-z]{8,64}$"
_REGISTRATION_ID = r"^creg_[0-9A-Za-z]{8,64}$"
_DIGEST = r"^sha256:[0-9a-f]{64}$"
_SECRET_STORAGE_REF = re.compile(r"^(?:keyring|file)://[^\s]{1,512}$")
_SECRET_LIKE_KEY = re.compile(
    r"(?:secret|token|password|passwd|credential|api[-_]?key|private[-_]?key|"
    r"authorization|cookie)",
    re.IGNORECASE,
)
_MAX_STDIN_BYTES = 65_536

_AUDIENCE = ["caseloop-public-api"]
_SCOPES = [
    "artifacts:read",
    "capabilities:read",
    "cases:read",
    "signals:write",
]
_CONTROLLER_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "signal": (
        "signal-controller",
        ("signals.link-case", "signals.submit"),
    ),
    "case": ("case-controller", ("cases.open-from-signal",)),
    "evidence": ("evidence-controller", ("evidence.record",)),
}


class BootstrapError(RuntimeError):
    """Stable, secret-free local bootstrap failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _StrictJSONError(ValueError):
    """Internal parser rejection whose message never includes caller input."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise _StrictJSONError("strict_json_duplicate_key")
        value[key] = child
    return value


def _reject_json_constant(_constant: str) -> Any:
    raise _StrictJSONError("strict_json_nonfinite_number")


def _load_strict_json(raw: str) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManualSourceRequest(_StrictModel):
    source_id: str = Field(pattern=_SOURCE_ID)
    connector_kind: Literal["manual"]
    state: Literal["ACTIVE"]
    credential_ref: None
    config: dict[str, Any] = Field(default_factory=dict)


class HumanPrincipalRequest(_StrictModel):
    principal_id: str = Field(pattern=_PRINCIPAL_ID)
    subject: str = Field(min_length=1, max_length=256)


class OpaqueCredentialRequest(_StrictModel):
    credential_id: str = Field(pattern=_CREDENTIAL_ID)
    bearer_token: SecretStr
    jti: SecretStr
    issued_at: datetime
    not_before: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def _validate_secret_and_time_bounds(self) -> "OpaqueCredentialRequest":
        if not 32 <= len(self.bearer_token.get_secret_value()) <= 4096:
            raise ValueError("opaque bearer length is invalid")
        if not 16 <= len(self.jti.get_secret_value()) <= 512:
            raise ValueError("opaque credential jti length is invalid")
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.issued_at, self.not_before, self.expires_at)
        ):
            raise ValueError("credential times must carry an explicit timezone")
        issued = _as_utc(self.issued_at)
        not_before = _as_utc(self.not_before)
        expires = _as_utc(self.expires_at)
        if not (issued <= not_before < expires):
            raise ValueError("credential time order is invalid")
        return self


class ControllerIdentityRequest(_StrictModel):
    registration_id: str = Field(pattern=_REGISTRATION_ID)
    principal_id: str = Field(pattern=_PRINCIPAL_ID)


class ControllerSetRequest(_StrictModel):
    signal: ControllerIdentityRequest
    case: ControllerIdentityRequest
    evidence: ControllerIdentityRequest


class Stage1ALocalBootstrapRequest(_StrictModel):
    schema_version: Literal["1.0"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    environment_id: str = Field(pattern=_ENVIRONMENT_ID)
    source: ManualSourceRequest
    principal: HumanPrincipalRequest
    credential: OpaqueCredentialRequest
    controllers: ControllerSetRequest
    secret_storage_ref: str

    @model_validator(mode="after")
    def _validate_identity_separation(self) -> "Stage1ALocalBootstrapRequest":
        if not _SECRET_STORAGE_REF.fullmatch(self.secret_storage_ref):
            raise ValueError("secret storage reference is invalid")
        identities = [
            self.controllers.signal,
            self.controllers.case,
            self.controllers.evidence,
        ]
        registration_ids = {item.registration_id for item in identities}
        controller_principals = {item.principal_id for item in identities}
        if len(registration_ids) != 3 or len(controller_principals) != 3:
            raise ValueError("controller identities must be unique")
        if self.principal.principal_id in controller_principals:
            raise ValueError("controller cannot be the public principal")
        bearer = self.credential.bearer_token.get_secret_value()
        jti = self.credential.jti.get_secret_value()
        if bearer in self.secret_storage_ref or jti in self.secret_storage_ref:
            raise ValueError("secret storage reference cannot contain credential material")
        return self


class SourceBootstrapReceipt(_StrictModel):
    source_id: str = Field(pattern=_SOURCE_ID)
    connection_digest: str = Field(pattern=_DIGEST)
    created: bool


class PrincipalBootstrapReceipt(_StrictModel):
    principal_id: str = Field(pattern=_PRINCIPAL_ID)
    claims_digest: str = Field(pattern=_DIGEST)
    created: bool


class CredentialBootstrapReceipt(_StrictModel):
    credential_id: str = Field(pattern=_CREDENTIAL_ID)
    jti_digest: str = Field(pattern=_DIGEST)
    claims_digest: str = Field(pattern=_DIGEST)
    expires_at: datetime
    created: bool


class ControllerBootstrapReceipt(_StrictModel):
    owner: str
    registration_id: str = Field(pattern=_REGISTRATION_ID)
    controller_principal: str = Field(pattern=_PRINCIPAL_ID)
    registration_digest: str = Field(pattern=_DIGEST)
    registration_audit_ref: str
    created: bool


class Stage1ALocalBootstrapReceipt(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["CREATED", "REUSED", "MIXED"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    environment_id: str = Field(pattern=_ENVIRONMENT_ID)
    source: SourceBootstrapReceipt
    principal: PrincipalBootstrapReceipt
    credential: CredentialBootstrapReceipt
    controllers: list[ControllerBootstrapReceipt]
    transaction_id: str
    command_audit_ref: str
    secret_storage_ref: str


SchemaVerifier = Callable[[Session], None]
Clock = Callable[[], datetime]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_time(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    return _as_utc(left) == _as_utc(right)


def _secret_text(value: Any) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, str):
        return value
    return ""


def _nested_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for child in value.values():
            result.extend(_nested_strings(child))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        result = []
        for child in value:
            result.extend(_nested_strings(child))
        return result
    return []


def _validate_independent_public_secrets(settings: Settings) -> None:
    pepper = settings.public_credential_hash_pepper.get_secret_value()
    cursor = settings.public_cursor_signing_key.get_secret_value()
    if not pepper or not cursor or hmac.compare_digest(pepper, cursor):
        raise BootstrapError("bootstrap.public_secret_configuration_invalid")

    forbidden: list[str] = []
    for field_name in type(settings).model_fields:
        if field_name in {
            "public_credential_hash_pepper",
            "public_cursor_signing_key",
        }:
            continue
        if not re.search(r"(?:token|secret|password|credential|key)", field_name):
            continue
        value = getattr(settings, field_name)
        if field_name == "control_plane_role_tokens_json":
            try:
                value = json.loads(value)
            except Exception:
                raise BootstrapError(
                    "bootstrap.public_secret_configuration_invalid"
                ) from None
            forbidden.extend(item for item in _nested_strings(value) if item)
            continue
        text_value = _secret_text(value)
        if text_value:
            forbidden.append(text_value)
    if any(
        hmac.compare_digest(candidate, pepper)
        or hmac.compare_digest(candidate, cursor)
        for candidate in forbidden
    ):
        raise BootstrapError("bootstrap.public_secret_configuration_invalid")
    if not settings.public_auth_issuer:
        raise BootstrapError("bootstrap.public_secret_configuration_invalid")


def _contains_secret_like_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET_LIKE_KEY.search(str(key)) or _contains_secret_like_key(child):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret_like_key(child) for child in value)
    return False


def _alembic_script() -> ScriptDirectory:
    control_plane = Path(__file__).resolve().parents[2]
    config = AlembicConfig(str(control_plane / "alembic.ini"))
    config.set_main_option("script_location", str(control_plane / "alembic"))
    return ScriptDirectory.from_config(config)


def verify_stage1a_alembic_head(
    session: Session, *, require_postgresql: bool = True
) -> None:
    """Require one DB head, equal to the local head, whose ancestry has 007."""

    try:
        if require_postgresql and session.get_bind().dialect.name != "postgresql":
            raise BootstrapError("bootstrap.postgresql_required")
        versions = list(
            session.execute(sa.text("SELECT version_num FROM alembic_version")).scalars()
        )
        script = _alembic_script()
        heads = list(script.get_heads())
        if len(versions) != 1 or len(heads) != 1 or versions[0] != heads[0]:
            raise BootstrapError("bootstrap.schema_revision_not_ready")
        ancestry = {
            revision.revision
            for revision in script.iterate_revisions(heads[0], "base")
        }
        if "007" not in ancestry:
            raise BootstrapError("bootstrap.schema_revision_not_ready")
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("bootstrap.schema_revision_not_ready") from None


def _acquire_workspace_bootstrap_lock(session: Session, workspace_id: str) -> None:
    """Serialize absence checks on PG so two initializers cannot both win."""

    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"agentmed:stage1a-local:{workspace_id}"},
    )


def _sha256_text(value: SecretStr) -> str:
    return "sha256:" + hashlib.sha256(
        value.get_secret_value().encode("utf-8")
    ).hexdigest()


def _ensure_current_credential(request: Stage1ALocalBootstrapRequest, now: datetime) -> None:
    issued = _as_utc(request.credential.issued_at)
    not_before = _as_utc(request.credential.not_before)
    expires = _as_utc(request.credential.expires_at)
    current = _as_utc(now)
    if issued > current or not_before > current or expires <= current:
        raise BootstrapError("bootstrap.credential_not_current")


def _connection_values(
    request: Stage1ALocalBootstrapRequest,
) -> tuple[dict[str, Any], str]:
    record = {
        "schema_version": "1.0",
        "workspace_id": request.workspace_id,
        "source_id": request.source.source_id,
        "connector_kind": "manual",
        "state": "ACTIVE",
        "credential_ref": None,
        "config": request.source.config,
        "revision": 1,
        "created_by_principal": request.principal.principal_id,
    }
    try:
        digest = canonical_digest(record)
    except V4IntegrityError:
        raise BootstrapError("bootstrap.source_config_invalid") from None
    return record, digest


def _claim_values(
    request: Stage1ALocalBootstrapRequest, settings: Settings
) -> tuple[dict[str, Any], str, str]:
    claims = {
        "schema_version": "1.0",
        "issuer": settings.public_auth_issuer,
        "subject": request.principal.subject,
        "principal_type": "human",
        "audiences": _AUDIENCE,
        "workspace_id": request.workspace_id,
        "project_ids": [request.project_id],
        "environment_ids": [request.environment_id],
        "scopes": _SCOPES,
    }
    return (
        claims,
        canonical_digest(claims),
        digest_public_subject(request.principal.subject),
    )


def _service_identity_digest(
    *, workspace_id: str, owner: str, controller_principal: str
) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "workspace_id": workspace_id,
            "owner": owner,
            "controller_principal": controller_principal,
            "principal_type": "CONTROLLER_SERVICE",
            "service": "agentmed-control-plane",
        }
    )


def _assert_exact_source(
    row: SourceConnection, expected: dict[str, Any], digest: str
) -> None:
    checks = {
        "source_id": expected["source_id"],
        "workspace_id": expected["workspace_id"],
        "connector_kind": "manual",
        "state": "ACTIVE",
        "credential_ref": None,
        "config": expected["config"],
        "connection_digest": digest,
        "revision": 1,
        "created_by_principal": expected["created_by_principal"],
    }
    if any(getattr(row, key) != value for key, value in checks.items()):
        raise BootstrapError("bootstrap.source_drift")


def _assert_exact_principal(
    row: PublicPrincipal,
    *,
    request: Stage1ALocalBootstrapRequest,
    claims_digest: str,
    subject_digest: str,
) -> None:
    checks = {
        "principal_id": request.principal.principal_id,
        "workspace_id": request.workspace_id,
        "principal_type": "human",
        "state": "ACTIVE",
        "subject_digest": subject_digest,
        "audiences": _AUDIENCE,
        "project_ids": [request.project_id],
        "environment_ids": [request.environment_id],
        "scopes": _SCOPES,
        "claims_digest": claims_digest,
        "revoked_at": None,
    }
    if any(getattr(row, key) != value for key, value in checks.items()):
        raise BootstrapError("bootstrap.principal_drift")


def _assert_exact_credential(
    row: PublicCredential,
    *,
    request: Stage1ALocalBootstrapRequest,
    settings: Settings,
    credential_hash: str,
    jti_digest: str,
    claims_digest: str,
) -> None:
    checks = {
        "credential_id": request.credential.credential_id,
        "workspace_id": request.workspace_id,
        "principal_id": request.principal.principal_id,
        "issuer": settings.public_auth_issuer,
        "subject": request.principal.subject,
        "hash_algorithm": "hmac-sha256-v1",
        "jti_digest": jti_digest,
        "claims_digest": claims_digest,
        "audiences": _AUDIENCE,
        "project_ids": [request.project_id],
        "environment_ids": [request.environment_id],
        "scopes": _SCOPES,
        "state": "ACTIVE",
        "revoked_at": None,
    }
    if any(getattr(row, key) != value for key, value in checks.items()):
        raise BootstrapError("bootstrap.credential_drift")
    if not hmac.compare_digest(row.credential_hash, credential_hash):
        raise BootstrapError("bootstrap.credential_drift")
    if not all(
        (
            _same_time(row.issued_at, request.credential.issued_at),
            _same_time(row.not_before, request.credential.not_before),
            _same_time(row.expires_at, request.credential.expires_at),
        )
    ):
        raise BootstrapError("bootstrap.credential_drift")


def _registration_audit_evidence(
    *, owner: str, registration_id: str, controller_principal: str
) -> dict[str, Any]:
    return {
        "owner": owner,
        "controller_registration_id": registration_id,
        "controller_principal": controller_principal,
    }


def _validate_registration_audit(
    session: Session,
    row: ControllerRegistration,
    *,
    human_principal: str,
) -> None:
    if not row.registration_audit_ref.startswith("audit://aud_"):
        raise BootstrapError("bootstrap.controller_registration_drift")
    audit = session.get(Audit, row.registration_audit_ref.removeprefix("audit://"))
    evidence = _registration_audit_evidence(
        owner=row.owner,
        registration_id=row.controller_registration_id,
        controller_principal=row.controller_principal,
    )
    expected_target = (
        row.controller_registration_id
    )
    if (
        audit is None
        or audit.contract_version != "v4"
        or audit.workspace_id != row.workspace_id
        or audit.actor_principal != human_principal
        or audit.actor != human_principal
        or audit.action != "controllers.register"
        or audit.target != expected_target
        or audit.result != "success"
        or audit.error_code is not None
        or audit.params_digest
        != canonical_digest(
            {
                "owner": row.owner,
                "service_identity_digest": row.service_identity_digest,
            }
        )
        or audit.evidence_refs != evidence
    ):
        raise BootstrapError("bootstrap.controller_registration_drift")


def _controller_input(
    request: Stage1ALocalBootstrapRequest, kind: str
) -> ControllerIdentityRequest:
    return getattr(request.controllers, kind)


def _preflight_existing_controller(
    session: Session,
    *,
    request: Stage1ALocalBootstrapRequest,
    kind: str,
    now: datetime,
    contracts_root: str | Path | None,
) -> ControllerRegistration | None:
    owner, commands = _CONTROLLER_SPECS[kind]
    identity = _controller_input(request, kind)
    active = list(
        session.scalars(
            select(ControllerRegistration)
            .where(
                ControllerRegistration.workspace_id == request.workspace_id,
                ControllerRegistration.owner == owner,
                ControllerRegistration.state == "ACTIVE",
            )
            .with_for_update()
        ).all()
    )
    if len(active) > 1:
        raise BootstrapError(
            "bootstrap.multiple_active_controller_registrations"
        )
    if not active:
        occupied = session.get(
            ControllerRegistration, (identity.registration_id, 1)
        )
        if occupied is not None:
            raise BootstrapError("bootstrap.controller_registration_drift")
        return None

    row = active[0]
    if (
        row.controller_registration_id != identity.registration_id
        or row.revision != 1
        or row.state != "ACTIVE"
        or row.workspace_id != request.workspace_id
        or row.owner != owner
        or row.controller_principal != identity.principal_id
        or row.allowed_commands != list(commands)
        or row.previous_snapshot is not None
        or row.expires_at is not None
        or row.registered_by_human_principal != request.principal.principal_id
        or _as_utc(row.valid_from) > _as_utc(now)
    ):
        raise BootstrapError("bootstrap.controller_registration_drift")
    _validate_registration_audit(
        session, row, human_principal=request.principal.principal_id
    )
    try:
        expected = build_controller_registration_record(
            controller_registration_id=identity.registration_id,
            workspace_id=request.workspace_id,
            owner=owner,
            controller_principal=identity.principal_id,
            allowed_commands=list(commands),
            service_identity_digest=_service_identity_digest(
                workspace_id=request.workspace_id,
                owner=owner,
                controller_principal=identity.principal_id,
            ),
            registered_by_human_principal=request.principal.principal_id,
            registration_audit_ref=row.registration_audit_ref,
            valid_from=_as_utc(row.valid_from),
            registered_at=_as_utc(row.registered_at),
            expires_at=None,
            revision=1,
            previous_snapshot=None,
            contracts_root=contracts_root,
        )
    except AuthorityError:
        raise BootstrapError("bootstrap.controller_registration_drift") from None
    if row.registration_digest != expected.registration_digest:
        raise BootstrapError("bootstrap.controller_registration_drift")
    for key, value in expected.row_values.items():
        actual = getattr(row, key)
        if isinstance(value, datetime):
            if not _same_time(actual, value):
                raise BootstrapError("bootstrap.controller_registration_drift")
        elif actual != value:
            raise BootstrapError("bootstrap.controller_registration_drift")
    return row


def execute_stage1a_local_bootstrap(
    session: Session,
    request: Stage1ALocalBootstrapRequest,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    schema_verifier: SchemaVerifier = verify_stage1a_alembic_head,
    audit_service: V4AuditService | None = None,
    contracts_root: str | Path | None = None,
) -> Stage1ALocalBootstrapReceipt:
    """Flush one exact bootstrap transaction; caller owns commit/rollback."""

    configured = settings or get_settings()
    current = _as_utc(now or datetime.now(timezone.utc))
    _validate_independent_public_secrets(configured)
    schema_verifier(session)
    _ensure_current_credential(request, current)
    if _contains_secret_like_key(request.source.config):
        raise BootstrapError("bootstrap.source_config_contains_secret_key")
    raw_secrets = (
        request.credential.bearer_token.get_secret_value(),
        request.credential.jti.get_secret_value(),
    )
    exposed_inputs = (
        request.principal.subject,
        request.secret_storage_ref,
        json.dumps(request.source.config, sort_keys=True, default=str),
    )
    if any(secret in value for secret in raw_secrets for value in exposed_inputs):
        raise BootstrapError("bootstrap.credential_material_reused")
    _acquire_workspace_bootstrap_lock(session, request.workspace_id)

    source_values, connection_digest = _connection_values(request)
    _, claims_digest, subject_digest = _claim_values(request, configured)
    credential_hash = hash_opaque_bearer(
        request.credential.bearer_token,
        configured.public_credential_hash_pepper,
    )
    jti_digest = _sha256_text(request.credential.jti)

    active_sources = list(
        session.scalars(
            select(SourceConnection)
            .where(
                SourceConnection.workspace_id == request.workspace_id,
                SourceConnection.connector_kind == "manual",
                SourceConnection.state == "ACTIVE",
            )
            .with_for_update()
        ).all()
    )
    if len(active_sources) > 1:
        raise BootstrapError("bootstrap.multiple_active_manual_sources")
    source = session.get(SourceConnection, request.source.source_id)
    source_created = source is None
    if source is not None:
        _assert_exact_source(source, source_values, connection_digest)
    elif active_sources:
        raise BootstrapError("bootstrap.source_drift")

    principal = session.get(PublicPrincipal, request.principal.principal_id)
    principal_created = principal is None
    if principal is not None:
        _assert_exact_principal(
            principal,
            request=request,
            claims_digest=claims_digest,
            subject_digest=subject_digest,
        )

    active_credentials = list(
        session.scalars(
            select(PublicCredential)
            .where(
                PublicCredential.workspace_id == request.workspace_id,
                PublicCredential.principal_id == request.principal.principal_id,
                PublicCredential.state == "ACTIVE",
                PublicCredential.revoked_at.is_(None),
            )
            .with_for_update()
        ).all()
    )
    if len(active_credentials) > 1:
        raise BootstrapError("bootstrap.multiple_active_public_credentials")
    credential = session.get(PublicCredential, request.credential.credential_id)
    credential_created = credential is None
    if credential is not None:
        _assert_exact_credential(
            credential,
            request=request,
            settings=configured,
            credential_hash=credential_hash,
            jti_digest=jti_digest,
            claims_digest=claims_digest,
        )
    elif active_credentials:
        raise BootstrapError("bootstrap.credential_drift")

    controller_public_principals = list(
        session.scalars(
            select(PublicPrincipal.principal_id).where(
                PublicPrincipal.principal_id.in_(
                    [
                        request.controllers.signal.principal_id,
                        request.controllers.case.principal_id,
                        request.controllers.evidence.principal_id,
                    ]
                )
            )
        ).all()
    )
    if controller_public_principals:
        raise BootstrapError("bootstrap.controller_is_public_principal")

    existing_controllers = {
        kind: _preflight_existing_controller(
            session,
            request=request,
            kind=kind,
            now=current,
            contracts_root=contracts_root,
        )
        for kind in _CONTROLLER_SPECS
    }

    transaction_id = new_transaction_id()
    audit = audit_service or V4AuditService(session, clock=lambda: current)

    if source is None:
        source = SourceConnection(
            source_id=request.source.source_id,
            workspace_id=request.workspace_id,
            connector_kind="manual",
            state="ACTIVE",
            credential_ref=None,
            config=request.source.config,
            connection_digest=connection_digest,
            revision=1,
            created_by_principal=request.principal.principal_id,
        )
        session.add(source)
    if principal is None:
        principal = PublicPrincipal(
            principal_id=request.principal.principal_id,
            workspace_id=request.workspace_id,
            principal_type="human",
            state="ACTIVE",
            subject_digest=subject_digest,
            audiences=list(_AUDIENCE),
            project_ids=[request.project_id],
            environment_ids=[request.environment_id],
            scopes=list(_SCOPES),
            claims_digest=claims_digest,
            revoked_at=None,
        )
        session.add(principal)
    # The v4 projection models intentionally have no ORM relationships.  A
    # flush boundary makes the principal FK target visible before SQLAlchemy
    # emits the credential INSERT; it remains inside this one transaction.
    if source_created or principal_created:
        try:
            session.flush()
        except Exception as exc:
            raise BootstrapError("bootstrap.persistence_failed") from exc
    if credential is None:
        credential = PublicCredential(
            credential_id=request.credential.credential_id,
            workspace_id=request.workspace_id,
            principal_id=request.principal.principal_id,
            issuer=configured.public_auth_issuer,
            subject=request.principal.subject,
            credential_hash=credential_hash,
            hash_algorithm="hmac-sha256-v1",
            jti_digest=jti_digest,
            claims_digest=claims_digest,
            audiences=list(_AUDIENCE),
            project_ids=[request.project_id],
            environment_ids=[request.environment_id],
            scopes=list(_SCOPES),
            state="ACTIVE",
            issued_at=_as_utc(request.credential.issued_at),
            not_before=_as_utc(request.credential.not_before),
            expires_at=_as_utc(request.credential.expires_at),
            revoked_at=None,
        )
        session.add(credential)
        try:
            session.flush()
        except Exception as exc:
            raise BootstrapError("bootstrap.persistence_failed") from exc

    controller_receipts: list[ControllerBootstrapReceipt] = []
    for kind, (owner, commands) in _CONTROLLER_SPECS.items():
        identity = _controller_input(request, kind)
        registration = existing_controllers[kind]
        created = registration is None
        if registration is None:
            service_identity_digest = _service_identity_digest(
                workspace_id=request.workspace_id,
                owner=owner,
                controller_principal=identity.principal_id,
            )
            evidence = _registration_audit_evidence(
                owner=owner,
                registration_id=identity.registration_id,
                controller_principal=identity.principal_id,
            )
            recorded_audit = audit.record(
                workspace_id=request.workspace_id,
                actor_principal=request.principal.principal_id,
                action="controllers.register",
                target=identity.registration_id,
                params={
                    "owner": owner,
                    "service_identity_digest": service_identity_digest,
                },
                transaction_id=transaction_id,
                evidence_refs=evidence,
                occurred_at=current,
            )
            try:
                built = build_controller_registration_record(
                    controller_registration_id=identity.registration_id,
                    workspace_id=request.workspace_id,
                    owner=owner,
                    controller_principal=identity.principal_id,
                    allowed_commands=list(commands),
                    service_identity_digest=service_identity_digest,
                    registered_by_human_principal=request.principal.principal_id,
                    registration_audit_ref=recorded_audit.audit_ref,
                    valid_from=current,
                    registered_at=current,
                    contracts_root=contracts_root,
                )
            except AuthorityError as exc:
                raise BootstrapError("bootstrap.authority_dependency_invalid") from exc
            registration = ControllerRegistration(**built.row_values)
            session.add(registration)
            session.flush()
        controller_receipts.append(
            ControllerBootstrapReceipt(
                owner=owner,
                registration_id=registration.controller_registration_id,
                controller_principal=registration.controller_principal,
                registration_digest=registration.registration_digest,
                registration_audit_ref=registration.registration_audit_ref,
                created=created,
            )
        )

    created_flags = [
        source_created,
        principal_created,
        credential_created,
        *(item.created for item in controller_receipts),
    ]
    status: Literal["CREATED", "REUSED", "MIXED"]
    if all(created_flags):
        status = "CREATED"
    elif not any(created_flags):
        status = "REUSED"
    else:
        status = "MIXED"
    command_audit = audit.record(
        workspace_id=request.workspace_id,
        actor_principal=request.principal.principal_id,
        action="stage1a_local.bootstrap",
        target=f"workspace:{request.workspace_id}",
        params={
            "workspace_id": request.workspace_id,
            "project_id": request.project_id,
            "environment_id": request.environment_id,
            "source_id": request.source.source_id,
            "principal_id": request.principal.principal_id,
            "credential_id": request.credential.credential_id,
            "jti_digest": jti_digest,
            "claims_digest": claims_digest,
            "controller_registration_digests": {
                item.owner: item.registration_digest for item in controller_receipts
            },
            "result": status,
        },
        transaction_id=transaction_id,
        evidence_refs={
            "source_id": request.source.source_id,
            "principal_id": request.principal.principal_id,
            "credential_id": request.credential.credential_id,
            "controller_registration_ids": [
                item.registration_id for item in controller_receipts
            ],
        },
        occurred_at=current,
    )
    try:
        session.flush()
    except Exception as exc:
        raise BootstrapError("bootstrap.persistence_failed") from exc

    return Stage1ALocalBootstrapReceipt(
        status=status,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        environment_id=request.environment_id,
        source=SourceBootstrapReceipt(
            source_id=request.source.source_id,
            connection_digest=connection_digest,
            created=source_created,
        ),
        principal=PrincipalBootstrapReceipt(
            principal_id=request.principal.principal_id,
            claims_digest=claims_digest,
            created=principal_created,
        ),
        credential=CredentialBootstrapReceipt(
            credential_id=request.credential.credential_id,
            jti_digest=jti_digest,
            claims_digest=claims_digest,
            expires_at=_as_utc(request.credential.expires_at),
            created=credential_created,
        ),
        controllers=controller_receipts,
        transaction_id=transaction_id,
        command_audit_ref=command_audit.audit_ref,
        secret_storage_ref=request.secret_storage_ref,
    )


def _default_executor(
    request: Stage1ALocalBootstrapRequest,
) -> Stage1ALocalBootstrapReceipt:
    session = get_session_factory()()
    try:
        receipt = execute_stage1a_local_bootstrap(session, request)
        session.commit()
        return receipt
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _write_json(stream: TextIO, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def main(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    executor: Callable[
        [Stage1ALocalBootstrapRequest], Stage1ALocalBootstrapReceipt
    ]
    | None = None,
) -> int:
    """Read exactly one bounded JSON request and emit one secret-free JSON result."""

    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    _ = stderr or sys.stderr  # Reserved for non-secret diagnostics; intentionally unused.
    try:
        raw = input_stream.read(_MAX_STDIN_BYTES + 1)
        if len(raw.encode("utf-8")) > _MAX_STDIN_BYTES:
            raise BootstrapError("bootstrap.request_too_large")
        try:
            payload = _load_strict_json(raw)
            request = Stage1ALocalBootstrapRequest.model_validate(payload)
        except (
            json.JSONDecodeError,
            UnicodeError,
            ValidationError,
            TypeError,
            _StrictJSONError,
        ):
            raise BootstrapError("bootstrap.request_invalid") from None
        receipt = (executor or _default_executor)(request)
        if not isinstance(receipt, Stage1ALocalBootstrapReceipt):
            receipt = Stage1ALocalBootstrapReceipt.model_validate(receipt)
        _write_json(output_stream, receipt.model_dump(mode="json"))
        return 0
    except BootstrapError as exc:
        code = exc.code
    except V4AuditUnavailable:
        code = "bootstrap.audit_unavailable"
    except Exception:
        # Never serialize exception messages: dependency errors can contain a
        # database URL or a caller-supplied secret.
        code = "bootstrap.internal_error"
    _write_json(
        output_stream,
        {
            "schema_version": "1.0",
            "status": "ERROR",
            "error": {"code": code},
        },
    )
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through ``main``.
    raise SystemExit(main())


__all__ = [
    "BootstrapError",
    "Stage1ALocalBootstrapReceipt",
    "Stage1ALocalBootstrapRequest",
    "execute_stage1a_local_bootstrap",
    "main",
    "verify_stage1a_alembic_head",
]
