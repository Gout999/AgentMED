"""Settings source priority must preserve explicit isolated stores."""

from app.config import Settings


def test_explicit_database_url_overrides_environment(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://production.example/agentmed",
    )

    settings = Settings(database_url="sqlite:///:memory:")

    assert settings.database_url == "sqlite:///:memory:"
