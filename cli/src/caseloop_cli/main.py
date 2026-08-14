from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence, TextIO

import httpx
import rfc8785
from pydantic import ValidationError

from ._generated.manifest_v2 import SystemManifestImportRequest
from .client import PublicApiClient, RuntimeConfig
from .config import load_profile, read_credential, setting
from .discovery import DiscoveryError, discover
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
    "binding": re.compile(r"^acb_[0-9A-Za-z]{8,64}$"),
    "acceptance_revision": re.compile(r"^acr_[0-9A-Za-z]{8,64}$"),
    "digest": re.compile(r"^sha256:[0-9a-f]{64}$"),
}

_V1_COMMANDS = frozenset({"signal", "report", "evidence"})
_V2_COMMANDS = frozenset(
    {
        "application",
        "environment",
        "system-component",
        "dependency-edge",
        "system-manifest",
        "init",
    }
)
# ``case`` carries both v1 (get/timeline) and v2 (binding/acceptance/from-issue)
# actions; the per-action api-major checks live in run().
_V1_CASE_ACTIONS = frozenset({"get", "timeline"})
_V2_CASE_ACTIONS = frozenset(
    {"bind-application", "application-binding", "acceptance-criteria", "from-issue"}
)


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

    # V5-1C case actions (api-major 2).
    bind_application = case_actions.add_parser("bind-application")
    bind_application.add_argument("case_id")
    bind_application.add_argument("--application-id", required=True)
    bind_application.add_argument("--environment-id", required=True)
    bind_application.add_argument("--case-revision", type=int, default=1)
    bind_application.add_argument("--case-digest", required=True)
    bind_version = bind_application.add_mutually_exclusive_group()
    bind_version.add_argument("--system-version-set-id")
    bind_version.add_argument("--declared-version-unknown", action="store_true")
    bind_application.add_argument("--issue-snapshot-file")
    bind_application.add_argument("--idempotency-key")

    application_binding = case_actions.add_parser("application-binding")
    application_binding_actions = application_binding.add_subparsers(
        dest="action2", required=True, parser_class=SafeArgumentParser
    )
    binding_get = application_binding_actions.add_parser("get")
    binding_get.add_argument("case_id")
    binding_get.add_argument("--case-revision", type=int, default=1)
    binding_get.add_argument("--case-digest", required=True)

    acceptance_criteria = case_actions.add_parser("acceptance-criteria")
    acceptance_criteria_actions = acceptance_criteria.add_subparsers(
        dest="action2", required=True, parser_class=SafeArgumentParser
    )
    propose = acceptance_criteria_actions.add_parser("propose")
    propose.add_argument("case_id")
    propose.add_argument("--case-revision", type=int, default=1)
    propose.add_argument("--case-digest", required=True)
    propose.add_argument("--acceptance-json", required=True)
    propose.add_argument("--idempotency-key")
    criteria_get = acceptance_criteria_actions.add_parser("get")
    criteria_get.add_argument("case_id")
    criteria_get.add_argument("--case-revision", type=int, default=1)
    confirm = acceptance_criteria_actions.add_parser("confirm")
    confirm.add_argument("acceptance_criteria_revision_id")
    confirm.add_argument("--case-id", required=True)
    confirm.add_argument("--case-revision", type=int, default=1)
    confirm.add_argument("--proposed-revision-digest", required=True)
    confirm.add_argument("--confirmation-note")
    confirm.add_argument("--idempotency-key")

    from_issue = case_actions.add_parser("from-issue")
    from_issue.add_argument("github_url")
    from_issue.add_argument("--application-id", required=True)
    from_issue.add_argument("--environment-id", required=True)
    from_issue_version = from_issue.add_mutually_exclusive_group()
    from_issue_version.add_argument("--system-version-set-id")
    from_issue_version.add_argument("--declared-version-unknown", action="store_true")
    from_issue.add_argument("--snapshot-file")
    from_issue.add_argument("--refresh", action="store_true")
    from_issue.add_argument("--source-id")
    from_issue.add_argument("--reporter-ref")
    from_issue.add_argument("--idempotency-key")

    evidence = commands.add_parser("evidence")
    evidence_get = evidence.add_subparsers(dest="action", required=True, parser_class=SafeArgumentParser).add_parser("get")
    evidence_get.add_argument("receipt_id")

    application = commands.add_parser("application")
    application_actions = application.add_subparsers(
        dest="action", required=True, parser_class=SafeArgumentParser
    )
    application_register = application_actions.add_parser("register")
    _application_options(application_register)
    application_get = application_actions.add_parser("get")
    application_get.add_argument("application_id")

    environment = commands.add_parser("environment")
    environment_actions = environment.add_subparsers(
        dest="action", required=True, parser_class=SafeArgumentParser
    )
    environment_register = environment_actions.add_parser("register")
    _environment_options(environment_register)
    environment_get = environment_actions.add_parser("get")
    environment_get.add_argument("environment_id")

    component = commands.add_parser("system-component")
    component_actions = component.add_subparsers(
        dest="action", required=True, parser_class=SafeArgumentParser
    )
    component_register = component_actions.add_parser("register")
    _component_options(component_register)
    component_get = component_actions.add_parser("get")
    component_get.add_argument("component_id")

    edge = commands.add_parser("dependency-edge")
    edge_actions = edge.add_subparsers(
        dest="action", required=True, parser_class=SafeArgumentParser
    )
    edge_record = edge_actions.add_parser("record")
    _edge_options(edge_record)
    edge_get = edge_actions.add_parser("get")
    edge_get.add_argument("edge_id")

    system_manifest = commands.add_parser("system-manifest")
    manifest_actions = system_manifest.add_subparsers(
        dest="action", required=True, parser_class=SafeArgumentParser
    )
    for action in ("import", "record", "validate"):
        action_parser = manifest_actions.add_parser(action)
        action_parser.add_argument("--manifest-file", required=True)
        action_parser.add_argument("--idempotency-key")
    manifest_get = manifest_actions.add_parser("get")
    manifest_get.add_argument("system_version_set_id")
    manifest_diff = manifest_actions.add_parser("diff")
    manifest_diff.add_argument("--base-system-version-set-id", required=True)
    manifest_diff.add_argument("--target-system-version-set-id", required=True)

    init = commands.add_parser("init")
    init.add_argument("repo")
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
    # ``caseloop init`` drafts carry an informational ``_discovery`` section;
    # strip underscore-prefixed metadata keys before canonical validation.
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


