# Phase 1: Repo scaffold & worker skeleton - Pattern Map

**Mapped:** 2026-06-01
**Files analyzed:** 38 (new files to create)
**Analogs found:** 27 with sibling-repo match / 11 no analog (worker-specific or placeholder)

---

## File Classification

| New File | Role | Data Flow | Closest Analog | Match Quality |
|----------|------|-----------|----------------|---------------|
| `pyproject.toml` | config | — | `../platform-api/pyproject.toml` | exact (drop FastAPI/Granian/Stripe; swap deps) |
| `.env.example` | config | — | `../platform-api/.env.example` | exact (drop Keycloak/Stripe; add worker vars) |
| `.dockerignore` | config | — | `../platform-api/.dockerignore` | exact copy |
| `.gitignore` | config | — | `../platform-api/.dockerignore` structure | role-match |
| `.python-version` | config | — | no analog — single-line `3.14` | no analog |
| `alembic.ini` | config | — | `../platform-api/alembic.ini` | exact (single `[provisioning]` section) |
| `Makefile` | config | — | `../platform-api/Makefile` | exact (swap multi-tree → single tree; swap `api` → `worker`) |
| `Dockerfile` | config | — | `../platform-api/Dockerfile` | exact (swap ENTRYPOINT/EXPOSE/HEALTHCHECK; add migrations copy) |
| `docker-compose.yml` | config | — | `../platform-api/docker-compose.yml` | exact (swap `api` → `worker`; drop KEYCLOAK; port 8001) |
| `.github/workflows/ci.yml` | config / CI | — | `../platform-api/.github/workflows/ci.yml` | exact (drop integration gate; worker coverage source) |
| `src/provisioning_worker/__init__.py` | config | — | no analog — empty package marker | no analog |
| `src/provisioning_worker/__main__.py` | boot | — | no analog — worker has no Granian; different boot shape | no analog |
| `src/provisioning_worker/main.py` | boot | event-driven | no analog — TaskGroup composition root; no platform-api precedent | no analog |
| `src/provisioning_worker/settings.py` | config | — | `../platform-api/src/platform_api/settings.py` | exact (drop Keycloak/Stripe/debug; add worker vars) |
| `src/provisioning_worker/infrastructure/logging.py` | infrastructure | — | `../platform-api/src/platform_api/infrastructure/logging.py` | exact (drop granian logger silencing) |
| `src/provisioning_worker/infrastructure/observability.py` | infrastructure | — | `../platform-api/src/platform_api/infrastructure/observability.py` | exact (swap FastAPI+aiohttp-client → RedisInstrumentor) |
| `src/provisioning_worker/infrastructure/db.py` | infrastructure | CRUD | `../platform-api/src/platform_api/infrastructure/database.py` | exact (drop `get_session` FastAPI dep; keep engine + `session_scope`) |
| `src/provisioning_worker/infrastructure/health_server.py` | infrastructure | request-response | no analog — aiohttp AppRunner not used in platform-api | no analog |
| `src/provisioning_worker/infrastructure/outbox_relay.py` | infrastructure | event-driven | `../platform-api/src/platform_api/infrastructure/outbox_relay.py` | role-match (Phase 1: no-op poll; real drain Phase 4) |
| `src/provisioning_worker/infrastructure/__init__.py` | config | — | no analog — empty package marker | no analog |
| `migrations/provisioning/env.py` | migration | CRUD | `../platform-api/migrations/catalog/env.py` | exact (swap schema `catalog` → `provisioning`; `target_metadata = None` in Phase 1) |
| `migrations/provisioning/script.py.mako` | migration | — | `../platform-api/migrations/catalog/script.py.mako` | exact (already has no `from __future__ import annotations`) |
| `migrations/provisioning/versions/` | migration | — | `../platform-api/migrations/catalog/versions/` structure | exact (empty dir in Phase 1) |
| `src/provisioning_worker/ports/__init__.py` | port-adapter placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/adapters/__init__.py` | port-adapter placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/shared/__init__.py` | port-adapter placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/events/__init__.py` | port-adapter placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/__init__.py` | port-adapter placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/models.py` | model placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/schemas.py` | model placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/repository.py` | service placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/service.py` | service placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/handlers.py` | service placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/tasks.py` | service placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `src/provisioning_worker/modules/provisioning/spec.py` | service placeholder | — | no analog — docstring-only placeholder (D-10) | no analog |
| `tests/conftest.py` | test | — | no analog — minimal stubs only in Phase 1 | no analog |
| `tests/provisioning/__init__.py` | test | — | no analog — empty file | no analog |
| `tests/test_health.py` | test | request-response | no analog — worker-specific aiohttp smoke test | no analog |

