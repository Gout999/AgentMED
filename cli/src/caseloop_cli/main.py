from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid

from caseloop_cli.discovery import DiscoveryError, discover, render_draft
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence, TextIO

import httpx
from pydantic import ValidationError

from ._generated.manifest_v2 import (
    SystemManifestImportRequest,
    SystemVersionRecordRequest,
)
from ._generated.operation_manifest import (
    CliOperation,
    CliOperationManifestError,
    V2_CLI_OPERATIONS,
)
from .client import PublicApiClient, RuntimeConfig
from .config import load_profile, read_credential, setting
from .errors import CliError, ExitFamily


_IDS = {
    "workspace": re.compile(r"^ws_[0-9A-Za-z]{8,64}$"),
    "source": re.compile(r"^src_[0-9A-Za-z]{8,64}$"),
    "project": re.compile(r"^proj_[0-9A-Za-z]{8,64}$"),
    "environment": re.compile(r"^env_[0-9A-Za-z]{8,64}$"),
    "governed_agent": re.compile(r"^ga_[0-9A-Za-z]{8,64}$"),
    "case": re.compile(r"^case_[0-9A-Za-z]{8,64}$"),
    "receipt": re.compile(r"^ter_[0-9A-Za-z]{8,64}$"),
    "cursor": re.compile(r"^cur_[0-9A-Za-z_-]{8,512}$"),
    "application": re.compile(r"^app_[0-9A-Za-z]{8,64}$"),
    "component": re.compile(r"^cmp_[0-9A-Za-z]{8,64}$"),
    "edge": re.compile(r"^de_[0-9A-Za-z]{8,64}$"),
    "version_set": re.compile(r"^vset_[0-9A-Za-z]{8,64}$"),
    "principal": re.compile(r"^prn_[0-9A-Za-z]{8,64}$"),
    "digest": re.compile(r"^sha256:[0-9a-f]{64}$"),
}

_V1_COMMANDS = frozenset({"signal", "report", "evidence"})
# v2-gated command names derived from the C1 activated-operation manifest
# (``contracts/v5/generated/operation-manifest.json``).  ``capabilities``
# stays a default-major v1 command that additionally supports explicit
# --api-version 2, so it is deliberately not part of the gate set.
_V2_COMMANDS = frozenset(
    operation.command
    for operation in V2_CLI_OPERATIONS
    if operation.command != "capabilities"
)
# (command, action) -> operation metadata for every v2-gated intent.
_V2_OPERATIONS: dict[tuple[str, str], CliOperation] = {
    (operation.command, operation.action): operation
    for operation in V2_CLI_OPERATIONS
    if operation.command != "capabilities"
}
_V1_CASE_ACTIONS = frozenset({"get", "timeline"})


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise CliError("CLI_USAGE_INVALID", ExitFamily.INPUT)


def _signal_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-id")
    parser.add_argument("--summary", required=True)
    parser.add_argument("--body")
    parser.add_argument("--reporter-ref")
    parser.add_argument("--project-id")
    parser.add_argument("--environment-id")
    parser.add_argument("--governed-agent-id")
    parser.add_argument("--privacy", choices=("PUBLIC", "INTERNAL"), default="INTERNAL")
    parser.add_argument("--source-event-id")
    parser.add_argument("--occurred-at")
    parser.add_argument("--idempotency-key")


def _application_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--owner-principal-id", action="append", required=True)
    parser.add_argument("--criticality", choices=("P0", "P1", "P2", "P3"), required=True)
    parser.add_argument(
        "--data-classification",
        choices=("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"),
        required=True,
    )
    parser.add_argument("--governance-mode", choices=("MANAGED", "OBSERVED"), required=True)
    parser.add_argument("--idempotency-key")


def _environment_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--logical-name", required=True)
    parser.add_argument(
        "--risk-classification", choices=("LOW", "MEDIUM", "HIGH", "CRITICAL"), required=True
    )
    parser.add_argument("--idempotency-key")