_ISSUE_URL = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]{1,128})/"
    r"(?P<repo>[A-Za-z0-9_.-]{1,128})/issues/(?P<number>[1-9][0-9]{0,9})$"
)
_ISSUE_CACHE_ENV = "CASELOOP_CACHE_DIR"


def _parse_issue_url(url: str) -> tuple[str, str, int]:
    match = _ISSUE_URL.fullmatch(url)
    if match is None:
        raise CliError("ISSUE_URL_INVALID", ExitFamily.INPUT)
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def _issue_cache_dir(env: dict[str, str]) -> Path:
    configured = env.get(_ISSUE_CACHE_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "caseloop" / "issues"


def _fetch_issue_snapshot(
    url: str,
    *,
    snapshot_file: str | None,
    refresh: bool,
    env: dict[str, str],
    uuid_factory: Callable[[], uuid.UUID],
) -> dict[str, object]:
    """Read-only issue snapshot fetch: local snapshot file, else cached GitHub
    API response, else a live read-only GET.  Never writes to the remote."""
    if snapshot_file is not None:
        try:
            raw = Path(snapshot_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError("ISSUE_SNAPSHOT_UNREADABLE", ExitFamily.INPUT) from exc
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CliError("ISSUE_SNAPSHOT_INVALID_JSON", ExitFamily.INPUT) from exc
        if not isinstance(payload, dict):
            raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
        return payload

    owner, repo, number = _parse_issue_url(url)
    cache = _issue_cache_dir(env)
    cache_file = cache / f"{owner}-{repo}-{number}.json"
    if cache_file.is_file() and not refresh:
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise CliError("ISSUE_SNAPSHOT_UNREADABLE", ExitFamily.INPUT) from exc
        if not isinstance(payload, dict):
            raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
        return payload

    api_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    request = urllib.request.Request(
        api_url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "caseloop-cli"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - read-only public GET
            raw = response.read()
    except Exception as exc:  # noqa: BLE001 - stable boundary for network failures
        raise CliError("ISSUE_FETCH_FAILED", ExitFamily.TEMPORARY) from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError("ISSUE_SNAPSHOT_INVALID_JSON", ExitFamily.INPUT) from exc
    if not isinstance(payload, dict):
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        # A read-only snapshot is still usable without the local cache.
        pass
    return payload


def _issue_snapshot_payload(payload: dict[str, object]) -> dict[str, object]:
    title = payload.get("title")
    if not isinstance(title, str) or not title:
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    # Raw/cached GitHub snapshots created by the earlier 1C path may contain
    # JSON null for these derived flags.  Treat null like an omitted flag and
    # continue deriving ``edited`` from the provider timestamps; still reject
    # every non-null, non-boolean value.
    edited_flag_invalid = (
        "edited_flag" in payload
        and payload["edited_flag"] is not None
        and type(payload["edited_flag"]) is not bool
    )
    deleted_flag_invalid = (
        "deleted_flag" in payload
        and payload["deleted_flag"] is not None
        and type(payload["deleted_flag"]) is not bool
    )
    if edited_flag_invalid or deleted_flag_invalid:
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    created_at = payload.get("created_at")
    updated_at = payload.get("updated_at")
    edited_flag = bool(payload.get("edited_flag", False)) or (
        isinstance(created_at, str)
        and isinstance(updated_at, str)
        and created_at != updated_at
    )
    state = payload.get("state")
    deleted_flag = bool(payload.get("deleted_flag", False)) or state in {
        "deleted",
        "DELETED",
    }
    return {
        "title": title,
        "body": payload.get("body") if isinstance(payload.get("body"), str) else "",
        "state": state if isinstance(state, str) else None,
        "number": payload.get("number"),
        "html_url": payload.get("html_url"),
        "user": {"login": (payload.get("user") or {}).get("login")} if isinstance(payload.get("user"), dict) else None,
        "created_at": created_at if isinstance(created_at, str) else None,
        "updated_at": updated_at if isinstance(updated_at, str) else None,
        "edited_flag": edited_flag,
        "deleted_flag": deleted_flag,
    }


def _validate_github_snapshot_identity(
    payload: dict[str, object], *, owner: str, repo: str, number: int
) -> None:
    """Bind a cached/local GitHub payload to the URL named by the operator."""

    snapshot_number = payload.get("number")
    snapshot_url = payload.get("html_url")
    if type(snapshot_number) is not int or snapshot_number != number:
        raise CliError("ISSUE_SNAPSHOT_IDENTITY_MISMATCH", ExitFamily.INPUT)
    if not isinstance(snapshot_url, str):
        raise CliError("ISSUE_SNAPSHOT_IDENTITY_MISMATCH", ExitFamily.INPUT)
    try:
        snapshot_owner, snapshot_repo, snapshot_url_number = _parse_issue_url(
            snapshot_url
        )
    except CliError:
        raise CliError("ISSUE_SNAPSHOT_IDENTITY_MISMATCH", ExitFamily.INPUT) from None
    if (
        snapshot_owner.lower() != owner.lower()
        or snapshot_repo.lower() != repo.lower()
        or snapshot_url_number != number
    ):
        raise CliError("ISSUE_SNAPSHOT_IDENTITY_MISMATCH", ExitFamily.INPUT)


def _load_issue_snapshot_request(path: str) -> dict[str, object]:
    """Load the exact data-only IssueSnapshotRequest used by bind-application.

    Unlike ``case from-issue --snapshot-file`` (which accepts a raw GitHub API
    response), this file is an explicit source envelope and can represent a
    manual maintainer snapshot without inventing GitHub identity fields.
    """

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError("ISSUE_SNAPSHOT_UNREADABLE", ExitFamily.INPUT) from exc
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CliError("ISSUE_SNAPSHOT_INVALID_JSON", ExitFamily.INPUT) from exc
    if not isinstance(value, dict):
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    allowed = {
        "source_kind",
        "source_url",
        "external_repo",
        "external_issue_number",
        "snapshot_payload",
        "edited_flag",
        "deleted_flag",
        "fetched_at",
    }
    if set(value) - allowed:
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    source_kind = value.get("source_kind")
    snapshot_payload = value.get("snapshot_payload")
    fetched_at = value.get("fetched_at")
    if (
        source_kind not in {"github_issue", "manual"}
        or not isinstance(snapshot_payload, dict)
        or not isinstance(fetched_at, str)
    ):
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    title = snapshot_payload.get("title")
    body = snapshot_payload.get("body")
    if (
        not isinstance(title, str)
        or not 1 <= len(title) <= 512
        or (body is not None and not isinstance(body, str))
    ):
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    # Validate and normalize the timestamp before it reaches the server wire.
    canonical_fetched_at = _occurred_at(fetched_at, lambda: datetime.now(timezone.utc))
    edited_flag = value.get("edited_flag", False)
    deleted_flag = value.get("deleted_flag", False)
    if type(edited_flag) is not bool or type(deleted_flag) is not bool:
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)

    source_url = value.get("source_url")
    external_repo = value.get("external_repo")
    external_issue_number = value.get("external_issue_number")
    if source_kind == "github_issue":
        if (
            not isinstance(source_url, str)
            or not source_url.startswith(("https://", "http://"))
            or len(source_url) > 1024
            or not isinstance(external_repo, str)
            or not 1 <= len(external_repo) <= 256
            or type(external_issue_number) is not int
            or external_issue_number < 1
        ):
            raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)
    elif (
        external_repo is not None
        or external_issue_number is not None
        or (
            source_url is not None
            and (not isinstance(source_url, str) or not 1 <= len(source_url) <= 1024)
        )
    ):
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT)

    canonical_payload = dict(snapshot_payload)
    canonical_payload["edited_flag"] = edited_flag
    canonical_payload["deleted_flag"] = deleted_flag
    return {
        "source_kind": source_kind,
        "source_url": source_url,
        "external_repo": external_repo,
        "external_issue_number": external_issue_number,
        "snapshot_payload": canonical_payload,
        "edited_flag": edited_flag,
        "deleted_flag": deleted_flag,
        "fetched_at": canonical_fetched_at,
    }