---

## Pattern Assignments

### `pyproject.toml` (config)

**Analog:** `../platform-api/pyproject.toml`

**Build system + project header** (lines 1–11):
```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "provisioning-worker"
version = "0.1.0"
description = "Provisioning worker for the Odoo Entitlements SaaS Platform"
readme = "README.md"
requires-python = ">=3.14,<3.15"
authors = [{ name = "Yevhenii" }]
```

**Runtime dependencies** — copy platform-api lines 16–55 and apply these deltas:
- Remove: `fastapi`, `granian`, `valkey-glide`, `pyjwt`, `stripe`, `prometheus-client`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-aiohttp-client`
- Keep: `pydantic==2.13.*`, `pydantic-settings==2.7.*`, `sqlalchemy[asyncio]==2.0.*`, `alembic==1.18.*`, `psycopg[binary]==3.3.*`, `taskiq==0.12.*`, `taskiq-redis==1.2.*`, `aiohttp==3.*`, `python-ulid==3.1.*`, `structlog==25.*`, all `opentelemetry-*==1.41.*` / `0.62b0`
- Add: `redis>=5` (unified client; replaces `valkey-glide` for this worker)

**Dev dependencies** (lines 58–71 — copy with delta):
- Remove: `httpx`, `anyio`, `granian[reload]`
- Keep: `pytest==9.*`, `pytest-asyncio==1.3.*`, `pytest-cov==6.*`, `testcontainers[postgres,redis]==4.14.*`, `ruff==0.15.*`

**No `[project.scripts]` entry** — `python -m provisioning_worker` is the entrypoint, no console script alias needed.

**Hatchling target** (lines 80–82):
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/provisioning_worker"]
```

**Ruff config** (lines 87–128) — copy exactly, changing only:
```toml
[tool.ruff.lint.isort]
known-first-party = ["provisioning_worker"]
```

**pytest config** (lines 132–157) — copy exactly, changing only:
```toml
[tool.coverage.run]
source = ["src/provisioning_worker"]
```

---

### `.env.example` (config)

**Analog:** `../platform-api/.env.example`

**Shape to copy** (lines 1–62 structure), applying deltas:

```dotenv
# provisioner — local dev environment variables
# Copy to `.env` (gitignored).

# ----- Environment / app -----
ENVIRONMENT=dev
LOG_LEVEL=INFO

# ----- Database -----
DATABASE_URL=postgresql+psycopg://platform:platform_dev_password@localhost:5432/platform
DATABASE_URL_SYNC=postgresql+psycopg://platform:platform_dev_password@localhost:5432/platform

# ----- Valkey -----
VALKEY_URL=redis://localhost:6379/0

# ----- Consumer -----
PROVISIONING_CONSUMER_GROUP=cg.provisioning-convergence
CONSUMER_NAME=worker-1

# ----- Adapters -----
DEPLOYMENT_ADAPTER=fake
NOTIFICATION_TRANSPORT=console

# ----- Health -----
HEALTH_PORT=8001

# ----- Outbox relay -----
OUTBOX_POLL_SECONDS=1.0
OUTBOX_BATCH_SIZE=100

# ----- Instance provisioning -----
INSTANCE_DOMAIN_SUFFIX=example.local
ODOO_BASE_IMAGE=odoo:17

# ----- OpenTelemetry (optional in dev) -----
# OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
# OTEL_SERVICE_NAME=provisioning-worker

# ----- M2 / secrets (commented out — not used in M1) -----
# COOLIFY_API_URL=
# COOLIFY_API_TOKEN=
# SMTP_HOST=
# SMTP_PORT=587
# SMTP_USERNAME=
# SMTP_PASSWORD=
```

**Key deltas from platform-api:** No `DEBUG`, `SERVICE_NAME`, `DB_POOL_*`, `KEYCLOAK_*`, `STRIPE_*`. Add `PROVISIONING_CONSUMER_GROUP`, `CONSUMER_NAME`, `DEPLOYMENT_ADAPTER`, `NOTIFICATION_TRANSPORT`, `HEALTH_PORT`, `INSTANCE_DOMAIN_SUFFIX`, `ODOO_BASE_IMAGE`.

---

### `.dockerignore` (config)

