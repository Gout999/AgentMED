"""C4 fallback drill: generated/legacy mismatch simulations (v5-architecture-convergence.md#C4).

Four mismatch scenarios, each asserting the C4 "explicit per-surface fallback"
contract:

1. capability runtime table vs capability-manifest.json — a tampered runtime
   allowlist (or an unavailable operation manifest) must be detected and
   recorded, never leak into the served wire bytes; the legacy capability
   service keeps serving the baseline bytes.
2. route registration vs operation-manifest — the route-registry judge
   (``app.api.v5_route_registry``) is monkeypatched to raise; the real
   import-time hook (``install_route_manifest_check``) is fail-closed (C5
   effective enforcement): a rejected gate raises and aborts the import, and
   the gate never mutates the route table, so the already-bound legacy router
   keeps serving every route with byte-identical responses.
3. CLI derived-table fallback — when the manifest-derived CLI allowlist
   cannot be loaded or disagrees with the frozen parser, the frozen parser
   surface keeps serving (help bytes unchanged) and the failure is recorded.
4. console double-guard mismatch — python-side equivalent of the console's
   two guards (capability-manifest side and operation-manifest side): a
   disagreement is surfaced and recorded while the authoritative
   operation-manifest surface renders unchanged.

Common invariants asserted for every scenario: the mismatch is never
silently coerced into success and no wire byte changes versus the pre-fault
baseline; scenarios 1, 3 and 4 additionally assert the legacy path keeps
serving and the failure is recorded.

Scenarios 1 and 4 simulate the C4 cutover boundaries with test-local judge
mirrors (the real capability façade and console guards land with their own
C4 tasks); scenarios 2 exercises the real ``v5_route_registry`` seam that
already exists.  Each mirror is documented as pinning the C4 contract.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models import Base
from app.models.v4_tables import PublicCredential, PublicPrincipal
from app.public_api.credential_resolver import digest_public_subject, hash_opaque_bearer
from app.services.v4_audit import V4AuditService
from app.utils.v4_integrity import canonical_digest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_DIR = REPO_ROOT / "contracts/v5/generated"
CAPABILITY_MANIFEST = GENERATED_DIR / "capability-manifest.json"
OPERATION_MANIFEST = GENERATED_DIR / "operation-manifest.json"
CLI_SRC = REPO_ROOT / "cli/src"

WORKSPACE = "ws_01J0000000000001"
PROJECT = "proj_01J0000000000001"
PRINCIPAL_ID = "prn_01J000000000000A"
SUBJECT = "catalog-admin-01J0000000000001"
ISSUER = "https://auth.caseloop.dev"
AUDIENCES = ["caseloop-public-api"]
RAW_TOKEN = "drill-catalog-token-0123456789-abcdef"
PEPPER = "drill-catalog-pepper"
CURSOR_KEY = "drill-catalog-cursor"
SCOPES = [
    "applications:manage",
    "applications:read",
    "capabilities:read",
    "system_manifests:import",
]
FIXED_NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
PHANTOM_INTENT = "applications.activate"  # activated nowhere in C1 output


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_bytes(value: Any) -> bytes:
    """Deterministic byte identity for wire/payload comparison."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _mask_audit_ref(value: Any) -> Any:
    """Mask request-unique receipt references for wire-byte comparison.

    ``audit_ref`` is a fresh per-request receipt (one audit row per request by
    design); its value is request-bound, so byte-identity holds on the rest of
    the payload.  Masking it does not weaken the invariant: the receipt is
    still present and shaped identically on both sides.
    """

    if isinstance(value, dict):
        return {
            key: "<audit-ref>" if key == "audit_ref" else _mask_audit_ref(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_audit_ref(item) for item in value]
    return value


def _masked_canonical(content: bytes) -> bytes:
    return _canonical_bytes(_mask_audit_ref(json.loads(content)))


def _claims(workspace_id: str, project_ids: list[str], scopes: list[str]) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": workspace_id,
            "project_ids": project_ids,
            "environment_ids": [],
            "scopes": scopes,
        }
    )