def _cmd_manifest_validate(args: argparse.Namespace, *, output_stream: TextIO) -> int:
    _load_manifest_payload(args.manifest_file)
    _write_json(output_stream, {"schema_version": "1.0", "manifest_valid": True})
    return int(ExitFamily.OK)


def _cmd_init(args: argparse.Namespace, *, output_stream: TextIO, error_stream: TextIO) -> int:
    try:
        result = discover(args.repo)
    except DiscoveryError as exc:
        raise CliError(exc.code, ExitFamily.INPUT) from None
    _write_json(output_stream, result.to_manifest_draft())
    error_stream.write(
        "caseloop init: draft only; no server state was written.\n"
        "Complete 'application'/'environment', then run:\n"
        "  caseloop --api-version 2 system-manifest validate --manifest-file <file>\n"
        "  caseloop --api-version 2 system-manifest import --manifest-file <file>\n"
    )
    return int(ExitFamily.OK)


def _canonical_digest(value: object) -> str:
    try:
        canonical = rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError):
        raise CliError("ISSUE_SNAPSHOT_INVALID", ExitFamily.INPUT) from None
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _declared_version_binding(
    args: argparse.Namespace,
    *,
    client: PublicApiClient,
    application_id: str,
    environment_id: str,
) -> dict[str, object]:
    """Resolve a declared VersionSet to its authoritative immutable envelope.

    Operators name only the VersionSet id.  Revision and record digest are
    read from the control plane so stale or hand-copied exact bindings never
    reach ``cases.bind-application``.  Omitting the id is represented honestly
    by the closed ``UNKNOWN`` alternative, never by ``null`` or a fake id.
    """

    version_set_id = getattr(args, "system_version_set_id", None)
    if version_set_id is not None:
        exact_id = _valid_id(version_set_id, "version_set", required=True)
        response = client.request(
            "GET", f"/api/v2/system-versions/{exact_id}", api_major=2
        )
        record = response.get("system_version_set")
        if not isinstance(record, dict):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        envelope = record.get("record_envelope")
        revision = envelope.get("revision") if isinstance(envelope, dict) else None
        digest = envelope.get("record_digest") if isinstance(envelope, dict) else None
        if (
            record.get("system_version_set_id") != exact_id
            or type(revision) is not int
            or revision < 1
            or not isinstance(digest, str)
            or not _IDS["digest"].fullmatch(digest)
        ):
            raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
        if (
            record.get("application_id") != application_id
            or record.get("declared_environment_id") != environment_id
        ):
            raise CliError("SYSTEM_VERSION_BINDING_INVALID", ExitFamily.INPUT)
        return {
            "kind": "SYSTEM_VERSION_SET",
            "id": exact_id,
            "revision": revision,
            "digest": digest,
        }
    return {"kind": "UNKNOWN", "reason": "NOT_DECLARED"}