**Analog:** `../platform-api/.dockerignore` — copy exactly (lines 1–27). No deltas required. The list covers `.git/`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.coverage`, `htmlcov/`, `coverage.xml`, `.idea/`, `.vscode/`, `.DS_Store`, `*.log`, `.env`, `.env.*`, `docs/`, `tests/`, `*.md`, `!README.md`, `Makefile`, `docker-compose*.yml`.

---

### `alembic.ini` (config)

**Analog:** `../platform-api/alembic.ini`

**Single-section pattern** — copy the `[alembic]` default section (lines 18–22) and ONE named section instead of four:

```ini
[alembic]
script_location = migrations/_default_must_use_named_section
prepend_sys_path = .
sqlalchemy.url = postgresql+psycopg://_unset_/_unset_

[provisioning]
script_location = migrations/provisioning
prepend_sys_path = .
version_table = alembic_version
version_table_schema = provisioning
file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(slug)s
```

Copy the `[loggers]`/`[handlers]`/`[formatters]` section (lines 54–87) verbatim — it is boilerplate that ruff does not touch.

---

### `Makefile` (config)

**Analog:** `../platform-api/Makefile`

**Header and variable setup** (lines 1–11) — copy exactly:
```makefile
SHELL := /bin/bash
UV    := uv

ifneq (,$(wildcard ./.env))
include .env
export
endif

.DEFAULT_GOAL := help
```

**`.PHONY` list** — copy and apply deltas: replace `revision-catalog revision-subscription revision-billing revision-shared` with `revision`; replace `migrate-catalog migrate-subscription migrate-billing migrate-shared` with `migrate`; replace `run run-prod` with just `run`; remove `openapi`.

**`help` target** (line 29) — copy awk one-liner exactly.

**Install/sync targets** (lines 35–46) — copy exactly.

**`run` target** (delta from line 52):
```makefile
run:  ## Run the worker locally (python -m provisioning_worker).
	$(UV) run python -m provisioning_worker
```

**Lint/format/check targets** (lines 62–73) — copy exactly.

**Test targets** (lines 79–87) — copy with delta on coverage source:
```makefile
test-cov:  ## Run unit tests with coverage report.
	$(UV) run pytest --cov=provisioning_worker --cov-report=term-missing --cov-report=xml -m "not integration"
```

**Single-tree migrate/revision** (delta from lines 92–124):
```makefile
migrate:  ## Apply the provisioning Alembic tree.
	$(UV) run alembic -n provisioning upgrade head

migrate-down:  ## Downgrade the provisioning tree one step.
	$(UV) run alembic -n provisioning downgrade -1

revision:  ## New revision under migrations/provisioning (use name=...).
	$(UV) run alembic -n provisioning revision --autogenerate -m "$(name)"
```

**`psql` target** (line 131) — copy exactly:
```makefile
psql:  ## Open a psql shell on the platform DB.
	psql "$${DATABASE_URL_SYNC/+psycopg/}"
```

**`infra-*` delegation targets** (lines 143–150) — copy exactly.

**Docker targets** (lines 163–185) — copy and apply deltas: replace `api` with `worker` in every `$(COMPOSE)` invocation; replace the four-tree `docker-migrate` with single tree:
```makefile
COMPOSE := docker compose

docker-build:  ## Build the worker image via docker compose.
	$(COMPOSE) build worker

docker-run:  ## Run the worker service in the foreground.
	$(COMPOSE) up worker

docker-up:  ## Run the worker service detached.
	$(COMPOSE) up -d worker

docker-down:  ## Stop and remove the worker service container.
	$(COMPOSE) down

docker-logs:  ## Tail logs for the worker service.
	$(COMPOSE) logs -f worker

docker-migrate:  ## Apply the provisioning Alembic tree from a one-shot container.
	$(COMPOSE) run --rm --entrypoint alembic worker -n provisioning upgrade head

up: infra-up docker-build docker-migrate docker-run  ## First-time dev loop: infra -> build -> migrate -> run.
```

**`clean` target** (lines 195–198) — copy exactly; remove `openapi.json` from the `rm` line since there is no `openapi` target.

---

### `Dockerfile` (config)

**Analog:** `../platform-api/Dockerfile`

**Stage 1 builder** (lines 1–48) — copy exactly (comments, `ARG PYTHON_VERSION`, `ENV`, `COPY --from=ghcr.io/astral-sh/uv:0.11`, `WORKDIR /app`, cache-mount `uv sync` pattern, `COPY README.md`, `COPY src/`).

**Stage 2 runtime** (lines 53–89) — copy with these deltas:

Lines 53–68 (ENV, user creation, WORKDIR, `.venv` + `src` copy) — copy exactly.

Line 72-73 — copy exactly (alembic.ini + migrations copy; no `-n` suffix needed for single tree):
```dockerfile
COPY --chown=platform:platform alembic.ini /app/alembic.ini
COPY --chown=platform:platform migrations/ /app/migrations/
```

Line 75 — copy exactly: `USER platform`

Line 77 — delta: `EXPOSE 8001`

Lines 79–83 — delta (replace granian ENTRYPOINT/CMD):
```dockerfile
ENTRYPOINT ["python", "-m", "provisioning_worker"]
```
(No `CMD` — the worker takes no positional args.)

Lines 86–89 — delta (healthcheck port 8001):
```dockerfile
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2).status == 200 else 1)"
```

---

### `docker-compose.yml` (config)

**Analog:** `../platform-api/docker-compose.yml`

**Full file with deltas** from the analog (lines 1–55):

```yaml
name: provisioning-worker