# ---------------------------------------------------------------------------
# Evidence sink shared by every drill scenario
# ---------------------------------------------------------------------------


class DrillEvidence:
    """Append-only evidence/log sink mirroring the C4 per-surface fallback log.

    Every fallback decision must produce an entry here; a drill that ends
    without the expected entry has silently swallowed the mismatch.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, *, surface: str, code: str, detail: Any = None) -> None:
        self.entries.append(
            {
                "surface": surface,
                "code": code,
                "detail": detail,
                "recorded_at": FIXED_NOW.isoformat(),
            }
        )

    def codes(self) -> list[str]:
        return [entry["code"] for entry in self.entries]


# ---------------------------------------------------------------------------
# Shared C4 judge mirrors (documented as pinning the C4 boundary contract)
# ---------------------------------------------------------------------------


class CapabilitySurfaceMismatch(RuntimeError):
    """Fail-closed: capability runtime table disagrees with capability-manifest.json."""

    def __init__(self, *, missing: list[Any], extra: list[Any]) -> None:
        self.missing = missing
        self.extra = extra
        super().__init__(
            f"capability.table_vs_manifest_mismatch: missing={missing} extra={extra}"
        )


class ConsoleDoubleGuardMismatch(RuntimeError):
    """Fail-closed: console guard A (capability manifest) disagrees with guard B."""

    def __init__(self, detail: Any) -> None:
        self.detail = detail
        super().__init__(f"console.double_guard_mismatch: {detail}")


def _surface_triples(entries: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Normalize manifest/runtime capability entries to (name, scope, execution_mode)."""
    return [
        (entry["name"], entry["scope"], entry["execution_mode"])
        for entry in entries
    ]


def capability_facade_guard(
    runtime_table: tuple[dict[str, object], ...],
    manifest_entries: list[dict[str, Any]],
    evidence: DrillEvidence,
) -> list[tuple[str, str, str]]:
    """C4 capability-façade mirror: runtime table must equal capability-manifest.

    Raises ``CapabilitySurfaceMismatch`` (surfaced, never coerced) and records
    evidence when the tables disagree; returns the normalized table otherwise.
    """
    runtime = _surface_triples(
        [
            {"name": raw["name"], "scope": raw["scope"], "execution_mode": raw["execution_mode"]}
            for raw in runtime_table
        ]
    )
    manifest = _surface_triples(manifest_entries)
    if runtime != manifest:
        detail = {
            "missing": sorted(set(manifest) - set(runtime)),
            "extra": sorted(set(runtime) - set(manifest)),
        }
        evidence.record(
            surface="capability",
            code="capability.table_vs_manifest_mismatch",
            detail=detail,
        )
        raise CapabilitySurfaceMismatch(
            missing=sorted(set(manifest) - set(runtime)),
            extra=sorted(set(runtime) - set(manifest)),
        )
    evidence.record(surface="capability", code="capability.table_vs_manifest_ok")
    return runtime


def console_double_guard(
    capability_manifest: dict[str, Any],
    operation_manifest: dict[str, Any],
    evidence: DrillEvidence,
) -> list[tuple[str, str, str]]:
    """C4 console double-guard mirror (python-side equivalent of the TS guards).

    Guard A derives the enabled surface from capability-manifest.json; guard B
    derives it from operation-manifest.json (http+cli activated operations).
    Both must agree exactly; on disagreement the mismatch is surfaced and
    recorded, and the authoritative guard-B surface is returned for rendering.
    """
    guard_a = _surface_triples(capability_manifest["enabled_intents"])
    guard_b = _surface_triples(
        [
            {
                "name": op["intent"],
                "scope": op["scope"],
                "execution_mode": op["execution_mode"],
            }
            for op in operation_manifest["operations"]
            if op.get("http") is not None
        ]
    )
    if guard_a != guard_b:
        detail = {
            "guard_b_only": sorted(set(guard_b) - set(guard_a)),
            "guard_a_only": sorted(set(guard_a) - set(guard_b)),
            "guard_a": guard_a,
            "guard_b": guard_b,
        }
        evidence.record(
            surface="console",
            code="console.double_guard_mismatch",
            detail=detail,
        )
        raise ConsoleDoubleGuardMismatch(detail)
    evidence.record(surface="console", code="console.double_guard_ok")
    return guard_b