def _component_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--application-id", required=True)
    parser.add_argument(
        "--component-kind",
        choices=(
            "APPLICATION_CODE",
            "AGENT",
            "MODEL_BINDING",
            "PROMPT",
            "DATASET",
            "INDEX",
            "EMBEDDING",
            "RETRIEVER",
            "SKILL",
            "MCP_SERVER",
            "TOOL_SCHEMA",
            "POLICY",
            "MEMORY_POLICY",
            "RUNTIME_PROFILE",
            "CONNECTOR",
        ),
        required=True,
    )
    parser.add_argument("--logical-name", required=True)
    parser.add_argument("--owner-principal-id", action="append", required=True)
    parser.add_argument("--criticality", choices=("P0", "P1", "P2", "P3"), required=True)
    parser.add_argument(
        "--data-classification",
        choices=("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"),
        required=True,
    )
    parser.add_argument(
        "--permission-classification",
        choices=("READ_ONLY", "READ_WRITE", "ELEVATED"),
        required=True,
    )
    parser.add_argument(
        "--effect-classification", choices=("NONE", "LOCAL", "EXTERNAL"), required=True
    )
    parser.add_argument(
        "--dataset-role", choices=("RUNTIME_DATA", "EVALUATION_DATA", "SEALED_HOLDOUT")
    )
    parser.add_argument("--idempotency-key")


def _edge_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--from-component-id", required=True)
    parser.add_argument("--to-component-id", required=True)
    parser.add_argument(
        "--relation",
        choices=("DEPENDS_ON", "INVOKES", "DATA_FLOW", "CONTAINS", "REFERENCES"),
        required=True,
    )
    parser.add_argument("--required", action="store_true")
    parser.add_argument("--idempotency-key")


def _application_list_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--cursor")


def _manifest_import_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument("--idempotency-key")


def _system_version_record_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--environment-id", required=True)
    # Exact bindings are JSON documents: component revisions are a list,
    # topology revision is a single binding object.
    parser.add_argument("--component-revisions", required=True)
    parser.add_argument("--topology-revision", required=True)
    parser.add_argument("--exact-previous-version-set")
    parser.add_argument("--idempotency-key")


def _system_version_get_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--system-version-set-id", required=True)


def _system_version_diff_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-version-set-id", required=True)
    parser.add_argument("--target-version-set-id", required=True)