services:

  worker:
    build:
      context: .
      dockerfile: Dockerfile
    image: provisioning-worker:dev
    container_name: provisioning-worker
    restart: unless-stopped
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+psycopg://platform:platform_dev_password@platform-postgres:5432/platform
      DATABASE_URL_SYNC: postgresql+psycopg://platform:platform_dev_password@platform-postgres:5432/platform
      VALKEY_URL: redis://platform-valkey:6379/0
      # No KEYCLOAK_BASE_URL — this worker uses no Keycloak realm
    ports:
      - "${HEALTH_HOST_PORT:-8001}:8001"
    healthcheck:
      test:
        - "CMD"
        - "python"
        - "-c"
        - "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2).status == 200 else 1)"
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 10s
    networks:
      - platform-net

networks:
  platform-net:
    name: platform-net
    external: true
```

**Key deltas:** `name`, service key (`worker` not `api`), image name, container name, port `8001`, removed `KEYCLOAK_BASE_URL` environment override.

---

### `.github/workflows/ci.yml` (CI)

**Analog:** `../platform-api/.github/workflows/ci.yml`

**`on`/`concurrency`/`env` block** (lines 1–19) — copy exactly.

**`lint` job** (lines 25–49) — copy exactly.

**`test` job** (lines 54–88) — apply deltas:
- Remove the "Integration tests (testcontainers)" step entirely (D-15: off the PR gate)
- Change `--cov=platform_api` to `--cov=provisioning_worker`:
```yaml
  test:
    name: Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: Install uv
        uses: astral-sh/setup-uv@v6
        with:
          version: ${{ env.UV_VERSION }}
          enable-cache: true
          cache-dependency-glob: "**/uv.lock"
      - name: Set up Python
        run: uv python install ${{ env.PYTHON_VERSION }}
      - name: Install deps
        run: uv sync --frozen --extra dev
      - name: Unit tests
        run: uv run pytest -m "not integration" --cov=provisioning_worker --cov-report=xml
      - name: Upload coverage
        if: always()
        uses: actions/upload-artifact@v5
        with:
          name: coverage-xml
          path: coverage.xml
          if-no-files-found: ignore
```

**`build` job** (lines 93–113) — copy with delta on tag:
```yaml
          tags: provisioning-worker:ci-${{ github.sha }}
```

---

### `src/provisioning_worker/settings.py` (config)

**Analog:** `../platform-api/src/platform_api/settings.py`

**Module docstring + imports** (lines 1–16):
```python
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
```

**`model_config`** (lines 20–25) — copy exactly:
```python
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
```

**`environment` + `log_level`** (lines 27–31) — copy exactly:
```python
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
```

**`database_url` / `database_url_sync`** (lines 33–46) — copy the two fields; drop `db_max_overflow` and `db_pool_timeout` (or keep them — Claude's Discretion; keep for parity).

**`valkey_url`** (lines 49–52) — copy exactly.

**`otel_enabled` computed property** (lines 95–97) — copy exactly:
```python
    @property
    def otel_enabled(self) -> bool:
        return self.otel_exporter_otlp_endpoint is not None