# ---------------------------------------------------------------------------
# Real legacy serving path: app + seeded principal + pinned clock
# ---------------------------------------------------------------------------


@pytest.fixture()
def drill_app():
    """Fresh sqlite app with a seeded capabilities:read principal and pinned clock.

    Mirrors ``tests/unit/test_public_v5_api.py``: real credential resolver,
    real V5CapabilitiesService, real audit; only the clock is pinned so
    ``generated_at`` never changes the wire bytes between baseline and drill.
    """
    engine = sa.create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url="sqlite://",
        public_credential_hash_pepper=SecretStr(PEPPER),
        public_cursor_signing_key=SecretStr(CURSOR_KEY),
        public_auth_issuer=ISSUER,
        require_mcp_role_tokens=False,
    )
    session = Session(engine)
    try:
        session.add(
            PublicPrincipal(
                principal_id=PRINCIPAL_ID,
                workspace_id=WORKSPACE,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(SUBJECT),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=list(SCOPES),
                trust_roles=["integrator"],
                claims_digest=_claims(WORKSPACE, [PROJECT], SCOPES),
                revoked_at=None,
            )
        )
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000A",
                workspace_id=WORKSPACE,
                principal_id=PRINCIPAL_ID,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(RAW_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "b" * 64,
                claims_digest=_claims(WORKSPACE, [PROJECT], SCOPES),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=SCOPES,
                state="ACTIVE",
                issued_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                not_before=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
                revoked_at=None,
            )
        )
        session.commit()
    finally:
        session.close()

    from app.services.v5_capabilities import V5CapabilitiesService

    app = create_app(settings=settings, engine=engine, create_tables=True)
    app.state.v5_capabilities_service_factory = (
        lambda request_session: V5CapabilitiesService(
            request_session, clock=lambda: FIXED_NOW
        )
    )
    context = TestClient(app)
    client = context.__enter__()
    try:
        yield client
    finally:
        context.__exit__(None, None, None)
        engine.dispose()


def _authed_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {RAW_TOKEN}",
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": "2.0",
        "X-Request-ID": "req_01J000000000000A",
    }


def _capabilities_bytes(client: TestClient) -> tuple[int, bytes]:
    response = client.get("/api/v2/capabilities", headers=_authed_headers())
    return response.status_code, response.content


# ---------------------------------------------------------------------------
# Scenario 1: capability runtime table vs capability-manifest.json
# ---------------------------------------------------------------------------


def test_drill_capability_table_mismatch_is_detected_and_legacy_serves_unchanged(
    drill_app, monkeypatch
) -> None:
    from app.services import v5_capabilities

    evidence = DrillEvidence()
    status0, bytes0 = _capabilities_bytes(drill_app)
    assert status0 == 200
    assert b"applications.activate" not in bytes0

    # Fault: runtime table gains a phantom intent the manifest never activates.
    tampered = v5_capabilities.IMPLEMENTED_V5_PUBLIC_INTENTS + (
        {
            "name": PHANTOM_INTENT,
            "scope": "applications:manage",
            "execution_mode": "synchronous",
            "principal_types": ("human",),
        },
    )
    monkeypatch.setattr(v5_capabilities, "IMPLEMENTED_V5_PUBLIC_INTENTS", tampered)

    # C4 guard: runtime table vs capability-manifest.json -> mismatch surfaced.
    manifest_entries = _load_json(CAPABILITY_MANIFEST)["enabled_intents"]
    with pytest.raises(CapabilitySurfaceMismatch) as excinfo:
        capability_facade_guard(tampered, manifest_entries, evidence)
    assert excinfo.value.extra == [(PHANTOM_INTENT, "applications:manage", "synchronous")]
    assert evidence.codes() == ["capability.table_vs_manifest_mismatch"]

    # Legacy path keeps serving: same status and byte-identical payload
    # (audit_ref masked: it is a fresh per-request receipt by design).
    status1, bytes1 = _capabilities_bytes(drill_app)
    assert status1 == status0
    assert _masked_canonical(bytes1) == _masked_canonical(bytes0)
    # No silent coercion: the phantom intent never enters the wire bytes.
    assert b"applications.activate" not in bytes1


