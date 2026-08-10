from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Sequence, TextIO

import httpx

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
}


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


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="caseloop")
    parser.add_argument("--profile")
    parser.add_argument("--api-url")
    parser.add_argument("--workspace-id")
    parser.add_argument("--token-env")
    parser.add_argument("--token-file")
    parser.add_argument("--token-stdin", action="store_true")
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

    evidence = commands.add_parser("evidence")
    evidence_get = evidence.add_subparsers(dest="action", required=True, parser_class=SafeArgumentParser).add_parser("get")
    evidence_get.add_argument("receipt_id")
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
            result = client.request("GET", "/api/v1/capabilities")
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
