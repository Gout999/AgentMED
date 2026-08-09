"""MCP workers never receive Quality write authority."""

from common.config import Settings


def test_quality_write_token_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("QUALITY_WRITE_TOKEN", "must-not-enter-mcp-settings")

    settings = Settings(_env_file=None)

    assert not hasattr(settings, "quality_write_token")
    assert "quality_write_token" not in settings.model_dump()
