"""MCP workers never receive Quality write authority."""

from common.config import Settings


def test_quality_write_token_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("QUALITY_WRITE_TOKEN", "must-not-enter-mcp-settings")

    settings = Settings(_env_file=None)

    assert not hasattr(settings, "quality_write_token")
    assert "quality_write_token" not in settings.model_dump()


def test_notification_log_url_resolves_independently_from_primary_database():
    settings = Settings(
        database_url="sqlite:////tmp/caseloop-primary.db",
        notification_log_url="sqlite:////tmp/caseloop-notification.db",
        _env_file=None,
    )

    assert settings.resolved_notification_url == (
        "sqlite:////tmp/caseloop-notification.db"
    )
