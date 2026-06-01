"""Unit tests for structured logging configuration.

Tests that configure_logging sets up ConsoleRenderer in dev and
JSONRenderer in non-dev environments.
"""

import logging

import structlog
from structlog.processors import JSONRenderer

from provisioning_worker.infrastructure.logging import configure_logging
from provisioning_worker.settings import Settings


def test_console_output_dev() -> None:
    """configure_logging with dev environment uses ConsoleRenderer."""
    settings = Settings(
        environment="dev",
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
    configure_logging(settings)

    root = logging.getLogger()
    assert len(root.handlers) >= 1
    handler = root.handlers[0]
    formatter = handler.formatter
    # ProcessorFormatter wraps a ConsoleRenderer in dev
    assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
    # Verify the last processor is a ConsoleRenderer (not JSONRenderer).
    # ProcessorFormatter stores the renderer as the last element of `.processors`.
    assert isinstance(formatter.processors[-1], structlog.dev.ConsoleRenderer)


def test_json_output_non_dev() -> None:
    """configure_logging with staging environment uses JSONRenderer."""
    settings = Settings(
        environment="staging",
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
    configure_logging(settings)

    root = logging.getLogger()
    assert len(root.handlers) >= 1
    handler = root.handlers[0]
    formatter = handler.formatter
    assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
    # Verify the last processor is a JSONRenderer in non-dev.
    # ProcessorFormatter stores the renderer as the last element of `.processors`.
    assert isinstance(formatter.processors[-1], JSONRenderer)