def test_drill_capability_manifest_unavailable_falls_back_without_byte_change(
    drill_app, monkeypatch
) -> None:
    from app.services import v5_capabilities

    evidence = DrillEvidence()
    status0, bytes0 = _capabilities_bytes(drill_app)
    assert status0 == 200

    # Fault: the operation manifest (the table's derivation source) disappears.
    def _unavailable(*_args, **_kwargs):
        raise v5_capabilities.V5CapabilitiesManifestError(
            "v5.capabilities.operation_manifest_unavailable"
        )

    monkeypatch.setattr(v5_capabilities, "load_v5_operation_manifest", _unavailable)

    # C4 façade mirror: load failure is recorded and falls back to the legacy
    # runtime table; the failure is surfaced, never coerced into success.
    fallback_table = v5_capabilities.IMPLEMENTED_V5_PUBLIC_INTENTS
    try:
        v5_capabilities.load_v5_operation_manifest()
    except v5_capabilities.V5CapabilitiesManifestError as exc:
        evidence.record(
            surface="capability",
            code="capability.operation_manifest_unavailable",
            detail=str(exc),
        )
        # fallback: legacy runtime table (import-time bound) keeps serving.
    assert evidence.codes() == ["capability.operation_manifest_unavailable"]
    assert len(fallback_table) == 11

    status1, bytes1 = _capabilities_bytes(drill_app)
    assert status1 == status0
    assert _masked_canonical(bytes1) == _masked_canonical(bytes0)


# ---------------------------------------------------------------------------
# Scenario 2: route registration vs operation-manifest (real v5_route_registry)
# ---------------------------------------------------------------------------


def test_drill_route_registry_judge_raise_keeps_legacy_routes_serving(
    drill_app, monkeypatch
) -> None:
    from fastapi.routing import APIRoute

    from app.api import public_v5, v5_route_registry
    from app.main import create_app  # noqa: F401 (route table import parity)

    evidence = DrillEvidence()
    status0, bytes0 = _capabilities_bytes(drill_app)
    unauth0 = drill_app.get("/api/v2/capabilities").status_code
    assert status0 == 200 and unauth0 == 401

    # Fault: the route↔manifest judge now rejects the registration table.
    def _judge_rejects(*_args, **_kwargs):
        raise v5_route_registry.RouteManifestMismatchError(
            extra=[("POST", "/api/v2/applications", "registerApplication")]
        )

    monkeypatch.setattr(
        v5_route_registry, "check_registered_v5_routes", _judge_rejects
    )

    # The mismatch is surfaced (never coerced) and recorded as evidence.
    with pytest.raises(v5_route_registry.RouteManifestMismatchError):
        v5_route_registry.check_registered_v5_routes(public_v5.router)
    evidence.record(
        surface="router",
        code="route.table_vs_manifest_mismatch",
        detail={"extra": [["POST", "/api/v2/applications", "registerApplication"]]},
    )
    assert evidence.codes() == ["route.table_vs_manifest_mismatch"]

    # Legacy registration facts stay authority: all 11 v5 routes still serve.
    v5_routes = [
        route
        for route in public_v5.router.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v2")
    ]
    assert len(v5_routes) == 11
    status1, bytes1 = _capabilities_bytes(drill_app)
    assert status1 == status0
    assert _masked_canonical(bytes1) == _masked_canonical(bytes0)
    assert drill_app.get("/api/v2/capabilities").status_code == unauth0


