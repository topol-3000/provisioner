"""Unit tests for OpenTelemetry tracing configuration.

Tests that configure_tracing installs the TracerProvider even without
a backend, and that calling it twice is idempotent.
"""

from opentelemetry import trace
from opentelemetry.trace import ProxyTracerProvider

import provisioning_worker.infrastructure.observability as obs_module
from provisioning_worker.infrastructure.observability import configure_tracing
from provisioning_worker.settings import Settings


def test_tracing_no_backend() -> None:
    """configure_tracing installs TracerProvider even with no OTLP endpoint."""
    # Reset the configured guard so this test is reliable
    obs_module._configured = False

    settings = Settings(
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        otel_exporter_otlp_endpoint=None,
    )
    configure_tracing(settings)

    provider = trace.get_tracer_provider()
    # Our TracerProvider was installed (not the default ProxyTracerProvider)
    assert not isinstance(provider, ProxyTracerProvider)


def test_tracing_idempotent() -> None:
    """configure_tracing can be called twice without raising."""
    # Reset so this test is self-contained
    obs_module._configured = False

    settings = Settings(
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
    configure_tracing(settings)
    # Second call must not raise
    configure_tracing(settings)