def _id_positional(argument: str) -> Callable[[argparse.ArgumentParser], None]:
    def _add(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(argument)

    return _add


# Hand-written parser registration for each activated (command, action) pair:
# the option shapes are CLI payload concerns the C1 manifest does not carry.
# A manifest entry without a registration fails closed at parser build time.
_V2_ACTION_OPTIONS: dict[
    tuple[str, str], Callable[[argparse.ArgumentParser], None]
] = {
    ("application", "register"): _application_options,
    ("application", "get"): _id_positional("application_id"),
    ("application", "list"): _application_list_options,
    ("environment", "register"): _environment_options,
    ("environment", "get"): _id_positional("environment_id"),
    ("system-component", "register"): _component_options,
    ("system-component", "get"): _id_positional("component_id"),
    ("dependency-edge", "record"): _edge_options,
    ("dependency-edge", "get"): _id_positional("edge_id"),
    ("system-manifest", "import"): _manifest_import_options,
    # ``validate`` is a local-only command, not an activated operation; it
    # stays registered beside the derived ``import`` action.
    ("system-manifest", "validate"): _manifest_import_options,
    ("system-version", "record"): _system_version_record_options,
    ("system-version", "get"): _system_version_get_options,
    ("system-version", "diff"): _system_version_diff_options,
}


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="caseloop")
    parser.add_argument("--profile")
    parser.add_argument("--api-url")
    parser.add_argument("--workspace-id")
    parser.add_argument("--token-env")
    parser.add_argument("--token-file")
    parser.add_argument("--token-stdin", action="store_true")
    parser.add_argument(
        "--api-version",
        choices=("1", "2"),
        default="1",
        help="Explicit public API major; v2 commands require --api-version 2.",
    )
    commands = parser.add_subparsers(dest="command", required=True, parser_class=SafeArgumentParser)

    init = commands.add_parser("init")
    init.add_argument("directory")

    capabilities = commands.add_parser("capabilities")
    capabilities.add_subparsers(dest="action", required=True, parser_class=SafeArgumentParser).add_parser("get")

    signal = commands.add_parser("signal")
    submit = signal.add_subparsers(dest="action", required=True, parser_class=SafeArgumentParser).add_parser("submit")
    _signal_options(submit)
    report = commands.add_parser("report")
    _signal_options(report)

    case = commands.add_parser("case")
    case_actions = case.add_subparsers(dest="action", required=True, parser_class=SafeArgumentParser)
    case_get = case_actions.add_parser("get")
    case_get.add_argument("case_id")
    timeline = case_actions.add_parser("timeline")
    timeline.add_argument("case_id")
    timeline.add_argument("--limit", type=int, default=50)
    timeline.add_argument("--cursor")

    evidence = commands.add_parser("evidence")
    evidence_get = evidence.add_subparsers(dest="action", required=True, parser_class=SafeArgumentParser).add_parser("get")
    evidence_get.add_argument("receipt_id")

    # v2 command surface derived from the C1 activated-operation manifest:
    # only activated intents produce subcommands, actions and help entries.
    # ``capabilities`` is a v1 command with explicit v2 support and stays on
    # the hand-written v1 surface above.
    v2_actions: dict[str, list[str]] = {}
    for operation in V2_CLI_OPERATIONS:
        if operation.command == "capabilities":
            continue
        v2_actions.setdefault(operation.command, []).append(operation.action)
    # ``system-manifest validate`` is a local-only command (no HTTP operation)
    # and must remain available beside the derived ``import`` action.
    v2_actions.setdefault("system-manifest", []).append("validate")
    for command in sorted(v2_actions):
        group = commands.add_parser(command)
        actions = group.add_subparsers(
            dest="action", required=True, parser_class=SafeArgumentParser
        )
        for action in sorted(v2_actions[command]):
            options = _V2_ACTION_OPTIONS.get((command, action))
            if options is None:
                raise CliOperationManifestError(
                    "v5.cli.operation_manifest_invalid: "
                    f"no CLI parser registration for activated intent {command} {action}"
                )
            options(actions.add_parser(action))

    return parser


def _required(value: str | None, code: str) -> str:
    if not value:
        raise CliError(code, ExitFamily.CONFIG)
    return value


def _valid_id(value: str | None, kind: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if value is None or not _IDS[kind].fullmatch(value):
        raise CliError(f"{kind.upper()}_ID_INVALID", ExitFamily.INPUT)
    return value


def _occurred_at(value: str | None, now: Callable[[], datetime]) -> str:
    if value is None:
        moment = now()
    else:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise CliError("OCCURRED_AT_INVALID", ExitFamily.INPUT) from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise CliError("OCCURRED_AT_INVALID", ExitFamily.INPUT)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_json(stream: TextIO, payload: object) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _load_manifest_payload(path: str) -> dict[str, object]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError("MANIFEST_FILE_UNREADABLE", ExitFamily.INPUT) from exc
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError("MANIFEST_INVALID_JSON", ExitFamily.INPUT) from exc
    if not isinstance(payload, dict):
        raise CliError("MANIFEST_INVALID", ExitFamily.INPUT)
    # Manifest drafts from the local discovery renderer carry an
    # informational ``_discovery`` section; strip underscore-prefixed
    # metadata keys before canonical validation.
    payload = {key: value for key, value in payload.items() if not key.startswith("_")}
    try:
        model = SystemManifestImportRequest.model_validate(payload)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in exc.errors()})
        raise CliError(
            "MANIFEST_INVALID",
            ExitFamily.INPUT,
            payload={"fields": fields},
        ) from None
    # Send the server-identical canonical dump (defaults included) so the
    # request-fingerprint binding on the import receipt matches exactly.
    return model.model_dump(mode="json")