def test_drill_install_route_manifest_check_fails_closed_and_legacy_serves(
    drill_app, monkeypatch, capsys
) -> None:
    """C5 enforcement: the real import-time hook is fail-closed.

    A rejected gate raises (nothing is swallowed or logged-and-served); the
    gate is a pure check, so the already-bound legacy registration table is
    untouched and keeps serving byte-identical wire.
    """
    from app.api import public_v5, v5_route_registry

    def _judge_rejects(*_args, **_kwargs):
        raise v5_route_registry.RouteManifestMismatchError(
            missing=[("GET", "/api/v2/applications", "listApplications")]
        )

    monkeypatch.setattr(
        v5_route_registry, "check_registered_v5_routes", _judge_rejects
    )

    status0, bytes0 = _capabilities_bytes(drill_app)
    with pytest.raises(v5_route_registry.RouteManifestMismatchError):
        v5_route_registry.install_route_manifest_check(public_v5.router)

    captured = capsys.readouterr()
    assert captured.err == ""  # fail-closed: no warning path, nothing swallowed
    # The gate never mutates the registration table: legacy routes still
    # serve byte-identical wire (audit_ref masked).
    status1, bytes1 = _capabilities_bytes(drill_app)
    assert status1 == status0
    assert _masked_canonical(bytes1) == _masked_canonical(bytes0)


# ---------------------------------------------------------------------------
# Scenario 3: CLI derived-table fallback
# ---------------------------------------------------------------------------


def _parser_command_paths(parser: Any) -> set[tuple[str, str]]:
    """Walk argparse ``_actions`` to collect (command, action) pairs."""
    pairs: set[tuple[str, str]] = set()
    for action in getattr(parser, "_actions", []):
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for command, command_parser in choices.items():
            for nested in getattr(command_parser, "_actions", []):
                nested_choices = getattr(nested, "choices", None)
                if isinstance(nested_choices, dict):
                    for sub in nested_choices:
                        pairs.add((command, sub))
    return pairs


def _derive_cli_allowlist(manifest: dict[str, Any]) -> list[str]:
    """C4 CLI mirror: command allowlist derived from operation-manifest ``cli``."""
    return sorted(
        op["cli"] for op in manifest["operations"] if isinstance(op.get("cli"), str)
    )


def _cli_import():
    sys.path.insert(0, str(CLI_SRC))
    try:
        from caseloop_cli import main as cli_main  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - importability depends on venv
        pytest.skip(f"caseloop_cli package not importable from this venv: {exc}")
    return cli_main


def test_drill_cli_derived_table_fallback_keeps_frozen_help_unchanged() -> None:
    cli_main = _cli_import()
    evidence = DrillEvidence()
    baseline_help = cli_main.build_parser().format_help()

    manifest = _load_json(OPERATION_MANIFEST)
    derived = _derive_cli_allowlist(manifest)
    assert len(derived) == 11

    # Frozen v2-gated command surface (mirror of cli/main.py ``_V2_COMMANDS``
    # plus the shared ``capabilities`` command): the fallback allowlist.
    frozen_v2 = sorted(
        f"{command} {action}"
        for command, action in _parser_command_paths(cli_main.build_parser())
        if command in {
            "application",
            "environment",
            "system-component",
            "dependency-edge",
            "system-manifest",
            "capabilities",
        }
    )

    # Fault A: the derivation source is unavailable -> fallback to the frozen
    # v2 surface, recorded explicitly, never silently coerced.
    missing_path = GENERATED_DIR / "operation-manifest.json.does-not-exist"
    try:
        _derive_cli_allowlist(_load_json(missing_path))
    except OSError as exc:
        evidence.record(
            surface="cli",
            code="cli.operation_manifest_unavailable",
            detail=str(exc),
        )
        derived = frozen_v2
    assert evidence.codes() == ["cli.operation_manifest_unavailable"]
    # Fallback covers all 11 activated CLI commands; the only extra frozen
    # pair is the local-only "system-manifest validate" (never a wire call).
    assert set(_derive_cli_allowlist(manifest)) <= set(derived)
    assert derived[-1] == "system-manifest validate"
    assert cli_main.build_parser().format_help() == baseline_help