def _versioned_idempotency_key(base: str, snapshot_version: str) -> str:
    if not 8 <= len(base) <= 512:
        raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
    candidate = f"{base}-{snapshot_version[:24]}"
    if len(candidate) <= 128:
        return candidate
    digest = hashlib.sha256(f"{base}\x00{snapshot_version}".encode("utf-8")).hexdigest()
    return f"case-from-issue-{digest}"


def _authoritative_proposed_revision_binding(
    *,
    client: PublicApiClient,
    case_id: str,
    case_revision: int,
    revision_id: str,
    expected_digest: str,
) -> dict[str, object]:
    """Resolve a proposal's exact immutable binding from the Case read model."""

    response = client.request(
        "GET",
        f"/api/v2/cases/{case_id}/acceptance-criteria",
        params=[("case_revision", str(case_revision))],
        api_major=2,
    )
    exact_case = response.get("exact_case_binding")
    revisions = response.get("revisions")
    if (
        not isinstance(exact_case, dict)
        or exact_case.get("case_id") != case_id
        or exact_case.get("case_revision") != case_revision
        or not isinstance(exact_case.get("case_digest"), str)
        or not _IDS["digest"].fullmatch(exact_case["case_digest"])
        or not isinstance(revisions, list)
    ):
        raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
    matches = [
        item
        for item in revisions
        if isinstance(item, dict)
        and item.get("acceptance_criteria_revision_id") == revision_id
    ]
    if not matches:
        raise CliError("PROPOSED_REVISION_NOT_FOUND", ExitFamily.NOT_FOUND)
    if len(matches) != 1:
        raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
    proposal = matches[0]
    envelope = proposal.get("record_envelope")
    revision = envelope.get("revision") if isinstance(envelope, dict) else None
    digest = envelope.get("record_digest") if isinstance(envelope, dict) else None
    if (
        proposal.get("workspace_id") != response.get("workspace_id")
        or proposal.get("exact_case_binding") != exact_case
        or proposal.get("confirmation_status") != "PROPOSED"
        or type(revision) is not int
        or revision < 1
        or not isinstance(digest, str)
        or not _IDS["digest"].fullmatch(digest)
    ):
        raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
    if digest != expected_digest:
        raise CliError("PROPOSED_REVISION_BINDING_MISMATCH", ExitFamily.INPUT)
    return {
        "kind": "ACCEPTANCE_CRITERIA_REVISION",
        "id": revision_id,
        "revision": revision,
        "digest": digest,
    }


