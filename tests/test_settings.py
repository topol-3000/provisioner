"""Unit tests for Settings.

Tests that default values are correct, otel_enabled property works,
and required fields have proper defaults.
"""

from typing import TYPE_CHECKING

from provisioning_worker.settings import Settings

if TYPE_CHECKING:
    import pytest

_DEFAULT_HEALTH_PORT = 8001


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings instantiated with minimal env vars has expected defaults."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("HEALTH_PORT", raising=False)
    monkeypatch.delenv("DEPLOYMENT_ADAPTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    settings = Settings(
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
    assert settings.environment == "dev"
    assert settings.health_port == _DEFAULT_HEALTH_PORT
    assert settings.deployment_adapter == "fake"


def test_otel_enabled_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """otel_enabled is False when no OTLP endpoint is configured."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    settings = Settings(
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
    assert settings.otel_enabled is False


def test_otel_enabled_true() -> None:
    """otel_enabled is True when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    settings = Settings(
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        otel_exporter_otlp_endpoint="http://localhost:4317",
    )
    assert settings.otel_enabled is True