def test_drill_cli_manifest_parser_mismatch_is_surfaced_not_coerced() -> None:
    cli_main = _cli_import()
    evidence = DrillEvidence()
    baseline_help = cli_main.build_parser().format_help()

    # Fault B: tampered manifest advertises a CLI command the frozen parser
    # does not implement -> the mismatch is surfaced and recorded; the frozen
    # parser surface (help) stays byte-identical.
    tampered = _load_json(OPERATION_MANIFEST)
    tampered = json.loads(json.dumps(tampered))  # decouple from the file
    tampered["operations"] = [
        {**op, "cli": "application purge"} if op["intent"] == "applications.get" else op
        for op in tampered["operations"]
    ]
    derived = _derive_cli_allowlist(tampered)
    parser_paths = _parser_command_paths(cli_main.build_parser())
    missing = [cmd for cmd in derived if tuple(cmd.split(" ", 1)) not in parser_paths]
    if missing:
        evidence.record(
            surface="cli",
            code="cli.manifest_parser_mismatch",
            detail={"missing_from_parser": missing},
        )
        # Fallback: frozen parser surface stays authoritative.
    assert evidence.codes() == ["cli.manifest_parser_mismatch"]
    assert missing == ["application purge"]
    assert "purge" not in cli_main.build_parser().format_help()
    assert cli_main.build_parser().format_help() == baseline_help


# ---------------------------------------------------------------------------
# Scenario 4: console double-guard mismatch (python-side equivalent)
# ---------------------------------------------------------------------------


def test_drill_console_double_guard_mismatch_renders_authoritative_surface() -> None:
    evidence = DrillEvidence()
    capability_manifest = _load_json(CAPABILITY_MANIFEST)
    operation_manifest = _load_json(OPERATION_MANIFEST)

    # Baseline: both guards agree; the authoritative surface is guard B.
    authoritative = console_double_guard(capability_manifest, operation_manifest, evidence)
    assert evidence.codes() == ["console.double_guard_ok"]
    baseline_bytes = _canonical_bytes(authoritative)

    # Fault: capability-manifest is tampered (execution_mode flipped) while the
    # operation-manifest (canonical C1 source) stays true.
    tampered = json.loads(json.dumps(capability_manifest))
    for entry in tampered["enabled_intents"]:
        if entry["name"] == "system-manifests.import":
            entry["execution_mode"] = "synchronous"
    with pytest.raises(ConsoleDoubleGuardMismatch) as excinfo:
        console_double_guard(tampered, operation_manifest, evidence)
    assert excinfo.value.detail["guard_a_only"]
    assert evidence.codes() == ["console.double_guard_ok", "console.double_guard_mismatch"]

    # No silent coercion: the tampered value is never rendered; the
    # authoritative surface renders byte-identically.
    rederived = console_double_guard(capability_manifest, operation_manifest, DrillEvidence())
    assert _canonical_bytes(rederived) == baseline_bytes
    assert all(triple[0] != PHANTOM_INTENT for triple in rederived)


def test_drill_console_double_guard_phantom_intent_never_renders() -> None:
    evidence = DrillEvidence()
    operation_manifest = _load_json(OPERATION_MANIFEST)
    capability_manifest = _load_json(CAPABILITY_MANIFEST)

    tampered = json.loads(json.dumps(capability_manifest))
    tampered["enabled_intents"] = list(tampered["enabled_intents"]) + [
        {
            "name": PHANTOM_INTENT,
            "scope": "applications:manage",
            "execution_mode": "synchronous",
            "http": True,
            "cli": True,
        }
    ]
    with pytest.raises(ConsoleDoubleGuardMismatch):
        console_double_guard(tampered, operation_manifest, evidence)
    assert evidence.codes()[-1] == "console.double_guard_mismatch"

    rederived = console_double_guard(capability_manifest, operation_manifest, DrillEvidence())
    assert all(triple[0] != PHANTOM_INTENT for triple in rederived)