```

**`get_settings` cached accessor** (lines 161–164) — copy exactly but drop the FastAPI dependency comment:
```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()  # type: ignore[call-arg]
```

**Worker-specific fields to add** (no platform-api analog):
```python
    # ----- Valkey consumer -----
    provisioning_consumer_group: str = "cg.provisioning-convergence"
    consumer_name: str = "worker-1"

    # ----- Adapters -----
    deployment_adapter: Literal["fake", "coolify"] = "fake"
    notification_transport: Literal["console", "smtp"] = "console"

    # ----- Health -----
    health_port: int = Field(default=8001, ge=1, le=65535)

    # ----- Outbox relay -----
    outbox_poll_seconds: float = Field(default=1.0, gt=0.0, le=60.0)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)

    # ----- Instance provisioning -----
    instance_domain_suffix: str = "example.local"
    odoo_base_image: str = "odoo:17"

    # ----- OpenTelemetry -----
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "provisioning-worker"
```

**Fields to DROP** (present in platform-api, absent here): `debug`, `service_name`, all `keycloak_*`, all `stripe_*`, the `customers_realm_issuer`/`operators_realm_issuer` computed fields, `jwks_url()`, `otel_resource_attributes`, the two Stripe `@field_validator`/`@model_validator` methods.

---

### `src/provisioning_worker/infrastructure/logging.py` (infrastructure)

**Analog:** `../platform-api/src/platform_api/infrastructure/logging.py`

**Imports** (lines 1–32) — copy exactly.

**`configure_logging` function** (lines 34–80) — copy lines 34–80 exactly, with one delta:

Remove lines 83–87 (the granian logger silencing block):
```python
    # DROP this block — no Granian in the worker:
    # for noisy in ("granian", "granian.access", "_granian"):
    #     log = logging.getLogger(noisy)
    #     log.handlers = []
    #     log.propagate = True
```

**`get_logger` convenience wrapper** (lines 89–91) — copy exactly:
```python
def get_logger(name: str | None = None) -> BoundLogger:
    """Convenience wrapper. Always call after `configure_logging`."""
    return structlog.get_logger(name)
```

**TYPE_CHECKING import** — change `from platform_api.settings import Settings` to `from provisioning_worker.settings import Settings`.

---

### `src/provisioning_worker/infrastructure/observability.py` (infrastructure)

**Analog:** `../platform-api/src/platform_api/infrastructure/observability.py`

**Module docstring** (lines 1–10) — adapt:
```python
"""OpenTelemetry setup.

Always installs the TracerProvider (even without a backend) so manual
`trace.get_tracer(...)` calls never blow up in dev. psycopg and redis
are auto-instrumented at boot. The OTLP exporter is added only when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set.

aiohttp-client instrumentation is reserved for M2 (Coolify adapter).
Metrics are Phase 5.
"""
```

**Imports** (lines 13–28) — copy with deltas:
```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
```

**Remove:** `AioHttpClientInstrumentor`, `FastAPIInstrumentor`, `TYPE_CHECKING`/FastAPI import.

**`_configured` guard + `configure_tracing`** (lines 31–63) — copy the `_configured` idempotency guard and the function body exactly, then replace the instrumentation lines:
```python
    # Auto-instrument psycopg + redis. aiohttp-client is reserved for M2.
    PsycopgInstrumentor().instrument()
    RedisInstrumentor().instrument()
```

**Remove:** `instrument_fastapi(app)` function entirely.

**TYPE_CHECKING import** — change to `from provisioning_worker.settings import Settings`.

---

### `src/provisioning_worker/infrastructure/db.py` (infrastructure, CRUD)

**Analog:** `../platform-api/src/platform_api/infrastructure/database.py`

**Module docstring** (lines 1–14) — adapt (remove FastAPI `Depends` reference; note worker uses `session_scope`):
```python
"""Async SQLAlchemy 2.0 engine and session factory.

Usage in service/handler code:

    from provisioning_worker.infrastructure.db import session_scope

    async with session_scope() as session:
        ...

The engine is created lazily on first call to `get_engine()` and reused
for the lifetime of the process. Call `dispose_engine()` on shutdown.
"""
```

**Imports** (lines 16–30) — copy exactly except:
- Change `from platform_api.settings import Settings, get_settings` → `from provisioning_worker.settings import Settings, get_settings`
- Remove `from platform_api.shared.json_encoder import dumps as _json_dumps`

**`_build_engine`** (lines 36–54) — copy, removing the `json_serializer=_json_dumps` line (no custom JSON encoder in Phase 1).

**`get_engine`, `get_session_factory`, `session_scope`, `dispose_engine`** (lines 57–110) — copy exactly (the worker does not need `get_session` FastAPI dependency; omit that function).

---

### `src/provisioning_worker/infrastructure/outbox_relay.py` (infrastructure, event-driven)

**Analog:** `../platform-api/src/platform_api/infrastructure/outbox_relay.py`

**Phase 1 shape:** The relay runs but the `event_outbox` table does not exist until Phase 4. Use a no-op poll loop (RESEARCH Pattern 12). The real drain logic from the analog (`_drain_once`, `EventOutbox` model query) is NOT copied — it lands in Phase 4.

**Module docstring:**
```python
"""Outbox relay — Phase 1 no-op poll loop.

In Phase 1 the `event_outbox` table does not yet exist. This concern
runs the poll loop and logs startup, but performs no DB query per
iteration. Phase 4 replaces the loop body with the real drain.

The poll-sleep pattern (asyncio.wait_for + contextlib.suppress) is
copied from platform-api/infrastructure/outbox_relay.py.
"""
```

**Poll loop** (RESEARCH Pattern 12 + analog lines 74–84 for the sleep idiom):
```python
import asyncio
import contextlib
import structlog
from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)