def _cmd_case_from_issue(
    args: argparse.Namespace,
    *,
    client: PublicApiClient,
    env: dict[str, str],
    profile: dict[str, object],
    uuid_factory: Callable[[], uuid.UUID],
    clock: Callable[[], datetime],
    output_stream: TextIO,
    error_stream: TextIO,
) -> dict[str, object]:
    """``caseloop case from-issue <github-url>`` orchestration.

    Composes only canonical intents: signals.submit → cases.bind-application →
    acceptance-criteria.propose (draft).  The issue snapshot is read-only data
    (local file / cached GET); issue text is never an instruction and nothing
    is ever auto-confirmed.  Deterministic source-event ids and idempotency
    keys make retries safe (no duplicate case, no second owner).
    """
    owner, repo, number = _parse_issue_url(args.github_url)
    snapshot = _fetch_issue_snapshot(
        args.github_url,
        snapshot_file=args.snapshot_file,
        refresh=args.refresh,
        env=env,
        uuid_factory=uuid_factory,
    )
    _validate_github_snapshot_identity(
        snapshot, owner=owner, repo=repo, number=number
    )
    normalized = _issue_snapshot_payload(snapshot)
    # Send and hash the same canonical payload that the server persists.  The
    # request model mirrors the derived flags into ``snapshot_payload``; if the
    # CLI hashes/sends legacy nulls here, the server's authoritative
    # idempotency receipt no longer matches the wire request.
    canonical_snapshot = dict(snapshot)
    canonical_snapshot["edited_flag"] = bool(normalized["edited_flag"])
    canonical_snapshot["deleted_flag"] = bool(normalized["deleted_flag"])
    snapshot_digest = _canonical_digest(canonical_snapshot)
    snapshot_version = snapshot_digest.removeprefix("sha256:")
    source_event_id = f"github-issue:{owner}:{repo}:{number}:{snapshot_version}"
    if not 1 <= len(source_event_id) <= 512:
        raise CliError("SOURCE_EVENT_ID_INVALID", ExitFamily.INPUT)
    provider_version_time = snapshot.get("updated_at") or snapshot.get("created_at")
    if not isinstance(provider_version_time, str):
        raise CliError("ISSUE_SNAPSHOT_VERSION_MISSING", ExitFamily.INPUT)
    occurred_at = _occurred_at(provider_version_time, clock)
    title = normalized["title"]
    if len(title) > 256:
        title = title[:253] + "..."
    body = str(normalized["body"] or "")
    if len(body) > 20_000:
        body = body[:19_997] + "..."

    source_id = _valid_id(
        setting(args.source_id, env, "CASELOOP_SOURCE_ID", profile, "source_id"),
        "source",
        required=True,
    )
    reporter_ref = _required(
        setting(args.reporter_ref, env, "CASELOOP_REPORTER_REF", profile, "reporter_ref"),
        "REPORTER_REF_REQUIRED",
    )
    if len(reporter_ref) > 256:
        raise CliError("SIGNAL_INPUT_INVALID", ExitFamily.INPUT)

    # Resolve the Case's product scope before creating any Signal.  This keeps
    # the S1A Case correlated to the known V5 application/environment instead
    # of emitting an unscoped project_id=null Case and discovering a bad target
    # only after the write.
    application_id = _valid_id(args.application_id, "application", required=True)
    environment_id = _valid_id(args.environment_id, "environment", required=True)
    application_response = client.request(
        "GET", f"/api/v2/applications/{application_id}", api_major=2
    )
    application_record = application_response.get("application")
    project_id = (
        application_record.get("project_id")
        if isinstance(application_record, dict)
        else None
    )
    if (
        not isinstance(application_record, dict)
        or application_record.get("workspace_id")
        != application_response.get("workspace_id")
        or not isinstance(project_id, str)
        or not _IDS["project"].fullmatch(project_id)
    ):
        raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
    if application_record.get("lifecycle_state") != "ACTIVE":
        raise CliError("APPLICATION_NOT_ACTIVE", ExitFamily.INPUT)

    environment_response = client.request(
        "GET", f"/api/v2/environments/{environment_id}", api_major=2
    )
    environment_record = environment_response.get("environment")
    if (
        not isinstance(environment_record, dict)
        or environment_record.get("workspace_id")
        != environment_response.get("workspace_id")
        or environment_record.get("application_id") != application_id
    ):
        raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
    if environment_record.get("lifecycle_state") != "ACTIVE":
        raise CliError("ENVIRONMENT_NOT_ACTIVE", ExitFamily.INPUT)
    declared_version = _declared_version_binding(
        args,
        client=client,
        application_id=application_id,
        environment_id=environment_id,
    )

    attachment_digest = snapshot_digest
    signal_idem = _versioned_idempotency_key(
        args.idempotency_key or f"case-from-issue-{owner}-{repo}-{number}",
        snapshot_version,
    )
    signal_payload = {
        "schema_version": "1.0",
        "source_id": source_id,
        "source_event_id": source_event_id,
        "source_event_version": snapshot_version,
        "signal_kind": "maintainer_report",
        "reporter": {"kind": "maintainer", "source_subject_ref": reporter_ref},
        "project_id": project_id,
        "environment_id": environment_id,
        "governed_agent_id": None,
        "occurred_at": occurred_at,
        "content": {
            "summary": title,
            "body": body,
            "attachments": [
                {
                    "uri": args.github_url,
                    "digest": attachment_digest,
                    "media_type": "application/json",
                }
            ],
        },
        "run_locator": None,
        "privacy_classification": "PUBLIC",
    }
    signal_response = client.request(
        "POST",
        "/api/v1/signals",
        body=json.dumps(
            signal_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        idempotency_key=signal_idem,
        api_major=1,
    )
    case_id = signal_response["case"]["case_id"]
    case_revision = signal_response["case"]["revision"]

    # Resolve the exact case binding (case_id, case_revision, case_digest) from
    # the authoritative read path, then bind and propose as canonical intents.
    criteria_response = client.request(
        "GET",
        f"/api/v2/cases/{case_id}/acceptance-criteria",
        params=[("case_revision", str(case_revision))],
        api_major=2,
    )
    exact_binding = criteria_response.get("exact_case_binding")
    if not isinstance(exact_binding, dict) or not isinstance(
        exact_binding.get("case_digest"), str
    ):
        raise CliError("REMOTE_BINDING_INVALID", ExitFamily.PROTOCOL)
    case_digest = exact_binding["case_digest"]

    fetched_at = occurred_at
    bind_idem = _versioned_idempotency_key(
        f"case-bind-{owner}-{repo}-{number}", snapshot_version
    )
    bind_payload = {
        "schema_version": "2.0",
        "case_id": case_id,
        "case_revision": case_revision,
        "case_digest": case_digest,
        "application_id": application_id,
        "environment_id": environment_id,
        "declared_system_version_set_binding_or_unknown": declared_version,
        "issue_snapshot": {
            "source_kind": "github_issue",
            "source_url": args.github_url,
            "external_repo": f"{owner}/{repo}",
            "external_issue_number": number,
            "snapshot_payload": canonical_snapshot,
            "edited_flag": bool(normalized.get("edited_flag", False)),
            "deleted_flag": bool(normalized.get("deleted_flag", False)),
            "fetched_at": fetched_at,
        },
    }
    bind_response = client.request(
        "POST",
        f"/api/v2/cases/{case_id}:bind-application",
        body=json.dumps(
            bind_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        idempotency_key=bind_idem,
        api_major=2,
    )
    propose_idem = _versioned_idempotency_key(
        f"acceptance-propose-{owner}-{repo}-{number}", snapshot_version
    )
    propose_payload = {
        "schema_version": "2.0",
        "case_id": case_id,
        "case_revision": case_revision,
        "case_digest": case_digest,
        "acceptance_source": {
            "kind": "github_issue",
            "url": args.github_url,
            "repo": f"{owner}/{repo}",
            "number": number,
        },
        "reproducer_input": {
            "kind": "github_issue_body",
            "untrusted": True,
            "issue_url": args.github_url,
            "issue_body": body,
        },
        "reproducer_environment": None,
        "expected_behavior": {
            "kind": "maintainer_review_required",
            "untrusted": True,
            "issue_title": title,
            "note": "draft derived from the issue title only; expected behavior "
            "is not acceptance truth until confirmed by a human maintainer",
        },
        "oracle_or_evaluator": None,
        "applicable_workload_profile": {
            "name": "unknown",
            "note": "workload profile must be confirmed by a human",
        },
        "applicable_deployment_profile": {
            "name": "unknown",
            "note": "deployment profile must be confirmed by a human",
        },
    }
    propose_response = client.request(
        "POST",
        f"/api/v2/cases/{case_id}:propose-acceptance-criteria",
        body=json.dumps(
            propose_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        idempotency_key=propose_idem,
        api_major=2,
    )
    revision = propose_response["acceptance_criteria_revision"]
    revision_id = revision["acceptance_criteria_revision_id"]
    revision_number = revision["record_envelope"]["revision"]
    revision_digest = revision["record_envelope"]["record_digest"]

    error_stream.write(
        f"caseloop case from-issue: case {case_id} bound to "
        f"{bind_response['application_case_binding']['application_id']}; "
        f"acceptance draft {revision_id} recorded as PROPOSED (untrusted).\n"
        "No acceptance criteria were auto-confirmed. A reauthenticated human "
        "maintainer/domain reviewer may confirm the draft; confirmation remains "
        "non-executable until V5-4 materializes a ResolutionContract:\n"
        f"  caseloop --api-version 2 case acceptance-criteria confirm {revision_id} "
        f"--case-id {case_id} --case-revision {case_revision} "
        f"--proposed-revision-digest {revision_digest}\n"
    )
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "case_revision": case_revision,
        "case_digest": case_digest,
        # This is the digest of the exact provider payload used as the source
        # event version.  The control plane separately computes the persisted
        # normalized IssueSourceSnapshot record digest.
        "source_snapshot_payload_digest": snapshot_digest,
        "source_event_version": snapshot_version,
        "application_case_binding_id": bind_response["application_case_binding"][
            "application_case_binding_id"
        ],
        "acceptance_criteria_revision_id": revision_id,
        "acceptance_criteria_record_revision": revision_number,
        "acceptance_criteria_revision_digest": revision_digest,
        "case_readiness": "NEEDS_ACCEPTANCE_CRITERIA",
        "next_action": {
            "code": "CONFIRM_ACCEPTANCE_CRITERIA",
            "command": (
                f"caseloop --api-version 2 case acceptance-criteria confirm "
                f"{revision_id} --case-id {case_id} "
                f"--case-revision {case_revision} "
                f"--proposed-revision-digest {revision_digest}"
            ),
        },
    }


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
            if args.action in _V2_CASE_ACTIONS and api_major != "2":
                raise CliError("API_VERSION_REQUIRED", ExitFamily.INPUT)

        # Local-only commands never need API credentials or a running server.
        if args.command == "init":
            return _cmd_init(args, output_stream=output_stream, error_stream=error_stream)
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
            selected_major = int(api_major)
            result = client.request(
                "GET",
                f"/api/v{selected_major}/capabilities",
                api_major=selected_major,
            )
        elif args.command == "application" and args.action == "register":
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
                "POST",
                "/api/v2/applications",
                body=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif args.command == "application" and args.action == "get":
            application_id = _valid_id(args.application_id, "application", required=True)
            result = client.request(
                "GET", f"/api/v2/applications/{application_id}", api_major=2
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
                "POST",
                "/api/v2/environments",
                body=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif args.command == "environment" and args.action == "get":
            environment_id = _valid_id(args.environment_id, "environment", required=True)
            result = client.request(
                "GET", f"/api/v2/environments/{environment_id}", api_major=2
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
                "POST",
                "/api/v2/system-components",
                body=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif args.command == "system-component" and args.action == "get":
            component_id = _valid_id(args.component_id, "component", required=True)
            result = client.request(
                "GET", f"/api/v2/system-components/{component_id}", api_major=2
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
                "POST",
                "/api/v2/dependency-edges",
                body=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif args.command == "dependency-edge" and args.action == "get":
            edge_id = _valid_id(args.edge_id, "edge", required=True)
            result = client.request(
                "GET", f"/api/v2/dependency-edges/{edge_id}", api_major=2
            )
        elif args.command == "system-manifest" and args.action in ("import", "record"):
            manifest_payload = _load_manifest_payload(args.manifest_file)
            idem = args.idempotency_key or f"system-manifest-import-{uuid_factory().hex}"
            if not 8 <= len(idem) <= 128:
                raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
            result = client.request(
                "POST",
                "/api/v2/system-manifests:import",
                body=json.dumps(
                    manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif args.command == "system-manifest" and args.action == "get":
            system_version_set_id = _valid_id(
                args.system_version_set_id, "version_set", required=True
            )
            result = client.request(
                "GET", f"/api/v2/system-versions/{system_version_set_id}", api_major=2
            )
        elif args.command == "system-manifest" and args.action == "diff":
            base_id = _valid_id(
                args.base_system_version_set_id, "version_set", required=True
            )
            target_id = _valid_id(
                args.target_system_version_set_id, "version_set", required=True
            )
            result = client.request(
                "GET",
                "/api/v2/system-versions:diff",
                params=[
                    ("base_system_version_set_id", base_id),
                    ("target_system_version_set_id", target_id),
                ],
                api_major=2,
            )
        elif args.command == "case" and args.action == "bind-application":
            case_id = _valid_id(args.case_id, "case", required=True)
            case_digest = _valid_id(args.case_digest, "digest", required=True) if args.case_digest else None
            if case_digest is None or not 1 <= args.case_revision:
                raise CliError("CASE_BINDING_INPUT_INVALID", ExitFamily.INPUT)
            idem = args.idempotency_key or f"case-bind-application-{uuid_factory().hex}"
            if not 8 <= len(idem) <= 128:
                raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
            application_id = _valid_id(
                args.application_id, "application", required=True
            )
            environment_id = _valid_id(
                args.environment_id, "environment", required=True
            )
            issue_snapshot = (
                _load_issue_snapshot_request(args.issue_snapshot_file)
                if args.issue_snapshot_file is not None
                else None
            )
            declared = _declared_version_binding(
                args,
                client=client,
                application_id=application_id,
                environment_id=environment_id,
            )
            payload = {
                "schema_version": "2.0",
                "case_id": case_id,
                "case_revision": args.case_revision,
                "case_digest": case_digest,
                "application_id": application_id,
                "environment_id": environment_id,
                "declared_system_version_set_binding_or_unknown": declared,
                "issue_snapshot": issue_snapshot,
            }
            result = client.request(
                "POST",
                f"/api/v2/cases/{case_id}:bind-application",
                body=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif (
            args.command == "case"
            and args.action == "application-binding"
            and args.action2 == "get"
        ):
            case_id = _valid_id(args.case_id, "case", required=True)
            case_digest = _valid_id(args.case_digest, "digest", required=True)
            result = client.request(
                "GET",
                f"/api/v2/cases/{case_id}/application-binding",
                params=[
                    ("case_revision", str(args.case_revision)),
                    ("case_digest", case_digest),
                ],
                api_major=2,
            )
        elif (
            args.command == "case"
            and args.action == "acceptance-criteria"
            and args.action2 == "propose"
        ):
            case_id = _valid_id(args.case_id, "case", required=True)
            case_digest = _valid_id(args.case_digest, "digest", required=True)
            try:
                draft = json.loads(args.acceptance_json)
            except json.JSONDecodeError as exc:
                raise CliError("ACCEPTANCE_JSON_INVALID", ExitFamily.INPUT) from None
            if not isinstance(draft, dict):
                raise CliError("ACCEPTANCE_JSON_INVALID", ExitFamily.INPUT)
            idem = args.idempotency_key or f"acceptance-propose-{uuid_factory().hex}"
            if not 8 <= len(idem) <= 128:
                raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
            for required in (
                "acceptance_source",
                "expected_behavior",
                "applicable_workload_profile",
                "applicable_deployment_profile",
            ):
                if required not in draft:
                    raise CliError("ACCEPTANCE_JSON_INVALID", ExitFamily.INPUT)
            # The request fingerprint must match the server's canonical model
            # dump, so optional fields are sent explicitly as null.
            draft.setdefault("reproducer_input", None)
            draft.setdefault("reproducer_environment", None)
            draft.setdefault("oracle_or_evaluator", None)
            payload = {
                "schema_version": "2.0",
                "case_id": case_id,
                "case_revision": args.case_revision,
                "case_digest": case_digest,
                **draft,
            }
            result = client.request(
                "POST",
                f"/api/v2/cases/{case_id}:propose-acceptance-criteria",
                body=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif (
            args.command == "case"
            and args.action == "acceptance-criteria"
            and args.action2 == "get"
        ):
            case_id = _valid_id(args.case_id, "case", required=True)
            result = client.request(
                "GET",
                f"/api/v2/cases/{case_id}/acceptance-criteria",
                params=[("case_revision", str(args.case_revision))],
                api_major=2,
            )
        elif (
            args.command == "case"
            and args.action == "acceptance-criteria"
            and args.action2 == "confirm"
        ):
            revision_id = _valid_id(
                args.acceptance_criteria_revision_id, "acceptance_revision", required=True
            )
            case_id = _valid_id(args.case_id, "case", required=True)
            if not isinstance(args.case_revision, int) or args.case_revision < 1:
                raise CliError("CASE_BINDING_INPUT_INVALID", ExitFamily.INPUT)
            proposed_digest = _valid_id(
                args.proposed_revision_digest, "digest", required=True
            )
            idem = args.idempotency_key or f"acceptance-confirm-{uuid_factory().hex}"
            if not 8 <= len(idem) <= 128:
                raise CliError("IDEMPOTENCY_KEY_INVALID", ExitFamily.INPUT)
            exact_proposed_binding = _authoritative_proposed_revision_binding(
                client=client,
                case_id=case_id,
                case_revision=args.case_revision,
                revision_id=revision_id,
                expected_digest=proposed_digest,
            )
            payload = {
                "schema_version": "2.0",
                "exact_proposed_revision_binding": exact_proposed_binding,
                "confirmation_note": args.confirmation_note,
            }
            result = client.request(
                "POST",
                f"/api/v2/acceptance-criteria/{revision_id}:confirm",
                body=json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8"),
                idempotency_key=idem,
                api_major=2,
            )
        elif args.command == "case" and args.action == "from-issue":
            result = _cmd_case_from_issue(
                args,
                client=client,
                env=actual_env,
                profile=profile,
                uuid_factory=uuid_factory,
                clock=clock,
                output_stream=output_stream,
                error_stream=error_stream,
            )
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
