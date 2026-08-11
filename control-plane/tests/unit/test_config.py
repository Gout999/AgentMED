"""Settings source priority must preserve explicit isolated stores."""

from app.config import Settings


def test_explicit_database_url_overrides_environment(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://production.example/caseloop",
    )

    settings = Settings(database_url="sqlite:///:memory:")

    assert settings.database_url == "sqlite:///:memory:"


def test_public_v5_route_switch_defaults_on_and_can_be_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_PUBLIC_V5", raising=False)
    assert Settings().enable_public_v5 is True
    monkeypatch.setenv("ENABLE_PUBLIC_V5", "false")
    assert Settings().enable_public_v5 is False