async def run_outbox_relay(settings: Settings, shutdown: asyncio.Event) -> None:
    """Drive the no-op outbox relay loop until shutdown is set.

    Phase 4 will replace the loop body with a real DB drain.

    Args:
        settings: Application settings — supplies outbox_poll_seconds.
        shutdown: Event set by the composition root on SIGTERM.
    """
    log.info("outbox relay started", poll_seconds=settings.outbox_poll_seconds)
    while not shutdown.is_set():
        # Phase 4 will replace this with: await _drain_once(settings, session_factory, bus)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=settings.outbox_poll_seconds)
    log.info("outbox relay stopped")
```

The `contextlib.suppress(TimeoutError)` + `asyncio.wait_for` sleep idiom is copied directly from analog lines 82–83.

---

### `src/provisioning_worker/infrastructure/health_server.py` (infrastructure, request-response)

**Analog:** No platform-api analog. aiohttp `AppRunner`/`TCPSite` is not used in platform-api (it uses granian/ASGI).

**Use RESEARCH Pattern 2** — the aiohttp AppRunner pattern:

```python
"""aiohttp health server — serves GET /healthz on HEALTH_PORT.

Runs as one of the four TaskGroup concerns. Uses AppRunner + TCPSite
so it does not block the event loop (unlike web.run_app()).
"""

import asyncio
import structlog
from aiohttp import web
from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)

async def run_health_server(settings: Settings, shutdown: asyncio.Event) -> None:
    """Start the /healthz aiohttp server and block until shutdown.

    Args:
        settings: Application settings — supplies health_port.
        shutdown: Event set by the composition root on SIGTERM.
    """
    app = web.Application()
    app.router.add_get("/healthz", _healthz)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.health_port)
    await site.start()

    log.info("health server listening", port=settings.health_port)
    await shutdown.wait()
    await runner.cleanup()

async def _healthz(request: web.Request) -> web.Response:
    return web.Response(content_type="application/json", text='{"status":"ok"}')
```

---

### `src/provisioning_worker/__main__.py` (boot)

**Analog:** `../platform-api/src/platform_api/__main__.py` — structural shape only. The content is completely different (no Granian).

**Pattern from analog** (module docstring + `main()` + `if __name__` guard):
```python
"""Entry point for `python -m provisioning_worker`."""

import asyncio
import sys

from provisioning_worker.main import run
from provisioning_worker.settings import get_settings


def main() -> None:
    """Boot the worker. Exits non-zero on any unhandled exception."""
    try:
        asyncio.run(run(get_settings()))
    except* Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

The `if __name__ == "__main__"` guard is copied from analog line 21. The `asyncio.run()` wrapper and `sys.exit(1)` are RESEARCH Pattern 1.

---

### `src/provisioning_worker/main.py` (boot, event-driven)

**Analog:** No platform-api analog. platform-api uses a FastAPI `@asynccontextmanager` lifespan; the worker uses `asyncio.TaskGroup`. This is the key worker-specific file.

**Use RESEARCH Pattern 1** (TaskGroup + SIGTERM) + Pattern 3 (consumer) + Pattern 4 (taskiq broker-connect) + Pattern 13 (fail-fast infra checks):

```python
"""Composition root — wires the four concurrent concerns.

Boots them under a single asyncio.TaskGroup (crash-only: D-01).
SIGTERM sets the shared shutdown event; each concern exits its loop
and the TaskGroup completes cleanly (D-02).
"""

import asyncio
import signal
import structlog

from provisioning_worker.infrastructure.db import get_engine, dispose_engine
from provisioning_worker.infrastructure.logging import configure_logging
from provisioning_worker.infrastructure.observability import configure_tracing
from provisioning_worker.infrastructure.health_server import run_health_server
from provisioning_worker.infrastructure.outbox_relay import run_outbox_relay
from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)

async def run(settings: Settings) -> None:
    """Wire adapters, check infra, then run the four concerns.

    Args:
        settings: Validated application settings.

    Raises:
        SystemExit: Non-zero if any concern crashes (D-01).
    """
    configure_logging(settings)
    configure_tracing(settings)

    log.info(
        "provisioning-worker starting",
        environment=settings.environment,
        deployment_adapter=settings.deployment_adapter,
    )

    # Fail-fast: verify Postgres + Valkey are reachable before starting concerns (D-05)
    await _check_postgres(settings)
    await _check_valkey(settings)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    engine = get_engine(settings)
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_run_consumer(settings, shutdown), name="consumer")
            tg.create_task(_run_convergence(settings, shutdown), name="convergence")
            tg.create_task(run_outbox_relay(settings, shutdown), name="outbox_relay")
            tg.create_task(run_health_server(settings, shutdown), name="health_server")
    except* Exception as eg:
        raise SystemExit(1) from eg.exceptions[0]
    finally:
        await dispose_engine()
```

