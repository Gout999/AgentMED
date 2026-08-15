"""Fail-closed local bootstrap for the V5 First System Case path.

This is the supported fresh-clone provisioner for the local V5-1A/B/C path.  A
single transaction creates the operator and maintainer principals, the opaque
operator credential, one manual ``SourceConnection``, the three V4 intake
controller roots, and the V5 catalog/version/case controller roots.  Trust
roles are written only to the server-owned principal rows and never accepted
from bearer claims.  Raw bearer/JTI values enter only on stdin; only digests
reach PostgreSQL.  Alembic must already be at the local head containing 011;
the exact local head (currently 012) is enforced at runtime.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
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
    QualityCase,
    SourceConnection,
)
from app.models.v5_tables import (
    AIApplication,
    AcceptanceCriteriaRevision,
    ApplicationCaseBinding,
    Environment,
)
from app.public_api.credential_resolver import (
    digest_public_subject,
    hash_opaque_bearer,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.services.authority import (
    AuthorityError,
    build_controller_registration_record,
    contract_catalog_digests,
)
from app.services.v5_authority import (
    V5AuthorityError,
    V5AuthorityService,
    V5_CATALOG_OWNER,
    build_v5_controller_registration_record,
    load_v5_contract_catalog,
)
from app.utils.ids import new_transaction_id
from app.utils.v4_integrity import V4IntegrityError, assert_record_digest, canonical_digest
from app.utils.v5_integrity import assert_v5_record_digest

_WORKSPACE_ID = r"^ws_[0-9A-Za-z]{8,64}$"
_PROJECT_ID = r"^proj_[0-9A-Za-z]{8,64}$"
_ENVIRONMENT_ID = r"^env_[0-9A-Za-z]{8,64}$"
_SOURCE_ID = r"^src_[0-9A-Za-z]{8,64}$"
_PRINCIPAL_ID = r"^prn_[0-9A-Za-z]{8,64}$"
_CREDENTIAL_ID = r"^cred_[0-9A-Za-z]{8,64}$"
_REGISTRATION_ID = r"^creg_[0-9A-Za-z]{8,64}$"
_ACCEPTANCE_REVISION_ID = r"^acr_[0-9A-Za-z]{8,64}$"
_DIGEST = r"^sha256:[0-9a-f]{64}$"
_SECRET_STORAGE_REF = re.compile(r"^(?:keyring|file)://[^\s]{1,512}$")
_SECRET_LIKE_KEY = re.compile(
    r"(?:secret|token|password|passwd|credential|api[-_]?key|private[-_]?key|"
    r"authorization|cookie)",
    re.IGNORECASE,
)
_MAX_STDIN_BYTES = 65_536
_OWNER_REAUTH_MAX_AGE = timedelta(minutes=5)

_AUDIENCE = ["caseloop-public-api"]
_OPERATOR_SCOPES = [
    "capabilities:read",
    "signals:write",
    "cases:read",
    "cases:bind",
    "acceptance_criteria:read",
    "acceptance_criteria:propose",
    "applications:manage",
    "applications:read",
    "system_manifests:import",
    "system_versions:read",
]
_OPERATOR_TRUST_ROLES = ["catalog_admin", "integrator"]
_OWNER_SCOPES = [
    "capabilities:read",
    "cases:read",
    "acceptance_criteria:read",
    "acceptance_criteria:confirm",
]
_OWNER_TRUST_ROLES = ["maintainer", "domain_reviewer"]
# The full application-catalog-controller command set from
# contracts/v5/aggregate-ownership.yaml record_authority.
_CATALOG_COMMANDS = (
    "applications.register",
    "applications.activate",
    "applications.archive",
    "applications.restore",
    "environments.register",
    "environments.retire",
    "environments.restore",
    "system-components.register",
    "system-components.activate",
    "system-components.deprecate",
    "system-components.reactivate",
    "system-components.retire",
    "dependency-edges.record",
)
_VERSION_COMMANDS = (
    "component-revisions.record",
    "topology-revisions.record",
    "system-versions.record",
    "bootstrap-attestations.record",
    "system-assignments.record",
)
_V5_CASE_COMMANDS = (
    "cases.bind-application",
    "acceptance-criteria.propose",
    "acceptance-criteria.confirm",
)
_V5_CONTROLLER_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "catalog": (V5_CATALOG_OWNER, _CATALOG_COMMANDS),
    "version": ("version-controller", _VERSION_COMMANDS),
    "case": ("case-controller", _V5_CASE_COMMANDS),
}
_V4_CONTROLLER_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "signal": ("signal-controller", ("signals.link-case", "signals.submit")),
    "case": ("case-controller", ("cases.open-from-signal",)),
    "evidence": ("evidence-controller", ("evidence.record",)),
}


class BootstrapError(RuntimeError):
    """Stable, secret-free local v5 bootstrap failure."""

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


class ControllerIdentityRequest(_StrictModel):
    registration_id: str = Field(pattern=_REGISTRATION_ID)
    principal_id: str = Field(pattern=_PRINCIPAL_ID)


class ControllerSetRequest(_StrictModel):
    signal: ControllerIdentityRequest
    case: ControllerIdentityRequest
    evidence: ControllerIdentityRequest


class ManualSourceRequest(_StrictModel):
    source_id: str = Field(pattern=_SOURCE_ID)
    connector_kind: Literal["manual"]
    state: Literal["ACTIVE"]
    credential_ref: None
    config: dict[str, Any] = Field(default_factory=dict)


class CatalogAdminPrincipalRequest(_StrictModel):
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


class V5CatalogLocalBootstrapRequest(_StrictModel):
    schema_version: Literal["1.0"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    owner_principal: CatalogAdminPrincipalRequest
    principal: CatalogAdminPrincipalRequest
    credential: OpaqueCredentialRequest
    source: ManualSourceRequest
    controller: ControllerIdentityRequest
    version_controller: ControllerIdentityRequest
    case_controller: ControllerIdentityRequest
    intake_controllers: ControllerSetRequest
    secret_storage_ref: str

    @model_validator(mode="after")
    def _validate_identity_separation(self) -> "V5CatalogLocalBootstrapRequest":
        if not _SECRET_STORAGE_REF.fullmatch(self.secret_storage_ref):
            raise ValueError("secret storage reference is invalid")
        public_identities = {
            self.owner_principal.principal_id,
            self.principal.principal_id,
        }
        controller_identities = (
            self.controller,
            self.version_controller,
            self.case_controller,
            self.intake_controllers.signal,
            self.intake_controllers.case,
            self.intake_controllers.evidence,
        )
        registration_ids = {item.registration_id for item in controller_identities}
        controller_principals = {item.principal_id for item in controller_identities}
        if len(public_identities) != 2:
            raise ValueError("owner and operator identities must be unique")
        if len(registration_ids) != len(controller_identities):
            raise ValueError("controller registration identities must be unique")
        if len(controller_principals) != len(controller_identities):
            raise ValueError("controller principal identities must be unique")
        if public_identities & controller_principals:
            raise ValueError("controller and public principal identities must be unique")
        bearer = self.credential.bearer_token.get_secret_value()
        jti = self.credential.jti.get_secret_value()
        serialized_source = json.dumps(self.source.config, sort_keys=True, default=str)
        if (
            bearer in self.secret_storage_ref
            or jti in self.secret_storage_ref
            or bearer in serialized_source
            or jti in serialized_source
        ):
            raise ValueError("secret storage reference cannot contain credential material")
        return self


class ExactProposedRevisionBindingRequest(_StrictModel):
    kind: Literal["ACCEPTANCE_CRITERIA_REVISION"]
    id: str = Field(pattern=_ACCEPTANCE_REVISION_ID)
    revision: int = Field(ge=1)
    digest: str = Field(pattern=_DIGEST)


class ExactEnvironmentBindingRequest(_StrictModel):
    kind: Literal["ENVIRONMENT"]
    id: str = Field(pattern=_ENVIRONMENT_ID)
    revision: int = Field(ge=1)
    digest: str = Field(pattern=_DIGEST)


class V5OperatorEnvironmentRotationRequest(_StrictModel):
    """Post-import operator credential rotation for one exact environment."""

    schema_version: Literal["1.0"]
    operation: Literal["operator_environment_rotation"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    principal: CatalogAdminPrincipalRequest
    previous_credential_id: str = Field(pattern=_CREDENTIAL_ID)
    credential: OpaqueCredentialRequest
    exact_environment_binding: ExactEnvironmentBindingRequest
    secret_storage_ref: str

    @model_validator(mode="after")
    def _validate_operator_rotation_shape(
        self,
    ) -> "V5OperatorEnvironmentRotationRequest":
        if not _SECRET_STORAGE_REF.fullmatch(self.secret_storage_ref):
            raise ValueError("secret storage reference is invalid")
        if self.previous_credential_id == self.credential.credential_id:
            raise ValueError("credential rotation requires a new credential identity")
        bearer = self.credential.bearer_token.get_secret_value()
        jti = self.credential.jti.get_secret_value()
        if bearer in self.secret_storage_ref or jti in self.secret_storage_ref:
            raise ValueError("secret storage reference cannot contain credential material")
        return self


class V5OwnerReauthenticationRequest(_StrictModel):
    """Second-phase local credential issuance after an exact proposal exists."""

    schema_version: Literal["1.0"]
    operation: Literal["owner_reauthentication"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    operator_principal_id: str = Field(pattern=_PRINCIPAL_ID)
    owner_principal: CatalogAdminPrincipalRequest
    credential: OpaqueCredentialRequest
    exact_proposed_revision_binding: ExactProposedRevisionBindingRequest
    secret_storage_ref: str

    @model_validator(mode="after")
    def _validate_owner_reauthentication_shape(
        self,
    ) -> "V5OwnerReauthenticationRequest":
        if not _SECRET_STORAGE_REF.fullmatch(self.secret_storage_ref):
            raise ValueError("secret storage reference is invalid")
        if self.operator_principal_id == self.owner_principal.principal_id:
            raise ValueError("owner and operator identities must be unique")
        bearer = self.credential.bearer_token.get_secret_value()
        jti = self.credential.jti.get_secret_value()
        if bearer in self.secret_storage_ref or jti in self.secret_storage_ref:
            raise ValueError("secret storage reference cannot contain credential material")
        return self


class ControllerBootstrapReceipt(_StrictModel):
    owner: str
    registration_id: str = Field(pattern=_REGISTRATION_ID)
    controller_principal: str = Field(pattern=_PRINCIPAL_ID)
    registration_digest: str = Field(pattern=_DIGEST)
    registration_audit_ref: str
    created: bool


class PrincipalBootstrapReceipt(_StrictModel):
    principal_id: str = Field(pattern=_PRINCIPAL_ID)
    claims_digest: str = Field(pattern=_DIGEST)
    trust_roles: list[str]
    created: bool


class OwnerPrincipalBootstrapReceipt(_StrictModel):
    principal_id: str = Field(pattern=_PRINCIPAL_ID)
    claims_digest: str = Field(pattern=_DIGEST)
    trust_roles: list[str]
    created: bool


class CredentialBootstrapReceipt(_StrictModel):
    credential_id: str = Field(pattern=_CREDENTIAL_ID)
    jti_digest: str = Field(pattern=_DIGEST)
    claims_digest: str = Field(pattern=_DIGEST)
    expires_at: datetime
    created: bool


class SourceBootstrapReceipt(_StrictModel):
    source_id: str = Field(pattern=_SOURCE_ID)
    connection_digest: str = Field(pattern=_DIGEST)
    created: bool


class V5CatalogLocalBootstrapReceipt(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["CREATED", "REUSED", "MIXED"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    source: SourceBootstrapReceipt
    controller: ControllerBootstrapReceipt
    controllers: list[ControllerBootstrapReceipt]
    owner_principal: OwnerPrincipalBootstrapReceipt
    principal: PrincipalBootstrapReceipt
    credential: CredentialBootstrapReceipt
    transaction_id: str
    command_audit_ref: str
    secret_storage_ref: str


class V5OwnerReauthenticationReceipt(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["owner_reauthentication"] = "owner_reauthentication"
    status: Literal["CREATED", "REUSED"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    owner_principal_id: str = Field(pattern=_PRINCIPAL_ID)
    operator_principal_id: str = Field(pattern=_PRINCIPAL_ID)
    exact_environment_binding: ExactEnvironmentBindingRequest
    exact_proposed_revision_binding: ExactProposedRevisionBindingRequest
    credential: CredentialBootstrapReceipt
    issuance_binding_digest: str = Field(pattern=_DIGEST)
    transaction_id: str
    command_audit_ref: str
    secret_storage_ref: str


class V5OperatorEnvironmentRotationReceipt(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    operation: Literal["operator_environment_rotation"] = (
        "operator_environment_rotation"
    )
    status: Literal["CREATED", "REUSED"]
    workspace_id: str = Field(pattern=_WORKSPACE_ID)
    project_id: str = Field(pattern=_PROJECT_ID)
    principal_id: str = Field(pattern=_PRINCIPAL_ID)
    exact_environment_binding: ExactEnvironmentBindingRequest
    previous_credential_id: str = Field(pattern=_CREDENTIAL_ID)
    credential: CredentialBootstrapReceipt
    rotation_binding_digest: str = Field(pattern=_DIGEST)
    transaction_id: str
    command_audit_ref: str
    secret_storage_ref: str


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


def _sha256_text(value: SecretStr) -> str:
    return "sha256:" + hashlib.sha256(value.get_secret_value().encode("utf-8")).hexdigest()


def _contains_secret_like_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET_LIKE_KEY.search(str(key)) or _contains_secret_like_key(child):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret_like_key(child) for child in value)
    return False


def _source_values(
    request: V5CatalogLocalBootstrapRequest,
) -> tuple[dict[str, Any], str]:
    values = {
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
    return values, canonical_digest(values)


def _assert_source(
    row: SourceConnection, *, expected: dict[str, Any], connection_digest: str
) -> None:
    checks = {
        "source_id": expected["source_id"],
        "workspace_id": expected["workspace_id"],
        "connector_kind": "manual",
        "state": "ACTIVE",
        "credential_ref": None,
        "config": expected["config"],
        "connection_digest": connection_digest,
        "revision": 1,
        "created_by_principal": expected["created_by_principal"],
    }
    if any(getattr(row, key) != value for key, value in checks.items()):
        raise BootstrapError("bootstrap.source_drift")


def _alembic_script() -> ScriptDirectory:
    control_plane = Path(__file__).resolve().parents[2]
    config = AlembicConfig(str(control_plane / "alembic.ini"))
    config.set_main_option("script_location", str(control_plane / "alembic"))
    return ScriptDirectory.from_config(config)


def verify_v5_catalog_alembic_head(
    session: Session, *, require_postgresql: bool = True
) -> None:
    """Require one DB head equal to local head with the complete V5-1C schema."""

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
        if not {"008", "009", "010", "011"}.issubset(ancestry):
            raise BootstrapError("bootstrap.schema_revision_not_ready")
    except BootstrapError:
        raise
    except Exception:
        raise BootstrapError("bootstrap.schema_revision_not_ready") from None


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
    if (
        audit is None
        or audit.contract_version != "v4"
        or audit.workspace_id != row.workspace_id
        or audit.actor_principal != human_principal
        or audit.actor != human_principal
        or audit.action != "controllers.register"
        or audit.target != row.controller_registration_id
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


def _v5_controller_input(
    request: V5CatalogLocalBootstrapRequest, kind: str
) -> ControllerIdentityRequest:
    if kind == "catalog":
        return request.controller
    if kind == "version":
        return request.version_controller
    if kind == "case":
        return request.case_controller
    raise BootstrapError("bootstrap.controller_kind_invalid")


def _v4_controller_input(
    request: V5CatalogLocalBootstrapRequest, kind: str
) -> ControllerIdentityRequest:
    return getattr(request.intake_controllers, kind)


def _v4_contracts_root(contracts_root: str | Path | None) -> str | Path | None:
    if contracts_root is None:
        return None
    candidate = Path(contracts_root)
    if candidate.name == "v5":
        return candidate.parent / "v4"
    if (candidate / "contracts" / "v5").is_dir():
        return candidate / "contracts" / "v4"
    return candidate


def _preflight_controller(
    session: Session,
    *,
    request: V5CatalogLocalBootstrapRequest,
    identity: ControllerIdentityRequest,
    owner: str,
    commands: tuple[str, ...],
    v5: bool,
    now: datetime,
    contracts_root: str | Path | None,
) -> ControllerRegistration | None:
    try:
        if v5:
            catalog = load_v5_contract_catalog(contracts_root)
            ownership_digest = catalog.ownership_digest
            event_digest = catalog.event_catalog_digest
        else:
            ownership_digest, event_digest = contract_catalog_digests(
                _v4_contracts_root(contracts_root)
            )
    except (AuthorityError, V5AuthorityError, OSError, ValueError):
        raise BootstrapError("bootstrap.authority_dependency_invalid") from None

    active = list(
        session.scalars(
            select(ControllerRegistration)
            .where(
                ControllerRegistration.workspace_id == request.workspace_id,
                ControllerRegistration.owner == owner,
                ControllerRegistration.state == "ACTIVE",
                ControllerRegistration.ownership_contract_digest == ownership_digest,
                ControllerRegistration.event_catalog_digest == event_digest,
            )
            .with_for_update()
        ).all()
    )
    if len(active) > 1:
        raise BootstrapError("bootstrap.multiple_active_controller_registrations")
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
        or row.workspace_id != request.workspace_id
        or row.owner != owner
        or row.controller_principal != identity.principal_id
        or row.allowed_commands != sorted(commands)
        or row.previous_snapshot is not None
        or row.expires_at is not None
        or row.registered_by_human_principal != request.principal.principal_id
        or _as_utc(row.valid_from) > now
    ):
        raise BootstrapError("bootstrap.controller_registration_drift")
    _validate_registration_audit(
        session, row, human_principal=request.principal.principal_id
    )
    service_identity_digest = _service_identity_digest(
        workspace_id=request.workspace_id,
        owner=owner,
        controller_principal=identity.principal_id,
    )
    try:
        builder = (
            build_v5_controller_registration_record
            if v5
            else build_controller_registration_record
        )
        expected = builder(
            controller_registration_id=identity.registration_id,
            workspace_id=request.workspace_id,
            owner=owner,
            controller_principal=identity.principal_id,
            allowed_commands=list(commands),
            service_identity_digest=service_identity_digest,
            registered_by_human_principal=request.principal.principal_id,
            registration_audit_ref=row.registration_audit_ref,
            valid_from=_as_utc(row.valid_from),
            registered_at=_as_utc(row.registered_at),
            expires_at=None,
            revision=1,
            previous_snapshot=None,
            contracts_root=(contracts_root if v5 else _v4_contracts_root(contracts_root)),
        )
    except (AuthorityError, V5AuthorityError):
        raise BootstrapError("bootstrap.controller_registration_drift") from None
    for key, value in expected.row_values.items():
        actual = getattr(row, key)
        if isinstance(value, datetime):
            if not _same_time(actual, value):
                raise BootstrapError("bootstrap.controller_registration_drift")
        elif actual != value:
            raise BootstrapError("bootstrap.controller_registration_drift")
    return row


def _create_controller(
    session: Session,
    *,
    request: V5CatalogLocalBootstrapRequest,
    identity: ControllerIdentityRequest,
    owner: str,
    commands: tuple[str, ...],
    v5: bool,
    now: datetime,
    transaction_id: str,
    audit: V4AuditService,
    contracts_root: str | Path | None,
) -> ControllerRegistration:
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
        occurred_at=now,
    )
    try:
        builder = (
            build_v5_controller_registration_record
            if v5
            else build_controller_registration_record
        )
        built = builder(
            controller_registration_id=identity.registration_id,
            workspace_id=request.workspace_id,
            owner=owner,
            controller_principal=identity.principal_id,
            allowed_commands=list(commands),
            service_identity_digest=service_identity_digest,
            registered_by_human_principal=request.principal.principal_id,
            registration_audit_ref=recorded_audit.audit_ref,
            valid_from=now,
            registered_at=now,
            contracts_root=(contracts_root if v5 else _v4_contracts_root(contracts_root)),
        )
    except (AuthorityError, V5AuthorityError) as exc:
        raise BootstrapError("bootstrap.authority_dependency_invalid") from exc
    registration = ControllerRegistration(**built.row_values)
    session.add(registration)
    session.flush()
    return registration


def execute_v5_catalog_local_bootstrap(
    session: Session,
    request: V5CatalogLocalBootstrapRequest,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    schema_verifier: Callable[[Session], None] = verify_v5_catalog_alembic_head,
    audit_service: V4AuditService | None = None,
    contracts_root: str | Path | None = None,
) -> V5CatalogLocalBootstrapReceipt:
    """Flush one exact v5 bootstrap transaction; caller owns commit/rollback."""

    configured = settings or get_settings()
    current = _as_utc(now or datetime.now(timezone.utc))
    schema_verifier(session)
    if not hasattr(PublicPrincipal, "trust_roles"):
        raise BootstrapError("bootstrap.schema_revision_not_ready")
    if _contains_secret_like_key(request.source.config):
        raise BootstrapError("bootstrap.source_config_contains_secret_key")
    issued = _as_utc(request.credential.issued_at)
    not_before = _as_utc(request.credential.not_before)
    expires = _as_utc(request.credential.expires_at)
    if issued > current or not_before > current or expires <= current:
        raise BootstrapError("bootstrap.credential_not_current")
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"agentmed:v5-catalog-local:{request.workspace_id}"},
        )

    claims = {
        "schema_version": "1.0",
        "issuer": configured.public_auth_issuer,
        "subject": request.principal.subject,
        "principal_type": "human",
        "audiences": _AUDIENCE,
        "workspace_id": request.workspace_id,
        "project_ids": [request.project_id],
        "environment_ids": [],
        "scopes": _OPERATOR_SCOPES,
    }
    claims_digest = canonical_digest(claims)
    subject_digest = digest_public_subject(request.principal.subject)
    credential_hash = hash_opaque_bearer(
        request.credential.bearer_token, configured.public_credential_hash_pepper
    )
    jti_digest = _sha256_text(request.credential.jti)

    source_values, connection_digest = _source_values(request)
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
        _assert_source(source, expected=source_values, connection_digest=connection_digest)
    elif active_sources:
        raise BootstrapError("bootstrap.source_drift")

    controller_identities = [
        request.controller,
        request.version_controller,
        request.case_controller,
        request.intake_controllers.signal,
        request.intake_controllers.case,
        request.intake_controllers.evidence,
    ]
    controller_public_principals = list(
        session.scalars(
            select(PublicPrincipal.principal_id).where(
                PublicPrincipal.principal_id.in_(
                    [item.principal_id for item in controller_identities]
                )
            )
        ).all()
    )
    if controller_public_principals:
        raise BootstrapError("bootstrap.controller_is_public_principal")

    existing_v5_controllers = {
        kind: _preflight_controller(
            session,
            request=request,
            identity=_v5_controller_input(request, kind),
            owner=owner,
            commands=commands,
            v5=True,
            now=current,
            contracts_root=contracts_root,
        )
        for kind, (owner, commands) in _V5_CONTROLLER_SPECS.items()
    }
    existing_v4_controllers = {
        kind: _preflight_controller(
            session,
            request=request,
            identity=_v4_controller_input(request, kind),
            owner=owner,
            commands=commands,
            v5=False,
            now=current,
            contracts_root=contracts_root,
        )
        for kind, (owner, commands) in _V4_CONTROLLER_SPECS.items()
    }

    principal = session.get(PublicPrincipal, request.principal.principal_id)
    principal_created = principal is None
    if principal is not None:
        expected_principal = {
            "principal_id": request.principal.principal_id,
            "workspace_id": request.workspace_id,
            "principal_type": "human",
            "state": "ACTIVE",
            "subject_digest": subject_digest,
            "audiences": _AUDIENCE,
            "project_ids": [request.project_id],
            "environment_ids": [],
            "scopes": _OPERATOR_SCOPES,
            "trust_roles": _OPERATOR_TRUST_ROLES,
            "claims_digest": claims_digest,
            "revoked_at": None,
        }
        if any(getattr(principal, key) != value for key, value in expected_principal.items()):
            raise BootstrapError("bootstrap.principal_drift")
    else:
        session.add(
            PublicPrincipal(
                principal_id=request.principal.principal_id,
                workspace_id=request.workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=subject_digest,
                audiences=list(_AUDIENCE),
                project_ids=[request.project_id],
                environment_ids=[],
                scopes=list(_OPERATOR_SCOPES),
                trust_roles=list(_OPERATOR_TRUST_ROLES),
                claims_digest=claims_digest,
                revoked_at=None,
            )
        )
        session.flush()

    # Owner principal: a human who may own catalog records.  Its scope set is
    # deliberately minimal; it is not a catalog writer by default.
    owner_scopes = list(_OWNER_SCOPES)
    owner_claims_digest = canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": configured.public_auth_issuer,
            "subject": request.owner_principal.subject,
            "principal_type": "human",
            "audiences": _AUDIENCE,
            "workspace_id": request.workspace_id,
            "project_ids": [request.project_id],
            "environment_ids": [],
            "scopes": owner_scopes,
        }
    )
    owner_subject_digest = digest_public_subject(request.owner_principal.subject)
    owner_principal = session.get(PublicPrincipal, request.owner_principal.principal_id)
    owner_created = owner_principal is None
    if owner_principal is not None:
        expected_owner = {
            "principal_id": request.owner_principal.principal_id,
            "workspace_id": request.workspace_id,
            "principal_type": "human",
            "state": "ACTIVE",
            "subject_digest": owner_subject_digest,
            "audiences": _AUDIENCE,
            "project_ids": [request.project_id],
            "environment_ids": [],
            "scopes": owner_scopes,
            "trust_roles": _OWNER_TRUST_ROLES,
            "claims_digest": owner_claims_digest,
            "revoked_at": None,
        }
        if any(getattr(owner_principal, key) != value for key, value in expected_owner.items()):
            raise BootstrapError("bootstrap.principal_drift")
    else:
        session.add(
            PublicPrincipal(
                principal_id=request.owner_principal.principal_id,
                workspace_id=request.workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=owner_subject_digest,
                audiences=list(_AUDIENCE),
                project_ids=[request.project_id],
                environment_ids=[],
                scopes=list(owner_scopes),
                trust_roles=list(_OWNER_TRUST_ROLES),
                claims_digest=owner_claims_digest,
                revoked_at=None,
            )
        )
        session.flush()

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
        expected_credential = {
            "credential_id": request.credential.credential_id,
            "workspace_id": request.workspace_id,
            "principal_id": request.principal.principal_id,
            "issuer": configured.public_auth_issuer,
            "subject": request.principal.subject,
            "hash_algorithm": "hmac-sha256-v1",
            "jti_digest": jti_digest,
            "claims_digest": claims_digest,
            "audiences": _AUDIENCE,
            "project_ids": [request.project_id],
            "environment_ids": [],
            "scopes": _OPERATOR_SCOPES,
            "state": "ACTIVE",
            "revoked_at": None,
        }
        if any(
            getattr(credential, key) != value for key, value in expected_credential.items()
        ):
            raise BootstrapError("bootstrap.credential_drift")
        if not hmac.compare_digest(credential.credential_hash, credential_hash):
            raise BootstrapError("bootstrap.credential_drift")
        if not all(
            (
                _same_time(credential.issued_at, request.credential.issued_at),
                _same_time(credential.not_before, request.credential.not_before),
                _same_time(credential.expires_at, request.credential.expires_at),
            )
        ):
            raise BootstrapError("bootstrap.credential_drift")
    elif active_credentials:
        raise BootstrapError("bootstrap.credential_drift")
    else:
        session.add(
            PublicCredential(
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
                environment_ids=[],
                scopes=list(_OPERATOR_SCOPES),
                state="ACTIVE",
                issued_at=issued,
                not_before=not_before,
                expires_at=expires,
                revoked_at=None,
            )
        )
        session.flush()

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
            created_at=current,
            updated_at=current,
        )
        session.add(source)
        session.flush()

    transaction_id = new_transaction_id()
    audit = audit_service or V4AuditService(session, clock=lambda: current)

    controller_receipts: list[ControllerBootstrapReceipt] = []
    for kind, (owner, commands) in _V5_CONTROLLER_SPECS.items():
        identity = _v5_controller_input(request, kind)
        registration = existing_v5_controllers[kind]
        created = registration is None
        if registration is None:
            registration = _create_controller(
                session,
                request=request,
                identity=identity,
                owner=owner,
                commands=commands,
                v5=True,
                now=current,
                transaction_id=transaction_id,
                audit=audit,
                contracts_root=contracts_root,
            )
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
    for kind, (owner, commands) in _V4_CONTROLLER_SPECS.items():
        identity = _v4_controller_input(request, kind)
        registration = existing_v4_controllers[kind]
        created = registration is None
        if registration is None:
            registration = _create_controller(
                session,
                request=request,
                identity=identity,
                owner=owner,
                commands=commands,
                v5=False,
                now=current,
                transaction_id=transaction_id,
                audit=audit,
                contracts_root=contracts_root,
            )
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
    catalog_registration = controller_receipts[0]

    created_flags = [
        source_created,
        principal_created,
        owner_created,
        credential_created,
        *(item.created for item in controller_receipts),
    ]
    if all(created_flags):
        status: Literal["CREATED", "REUSED", "MIXED"] = "CREATED"
    elif not any(created_flags):
        status = "REUSED"
    else:
        status = "MIXED"

    command_audit = audit.record(
        workspace_id=request.workspace_id,
        actor_principal=request.principal.principal_id,
        action="v5_catalog_local.bootstrap",
        target=f"workspace:{request.workspace_id}",
        params={
            "workspace_id": request.workspace_id,
            "project_id": request.project_id,
            "source_id": request.source.source_id,
            "principal_id": request.principal.principal_id,
            "credential_id": request.credential.credential_id,
            "jti_digest": jti_digest,
            "claims_digest": claims_digest,
            "owner_principal_id": request.owner_principal.principal_id,
            "operator_trust_roles": _OPERATOR_TRUST_ROLES,
            "owner_trust_roles": _OWNER_TRUST_ROLES,
            "controller_registration_digests": {
                item.registration_id: item.registration_digest
                for item in controller_receipts
            },
            "result": status,
        },
        transaction_id=transaction_id,
        evidence_refs={
            "owner_principal_id": request.owner_principal.principal_id,
            "principal_id": request.principal.principal_id,
            "credential_id": request.credential.credential_id,
            "source_id": request.source.source_id,
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

    return V5CatalogLocalBootstrapReceipt(
        status=status,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        source=SourceBootstrapReceipt(
            source_id=request.source.source_id,
            connection_digest=connection_digest,
            created=source_created,
        ),
        controller=catalog_registration,
        controllers=controller_receipts,
        owner_principal=OwnerPrincipalBootstrapReceipt(
            principal_id=request.owner_principal.principal_id,
            claims_digest=owner_claims_digest,
            trust_roles=list(_OWNER_TRUST_ROLES),
            created=owner_created,
        ),
        principal=PrincipalBootstrapReceipt(
            principal_id=request.principal.principal_id,
            claims_digest=claims_digest,
            trust_roles=list(_OPERATOR_TRUST_ROLES),
            created=principal_created,
        ),
        credential=CredentialBootstrapReceipt(
            credential_id=request.credential.credential_id,
            jti_digest=jti_digest,
            claims_digest=claims_digest,
            expires_at=expires,
            created=credential_created,
        ),
        transaction_id=transaction_id,
        command_audit_ref=command_audit.audit_ref,
        secret_storage_ref=request.secret_storage_ref,
    )


def _claims_digest_for_principal(
    *,
    settings: Settings,
    subject: str,
    workspace_id: str,
    project_id: str,
    scopes: list[str],
    environment_ids: list[str] | None = None,
) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": settings.public_auth_issuer,
            "subject": subject,
            "principal_type": "human",
            "audiences": _AUDIENCE,
            "workspace_id": workspace_id,
            "project_ids": [project_id],
            "environment_ids": environment_ids or [],
            "scopes": scopes,
        }
    )


def _validated_environment_authority(
    session: Session,
    *,
    workspace_id: str,
    project_id: str,
    environment_id: str,
    expected_revision: int | None = None,
    expected_digest: str | None = None,
) -> tuple[Environment, AIApplication]:
    environment = session.get(Environment, environment_id)
    if (
        environment is None
        or environment.workspace_id != workspace_id
        or environment.lifecycle_state != "ACTIVE"
        or (
            expected_revision is not None
            and environment.revision != expected_revision
        )
        or (
            expected_digest is not None
            and environment.record_digest != expected_digest
        )
    ):
        raise BootstrapError("bootstrap.environment_authority_drift")
    application = session.get(AIApplication, environment.application_id)
    if (
        application is None
        or application.workspace_id != workspace_id
        or application.project_id != project_id
        or application.lifecycle_state != "ACTIVE"
    ):
        raise BootstrapError("bootstrap.environment_authority_drift")
    try:
        environment_digest = assert_v5_record_digest(environment.envelope_payload)
        application_digest = assert_v5_record_digest(application.envelope_payload)
    except (V4IntegrityError, AttributeError, TypeError, ValueError):
        raise BootstrapError("bootstrap.environment_authority_drift") from None
    environment_envelope = environment.envelope_payload.get("record_envelope")
    application_envelope = application.envelope_payload.get("record_envelope")
    if (
        environment_digest != environment.record_digest
        or application_digest != application.record_digest
        or not isinstance(environment_envelope, dict)
        or not isinstance(application_envelope, dict)
        or environment.envelope_payload.get("environment_id")
        != environment.environment_id
        or environment.envelope_payload.get("workspace_id") != workspace_id
        or environment.envelope_payload.get("application_id")
        != application.application_id
        or environment.envelope_payload.get("lifecycle_state") != "ACTIVE"
        or environment_envelope.get("revision") != environment.revision
        or environment_envelope.get("record_digest") != environment.record_digest
        or environment_envelope.get("authority_receipt_id")
        != environment.authority_receipt_id
        or application.envelope_payload.get("application_id")
        != application.application_id
        or application.envelope_payload.get("workspace_id") != workspace_id
        or application.envelope_payload.get("project_id") != project_id
        or application.envelope_payload.get("lifecycle_state") != "ACTIVE"
        or application_envelope.get("revision") != application.revision
        or application_envelope.get("record_digest") != application.record_digest
        or application_envelope.get("authority_receipt_id")
        != application.authority_receipt_id
    ):
        raise BootstrapError("bootstrap.environment_authority_drift")
    authority = V5AuthorityService(session)
    try:
        authority.validate_receipt_binding(
            authority_receipt_id=environment.authority_receipt_id,
            workspace_id=workspace_id,
            subject_kind="ENVIRONMENT",
            subject_id=environment.environment_id,
            subject_revision=environment.revision,
            subject_digest=environment.record_digest,
        )
        authority.validate_receipt_binding(
            authority_receipt_id=application.authority_receipt_id,
            workspace_id=workspace_id,
            subject_kind="AI_APPLICATION",
            subject_id=application.application_id,
            subject_revision=application.revision,
            subject_digest=application.record_digest,
        )
    except V5AuthorityError:
        raise BootstrapError("bootstrap.environment_authority_drift") from None
    return environment, application


def execute_v5_operator_environment_rotation(
    session: Session,
    request: V5OperatorEnvironmentRotationRequest,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    schema_verifier: Callable[[Session], None] = verify_v5_catalog_alembic_head,
    audit_service: V4AuditService | None = None,
) -> V5OperatorEnvironmentRotationReceipt:
    """Rotate the project-only operator credential after environment import."""

    configured = settings or get_settings()
    current = _as_utc(now or datetime.now(timezone.utc))
    schema_verifier(session)
    if not hasattr(PublicPrincipal, "trust_roles"):
        raise BootstrapError("bootstrap.schema_revision_not_ready")
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {
                "lock_key": (
                    "agentmed:v5-operator-environment-rotation:"
                    f"{request.workspace_id}:{request.principal.principal_id}"
                )
            },
        )

    environment, _application = _validated_environment_authority(
        session,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        environment_id=request.exact_environment_binding.id,
        expected_revision=request.exact_environment_binding.revision,
        expected_digest=request.exact_environment_binding.digest,
    )
    environment_created_at = _as_utc(environment.created_at)
    initial_claims_digest = _claims_digest_for_principal(
        settings=configured,
        subject=request.principal.subject,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        scopes=list(_OPERATOR_SCOPES),
    )
    rotated_claims_digest = _claims_digest_for_principal(
        settings=configured,
        subject=request.principal.subject,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        scopes=list(_OPERATOR_SCOPES),
        environment_ids=[environment.environment_id],
    )
    principal = session.get(PublicPrincipal, request.principal.principal_id)
    static_principal = {
        "workspace_id": request.workspace_id,
        "principal_type": "human",
        "state": "ACTIVE",
        "subject_digest": digest_public_subject(request.principal.subject),
        "audiences": _AUDIENCE,
        "project_ids": [request.project_id],
        "scopes": _OPERATOR_SCOPES,
        "trust_roles": _OPERATOR_TRUST_ROLES,
        "revoked_at": None,
    }
    if principal is None or any(
        getattr(principal, key) != value for key, value in static_principal.items()
    ):
        raise BootstrapError("bootstrap.operator_environment_rotation.principal_drift")
    if (
        principal.environment_ids == []
        and principal.claims_digest == initial_claims_digest
    ):
        replay = False
    elif (
        principal.environment_ids == [environment.environment_id]
        and principal.claims_digest == rotated_claims_digest
    ):
        replay = True
    else:
        raise BootstrapError("bootstrap.operator_environment_rotation.principal_drift")

    previous = session.get(PublicCredential, request.previous_credential_id)
    expected_previous = {
        "workspace_id": request.workspace_id,
        "principal_id": request.principal.principal_id,
        "issuer": configured.public_auth_issuer,
        "subject": request.principal.subject,
        "hash_algorithm": "hmac-sha256-v1",
        "claims_digest": initial_claims_digest,
        "audiences": _AUDIENCE,
        "project_ids": [request.project_id],
        "environment_ids": [],
        "scopes": _OPERATOR_SCOPES,
    }
    if previous is None or any(
        getattr(previous, key) != value for key, value in expected_previous.items()
    ):
        raise BootstrapError(
            "bootstrap.operator_environment_rotation.previous_credential_drift"
        )
    try:
        previous_issued = _as_utc(previous.issued_at)
        previous_not_before = _as_utc(previous.not_before)
        previous_expires = _as_utc(previous.expires_at)
    except (AttributeError, TypeError, ValueError):
        raise BootstrapError(
            "bootstrap.operator_environment_rotation.previous_credential_drift"
        ) from None
    if not (
        previous_issued
        <= previous_not_before
        <= environment_created_at
        < previous_expires
    ):
        raise BootstrapError(
            "bootstrap.operator_environment_rotation.previous_credential_drift"
        )
    if replay:
        if previous.state != "REVOKED" or previous.revoked_at is None:
            raise BootstrapError(
                "bootstrap.operator_environment_rotation.previous_credential_drift"
            )
    elif previous.state != "ACTIVE" or previous.revoked_at is not None:
        raise BootstrapError(
            "bootstrap.operator_environment_rotation.previous_credential_drift"
        )

    issued_at = _as_utc(request.credential.issued_at)
    not_before = _as_utc(request.credential.not_before)
    expires_at = _as_utc(request.credential.expires_at)
    credential_hash = hash_opaque_bearer(
        request.credential.bearer_token,
        configured.public_credential_hash_pepper,
    )
    jti_digest = _sha256_text(request.credential.jti)
    conflicts = list(
        session.scalars(
            select(PublicCredential)
            .where(
                sa.or_(
                    PublicCredential.credential_id
                    == request.credential.credential_id,
                    PublicCredential.credential_hash == credential_hash,
                    sa.and_(
                        PublicCredential.issuer == configured.public_auth_issuer,
                        PublicCredential.jti_digest == jti_digest,
                    ),
                )
            )
            .with_for_update()
        ).all()
    )
    if len(conflicts) > 1:
        raise BootstrapError(
            "bootstrap.operator_environment_rotation.credential_material_reused"
        )
    credential = conflicts[0] if conflicts else None
    if replay:
        if credential is None:
            raise BootstrapError(
                "bootstrap.operator_environment_rotation.credential_drift"
            )
    elif credential is not None:
        raise BootstrapError(
            "bootstrap.operator_environment_rotation.credential_material_reused"
        )

    if credential is not None:
        expected_credential = {
            "credential_id": request.credential.credential_id,
            "workspace_id": request.workspace_id,
            "principal_id": request.principal.principal_id,
            "issuer": configured.public_auth_issuer,
            "subject": request.principal.subject,
            "hash_algorithm": "hmac-sha256-v1",
            "jti_digest": jti_digest,
            "claims_digest": rotated_claims_digest,
            "audiences": _AUDIENCE,
            "project_ids": [request.project_id],
            "environment_ids": [environment.environment_id],
            "scopes": _OPERATOR_SCOPES,
            "state": "ACTIVE",
            "revoked_at": None,
        }
        if any(
            getattr(credential, key) != value
            for key, value in expected_credential.items()
        ) or not hmac.compare_digest(credential.credential_hash, credential_hash):
            raise BootstrapError(
                "bootstrap.operator_environment_rotation.credential_drift"
            )
        if not all(
            (
                _same_time(credential.issued_at, request.credential.issued_at),
                _same_time(credential.not_before, request.credential.not_before),
                _same_time(credential.expires_at, request.credential.expires_at),
            )
        ):
            raise BootstrapError(
                "bootstrap.operator_environment_rotation.credential_drift"
            )
    else:
        if (
            issued_at <= environment_created_at
            or issued_at > current
            or current - issued_at > _OWNER_REAUTH_MAX_AGE
            or not_before > current
            or expires_at <= current
        ):
            raise BootstrapError(
                "bootstrap.operator_environment_rotation.credential_not_fresh"
            )
        principal.environment_ids = [environment.environment_id]
        principal.claims_digest = rotated_claims_digest
        previous.state = "REVOKED"
        previous.revoked_at = current
        credential = PublicCredential(
            credential_id=request.credential.credential_id,
            workspace_id=request.workspace_id,
            principal_id=request.principal.principal_id,
            issuer=configured.public_auth_issuer,
            subject=request.principal.subject,
            credential_hash=credential_hash,
            hash_algorithm="hmac-sha256-v1",
            jti_digest=jti_digest,
            claims_digest=rotated_claims_digest,
            audiences=list(_AUDIENCE),
            project_ids=[request.project_id],
            environment_ids=[environment.environment_id],
            scopes=list(_OPERATOR_SCOPES),
            state="ACTIVE",
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
            revoked_at=None,
        )
        session.add(credential)

    rotation_binding = {
        "schema_version": "1.0",
        "kind": "OPERATOR_ENVIRONMENT_CREDENTIAL_ROTATION",
        "workspace_id": request.workspace_id,
        "project_id": request.project_id,
        "principal_id": request.principal.principal_id,
        "previous_credential_id": request.previous_credential_id,
        "credential_id": request.credential.credential_id,
        "jti_digest": jti_digest,
        "claims_digest": rotated_claims_digest,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "exact_environment_binding": request.exact_environment_binding.model_dump(
            mode="json"
        ),
    }
    rotation_binding_digest = canonical_digest(rotation_binding)
    status: Literal["CREATED", "REUSED"] = "REUSED" if replay else "CREATED"
    transaction_id = new_transaction_id()
    audit = audit_service or V4AuditService(session, clock=lambda: current)
    command_audit = audit.record(
        workspace_id=request.workspace_id,
        actor_principal=request.principal.principal_id,
        action="v5_catalog_local.operator_environment_rotation",
        target=request.credential.credential_id,
        params={
            "rotation_binding_digest": rotation_binding_digest,
            "result": status,
        },
        transaction_id=transaction_id,
        evidence_refs={
            "principal_id": request.principal.principal_id,
            "previous_credential_id": request.previous_credential_id,
            "credential_id": request.credential.credential_id,
            "jti_digest": jti_digest,
            "claims_digest": rotated_claims_digest,
            "exact_environment_binding": (
                request.exact_environment_binding.model_dump(mode="json")
            ),
            "rotation_binding_digest": rotation_binding_digest,
        },
        occurred_at=current,
    )
    try:
        session.flush()
    except Exception as exc:
        raise BootstrapError(
            "bootstrap.operator_environment_rotation.persistence_failed"
        ) from exc
    return V5OperatorEnvironmentRotationReceipt(
        status=status,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        principal_id=request.principal.principal_id,
        exact_environment_binding=request.exact_environment_binding,
        previous_credential_id=request.previous_credential_id,
        credential=CredentialBootstrapReceipt(
            credential_id=request.credential.credential_id,
            jti_digest=jti_digest,
            claims_digest=rotated_claims_digest,
            expires_at=expires_at,
            created=not replay,
        ),
        rotation_binding_digest=rotation_binding_digest,
        transaction_id=transaction_id,
        command_audit_ref=command_audit.audit_ref,
        secret_storage_ref=request.secret_storage_ref,
    )


def _assert_operator_proposal_authority(
    session: Session,
    *,
    request: V5OwnerReauthenticationRequest,
    settings: Settings,
    proposed_at: datetime,
    environment_id: str,
) -> None:
    principal = session.get(PublicPrincipal, request.operator_principal_id)
    if principal is None:
        raise BootstrapError("bootstrap.owner_reauthentication.operator_drift")
    expected_principal = {
        "workspace_id": request.workspace_id,
        "principal_type": "human",
        "state": "ACTIVE",
        "audiences": _AUDIENCE,
        "project_ids": [request.project_id],
        "environment_ids": [environment_id],
        "scopes": _OPERATOR_SCOPES,
        "trust_roles": _OPERATOR_TRUST_ROLES,
        "revoked_at": None,
    }
    if any(getattr(principal, key) != value for key, value in expected_principal.items()):
        raise BootstrapError("bootstrap.owner_reauthentication.operator_drift")

    credentials = list(
        session.scalars(
            select(PublicCredential)
            .where(
                PublicCredential.workspace_id == request.workspace_id,
                PublicCredential.principal_id == request.operator_principal_id,
                PublicCredential.state == "ACTIVE",
                PublicCredential.revoked_at.is_(None),
            )
            .with_for_update()
        ).all()
    )
    if len(credentials) != 1:
        raise BootstrapError("bootstrap.owner_reauthentication.operator_drift")
    credential = credentials[0]
    expected_claims_digest = _claims_digest_for_principal(
        settings=settings,
        subject=credential.subject,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        scopes=list(_OPERATOR_SCOPES),
        environment_ids=[environment_id],
    )
    expected_credential = {
        "workspace_id": request.workspace_id,
        "principal_id": request.operator_principal_id,
        "issuer": settings.public_auth_issuer,
        "hash_algorithm": "hmac-sha256-v1",
        "claims_digest": expected_claims_digest,
        "audiences": _AUDIENCE,
        "project_ids": [request.project_id],
        "environment_ids": [environment_id],
        "scopes": _OPERATOR_SCOPES,
        "state": "ACTIVE",
        "revoked_at": None,
    }
    if any(getattr(credential, key) != value for key, value in expected_credential.items()):
        raise BootstrapError("bootstrap.owner_reauthentication.operator_drift")
    if (
        principal.claims_digest != expected_claims_digest
        or principal.subject_digest != digest_public_subject(credential.subject)
    ):
        raise BootstrapError("bootstrap.owner_reauthentication.operator_drift")
    try:
        issued_at = _as_utc(credential.issued_at)
        not_before = _as_utc(credential.not_before)
        expires_at = _as_utc(credential.expires_at)
    except (AttributeError, TypeError, ValueError):
        raise BootstrapError("bootstrap.owner_reauthentication.operator_drift") from None
    if not issued_at <= not_before <= proposed_at < expires_at:
        raise BootstrapError("bootstrap.owner_reauthentication.operator_drift")


def _load_exact_proposed_revision(
    session: Session,
    *,
    request: V5OwnerReauthenticationRequest,
    current: datetime,
) -> tuple[AcceptanceCriteriaRevision, QualityCase, Environment]:
    binding = request.exact_proposed_revision_binding
    proposed = session.get(AcceptanceCriteriaRevision, binding.id)
    if (
        proposed is None
        or proposed.workspace_id != request.workspace_id
        or proposed.revision != binding.revision
        or proposed.record_digest != binding.digest
        or proposed.confirmation_status != "PROPOSED"
        or proposed.proposer_principal != request.operator_principal_id
        or proposed.proposer_principal == request.owner_principal.principal_id
    ):
        raise BootstrapError("bootstrap.owner_reauthentication.proposal_drift")
    try:
        verified_digest = assert_v5_record_digest(proposed.envelope_payload)
    except (V4IntegrityError, AttributeError, TypeError, ValueError):
        raise BootstrapError(
            "bootstrap.owner_reauthentication.proposal_integrity_invalid"
        ) from None
    payload = proposed.envelope_payload
    record_envelope = payload.get("record_envelope")
    exact_case = payload.get("exact_case_binding")
    if (
        verified_digest != proposed.record_digest
        or not isinstance(record_envelope, dict)
        or record_envelope.get("revision") != proposed.revision
        or payload.get("acceptance_criteria_revision_id")
        != proposed.acceptance_criteria_revision_id
        or payload.get("workspace_id") != proposed.workspace_id
        or payload.get("confirmation_status") != "PROPOSED"
        or payload.get("proposer_principal") != proposed.proposer_principal
        or not isinstance(exact_case, dict)
        or exact_case.get("case_id") != proposed.case_id
        or exact_case.get("case_revision") != proposed.case_revision
        or exact_case.get("case_digest") != proposed.case_digest
    ):
        raise BootstrapError(
            "bootstrap.owner_reauthentication.proposal_integrity_invalid"
        )
    proposed_at = _as_utc(proposed.proposed_at)
    if proposed_at > current:
        raise BootstrapError("bootstrap.owner_reauthentication.proposal_drift")

    quality_case = session.get(QualityCase, proposed.case_id)
    if (
        quality_case is None
        or quality_case.workspace_id != request.workspace_id
        or quality_case.project_id != request.project_id
        or quality_case.environment_id is None
        or quality_case.revision != proposed.case_revision
        or quality_case.record_digest != proposed.case_digest
    ):
        raise BootstrapError("bootstrap.owner_reauthentication.case_drift")
    try:
        assert_record_digest(
            quality_case.snapshot_payload,
            self_digest_field="record_digest",
        )
    except (V4IntegrityError, AttributeError, TypeError, ValueError):
        raise BootstrapError("bootstrap.owner_reauthentication.case_drift") from None
    if quality_case.snapshot_payload.get("record_digest") != quality_case.record_digest:
        raise BootstrapError("bootstrap.owner_reauthentication.case_drift")

    environment, application = _validated_environment_authority(
        session,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        environment_id=quality_case.environment_id,
    )
    if request.owner_principal.principal_id not in application.owner_principal_ids:
        raise BootstrapError("bootstrap.owner_reauthentication.owner_drift")
    case_binding = session.scalar(
        select(ApplicationCaseBinding)
        .where(
            ApplicationCaseBinding.workspace_id == request.workspace_id,
            ApplicationCaseBinding.case_id == quality_case.case_id,
            ApplicationCaseBinding.case_revision == quality_case.revision,
            ApplicationCaseBinding.case_digest == quality_case.record_digest,
        )
        .with_for_update()
    )
    if (
        case_binding is None
        or case_binding.application_id != application.application_id
        or case_binding.environment_id != environment.environment_id
        or case_binding.revision != 1
    ):
        raise BootstrapError("bootstrap.owner_reauthentication.case_binding_drift")
    try:
        binding_record_digest = assert_v5_record_digest(
            case_binding.envelope_payload
        )
    except (V4IntegrityError, AttributeError, TypeError, ValueError):
        raise BootstrapError(
            "bootstrap.owner_reauthentication.case_binding_drift"
        ) from None
    binding_envelope = case_binding.envelope_payload.get("record_envelope")
    exact_case_binding = case_binding.envelope_payload.get("exact_case_binding")
    expected_binding_digest = canonical_digest(
        {
            "application_id": application.application_id,
            "environment_id": environment.environment_id,
            "declared_system_version_set_binding_or_unknown": (
                case_binding.declared_system_version_set_binding_or_unknown
            ),
        }
    )
    if (
        binding_record_digest != case_binding.record_digest
        or not isinstance(binding_envelope, dict)
        or binding_envelope.get("revision") != case_binding.revision
        or binding_envelope.get("record_digest") != case_binding.record_digest
        or binding_envelope.get("authority_receipt_id")
        != case_binding.authority_receipt_id
        or case_binding.envelope_payload.get("application_case_binding_id")
        != case_binding.application_case_binding_id
        or case_binding.envelope_payload.get("workspace_id") != request.workspace_id
        or case_binding.envelope_payload.get("application_id")
        != application.application_id
        or case_binding.envelope_payload.get("environment_id")
        != environment.environment_id
        or case_binding.envelope_payload.get("binding_digest")
        != case_binding.binding_digest
        or case_binding.binding_digest != expected_binding_digest
        or not isinstance(exact_case_binding, dict)
        or exact_case_binding.get("case_id") != quality_case.case_id
        or exact_case_binding.get("case_revision") != quality_case.revision
        or exact_case_binding.get("case_digest") != quality_case.record_digest
    ):
        raise BootstrapError("bootstrap.owner_reauthentication.case_binding_drift")
    try:
        V5AuthorityService(session).validate_receipt_binding(
            authority_receipt_id=case_binding.authority_receipt_id,
            workspace_id=request.workspace_id,
            subject_kind="APPLICATION_CASE_BINDING",
            subject_id=case_binding.application_case_binding_id,
            subject_revision=case_binding.revision,
            subject_digest=case_binding.record_digest,
        )
    except V5AuthorityError:
        raise BootstrapError(
            "bootstrap.owner_reauthentication.case_binding_drift"
        ) from None
    return proposed, quality_case, environment


def execute_v5_owner_reauthentication(
    session: Session,
    request: V5OwnerReauthenticationRequest,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    schema_verifier: Callable[[Session], None] = verify_v5_catalog_alembic_head,
    audit_service: V4AuditService | None = None,
) -> V5OwnerReauthenticationReceipt:
    """Issue one exact, post-proposal owner credential; caller owns commit."""

    configured = settings or get_settings()
    current = _as_utc(now or datetime.now(timezone.utc))
    schema_verifier(session)
    if not hasattr(PublicPrincipal, "trust_roles"):
        raise BootstrapError("bootstrap.schema_revision_not_ready")
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {
                "lock_key": (
                    "agentmed:v5-owner-reauth:"
                    f"{request.workspace_id}:{request.owner_principal.principal_id}"
                )
            },
        )

    proposed, _quality_case, environment = _load_exact_proposed_revision(
        session,
        request=request,
        current=current,
    )
    proposed_at = _as_utc(proposed.proposed_at)
    _assert_operator_proposal_authority(
        session,
        request=request,
        settings=configured,
        proposed_at=proposed_at,
        environment_id=environment.environment_id,
    )

    owner = session.get(PublicPrincipal, request.owner_principal.principal_id)
    initial_owner_claims_digest = _claims_digest_for_principal(
        settings=configured,
        subject=request.owner_principal.subject,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        scopes=list(_OWNER_SCOPES),
    )
    owner_claims_digest = _claims_digest_for_principal(
        settings=configured,
        subject=request.owner_principal.subject,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        scopes=list(_OWNER_SCOPES),
        environment_ids=[environment.environment_id],
    )
    static_owner = {
        "workspace_id": request.workspace_id,
        "principal_type": "human",
        "state": "ACTIVE",
        "subject_digest": digest_public_subject(request.owner_principal.subject),
        "audiences": _AUDIENCE,
        "project_ids": [request.project_id],
        "scopes": _OWNER_SCOPES,
        "trust_roles": _OWNER_TRUST_ROLES,
        "revoked_at": None,
    }
    if owner is None or any(
        getattr(owner, key) != value for key, value in static_owner.items()
    ):
        raise BootstrapError("bootstrap.owner_reauthentication.owner_drift")
    if (
        owner.environment_ids == []
        and owner.claims_digest == initial_owner_claims_digest
    ):
        owner_is_reauthenticated = False
    elif (
        owner.environment_ids == [environment.environment_id]
        and owner.claims_digest == owner_claims_digest
    ):
        owner_is_reauthenticated = True
    else:
        raise BootstrapError("bootstrap.owner_reauthentication.owner_drift")

    issued_at = _as_utc(request.credential.issued_at)
    not_before = _as_utc(request.credential.not_before)
    expires_at = _as_utc(request.credential.expires_at)
    if issued_at <= proposed_at:
        raise BootstrapError("bootstrap.owner_reauthentication.credential_not_fresh")

    credential_hash = hash_opaque_bearer(
        request.credential.bearer_token,
        configured.public_credential_hash_pepper,
    )
    jti_digest = _sha256_text(request.credential.jti)
    conflicting_credentials = list(
        session.scalars(
            select(PublicCredential)
            .where(
                sa.or_(
                    PublicCredential.credential_id
                    == request.credential.credential_id,
                    PublicCredential.credential_hash == credential_hash,
                    sa.and_(
                        PublicCredential.issuer == configured.public_auth_issuer,
                        PublicCredential.jti_digest == jti_digest,
                    ),
                )
            )
            .with_for_update()
        ).all()
    )
    if len(conflicting_credentials) > 1:
        raise BootstrapError(
            "bootstrap.owner_reauthentication.credential_material_reused"
        )
    credential = (
        conflicting_credentials[0] if conflicting_credentials else None
    )
    credential_created = credential is None
    if credential is not None:
        if credential.credential_id != request.credential.credential_id:
            raise BootstrapError(
                "bootstrap.owner_reauthentication.credential_material_reused"
            )
        if not owner_is_reauthenticated:
            raise BootstrapError("bootstrap.owner_reauthentication.owner_drift")
        expected_credential = {
            "workspace_id": request.workspace_id,
            "principal_id": request.owner_principal.principal_id,
            "issuer": configured.public_auth_issuer,
            "subject": request.owner_principal.subject,
            "hash_algorithm": "hmac-sha256-v1",
            "jti_digest": jti_digest,
            "claims_digest": owner_claims_digest,
            "audiences": _AUDIENCE,
            "project_ids": [request.project_id],
            "environment_ids": [environment.environment_id],
            "scopes": _OWNER_SCOPES,
            "state": "ACTIVE",
            "revoked_at": None,
        }
        if any(
            getattr(credential, key) != value
            for key, value in expected_credential.items()
        ) or not hmac.compare_digest(credential.credential_hash, credential_hash):
            raise BootstrapError(
                "bootstrap.owner_reauthentication.credential_drift"
            )
        if not all(
            (
                _same_time(credential.issued_at, request.credential.issued_at),
                _same_time(credential.not_before, request.credential.not_before),
                _same_time(credential.expires_at, request.credential.expires_at),
            )
        ):
            raise BootstrapError(
                "bootstrap.owner_reauthentication.credential_drift"
            )
    else:
        if owner_is_reauthenticated:
            raise BootstrapError("bootstrap.owner_reauthentication.owner_drift")
        if (
            issued_at > current
            or current - issued_at > _OWNER_REAUTH_MAX_AGE
            or not_before > current
            or expires_at <= current
        ):
            raise BootstrapError(
                "bootstrap.owner_reauthentication.credential_not_fresh"
            )
        owner.environment_ids = [environment.environment_id]
        owner.claims_digest = owner_claims_digest
        credential = PublicCredential(
            credential_id=request.credential.credential_id,
            workspace_id=request.workspace_id,
            principal_id=request.owner_principal.principal_id,
            issuer=configured.public_auth_issuer,
            subject=request.owner_principal.subject,
            credential_hash=credential_hash,
            hash_algorithm="hmac-sha256-v1",
            jti_digest=jti_digest,
            claims_digest=owner_claims_digest,
            audiences=list(_AUDIENCE),
            project_ids=[request.project_id],
            environment_ids=[environment.environment_id],
            scopes=list(_OWNER_SCOPES),
            state="ACTIVE",
            issued_at=issued_at,
            not_before=not_before,
            expires_at=expires_at,
            revoked_at=None,
        )
        session.add(credential)
        session.flush()

    issuance_binding = {
        "schema_version": "1.0",
        "kind": "OWNER_REAUTHENTICATION_CREDENTIAL",
        "workspace_id": request.workspace_id,
        "project_id": request.project_id,
        "operator_principal_id": request.operator_principal_id,
        "owner_principal_id": request.owner_principal.principal_id,
        "credential_id": request.credential.credential_id,
        "jti_digest": jti_digest,
        "claims_digest": owner_claims_digest,
        "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "exact_environment_binding": {
            "kind": "ENVIRONMENT",
            "id": environment.environment_id,
            "revision": environment.revision,
            "digest": environment.record_digest,
        },
        "exact_proposed_revision_binding": (
            request.exact_proposed_revision_binding.model_dump(mode="json")
        ),
    }
    issuance_binding_digest = canonical_digest(issuance_binding)
    status: Literal["CREATED", "REUSED"] = (
        "CREATED" if credential_created else "REUSED"
    )
    transaction_id = new_transaction_id()
    audit = audit_service or V4AuditService(session, clock=lambda: current)
    command_audit = audit.record(
        workspace_id=request.workspace_id,
        actor_principal=request.owner_principal.principal_id,
        action="v5_catalog_local.owner_reauthentication",
        target=request.credential.credential_id,
        params={
            "issuance_binding_digest": issuance_binding_digest,
            "result": status,
        },
        transaction_id=transaction_id,
        evidence_refs={
            "credential_id": request.credential.credential_id,
            "jti_digest": jti_digest,
            "claims_digest": owner_claims_digest,
            "owner_principal_id": request.owner_principal.principal_id,
            "operator_principal_id": request.operator_principal_id,
            "exact_environment_binding": issuance_binding[
                "exact_environment_binding"
            ],
            "exact_proposed_revision_binding": (
                request.exact_proposed_revision_binding.model_dump(mode="json")
            ),
            "issuance_binding_digest": issuance_binding_digest,
        },
        occurred_at=current,
    )
    try:
        session.flush()
    except Exception as exc:
        raise BootstrapError(
            "bootstrap.owner_reauthentication.persistence_failed"
        ) from exc

    return V5OwnerReauthenticationReceipt(
        status=status,
        workspace_id=request.workspace_id,
        project_id=request.project_id,
        owner_principal_id=request.owner_principal.principal_id,
        operator_principal_id=request.operator_principal_id,
        exact_environment_binding=ExactEnvironmentBindingRequest.model_validate(
            issuance_binding["exact_environment_binding"]
        ),
        exact_proposed_revision_binding=request.exact_proposed_revision_binding,
        credential=CredentialBootstrapReceipt(
            credential_id=request.credential.credential_id,
            jti_digest=jti_digest,
            claims_digest=owner_claims_digest,
            expires_at=expires_at,
            created=credential_created,
        ),
        issuance_binding_digest=issuance_binding_digest,
        transaction_id=transaction_id,
        command_audit_ref=command_audit.audit_ref,
        secret_storage_ref=request.secret_storage_ref,
    )


def _default_executor(
    request: V5CatalogLocalBootstrapRequest,
) -> V5CatalogLocalBootstrapReceipt:
    session = get_session_factory()()
    try:
        receipt = execute_v5_catalog_local_bootstrap(session, request)
        session.commit()
        return receipt
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _default_owner_reauthentication_executor(
    request: V5OwnerReauthenticationRequest,
) -> V5OwnerReauthenticationReceipt:
    session = get_session_factory()()
    try:
        receipt = execute_v5_owner_reauthentication(session, request)
        session.commit()
        return receipt
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _default_operator_environment_rotation_executor(
    request: V5OperatorEnvironmentRotationRequest,
) -> V5OperatorEnvironmentRotationReceipt:
    session = get_session_factory()()
    try:
        receipt = execute_v5_operator_environment_rotation(session, request)
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
        [V5CatalogLocalBootstrapRequest], V5CatalogLocalBootstrapReceipt
    ]
    | None = None,
    reauthentication_executor: Callable[
        [V5OwnerReauthenticationRequest], V5OwnerReauthenticationReceipt
    ]
    | None = None,
    operator_rotation_executor: Callable[
        [V5OperatorEnvironmentRotationRequest],
        V5OperatorEnvironmentRotationReceipt,
    ]
    | None = None,
) -> int:
    """Read exactly one bounded JSON request and emit one secret-free JSON result."""

    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    _ = stderr or sys.stderr
    try:
        raw = input_stream.read(_MAX_STDIN_BYTES + 1)
        if len(raw.encode("utf-8")) > _MAX_STDIN_BYTES:
            raise BootstrapError("bootstrap.request_too_large")
        try:
            payload = _load_strict_json(raw)
            if (
                isinstance(payload, dict)
                and payload.get("operation") == "owner_reauthentication"
            ):
                reauthentication_request = (
                    V5OwnerReauthenticationRequest.model_validate(payload)
                )
                receipt: (
                    V5CatalogLocalBootstrapReceipt
                    | V5OwnerReauthenticationReceipt
                    | V5OperatorEnvironmentRotationReceipt
                ) = (
                    reauthentication_executor
                    or _default_owner_reauthentication_executor
                )(reauthentication_request)
                if not isinstance(receipt, V5OwnerReauthenticationReceipt):
                    receipt = V5OwnerReauthenticationReceipt.model_validate(receipt)
            elif (
                isinstance(payload, dict)
                and payload.get("operation") == "operator_environment_rotation"
            ):
                rotation_request = V5OperatorEnvironmentRotationRequest.model_validate(
                    payload
                )
                receipt = (
                    operator_rotation_executor
                    or _default_operator_environment_rotation_executor
                )(rotation_request)
                if not isinstance(receipt, V5OperatorEnvironmentRotationReceipt):
                    receipt = V5OperatorEnvironmentRotationReceipt.model_validate(
                        receipt
                    )
            else:
                request = V5CatalogLocalBootstrapRequest.model_validate(payload)
                receipt = (executor or _default_executor)(request)
                if not isinstance(receipt, V5CatalogLocalBootstrapReceipt):
                    receipt = V5CatalogLocalBootstrapReceipt.model_validate(receipt)
        except (
            json.JSONDecodeError,
            UnicodeError,
            ValidationError,
            TypeError,
            _StrictJSONError,
        ):
            raise BootstrapError("bootstrap.request_invalid") from None
        _write_json(output_stream, receipt.model_dump(mode="json"))
        return 0
    except BootstrapError as exc:
        code = exc.code
    except V4AuditUnavailable:
        code = "bootstrap.audit_unavailable"
    except Exception:
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
    "ExactEnvironmentBindingRequest",
    "ExactProposedRevisionBindingRequest",
    "V5CatalogLocalBootstrapReceipt",
    "V5CatalogLocalBootstrapRequest",
    "V5OperatorEnvironmentRotationReceipt",
    "V5OperatorEnvironmentRotationRequest",
    "V5OwnerReauthenticationReceipt",
    "V5OwnerReauthenticationRequest",
    "execute_v5_catalog_local_bootstrap",
    "execute_v5_operator_environment_rotation",
    "execute_v5_owner_reauthentication",
    "main",
    "verify_v5_catalog_alembic_head",
]
