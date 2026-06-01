"""Unit tests for Settings.

Tests that default values are correct, otel_enabled property works,
and the required DSN fields raise when absent.
"""

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from provisioning_worker.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path

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


def test_missing_required_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings raises ValidationError when DSN fields are absent.

    DATABASE_URL, DATABASE_URL_SYNC, and VALKEY_URL must be present
    (T-2: fail-fast at startup, no silent default). Passing _env_file=None
    prevents a developer's local .env from making this test falsely pass.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_SYNC", raising=False)
    monkeypatch.delenv("VALKEY_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_env_file_loading(tmp_path: Path) -> None:
    """Settings loads values from a .env file.

    Writes a temp env file with required DSNs and a distinctive CONSUMER_NAME,
    then verifies Settings picks up the consumer name from the file.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+psycopg://u:p@localhost:5432/db\n"
        "DATABASE_URL_SYNC=postgresql+psycopg://u:p@localhost:5432/db\n"
        "VALKEY_URL=redis://localhost:6379/0\n"
        "CONSUMER_NAME=worker-from-env-file\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=str(env_file))  # type: ignore[call-arg]

    assert settings.consumer_name == "worker-from-env-file"
    assert str(settings.database_url).startswith("postgresql+")