Private concern functions (`_run_consumer`, `_run_convergence`) implement RESEARCH Patterns 3 and 4 respectively. Fail-fast checks (`_check_postgres`, `_check_valkey`) implement Pattern 13.

---

### `migrations/provisioning/env.py` (migration, CRUD)

**Analog:** `../platform-api/migrations/catalog/env.py`

**Full file** — copy lines 1–75 with these deltas:

Module docstring (lines 1–6):
```python
"""Alembic env.py for the `provisioning` schema.

Use:
    alembic -n provisioning upgrade head
    alembic -n provisioning revision --autogenerate -m "add instance"
"""
```

Line 14 — change import: `from provisioning_worker.settings import get_settings`

Line 16 — change schema: `SCHEMA = "provisioning"`

Lines 28–29 — Phase 1: no domain tables yet, use `None`:
```python
# Phase 1: no SQLAlchemy models yet. Phase 3 will import metadata from models.py.
target_metadata = None
```

Lines 31–35 — keep `_include_object` exactly as-is (schema filter still correct).

Lines 38–75 (`run_migrations_offline`, `run_migrations_online`, dispatch) — copy exactly.

**Critical:** No `from __future__ import annotations` anywhere in this file (forbidden by CLAUDE.md §6.1; platform-api analog already omits it).

---

### `migrations/provisioning/script.py.mako` (migration)

**Analog:** `../platform-api/migrations/catalog/script.py.mako`

**Copy exactly** (lines 1–25). The platform-api version already uses modern Python 3.14 typing (`str | None`, `collections.abc.Sequence`) and does NOT include `from __future__ import annotations`. This is the correct template to use verbatim.

---

### Placeholder modules (D-10)

All of the following files follow the same pattern: a single Google-style module docstring, nothing else. No imports, no class definitions, no function stubs.

The docstring should describe what the module will contain when its owning phase lands:

| File | Docstring content summary |
|------|--------------------------|
| `src/provisioning_worker/ports/__init__.py` | "Protocol interfaces for the deployment adapter and notification transport ports." |
| `src/provisioning_worker/adapters/__init__.py` | "Adapter implementations of the ports (FakeDeploymentAdapter, CoolifyAdapter, ConsoleNotificationTransport)." |
| `src/provisioning_worker/shared/__init__.py` | "Cross-cutting utilities: envelope, event consumer, error types, ULID helpers." |
| `src/provisioning_worker/events/__init__.py` | "Event payload models for consumed subscription.* events and produced instance.* events." |
| `src/provisioning_worker/modules/provisioning/__init__.py` | "Provisioning domain module: convergence service, state machine, handlers, tasks." |
| `src/provisioning_worker/modules/provisioning/models.py` | "SQLAlchemy mapped classes for the provisioning schema (instance, provisioning_task, etc.). Phase 3." |
| `src/provisioning_worker/modules/provisioning/schemas.py` | "Pydantic command and result models for the provisioning domain. Phase 3." |
| `src/provisioning_worker/modules/provisioning/repository.py` | "Async SQLAlchemy data access for the provisioning schema. ORM + SQL only, no Pydantic. Phase 3." |
| `src/provisioning_worker/modules/provisioning/service.py` | "Convergence service and 8-state instance state machine. Phase 3." |
| `src/provisioning_worker/modules/provisioning/handlers.py` | "One handler per consumed subscription.* event type. Phase 2." |
| `src/provisioning_worker/modules/provisioning/tasks.py` | "Taskiq retryable tasks — adapter calls with backoff. Phase 3." |
| `src/provisioning_worker/modules/provisioning/spec.py` | "InstanceSpec builder: converts subscription entitlements to desired deployment state. Phase 3." |

All `__init__.py` files in `src/provisioning_worker/`, `src/provisioning_worker/infrastructure/`, and `src/provisioning_worker/modules/` also get a brief module docstring identifying the package.

---

### `tests/conftest.py` (test)

**Analog:** No close analog — Phase 1 needs only minimal stubs (no real fixtures yet). The platform-api `tests/conftest.py` is fixture-heavy (testcontainers, database sessions, HTTP clients).

