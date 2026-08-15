from __future__ import annotations

import io
import json
import os
import stat

import pytest

from agentmed_cli.config import load_profile, read_credential
from agentmed_cli.errors import CliError


TOKEN = "opaque-token-must-stay-secret"


def test_profile_supports_only_nonsecret_json_and_yaml(tmp_path) -> None:
    json_path = tmp_path / "profile.json"
    json_path.write_text(
        json.dumps(
            {
                "api_url": "https://agentmed.example",
                "workspace_id": "ws_01J0000000000001",
                "source_id": "src_01J0000000000001",
                "token_env": "TEAM_AGENTMED_TOKEN",
            }
        ),
        encoding="utf-8",
    )
    yaml_path = tmp_path / "profile.yaml"
    yaml_path.write_text(
        "api_url: https://agentmed.example\nworkspace_id: ws_01J0000000000001\n",
        encoding="utf-8",
    )

    assert load_profile(json_path)["source_id"] == "src_01J0000000000001"
    assert load_profile(yaml_path)["workspace_id"] == "ws_01J0000000000001"


@pytest.mark.parametrize("secret_key", ["token", "api_key", "authorization", "bearer"])
def test_profile_rejects_inline_secrets(tmp_path, secret_key: str) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({secret_key: TOKEN}), encoding="utf-8")

    with pytest.raises(CliError) as caught:
        load_profile(path)

    assert caught.value.code == "PROFILE_CONTAINS_SECRET"
    assert TOKEN not in repr(caught.value)


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (".json", '{"api_url":"https://one.example","api_url":"https://two.example"}'),
        (".json", '{"API_URL":"https://one.example","api_url":"https://two.example"}'),
        (".yaml", "api_url: https://one.example\napi_url: https://two.example\n"),
        (".yaml", "API_URL: https://one.example\napi_url: https://two.example\n"),
    ],
)
def test_profile_rejects_exact_and_casefolded_duplicate_keys(
    tmp_path, suffix: str, content: str
) -> None:
    path = tmp_path / f"profile{suffix}"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(CliError) as caught:
        load_profile(path)

    assert caught.value.code == "PROFILE_FIELD_COLLISION"


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (".json", '{"api_url":NaN}'),
        (".json", '{"api_url":Infinity}'),
        (".yaml", "1: value\n"),
        (".yaml", "api_url: .nan\n"),
        (".yaml", "api_url: .inf\n"),
    ],
)
def test_profile_rejects_nonfinite_values_and_nonstring_keys(
    tmp_path, suffix: str, content: str
) -> None:
    path = tmp_path / f"profile{suffix}"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(CliError) as caught:
        load_profile(path)

    assert caught.value.code == "PROFILE_INVALID"


def test_token_environment_stdin_and_secure_file_sources(tmp_path) -> None:
    assert read_credential(env={"AGENTMED_PUBLIC_TOKEN": TOKEN}, stdin=io.StringIO("")) == TOKEN
    assert read_credential(env={}, stdin=io.StringIO(TOKEN + "\n"), token_stdin=True) == TOKEN

    path = tmp_path / "credential"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    assert read_credential(env={}, stdin=io.StringIO(""), token_file=path) == TOKEN


def test_stdin_credential_consumes_to_eof_and_allows_only_surrounding_whitespace() -> None:
    assert (
        read_credential(
            env={},
            stdin=io.StringIO("  \n" + TOKEN + "\n\t"),
            token_stdin=True,
        )
        == TOKEN
    )
    with pytest.raises(CliError) as caught:
        read_credential(
            env={},
            stdin=io.StringIO(TOKEN + "\nsecond-token"),
            token_stdin=True,
        )
    assert caught.value.code == "CREDENTIAL_INVALID"


def test_token_file_rejects_symlink_and_any_mode_other_than_0600(tmp_path) -> None:
    target = tmp_path / "target"
    target.write_text(TOKEN, encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(CliError) as symlink_error:
        read_credential(env={}, stdin=io.StringIO(""), token_file=link)
    assert symlink_error.value.code == "CREDENTIAL_FILE_UNSAFE"

    target.chmod(0o640)
    with pytest.raises(CliError) as mode_error:
        read_credential(env={}, stdin=io.StringIO(""), token_file=target)
    assert mode_error.value.code == "CREDENTIAL_FILE_UNSAFE"


def test_ambiguous_or_missing_credential_source_fails_closed(tmp_path) -> None:
    path = tmp_path / "credential"
    path.write_text(TOKEN, encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(CliError) as ambiguous:
        read_credential(
            env={"AGENTMED_PUBLIC_TOKEN": TOKEN},
            stdin=io.StringIO(""),
            token_file=path,
        )
    assert ambiguous.value.code == "CREDENTIAL_SOURCE_AMBIGUOUS"

    with pytest.raises(CliError) as missing:
        read_credential(env={}, stdin=io.StringIO(""))
    assert missing.value.code == "CREDENTIAL_REQUIRED"


def test_secret_file_is_owned_by_current_user(tmp_path, monkeypatch) -> None:
    path = tmp_path / "credential"
    path.write_text(TOKEN, encoding="utf-8")
    path.chmod(0o600)
    actual_lstat = os.lstat

    class ForeignOwner:
        def __init__(self, wrapped):
            for name in dir(wrapped):
                if name.startswith("st_"):
                    setattr(self, name, getattr(wrapped, name))
            self.st_uid = os.getuid() + 1

    monkeypatch.setattr(os, "lstat", lambda requested: ForeignOwner(actual_lstat(requested)))
    with pytest.raises(CliError) as caught:
        read_credential(env={}, stdin=io.StringIO(""), token_file=path)
    assert caught.value.code == "CREDENTIAL_FILE_UNSAFE"


def test_secret_file_revalidates_mode_and_owner_from_open_descriptor(tmp_path, monkeypatch) -> None:
    path = tmp_path / "credential"
    path.write_text(TOKEN, encoding="utf-8")
    path.chmod(0o600)
    actual_fstat = os.fstat

    class ChangedAfterOpen:
        def __init__(self, wrapped):
            for name in dir(wrapped):
                if name.startswith("st_"):
                    setattr(self, name, getattr(wrapped, name))
            self.st_mode = stat.S_IFREG | 0o640

    monkeypatch.setattr(os, "fstat", lambda descriptor: ChangedAfterOpen(actual_fstat(descriptor)))
    with pytest.raises(CliError) as caught:
        read_credential(env={}, stdin=io.StringIO(""), token_file=path)
    assert caught.value.code == "CREDENTIAL_FILE_UNSAFE"
