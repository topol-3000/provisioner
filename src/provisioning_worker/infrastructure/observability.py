"""OpenTelemetry setup.

Always installs the TracerProvider (even without a backend) so manual
`trace.get_tracer(...)` calls never blow up in dev. psycopg and redis
are auto-instrumented at boot. The OTLP exporter is added only when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set.

aiohttp-client instrumentation is reserved for M2 (Coolify adapter).
Metrics are Phase 5.
"""

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings


_configured = False


def configure_tracing(settings: Settings) -> None:
    """Install the global TracerProvider. Idempotent.

    Installs a TracerProvider with a Resource describing this service.
    The psycopg and redis libraries are auto-instrumented unconditionally.
    An OTLP BatchSpanProcessor is added only when `settings.otel_enabled`
    is True (i.e. `OTEL_EXPORTER_OTLP_ENDPOINT` is set).

    Args:
        settings: Application settings providing OTel configuration.
    """
    global _configured  # noqa: PLW0603
    if _configured:
        return
    _configured = True

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.1.0",
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)

    if settings.otel_enabled:
        exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=settings.environment == "dev",
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    # Auto-instrument psycopg + redis. aiohttp-client is reserved for M2.
    PsycopgInstrumentor().instrument()
    RedisInstrumentor().instrument()