**Shape:** Minimal file per RESEARCH Validation Architecture §Wave 0:
```python
"""Test configuration and shared fixtures.

Phase 1: no domain fixtures yet. Fixtures for DB sessions, fake adapters,
and the Valkey consumer are added in Phase 2+.
"""
```

---

### `tests/test_health.py` (test, request-response)

**Analog:** No platform-api analog. Worker-specific aiohttp smoke test.

**Shape** (per RESEARCH §Phase Requirements → Test Map and RESEARCH Pattern 2):
```python
"""Smoke tests for GET /healthz.

Uses an in-process AppRunner + TCPSite to start the health server on a
random port and verifies the 200 response — no real infra required.
"""
import asyncio
import aiohttp
import pytest
from provisioning_worker.infrastructure.health_server import run_health_server
from provisioning_worker.settings import Settings
```

The test starts `run_health_server` with a `shutdown` event, issues an aiohttp GET against `http://127.0.0.1:{port}/healthz`, asserts `status == 200` and `body == {"status": "ok"}`, then sets shutdown and awaits cleanup.

---

## Shared Patterns

### structlog module-level logger (applies to ALL non-placeholder modules)

**Source:** `../platform-api/src/platform_api/infrastructure/logging.py` line 38 + CLAUDE.md §6.6

```python
import structlog

log = structlog.get_logger(__name__)
```

One per module at module scope. Never `print`. Never per-function logger creation.

### `TYPE_CHECKING` import guard (applies to all infrastructure + service files)

**Source:** `../platform-api/src/platform_api/infrastructure/observability.py` lines 25–28

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings
```

Use for type hints that are only needed at type-check time (avoids circular imports). Follow this pattern in any file that references `Settings` only in function signatures.

### Google-style docstrings on all public functions/classes (applies everywhere)

**Source:** `../platform-api/src/platform_api/infrastructure/database.py` lines 57–63

```python
def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the shared engine, building it on first call."""
    ...
```

Every public module, class, function, and method must have a Google-style docstring. Private `_helpers` only when logic is non-obvious.

### No `from __future__ import annotations` (applies everywhere including migrations)

**Source:** CLAUDE.md §6.1; platform-api enforces this consistently (zero occurrences found across all read files).

Python 3.14 implements PEP 649 natively. This import is forbidden project-wide.

### `asyncio.wait_for + contextlib.suppress(TimeoutError)` sleep idiom (applies to all poll loops)

**Source:** `../platform-api/src/platform_api/infrastructure/outbox_relay.py` lines 82–83

```python
with contextlib.suppress(TimeoutError):
    await asyncio.wait_for(shutdown.wait(), timeout=settings.outbox_poll_seconds)
```

Use this pattern in any polling loop that must wake promptly on shutdown.

---

## No Analog Found

Files with no close match in the codebase (planner uses RESEARCH.md patterns instead):

| File | Role | Data Flow | Reason / RESEARCH Reference |
|------|------|-----------|----------------------------|
| `src/provisioning_worker/__main__.py` | boot | — | platform-api uses Granian; worker uses `asyncio.run()`. Use RESEARCH Pattern 1 shape. |
| `src/provisioning_worker/main.py` | boot | event-driven | platform-api is ASGI lifespan; worker needs `asyncio.TaskGroup`. Use RESEARCH Patterns 1, 3, 4, 13. |
| `src/provisioning_worker/infrastructure/health_server.py` | infrastructure | request-response | platform-api health endpoint is a FastAPI route, not a standalone aiohttp server. Use RESEARCH Pattern 2. |
| `.python-version` | config | — | Single-line file: `3.14`. |
| `src/provisioning_worker/__init__.py` | config | — | Empty package marker (PEP 420). |
| All `__init__.py` package markers | config | — | Docstring-only placeholders per D-10. |
| `tests/conftest.py` | test | — | Phase 1 needs no fixtures; full fixture set arrives Phase 2+. |
| `tests/test_health.py` | test | request-response | Worker-specific aiohttp in-process test. Use RESEARCH Pattern 2 as the component under test. |

---

## Metadata

**Analog search scope:** `../platform-api/` (Dockerfile, docker-compose.yml, .dockerignore, .env.example, Makefile, pyproject.toml, alembic.ini, .github/workflows/ci.yml, src/platform_api/settings.py, src/platform_api/infrastructure/{logging,observability,database,outbox_relay}.py, migrations/catalog/{env.py,script.py.mako}, src/platform_api/__main__.py)
**Sibling files read:** 17
**Pattern extraction date:** 2026-06-01