def _cmd_manifest_validate(args: argparse.Namespace, *, output_stream: TextIO) -> int:
    _load_manifest_payload(args.manifest_file)
    _write_json(output_stream, {"schema_version": "1.0", "manifest_valid": True})
    return int(ExitFamily.OK)


def _operation_path(operation: CliOperation, **ids: str) -> str:
    """Render a manifest path template with the validated resource id.

    A template/parameter mismatch means the frozen manifest drifted from the
    CLI handlers; fail closed instead of emitting a malformed URL.
    """
    try:
        return operation.path.format(**ids)
    except (KeyError, ValueError) as exc:
        raise CliError("CLI_USAGE_INVALID", ExitFamily.INPUT) from exc


def run(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> int:
    actual_env = dict(os.environ if env is None else env)
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    clock = now or (lambda: datetime.now(timezone.utc))
    try:
        args = build_parser().parse_args(argv)
        api_major = args.api_version
        if api_major == "2" and args.command in _V1_COMMANDS:
            raise CliError("API_MAJOR_MISMATCH", ExitFamily.INPUT)
        if api_major == "1" and args.command in _V2_COMMANDS:
            raise CliError("API_VERSION_REQUIRED", ExitFamily.INPUT)
        if args.command == "case":
            if args.action in _V1_CASE_ACTIONS and api_major != "1":
                raise CliError("API_MAJOR_MISMATCH", ExitFamily.INPUT)

        # Local-only commands never need API credentials or a running server.
        if args.command == "init":
            try:
                result = discover(args.directory)
            except DiscoveryError as exc:
                raise CliError("CLI_USAGE_INVALID", ExitFamily.INPUT) from exc
            _write_json(output_stream, json.loads(render_draft(result)))
            return int(ExitFamily.OK)
        if args.command == "system-manifest" and args.action == "validate":
            return _cmd_manifest_validate(args, output_stream=output_stream)

        profile_path = args.profile or actual_env.get("CASELOOP_PROFILE")
        profile = load_profile(profile_path) if profile_path else {}
        api_url = _required(
            setting(args.api_url, actual_env, "CASELOOP_API_URL", profile, "api_url"),
            "API_URL_REQUIRED",
        )
        workspace = _required(
            setting(args.workspace_id, actual_env, "CASELOOP_WORKSPACE_ID", profile, "workspace_id"),
            "WORKSPACE_ID_REQUIRED",
        )
        token_env = args.token_env or profile.get("token_env") or "CASELOOP_PUBLIC_TOKEN"
        token_file = args.token_file or profile.get("token_file")
        token = read_credential(
            env=actual_env,
            stdin=input_stream,
            token_env=token_env,
            token_file=token_file,
            token_stdin=args.token_stdin,
        )
        client = PublicApiClient(
            RuntimeConfig(api_url, workspace, token),
            transport=transport,
            sleep=sleep,
            uuid_factory=uuid_factory,
        )

        if args.command == "capabilities":
            capability_path = (
                "/api/v2/capabilities"
                if api_major == "2"
                else "/api/v1/capabilities"
            )
            result = client.request(
                "GET", capability_path, api_major=int(api_major)
            )
        elif args.command in _V2_COMMANDS:
            operation = _V2_OPERATIONS.get((args.command, args.action))
            if operation is None:
                raise CliError("CLI_USAGE_INVALID", ExitFamily.INPUT)
            if args.command == "application" and args.action == "register":
                idem = args.idempotency_key or f"application-register-{uuid_factory().hex}"
                if not 8 <= len(idem) <= 128:
                    raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
                if len(args.slug) > 64 or len(args.display_name) > 256:
                    raise CliError("APPLICATION_INPUT_INVALID", ExitFamily.INPUT)
                payload = {
                    "schema_version": "2.0",
                    "project_id": _valid_id(args.project_id, "project", required=True),
                    "slug": args.slug,
                    "display_name": args.display_name,
                    "owner_principal_ids": [
                        _valid_id(item, "principal", required=True)
                        for item in args.owner_principal_id
                    ],
                    "criticality": args.criticality,
                    "data_classification": args.data_classification,
                    "governance_mode": args.governance_mode,
                }
                result = client.request(
                    operation.method,
                    operation.path,
                    body=json.dumps(
                        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8"),
                    idempotency_key=idem,
                    api_major=2,
                )
            elif args.command == "application" and args.action == "get":
                application_id = _valid_id(args.application_id, "application", required=True)
                result = client.request(
                    operation.method,
                    _operation_path(operation, application_id=application_id),
                    api_major=2,
                )
            elif args.command == "application" and args.action == "list":
                if args.limit < 1 or args.limit > 100:
                    raise CliError("APPLICATION_INPUT_INVALID", ExitFamily.INPUT)
                params = [("limit", str(args.limit))]
                params.append(
                    (
                        "project_id",
                        _valid_id(args.project_id, "project", required=True),
                    )
                )
                if args.cursor is not None:
                    params.append(("cursor", args.cursor))
                result = client.request(
                    operation.method,
                    operation.path,
                    params=params,
                    api_major=2,
                )
            elif args.command == "environment" and args.action == "register":
                idem = args.idempotency_key or f"environment-register-{uuid_factory().hex}"
                if not 8 <= len(idem) <= 128:
                    raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
                if len(args.logical_name) > 128:
                    raise CliError("ENVIRONMENT_INPUT_INVALID", ExitFamily.INPUT)
                payload = {
                    "schema_version": "2.0",
                    "application_id": _valid_id(args.application_id, "application", required=True),
                    "logical_name": args.logical_name,
                    "risk_classification": args.risk_classification,
                }
                result = client.request(
                    operation.method,
                    operation.path,
                    body=json.dumps(
                        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8"),
                    idempotency_key=idem,
                    api_major=2,
                )
            elif args.command == "environment" and args.action == "get":
                environment_id = _valid_id(args.environment_id, "environment", required=True)
                result = client.request(
                    operation.method,
                    _operation_path(operation, environment_id=environment_id),
                    api_major=2,
                )
            elif args.command == "system-component" and args.action == "register":
                idem = args.idempotency_key or f"component-register-{uuid_factory().hex}"
                if not 8 <= len(idem) <= 128:
                    raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
                if len(args.logical_name) > 128:
                    raise CliError("COMPONENT_INPUT_INVALID", ExitFamily.INPUT)
                payload = {
                    "schema_version": "2.0",
                    "application_id": _valid_id(args.application_id, "application", required=True),
                    "component_kind": args.component_kind,
                    "logical_name": args.logical_name,
                    "owner_principal_ids": [
                        _valid_id(item, "principal", required=True)
                        for item in args.owner_principal_id
                    ],
                    "criticality": args.criticality,
                    "data_classification": args.data_classification,
                    "permission_classification": args.permission_classification,
                    "effect_classification": args.effect_classification,
                    # Always present so the request fingerprint covers the same
                    # canonical fields as the server-side model dump.
                    "dataset_role": args.dataset_role,
                }
                result = client.request(
                    operation.method,
                    operation.path,
                    body=json.dumps(
                        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8"),
                    idempotency_key=idem,
                    api_major=2,
                )
            elif args.command == "system-component" and args.action == "get":
                component_id = _valid_id(args.component_id, "component", required=True)
                result = client.request(
                    operation.method,
                    _operation_path(operation, component_id=component_id),
                    api_major=2,
                )
            elif args.command == "dependency-edge" and args.action == "record":
                idem = args.idempotency_key or f"edge-record-{uuid_factory().hex}"
                if not 8 <= len(idem) <= 128:
                    raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
                payload = {
                    "schema_version": "2.0",
                    "application_id": _valid_id(args.application_id, "application", required=True),
                    "from_component_id": _valid_id(
                        args.from_component_id, "component", required=True
                    ),
                    "to_component_id": _valid_id(args.to_component_id, "component", required=True),
                    "relation": args.relation,
                    "required": args.required,
                }
                result = client.request(
                    operation.method,
                    operation.path,
                    body=json.dumps(
                        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8"),
                    idempotency_key=idem,
                    api_major=2,
                )
            elif args.command == "dependency-edge" and args.action == "get":
                edge_id = _valid_id(args.edge_id, "edge", required=True)
                result = client.request(
                    operation.method,
                    _operation_path(operation, dependency_edge_id=edge_id),
                    api_major=2,
                )
            elif args.command == "system-manifest" and args.action == "import":
                manifest_payload = _load_manifest_payload(args.manifest_file)
                idem = args.idempotency_key or f"system-manifest-import-{uuid_factory().hex}"
                if not 8 <= len(idem) <= 128:
                    raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
                result = client.request(
                    operation.method,
                    operation.path,
                    body=json.dumps(
                        manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8"),
                    idempotency_key=idem,
                    api_major=2,
                )
            elif args.command == "system-version" and args.action == "record":
                idem = args.idempotency_key or f"system-version-record-{uuid_factory().hex}"
                if not 8 <= len(idem) <= 128:
                    raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
                try:
                    component_bindings = json.loads(args.component_revisions)
                    topology_binding = json.loads(args.topology_revision)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise CliError("SYSTEM_VERSION_INPUT_INVALID", ExitFamily.INPUT) from None
                if not isinstance(component_bindings, list) or not isinstance(
                    topology_binding, dict
                ):
                    raise CliError("SYSTEM_VERSION_INPUT_INVALID", ExitFamily.INPUT)
                if args.exact_previous_version_set is not None:
                    try:
                        previous_binding = json.loads(args.exact_previous_version_set)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        raise CliError(
                            "SYSTEM_VERSION_INPUT_INVALID", ExitFamily.INPUT
                        ) from None
                else:
                    previous_binding = None
                request_payload = {
                    "schema_version": "2.0",
                    "application_id": _valid_id(
                        args.application_id, "application", required=True
                    ),
                    "environment_id": _valid_id(
                        args.environment_id, "environment", required=True
                    ),
                    "exact_component_revision_bindings": component_bindings,
                    "exact_topology_revision_binding": topology_binding,
                    "exact_previous_system_version_set_binding_or_null": previous_binding,
                }
                try:
                    model = SystemVersionRecordRequest.model_validate(request_payload)
                except ValidationError as exc:
                    fields = sorted(
                        {
                            ".".join(str(part) for part in item["loc"])
                            for item in exc.errors()
                        }
                    )
                    raise CliError(
                        "SYSTEM_VERSION_INPUT_INVALID",
                        ExitFamily.INPUT,
                        payload={"fields": fields},
                    ) from None
                # Canonical dump (defaults included) so the request-fingerprint
                # binding on the record receipt matches the server model dump.
                payload = model.model_dump(mode="json")
                result = client.request(
                    operation.method,
                    operation.path,
                    body=json.dumps(
                        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8"),
                    idempotency_key=idem,
                    api_major=2,
                )
            elif args.command == "system-version" and args.action == "get":
                system_version_set_id = _valid_id(
                    args.system_version_set_id, "version_set", required=True
                )
                result = client.request(
                    operation.method,
                    _operation_path(
                        operation, system_version_set_id=system_version_set_id
                    ),
                    api_major=2,
                )
            elif args.command == "system-version" and args.action == "diff":
                params = [
                    (
                        "source_version_set_id",
                        _valid_id(
                            args.source_version_set_id, "version_set", required=True
                        ),
                    ),
                    (
                        "target_version_set_id",
                        _valid_id(
                            args.target_version_set_id, "version_set", required=True
                        ),
                    ),
                ]
                result = client.request(
                    operation.method,
                    operation.path,
                    params=params,
                    api_major=2,
                )
            else:
                raise CliError("CLI_USAGE_INVALID", ExitFamily.INPUT)
        elif args.command == "case" and args.action == "get":
            case_id = _valid_id(args.case_id, "case", required=True)
            result = client.request("GET", f"/api/v1/cases/{case_id}")
        elif args.command == "case" and args.action == "timeline":
            case_id = _valid_id(args.case_id, "case", required=True)
            if not 1 <= args.limit <= 200:
                raise CliError("TIMELINE_LIMIT_INVALID", ExitFamily.INPUT)
            params = [("limit", str(args.limit))]
            if args.cursor is not None:
                if not _IDS["cursor"].fullmatch(args.cursor):
                    raise CliError("TIMELINE_CURSOR_INVALID", ExitFamily.INPUT)
                params.append(("cursor", args.cursor))
            result = client.request("GET", f"/api/v1/cases/{case_id}/timeline", params=params)
        elif args.command == "evidence":
            receipt_id = _valid_id(args.receipt_id, "receipt", required=True)
            result = client.request("GET", f"/api/v1/evidence/{receipt_id}")
        else:
            source_id = setting(args.source_id, actual_env, "CASELOOP_SOURCE_ID", profile, "source_id")
            source_id = _valid_id(source_id, "source", required=True)
            reporter_ref = _required(
                setting(args.reporter_ref, actual_env, "CASELOOP_REPORTER_REF", profile, "reporter_ref"),
                "REPORTER_REF_REQUIRED",
            )
            if not 1 <= len(args.summary) <= 500 or len(reporter_ref) > 256:
                raise CliError("SIGNAL_INPUT_INVALID", ExitFamily.INPUT)
            if args.body is not None and len(args.body) > 20_000:
                raise CliError("SIGNAL_INPUT_INVALID", ExitFamily.INPUT)
            if args.idempotency_key is not None and (
                args.source_event_id is None or args.occurred_at is None
            ):
                raise CliError("STABLE_EVENT_FIELDS_REQUIRED", ExitFamily.INPUT)
            event_id = args.source_event_id or f"maintainer-report-{uuid_factory().hex}"
            if not 1 <= len(event_id) <= 512:
                raise CliError("SOURCE_EVENT_ID_INVALID", ExitFamily.INPUT)
            idem = args.idempotency_key or f"signal-submit-{uuid_factory().hex}"
            if not 8 <= len(idem) <= 128:
                raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
            payload = {
                "schema_version": "1.0",
                "source_id": source_id,
                "source_event_id": event_id,
                "source_event_version": "1",
                "signal_kind": "maintainer_report",
                "reporter": {"kind": "maintainer", "source_subject_ref": reporter_ref},
                "project_id": _valid_id(
                    setting(args.project_id, actual_env, "CASELOOP_PROJECT_ID", profile, "project_id"),
                    "project",
                ),
                "environment_id": _valid_id(
                    setting(
                        args.environment_id,
                        actual_env,
                        "CASELOOP_ENVIRONMENT_ID",
                        profile,
                        "environment_id",
                    ),
                    "environment",
                ),
                "governed_agent_id": _valid_id(
                    setting(
                        args.governed_agent_id,
                        actual_env,
                        "CASELOOP_GOVERNED_AGENT_ID",
                        profile,
                        "governed_agent_id",
                    ),
                    "governed_agent",
                ),
                "occurred_at": _occurred_at(args.occurred_at, clock),
                "content": {"summary": args.summary, "body": args.body, "attachments": []},
                "run_locator": None,
                "privacy_classification": args.privacy,
            }
            body = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            result = client.request(
                "POST", "/api/v1/signals", body=body, idempotency_key=idem
            )
        _write_json(output_stream, result)
        return int(ExitFamily.OK)
    except CliError as exc:
        _write_json(error_stream, exc.as_payload())
        return int(exc.exit_family)


def entrypoint() -> None:
    raise SystemExit(run())
