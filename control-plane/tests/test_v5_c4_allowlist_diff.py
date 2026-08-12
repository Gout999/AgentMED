"""C4 allowlist diff: runtime surfaces vs C1 generated artifacts.

Three exact-allowlist checks (v5-architecture-convergence.md#C4):

1. capability runtime table (``app.services.v5_capabilities.
   IMPLEMENTED_V5_PUBLIC_INTENTS``) == ``contracts/v5/generated/
   capability-manifest.json`` — name/scope/execution_mode item by item
   (order-sensitive, deterministic);
2. operation-manifest http entries == the 11 registered ``public_v5`` routes
   — via the real C4 judge (``app.api.v5_route_registry.
   check_registered_v5_routes``) plus an independent source-AST cross-check;
3. CLI-side allowlist — operation-manifest ``cli`` entries resolve in the
   frozen parser (``caseloop_cli.main.build_parser``), the v2-gated command
   groups match exactly (only the local-only ``system-manifest validate``
   extra), v1 commands stay preserved and the default API major stays 1.

The check is exact in both directions: nothing activated may be missing and
nothing unactivated may be exposed.  Only activated operations produce
routes, CLI commands, help or discovery entries.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_DIR = REPO_ROOT / "contracts/v5/generated"
CAPABILITY_MANIFEST = GENERATED_DIR / "capability-manifest.json"
OPERATION_MANIFEST = GENERATED_DIR / "operation-manifest.json"
PUBLIC_V5_SOURCE = REPO_ROOT / "control-plane/app/api/public_v5.py"
CLI_SRC = REPO_ROOT / "cli/src"
V2_PREFIX = "/api/v2"

EXPECTED_ACTIVATED_INTENTS = frozenset(
    {
        "capabilities.get",
        "applications.register",
        "applications.get",
        "applications.list",
        "environments.register",
        "environments.get",
        "system-components.register",
        "system-components.get",
        "dependency-edges.record",
        "dependency-edges.get",
        "system-manifests.import",
        "system-versions.record",
        "system-versions.get",
        "system-versions.diff",
        "cases.bind-application",
        "case-application-bindings.get",
        "acceptance-criteria.propose",
        "acceptance-criteria.get",
        "acceptance-criteria.confirm",
        "investigations.start",
        "operations.get",
        "operations.list",
        "operations.cancel-request",
    }
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _triples(entries: list[dict]) -> list[tuple[str, str, str]]:
    return [(entry["name"], entry["scope"], entry["execution_mode"]) for entry in entries]


# ---------------------------------------------------------------------------
# 1. Capability runtime table vs capability-manifest.json
# ---------------------------------------------------------------------------


def test_capability_runtime_table_matches_capability_manifest() -> None:
    from app.services.v5_capabilities import IMPLEMENTED_V5_PUBLIC_INTENTS

    manifest = _load_json(CAPABILITY_MANIFEST)
    manifest_entries = manifest["enabled_intents"]
    assert manifest["enabled_intent_count"] == len(manifest_entries) == 23

    runtime = _triples(
        [
            {
                "name": raw["name"],
                "scope": raw["scope"],
                "execution_mode": raw["execution_mode"],
            }
            for raw in IMPLEMENTED_V5_PUBLIC_INTENTS
        ]
    )
    expected = _triples(manifest_entries)
    # Exact per-item match in manifest order (deterministic) ...
    assert runtime == expected, (
        f"runtime table / capability-manifest mismatch:\n"
        f"  runtime : {runtime}\n  manifest: {expected}"
    )
    # ... and as sets, so no duplicate/renamed intent can hide.
    assert set(runtime) == set(expected)
    assert {intent for intent, _scope, _mode in runtime} == EXPECTED_ACTIVATED_INTENTS
    # Both http and cli are advertised for every activated intent.
    assert all(entry["http"] is True and entry["cli"] is True for entry in manifest_entries)


# ---------------------------------------------------------------------------
# 2. Operation-manifest http entries vs public_v5 registered routes
# ---------------------------------------------------------------------------


def _public_v5_route_decorators() -> set[tuple[str, str, str]]:
    """AST extraction of @router.get/post decorators in public_v5.py."""
    tree = ast.parse(PUBLIC_V5_SOURCE.read_text(encoding="utf-8"))
    routes: set[tuple[str, str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and func.attr in {"get", "post"}):
                continue
            if not decorator.args:
                continue
            method = func.attr.upper()
            path = ast.literal_eval(decorator.args[0])
            operation_id = None
            for keyword in decorator.keywords:
                if keyword.arg == "operation_id":
                    operation_id = ast.literal_eval(keyword.value)
            assert operation_id, f"{node.name}: route decorator lacks operation_id"
            routes.add((method, V2_PREFIX + path, operation_id))
    return routes


def test_operation_manifest_http_matches_public_v5_routes() -> None:
    from app.api import public_v5
    from app.api.v5_route_registry import check_registered_v5_routes

    manifest = _load_json(OPERATION_MANIFEST)
    operations = manifest["operations"]
    assert manifest["activated_intent_count"] == len(operations) == 23
    http_entries = [op["http"] for op in operations if op.get("http") is not None]
    assert len(http_entries) == 23
    assert {op["intent"] for op in operations} == EXPECTED_ACTIVATED_INTENTS

    expected = {
        (entry["method"].upper(), entry["path"], entry["operation_id"])
        for entry in http_entries
    }

    # Real C4 judge: registered router table must equal the manifest surface.
    check_registered_v5_routes(public_v5.router)  # raises on any missing/extra

    # Independent AST cross-check of the decorator surface.
    decorated = _public_v5_route_decorators()
    assert decorated == expected, (
        f"public_v5 decorators vs operation-manifest http mismatch:\n"
        f"  extra decorators : {sorted(decorated - expected)}\n"
        f"  missing from code: {sorted(expected - decorated)}"
    )

    # Only GET/POST; no activated intent may be reachable under another
    # method, and no unregistered handler may carry a route decorator.
    assert all(method in {"GET", "POST"} for method, _path, _op in expected)
    assert len({operation_id for _m, _p, operation_id in expected}) == 23


def test_public_v5_routes_and_v1_lane_majors_are_preserved() -> None:
    """v2 is the only new surface; the v1/v4 lane and default major are untouched."""
    from app.api import public_v4, public_v5

    assert public_v5.router.prefix == V2_PREFIX
    assert public_v4.router.prefix == "/api/v1"
    assert len(public_v4.router.routes) > 0, "v1/v4 preserved lane must stay registered"


def test_generated_request_schema_is_an_effective_fail_closed_gate() -> None:
    from app.public_api.v5_generated_wire import (
        GeneratedWireValidationError,
        validate_generated_wire,
    )
    from app.public_api.v5_models import ApplicationRegisterRequest

    payload = ApplicationRegisterRequest(
        schema_version="2.0",
        project_id="proj_01J0000000000001",
        slug="generated-gate",
        display_name="Generated gate",
        owner_principal_ids=["prn_01J0000000000001"],
        criticality="P1",
        data_classification="INTERNAL",
        governance_mode="MANAGED",
    ).model_dump(mode="json")
    validate_generated_wire(
        model_name="ApplicationRegisterRequest",
        direction="request",
        payload=payload,
    )
    payload["generated_schema_forbidden_extra"] = True
    with pytest.raises(GeneratedWireValidationError):
        validate_generated_wire(
            model_name="ApplicationRegisterRequest",
            direction="request",
            payload=payload,
        )


def test_public_v5_validates_response_before_commit() -> None:
    source = PUBLIC_V5_SOURCE.read_text(encoding="utf-8")
    assert "wire_response = _json_response(response" in source
    assert source.count("wire_response = _json_response(response") == source.count(
        "return wire_response"
    )
    assert "_commit(session)\n        return _json_response" not in source


# ---------------------------------------------------------------------------
# 3. CLI-side allowlist vs operation-manifest cli entries
# ---------------------------------------------------------------------------


def _cli_main():
    sys.path.insert(0, str(CLI_SRC))
    try:
        from caseloop_cli import main as cli_main  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - importability depends on venv
        pytest.skip(f"caseloop_cli package not importable from this venv: {exc}")
    return cli_main


def _parser_command_paths(parser) -> set[tuple[str, str]]:
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


def _parser_commands(parser) -> set[str]:
    """Top-level subcommand names regardless of nested actions."""
    commands: set[str] = set()
    for action in getattr(parser, "_actions", []):
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            commands.update(choices)
    return commands


def test_cli_allowlist_matches_operation_manifest() -> None:
    cli_main = _cli_main()
    manifest = _load_json(OPERATION_MANIFEST)
    parser_paths = _parser_command_paths(cli_main.build_parser())
    help_text = cli_main.build_parser().format_help()

    # Every activated operation's cli string resolves in the frozen parser and
    # appears in help; nothing beyond the manifest (+ the documented local-only
    # "system-manifest validate") is exposed as a v2 command path.
    manifest_cli_pairs = set()
    parser = cli_main.build_parser()
    parser_paths = _parser_command_paths(parser)
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict)
    )
    help_text = parser.format_help()
    for operation in manifest["operations"]:
        cli = operation.get("cli")
        assert isinstance(cli, str), operation["intent"]
        tokens = cli.split(" ")
        if cli == "acceptance-criteria confirm":
            # manifest carries the confirm action as a standalone 2-token
            # command; the CLI normalizes it into the case command family
            key = ("case", "acceptance-criteria", "confirm")
            path_key = ("case", "acceptance-criteria")
        elif len(tokens) == 3:
            key = (tokens[0], tokens[1], tokens[2])
            path_key = (tokens[0], tokens[1])
        else:
            key = (tokens[0], tokens[1], None)
            path_key = (tokens[0], tokens[1])
        manifest_cli_pairs.add(key)
        assert path_key in parser_paths, (
            f"manifest cli '{cli}' has no parser path"
        )
        # Nested actions appear only in the subcommand help, not the top-level
        # help; check both so a manifest cli entry is never hidden.
        command_parser = subparsers.choices[path_key[0]]
        command_help_flat = " ".join(command_parser.format_help().split())
        assert cli in " ".join(help_text.split()) or path_key[1] in command_help_flat, (
            f"manifest cli '{cli}' missing from CLI help"
        )
    assert len(manifest_cli_pairs) == 23

    v2_commands = (
        {key[0] for key in manifest_cli_pairs}
        - {"capabilities", "case"}
    )
    assert v2_commands == set(cli_main._V2_COMMANDS), (
        f"v2 command groups {sorted(v2_commands)} != cli _V2_COMMANDS "
        f"{sorted(cli_main._V2_COMMANDS)}"
    )

    frozen_v2_pairs = {
        (command, action)
        for command, action in parser_paths
        if command in v2_commands | {"capabilities"}
        or (command == "case" and action not in {"get", "timeline"})
    }
    manifest_path_keys = {key[:2] for key in manifest_cli_pairs}
        # Exact: manifest path keys plus local-only orchestration helpers.
    assert frozen_v2_pairs == manifest_path_keys | {
        ("system-manifest", "validate"),
            ("case", "from-issue"),
            ("operation", "wait"),
            ("operation", "follow"),
    }, (
        f"frozen v2 surface {sorted(frozen_v2_pairs)} != "
        f"manifest {sorted(manifest_cli_pairs)} + validate"
    )

    # v1 lane preserved: frozen stage-1a commands stay in the parser
    # ("report" is a leaf command with no nested action).
    assert {"signal", "report", "case", "evidence"} <= _parser_commands(parser)


def test_cli_default_api_major_stays_one() -> None:
    cli_main = _cli_main()
    parser = cli_main.build_parser()
    api_version = next(
        action for action in parser._actions if action.dest == "api_version"
    )
    assert api_version.default == "1", "CLI default API major must stay 1"
    # v2 commands are explicitly gated on --api-version 2 (never implicit).
    assert sorted(cli_main._V2_COMMANDS) == sorted(
        {"application", "environment", "system-component", "dependency-edge",
         "system-manifest", "system-version", "operation"}
    )
