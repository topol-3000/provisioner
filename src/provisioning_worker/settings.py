"""Worker settings.

Loaded once at startup from environment variables (and `.env` in dev) via
`pydantic-settings`. Every consumer takes `Settings` as an explicit
dependency rather than reading env vars at module scope — keeps tests
hermetic and adapters swappable.

See `.env.example` for the full set of recognised variables.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Provisioning-worker application settings.

    All fields are sourced from environment variables (or `.env` in dev).
    Required fields without defaults must be present; missing values cause
    a validation error at startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- App / environment -----
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ----- Database -----
    # Async DSN for the application; sync DSN for Alembic. Both target the
    # same Postgres cluster — see docs/architecture.md §Migrations.
    database_url: PostgresDsn = Field(
        description="Async SQLAlchemy DSN (psycopg driver).",
    )
    database_url_sync: PostgresDsn = Field(
        description="Sync SQLAlchemy DSN (psycopg) used by Alembic.",
    )

    # ----- Valkey (event bus + task broker) -----
    valkey_url: RedisDsn = Field(
        description="Valkey URL — Redis protocol. Used by the streams consumer and Taskiq.",
    )

    # ----- Valkey consumer -----
    provisioning_consumer_group: str = "cg.provisioning-convergence"
    consumer_name: str = "worker-1"
    consumer_reclaim_min_idle_ms: int = Field(
        default=60_000,
        ge=1_000,
        description=(
            "XAUTOCLAIM min-idle-time in milliseconds. Entries idle longer "
            "than this are reclaimed. Default ~60s."
        ),
    )

    # ----- Adapters -----
    deployment_adapter: Literal["fake", "coolify"] = "fake"
    notification_transport: Literal["console", "smtp"] = "console"

    # ----- Health -----
    health_port: int = Field(default=8001, ge=1, le=65535)

    # ----- Outbox relay -----
    outbox_poll_seconds: float = Field(
        default=1.0,
        gt=0.0,
        le=60.0,
        description="Outbox relay poll interval in seconds.",
    )
    outbox_batch_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Outbox relay batch size per poll.",
    )

    # ----- Instance provisioning — spec defaults (D-03) -----
    instance_domain_suffix: str = "example.local"
    odoo_base_image: str = "odoo:17"
    provisioning_default_seat_cap: int = Field(
        default=10,
        ge=1,
        description="Default seat cap for M1 placeholder specs (D-03).",
    )
    provisioning_default_resource_caps: str = Field(
        default="{}",
        description="JSON string of default resource caps; parsed at use site.",
    )

    # ----- Instance provisioning — retry backoff (D-08) -----
    provisioning_max_attempts: int = Field(
        default=5,
        ge=1,
        description="Maximum convergence task attempts before marking terminal failure.",
    )
    provisioning_base_delay_s: float = Field(
        default=2.0,
        gt=0.0,
        description="Base backoff delay in seconds (exponential backoff formula).",
    )
    provisioning_multiplier: float = Field(
        default=2.0,
        gt=1.0,
        description="Backoff multiplier applied per attempt.",
    )
    provisioning_cap_s: float = Field(
        default=60.0,
        gt=0.0,
        description="Maximum backoff delay in seconds (cap).",
    )

    # ----- OpenTelemetry (optional) -----
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "provisioning-worker"

    @property
    def otel_enabled(self) -> bool:
        """Return True when an OTLP endpoint is configured."""
        return self.otel_exporter_otlp_endpoint is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()  # type: ignore[call-arg]
