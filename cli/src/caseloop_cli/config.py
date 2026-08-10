from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, TextIO

import yaml

from .errors import CliError, ExitFamily


_ALLOWED_PROFILE_FIELDS = frozenset(
    {
        "api_url",
        "workspace_id",
        "source_id",
        "project_id",
        "environment_id",
        "governed_agent_id",
        "reporter_ref",
        "token_env",
        "token_file",
    }
)
_SECRET_PROFILE_FIELDS = frozenset({"token", "api_key", "authorization", "bearer"})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAX_PROFILE_BYTES = 65_536
_MAX_TOKEN_BYTES = 16_384


class _ProfileParseError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _unique_pairs(pairs: list[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    seen: set[str] = set()
    for key, value in pairs:
        if not isinstance(key, str):
            raise _ProfileParseError("PROFILE_INVALID")
        normalized = key.lower()
        if normalized in seen:
            raise _ProfileParseError("PROFILE_FIELD_COLLISION")
        seen.add(normalized)
        result[key] = value
    return result


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, object]:
    loader.flatten_mapping(node)
    pairs = [
        (
            loader.construct_object(key_node, deep=deep),
            loader.construct_object(value_node, deep=deep),
        )
        for key_node, value_node in node.value
    ]
    return _unique_pairs(pairs)


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_profile(path: str | Path) -> dict[str, str | None]:
    profile_path = Path(path)
    try:
        raw = profile_path.read_bytes()
    except OSError as exc:
        raise CliError("PROFILE_UNAVAILABLE", ExitFamily.CONFIG) from None
    if len(raw) > _MAX_PROFILE_BYTES:
        raise CliError("PROFILE_INVALID", ExitFamily.CONFIG)
    try:
        text = raw.decode("utf-8")
        if profile_path.suffix.lower() == ".json":
            value = json.loads(
                text,
                object_pairs_hook=_unique_pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    _ProfileParseError("PROFILE_INVALID")
                ),
            )
        elif profile_path.suffix.lower() in {".yaml", ".yml"}:
            value = yaml.load(text, Loader=_UniqueKeySafeLoader)
        else:
            raise CliError("PROFILE_FORMAT_UNSUPPORTED", ExitFamily.CONFIG)
    except CliError:
        raise
    except _ProfileParseError as exc:
        raise CliError(exc.code, ExitFamily.CONFIG) from None
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError):
        raise CliError("PROFILE_INVALID", ExitFamily.CONFIG) from None
    if value is None:
        value = {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CliError("PROFILE_INVALID", ExitFamily.CONFIG)
    normalized = {key.lower(): item for key, item in value.items()}
    if _SECRET_PROFILE_FIELDS.intersection(normalized):
        raise CliError("PROFILE_CONTAINS_SECRET", ExitFamily.CONFIG)
    if set(normalized) - _ALLOWED_PROFILE_FIELDS:
        raise CliError("PROFILE_UNKNOWN_FIELD", ExitFamily.CONFIG)
    if not all(item is None or isinstance(item, str) for item in normalized.values()):
        raise CliError("PROFILE_INVALID", ExitFamily.CONFIG)
    return normalized


def _validate_token(token: str) -> str:
    value = token.strip()
    if not value or len(value.encode("utf-8")) > _MAX_TOKEN_BYTES:
        raise CliError("CREDENTIAL_INVALID", ExitFamily.CONFIG)
    if any(character.isspace() for character in value):
        raise CliError("CREDENTIAL_INVALID", ExitFamily.CONFIG)
    return value


def _read_secure_file(path: Path) -> str:
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise CliError("CREDENTIAL_FILE_UNSAFE", ExitFamily.CONFIG)
        if stat.S_IMODE(before.st_mode) != 0o600:
            raise CliError("CREDENTIAL_FILE_UNSAFE", ExitFamily.CONFIG)
        if hasattr(os, "getuid") and before.st_uid != os.getuid():
            raise CliError("CREDENTIAL_FILE_UNSAFE", ExitFamily.CONFIG)
        if not hasattr(os, "O_NOFOLLOW"):
            raise CliError("CREDENTIAL_FILE_UNSAFE", ExitFamily.CONFIG)
        flags = os.O_RDONLY | os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            after = os.fstat(descriptor)
            if (
                (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or not stat.S_ISREG(after.st_mode)
                or stat.S_IMODE(after.st_mode) != 0o600
                or (hasattr(os, "getuid") and after.st_uid != os.getuid())
            ):
                raise CliError("CREDENTIAL_FILE_UNSAFE", ExitFamily.CONFIG)
            raw = os.read(descriptor, _MAX_TOKEN_BYTES + 1)
        finally:
            os.close(descriptor)
    except CliError:
        raise
    except OSError:
        raise CliError("CREDENTIAL_FILE_UNSAFE", ExitFamily.CONFIG) from None
    if len(raw) > _MAX_TOKEN_BYTES:
        raise CliError("CREDENTIAL_INVALID", ExitFamily.CONFIG)
    try:
        return _validate_token(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise CliError("CREDENTIAL_INVALID", ExitFamily.CONFIG) from None


def read_credential(
    *,
    env: dict[str, str],
    stdin: TextIO,
    token_env: str = "CASELOOP_PUBLIC_TOKEN",
    token_file: str | Path | None = None,
    token_stdin: bool = False,
) -> str:
    if not _ENV_NAME.fullmatch(token_env):
        raise CliError("CREDENTIAL_ENV_INVALID", ExitFamily.CONFIG)
    env_value = env.get(token_env)
    sources = int(bool(env_value)) + int(token_file is not None) + int(token_stdin)
    if sources > 1:
        raise CliError("CREDENTIAL_SOURCE_AMBIGUOUS", ExitFamily.CONFIG)
    if sources == 0:
        raise CliError("CREDENTIAL_REQUIRED", ExitFamily.CONFIG)
    if env_value:
        return _validate_token(env_value)
    if token_file is not None:
        return _read_secure_file(Path(token_file))
    try:
        content = stdin.read(_MAX_TOKEN_BYTES + 2)
    except Exception:
        raise CliError("CREDENTIAL_INVALID", ExitFamily.CONFIG) from None
    return _validate_token(content)


def setting(
    cli_value: str | None,
    env: dict[str, str],
    env_name: str,
    profile: dict[str, str | None],
    profile_name: str,
) -> str | None:
    return cli_value or env.get(env_name) or profile.get(profile_name)
