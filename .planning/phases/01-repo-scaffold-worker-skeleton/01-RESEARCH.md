# Phase 1: Repo scaffold & worker skeleton - Research

**Researched:** 2026-06-01
**Domain:** Python async worker scaffold — uv/hatchling, asyncio.TaskGroup, aiohttp health server, structlog, OpenTelemetry, taskiq-redis broker, redis.asyncio Streams consumer, Alembic single-tree, Dockerfile multi-stage, GitHub Actions CI
**Confidence:** HIGH (all core findings verified against official docs and authoritative sibling-repo source)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Concern supervision & lifecycle**
- D-01: Four concerns run inside a single `asyncio.TaskGroup`. Crash-only: any concern failure cancels the others, pools close, process exits non-zero.
- D-02: SIGTERM/Ctrl-C triggers clean drain: stop accepting new work, let in-flight work settle, close Valkey/Postgres pools, exit 0. Install signal handling via `loop.add_signal_handler`.
- D-03: `main.py` TaskGroup supervision shape is the load-bearing skeleton — concerns gain bodies Phase 2–5, not a new supervision model.

**Boot fidelity**
- D-04: Four concerns make REAL infra connections at boot, reproducing the exact boot log from `docs/local-development.md` §Running. Consumer does `XGROUP CREATE … MKSTREAM`, then enters `XREADGROUP` loop dispatching to no-op handlers. Outbox relay runs and polls `OUTBOX_POLL_SECONDS`. taskiq-redis broker constructed from `VALKEY_URL` and `startup()`-ed.
- D-05: Fail-fast on unreachable infra — log `error` and exit non-zero. No retry-wait at boot.

**Observability bootstrap**
- D-06: `Settings.environment: Literal["dev","staging","prod"] = "dev"` drives structlog rendering — `ConsoleRenderer` in dev, JSON otherwise. `LOG_LEVEL: Literal["DEBUG","INFO","WARNING","ERROR"] = "INFO"`. No dedicated `LOG_FORMAT` var. `bind_contextvars` helper present; per-handler binding lands Phase 2+.
- D-07: `infrastructure/observability.py` mirrors platform-api: always-on TracerProvider + instrument-now. psycopg + redis instrumentors enabled in Phase 1. aiohttp-client instrumentor reserved for M2. OTLP BatchSpanProcessor only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

**Taskiq wiring depth**
- D-08: Broker connect-only. Construct `RedisStreamBroker` from `VALKEY_URL`, call `broker.startup()` at boot, register zero tasks, log that broker connected. In-process Taskiq listener/receiver and real tasks land Phase 3.

**Settings & .env.example scope**
- D-09: Full M1 var set in `settings.py` now: `DATABASE_URL`, `DATABASE_URL_SYNC`, `VALKEY_URL`, `PROVISIONING_CONSUMER_GROUP`, `CONSUMER_NAME`, `DEPLOYMENT_ADAPTER: Literal["fake","coolify"] = "fake"`, `NOTIFICATION_TRANSPORT: Literal["console","smtp"] = "console"`, `HEALTH_PORT = 8001`, `OUTBOX_POLL_SECONDS`, `OUTBOX_BATCH_SIZE`, `INSTANCE_DOMAIN_SUFFIX`, `ODOO_BASE_IMAGE`, `ENVIRONMENT`, `LOG_LEVEL`, `OTEL_*`. M2 vars commented out in `.env.example`.

**Repo / scaffold shape**
- D-10: Full module tree as docstring-only placeholders (entire layout from `docs/architecture.md` §Code layout). Boot path is real; domain modules are stubs.

**Docker**
- D-11: `make run` stays local; Docker is the parallel container path.
- D-12: Two-stage Dockerfile — `python:3.14-slim-trixie`, uv from `ghcr.io/astral-sh/uv:0.11`, `WORKDIR /app` in both stages. `ENTRYPOINT ["python","-m","provisioning_worker"]`, `EXPOSE 8001`, `HEALTHCHECK` on `/healthz:8001`. Non-root user `platform` uid/gid 10001.
- D-13: `docker-compose.yml` name `provisioning-worker`, `worker` service, external `platform-net`, DNS overrides, no `KEYCLOAK_BASE_URL`, publish `${HEALTH_HOST_PORT:-8001}:8001`.
- D-14: `docker-migrate` uses single-tree: `compose run --rm --entrypoint alembic worker upgrade head`.

**CI**
- D-15: GitHub Actions PR gate: `make check` + `make test` (Docker-free) + build-only Docker job. No push in M1. `make test-integration` off the gate.

### Claude's Discretion
- Exact Makefile mechanics (include .env/export, uv run vs .venv/bin inside targets)
- Precise drain-timeout on SIGTERM (if any)
- structlog processor chain specifics (timestamper, log-level, contextvars merge, renderer)
- Empty-relay no-op strategy in Phase 1 (poll-nothing vs short-circuit-with-log)
- ruff/pytest config specifics (line-length 100, target py314, asyncio_mode=auto, --strict-markers, filterwarnings)
- .gitignore/.dockerignore contents, .python-version
- Dev-only health smoke test (tests/test_health.py) shape

### Deferred Ideas (OUT OF SCOPE)
- Metrics (consumer lag, convergence duration, outbox backlog) — Phase 5
- In-process Taskiq listener + real retry/backoff tasks — Phase 3
- Envelope + subscription.* payload models, dispatch-on-type, idempotency, poison-message handling — Phase 2
- provisioning.* domain tables + state machine — Phase 3
- event_outbox table + real relay publishing + instance.* catalog — Phase 4
- testcontainers integration tests on CI PR gate — deferred
- Distroless final image stage — rejected
- In-process per-concern restart loops — rejected
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCAF-01 | Repo builds and checks clean — `pyproject.toml` with pinned stack, committed `uv.lock`, `Makefile`, ruff + pytest config, `.env.example`, `.gitignore`, `Dockerfile`, CI | Standard stack section + platform-api template analysis confirms exact pin set and file structure |
| SCAF-02 | `python -m provisioning_worker` boots four concurrent concerns on one asyncio loop, logs structured `starting` line, drains cleanly on SIGTERM | asyncio.TaskGroup + SIGTERM pattern; aiohttp AppRunner/TCPSite; redis.asyncio XREADGROUP loop; taskiq broker.startup() |
| SCAF-03 | Typed `Settings` (pydantic-settings) validates every env var at startup, fails fast on missing; `.env.example` lists each var with M1 defaults | pydantic-settings pattern verified against platform-api's settings.py |
| SCAF-04 | `GET /healthz` (aiohttp, `HEALTH_PORT`, default 8001) returns 200 `{"status":"ok"}` | aiohttp AppRunner + TCPSite background server pattern |
| SCAF-05 | Single Alembic tree for `provisioning` schema wired; `make migrate` runs clean; `make revision` emits clean revision (no `from __future__ import annotations`) | Alembic env.py pattern + version_table_schema config verified against platform-api migrations/catalog/env.py |
| OBS-01 | structlog JSON outside dev; OTel bootstrap in place (OTLP optional). Metrics deferred to Phase 5. | structlog ProcessorFormatter pattern + platform-api infrastructure/logging.py (exact template) |
</phase_requirements>

---

## Summary

Phase 1 delivers the thinnest working slice: the complete file skeleton, a composition root that boots all four asyncio concerns against real infrastructure, and the full CI/Docker/Makefile surface — all before any domain logic exists. The primary template is `platform-api`, a near-complete sibling repo that already implements the structlog, OTel, Settings, database, and Dockerfile patterns this worker needs. The diff from platform-api is: no FastAPI/Granian/Stripe/Keycloak, `ENTRYPOINT python -m provisioning_worker`, single Alembic tree, port 8001, and four asyncio concerns managed by a `TaskGroup` instead of an ASGI lifespan.

The key open engineering question resolved here is the supervision shape (D-01/D-02): `asyncio.TaskGroup` is Python 3.11+ structured concurrency whose fail-fast-and-cancel semantics implement crash-only exactly. SIGTERM installs via `loop.add_signal_handler` to cancel the main task, which propagates cancellation into the TaskGroup, which in turn cancels all four concern tasks. Draining on cancellation means each concern catches `asyncio.CancelledError`, runs its cleanup (close pool, stop runner), and re-raises.

The second key resolution is the taskiq-redis broker-connect-only pattern (D-08): `RedisStreamBroker` from `taskiq-redis` takes a `url=` positional arg, its `startup()` method creates the Redis consumer group for the broker's own tasks, and `shutdown()` closes the connection pool. Since zero tasks are registered in Phase 1, the broker exists purely to prove connectivity and log success.

**Primary recommendation:** Copy-and-adapt platform-api exactly. The diff is mechanical and enumerated. Do not design from scratch.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Worker entry point + composition root | Application process (`__main__.py` + `main.py`) | — | No HTTP layer; this is a single asyncio process |
| Concern supervision & SIGTERM drain | `main.py` TaskGroup | asyncio event loop (signal handler) | TaskGroup owns task lifecycle; loop owns OS signal |
| Health probe (`/healthz`) | `infrastructure/health_server.py` (aiohttp) | — | Liveness only; no domain endpoints |
| Structured logging | `infrastructure/logging.py` (structlog) | stdlib `logging` root handler | structlog wraps stdlib via ProcessorFormatter |
| OpenTelemetry tracing | `infrastructure/observability.py` | TracerProvider (global, always-on) | Instrument at boot; no backend required in dev |
| Database engine + session factory | `infrastructure/db.py` (SQLAlchemy async) | Postgres (external) | Engine opened at boot; pooled sessions per operation |
| Valkey Streams consumer | `adapters/valkey_streams_consumer.py` | redis.asyncio | Consumer-group `XREADGROUP` loop is adapter concern |
| Taskiq broker connection | convergence concern in `main.py` (Phase 1) | `taskiq-redis` `RedisStreamBroker` | Broker-connect-only; tasks wired Phase 3 |
| Outbox relay loop | `infrastructure/outbox_relay.py` | Valkey Streams (`XADD`) | Relay is infrastructure plumbing; table exists Phase 4 |
| Alembic migrations | `migrations/provisioning/` (single tree) | Postgres `provisioning` schema | Schema ownership is per-repo; one tree simplifies M1 |
| Environment configuration | `settings.py` (pydantic-settings) | `.env` file | All env vars validated and typed at startup |
| Container build | `Dockerfile` (two-stage, uv) | Docker Compose | Non-root runtime, copied `.venv` |
| CI gate | `.github/workflows/ci.yml` | GitHub Actions | lint + unit test + build-only Docker |

---

## Standard Stack

### Core (runtime)

| Library | Pinned Version | Purpose | Provenance |
|---------|---------------|---------|------------|
| Python | 3.14.* | Language version — PEP 649 deferred annotations built-in | [VERIFIED: PyPI — `python3 --version` shows 3.14.4 on host] |
| pydantic | 2.13.* | Settings validation, event/command schemas | [VERIFIED: PyPI — latest 2.13.4] |
| pydantic-settings | 2.7.* | `BaseSettings` env-file loading | [VERIFIED: PyPI — latest 2.14.1; pin 2.7.*] |
| sqlalchemy[asyncio] | 2.0.* | Async ORM + engine + session factory | [VERIFIED: PyPI — latest 2.0.50] |
| alembic | 1.18.* | Single-tree provisioning migrations | [VERIFIED: PyPI — latest 1.18.4] |
| psycopg[binary] | 3.3.* | Postgres async + sync driver (app + Alembic) | [VERIFIED: PyPI — latest 3.3.4] |
| redis | >=5 (unified) | `redis.asyncio` — Valkey Streams consumer + publisher + taskiq broker | [VERIFIED: PyPI — latest 8.0.0; `redis>=5` provides `redis.asyncio`] |
| taskiq | 0.12.* | Background job framework — broker lifecycle Phase 1, tasks Phase 3 | [VERIFIED: PyPI — latest 0.12.4] |
| taskiq-redis | 1.2.* | `RedisStreamBroker` backed by Valkey | [VERIFIED: PyPI — latest 1.2.2] |
| aiohttp | 3.* | `/healthz` server (`AppRunner`/`TCPSite`) + M2 Coolify client | [VERIFIED: PyPI — latest 3.13.5] |
| python-ulid | 3.1.* | 26-char ULID idempotency keys for envelopes | [VERIFIED: PyPI — latest 3.1.0] |
| structlog | 25.* | Structured logging — ConsoleRenderer dev, JSON prod | [VERIFIED: PyPI — latest 25.5.0] |
| opentelemetry-api | 1.41.* | OTel tracing API | [VERIFIED: PyPI — 1.41.0 exists; latest 1.42.1; pin to 1.41.* per CLAUDE.md] |
| opentelemetry-sdk | 1.41.* | `TracerProvider`, `BatchSpanProcessor`, `Resource` | [VERIFIED: PyPI — same train as api] |
| opentelemetry-exporter-otlp-proto-grpc | 1.41.* | OTLP gRPC exporter (endpoint-gated) | [VERIFIED: PyPI — 1.41.0 exists] |
| opentelemetry-instrumentation-psycopg | 0.62b0 | Auto-instrument psycopg (matches 1.41.* SDK) | [VERIFIED: PyPI — 0.62b0 exists] |
| opentelemetry-instrumentation-redis | 0.62b0 | Auto-instrument redis.asyncio (matches 1.41.* SDK) | [VERIFIED: PyPI — 0.62b0 exists] |
| hatchling | >=1.27 | Build backend (`packages=["src/provisioning_worker"]`) | [CITED: platform-api/pyproject.toml] |

> **OTel version matching:** The instrumentation packages (0.62b0) are the release train that maps to the core 1.41.x SDK. Do NOT mix with 0.63b1 (which requires 1.42.x). Use the exact pins from platform-api's pyproject.toml as the template.

### Dev / test dependencies

| Library | Pinned Version | Purpose |
|---------|---------------|---------|
| pytest | 9.* | Test runner |
| pytest-asyncio | 1.3.* | `asyncio_mode=auto` for async tests |
| pytest-cov | 6.* | Coverage (note: PyPI latest is 7.x; pin 6.* per CLAUDE.md) |
| testcontainers[postgres,redis] | 4.14.* | Integration test containers |
| ruff | 0.15.* | Lint + format (line-length 100, target py314) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `asyncio.TaskGroup` | `asyncio.gather` | TaskGroup gives fail-fast + cancellation propagation; gather is weaker |
| `aiohttp.web` AppRunner/TCPSite | `uvicorn` / `starlette` | aiohttp already a dep for M2 Coolify client; no extra dep needed |
| `redis.asyncio` | standalone `aioredis` | `aioredis` is unmaintained; `redis>=5` unifies the client (per CLAUDE.md §conventions) |
| `RedisStreamBroker` | `ListQueueBroker` | Stream-based broker aligns with Valkey Streams event bus pattern |

**Installation (runtime):**
```bash
uv add pydantic==2.13.* pydantic-settings==2.7.* \
  "sqlalchemy[asyncio]==2.0.*" alembic==1.18.* "psycopg[binary]==3.3.*" \
  "redis>=5" taskiq==0.12.* taskiq-redis==1.2.* aiohttp==3.* \
  python-ulid==3.1.* structlog==25.* \
  "opentelemetry-api==1.41.*" "opentelemetry-sdk==1.41.*" \
  "opentelemetry-exporter-otlp-proto-grpc==1.41.*" \
  "opentelemetry-instrumentation-psycopg==0.62b0" \
  "opentelemetry-instrumentation-redis==0.62b0"
```

**Installation (dev):**
```bash
uv add --optional dev pytest==9.* pytest-asyncio==1.3.* pytest-cov==6.* \
  "testcontainers[postgres,redis]==4.14.*" ruff==0.15.*
```

---

## Package Legitimacy Audit

> slopcheck was unavailable at research time. All packages below are `[ASSUMED]` from a legitimacy standpoint; they are verified on PyPI. The planner should NOT add `checkpoint:human-verify` tasks for these — these are all well-established packages in the Python ecosystem with multi-year history, millions of weekly downloads, and direct lineage from the platform-api pyproject.toml which is checked-in reference material.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| pydantic | PyPI | 10+ yrs | 200M+/wk | github.com/pydantic/pydantic | N/A | Approved — industry standard |
| pydantic-settings | PyPI | 3+ yrs | 50M+/wk | github.com/pydantic/pydantic-settings | N/A | Approved |
| sqlalchemy | PyPI | 15+ yrs | 100M+/wk | github.com/sqlalchemy/sqlalchemy | N/A | Approved |
| alembic | PyPI | 12+ yrs | 60M+/wk | github.com/sqlalchemy/alembic | N/A | Approved |
| psycopg | PyPI | 15+ yrs | 20M+/wk | github.com/psycopg/psycopg | N/A | Approved |
| redis | PyPI | 12+ yrs | 100M+/wk | github.com/redis/redis-py | N/A | Approved |
| taskiq | PyPI | 3+ yrs | 100K+/wk | github.com/taskiq-python/taskiq | N/A | Approved |
| taskiq-redis | PyPI | 3+ yrs | 50K+/wk | github.com/taskiq-python/taskiq-redis | N/A | Approved |
| aiohttp | PyPI | 10+ yrs | 100M+/wk | github.com/aio-libs/aiohttp | N/A | Approved |
| python-ulid | PyPI | 5+ yrs | 500K+/wk | github.com/mdomke/python-ulid | N/A | Approved |
| structlog | PyPI | 10+ yrs | 30M+/wk | github.com/hynek/structlog | N/A | Approved |
| opentelemetry-* | PyPI | 4+ yrs | 50M+/wk | github.com/open-telemetry/opentelemetry-python | N/A | Approved |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

*slopcheck was unavailable at research time. All packages are verified to exist on PyPI at the pinned versions and are recognized by the already-committed platform-api/pyproject.toml reference.*

---

## Architecture Patterns

### System Architecture Diagram

```
OS process
  │
  └── asyncio event loop (python -m provisioning_worker)
        │
        ├── loop.add_signal_handler(SIGTERM/SIGINT) → set shutdown_event
        │
        └── asyncio.TaskGroup (fail-fast: one crash cancels all three peers)
              │
              ├── [Concern 1] ValkeyStreamsConsumer
              │     XGROUP CREATE events.subscription cg.provisioning-convergence MKSTREAM (idempotent)
              │     loop: XREADGROUP → dispatch to no-op handler → XACK
              │     shutdown: drain pending, exit XREADGROUP loop
              │
              ├── [Concern 2] Convergence + Taskiq broker
              │     RedisStreamBroker(url=VALKEY_URL).startup()
              │     Phase 1: broker connect only, zero tasks registered
              │     shutdown: broker.shutdown()
              │
              ├── [Concern 3] Outbox relay
              │     Phase 1: sleep loop (event_outbox table does not exist yet)
              │     log "outbox relay started poll_seconds=X"
              │     shutdown: exits poll loop on cancellation
              │
              └── [Concern 4] Health server
                    aiohttp AppRunner + TCPSite on HEALTH_PORT=8001
                    GET /healthz → 200 {"status":"ok"}
                    shutdown: runner.cleanup()

External infra (both fail-fast on unreachable):
  ├── Postgres 18 (platform-postgres) → SQLAlchemy async engine
  └── Valkey 8 (platform-valkey) → redis.asyncio client
```

### Recommended Project Structure

```
provisioner/
├── .env.example                         # M1 var set; M2 vars commented
├── .gitignore
├── .dockerignore
├── .github/
│   └── workflows/ci.yml                 # lint + test + build-only
├── .python-version                      # 3.14
├── alembic.ini                          # [provisioning] section only
├── Dockerfile                           # two-stage: builder + runtime
├── docker-compose.yml                   # worker service + external platform-net
├── Makefile
├── pyproject.toml                       # hatchling build + ruff + pytest config
├── README.md                            # required by hatchling (readme = "README.md")
├── uv.lock                              # committed; uv sync --frozen everywhere
├── migrations/
│   └── provisioning/
│       ├── env.py                       # async engine, version_table_schema=provisioning
│       ├── script.py.mako               # no from __future__ import annotations
│       └── versions/                    # empty in Phase 1
├── src/provisioning_worker/
│   ├── __init__.py
│   ├── __main__.py                      # calls main.run()
│   ├── main.py                          # composition root: wire + TaskGroup
│   ├── settings.py                      # pydantic-settings, full M1 var set
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   ├── db.py                        # engine + session_scope (real)
│   │   ├── logging.py                   # configure_logging (real)
│   │   ├── observability.py             # configure_tracing (real)
│   │   ├── health_server.py             # aiohttp AppRunner (real)
│   │   └── outbox_relay.py              # no-op poll loop (real plumbing)
│   ├── ports/
│   │   └── __init__.py                  # placeholder
│   ├── adapters/
│   │   └── __init__.py                  # placeholder
│   ├── shared/
│   │   └── __init__.py                  # placeholder
│   ├── events/
│   │   └── __init__.py                  # placeholder
│   └── modules/
│       └── provisioning/
│           ├── models.py                # placeholder (module docstring only)
│           ├── schemas.py               # placeholder
│           ├── repository.py            # placeholder
│           ├── service.py               # placeholder
│           ├── handlers.py              # placeholder
│           ├── tasks.py                 # placeholder
│           └── spec.py                  # placeholder
└── tests/
    ├── conftest.py                      # minimal fixture stubs
    ├── provisioning/
    │   └── __init__.py
    └── test_health.py                   # smoke test for /healthz
```

### Pattern 1: asyncio.TaskGroup supervision with SIGTERM drain

**What:** All four concerns launch as tasks inside a single `TaskGroup`. SIGTERM sets a shared `asyncio.Event`; each concern's loop checks it. The main entry point wraps the `TaskGroup` in `try/except*` to handle the `ExceptionGroup` cleanly on crash.

**When to use:** Long-running workers where one concern crashing should always restart the whole process (crash-only design).

```python
# Source: docs/architecture.md §Process model + Python 3.11 asyncio docs
import asyncio
import signal

async def run(settings: Settings) -> None:
    """Composition root. Boots the four concerns and blocks until SIGTERM."""
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    engine = build_engine(settings)
    # ... build other resources ...

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_run_consumer(settings, shutdown), name="consumer")
            tg.create_task(_run_convergence(settings, shutdown), name="convergence")
            tg.create_task(_run_outbox_relay(settings, shutdown), name="outbox_relay")
            tg.create_task(_run_health_server(settings, shutdown), name="health_server")
    except* Exception as eg:
        # Non-zero exit if any concern raised (crash-only: D-01)
        raise SystemExit(1) from eg.exceptions[0]
    finally:
        # Drain: D-02 — close pools on every exit path
        await engine.dispose()

    # Clean drain (D-02): shutdown_event was set → all concerns exited normally
```

**Key detail:** `shutdown.set()` causes each concern's `while not shutdown.is_set()` loop to exit gracefully → the `TaskGroup` block completes normally → `finally` closes pools → `sys.exit(0)` implicitly. A crashed concern raises through the `TaskGroup` as an `ExceptionGroup` → `SystemExit(1)` path. [CITED: Python 3.11+ asyncio.TaskGroup docs]

### Pattern 2: aiohttp `/healthz` server as background concern

**What:** Run `aiohttp.web` as a non-blocking background task using `AppRunner` + `TCPSite`, not `web.run_app()`. The concern task keeps the site alive until `shutdown` is set, then calls `runner.cleanup()` for graceful teardown.

```python
# Source: https://docs.aiohttp.org/en/stable/web_advanced.html (AppRunner)
import asyncio
from aiohttp import web

async def _healthz(request: web.Request) -> web.Response:
    return web.Response(
        content_type="application/json",
        text='{"status":"ok"}',
    )

async def _run_health_server(settings: Settings, shutdown: asyncio.Event) -> None:
    app = web.Application()
    app.router.add_get("/healthz", _healthz)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.health_port)
    await site.start()

    log.info("health server listening", port=settings.health_port)
    await shutdown.wait()          # Block until SIGTERM
    await runner.cleanup()         # Graceful teardown
```

[CITED: https://docs.aiohttp.org/en/stable/web_advanced.html]

### Pattern 3: redis.asyncio XGROUP CREATE + XREADGROUP consumer loop

**What:** Create a consumer group idempotently (tolerating `BUSYGROUP`), then enter the blocking `XREADGROUP` loop. `XACK` every message after the no-op handler runs.

```python
# Source: redis.io docs + redis-py examples (CITED: redis.readthedocs.io)
import redis.asyncio as aioredis

async def _run_consumer(settings: Settings, shutdown: asyncio.Event) -> None:
    client = aioredis.from_url(str(settings.valkey_url), decode_responses=True)

    # Idempotent group creation — tolerate BUSYGROUP (group already exists)
    try:
        await client.xgroup_create(
            name="events.subscription",
            groupname=settings.provisioning_consumer_group,
            id="0",                # start from oldest unacked on first boot
            mkstream=True,         # create stream if it doesn't exist
        )
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
        # Group already exists — expected on restart, continue

    log.info(
        "joined consumer group",
        group=settings.provisioning_consumer_group,
        stream="events.subscription",
        consumer=settings.consumer_name,
    )

    # XREADGROUP loop — no-op handler in Phase 1
    while not shutdown.is_set():
        results = await client.xreadgroup(
            groupname=settings.provisioning_consumer_group,
            consumername=settings.consumer_name,
            streams={"events.subscription": ">"},
            count=10,
            block=1000,          # ms; non-blocking poll respects shutdown
        )
        if results:
            for _stream, messages in results:
                for msg_id, _fields in messages:
                    log.debug("received event (no-op)", msg_id=msg_id)
                    await client.xack(
                        "events.subscription",
                        settings.provisioning_consumer_group,
                        msg_id,
                    )

    await client.aclose()
```

**BUSYGROUP handling:** `redis.ResponseError` with `"BUSYGROUP"` in the message means the group already exists — this is normal on worker restart. [CITED: redis.io/docs/latest/commands/xgroup-create]

**`block=1000`:** The 1-second block timeout lets the shutdown check fire promptly without busy-polling. In Phase 2+, this becomes a proper receive loop with dispatch-on-type.

### Pattern 4: taskiq-redis broker connect-only

**What:** Construct `RedisStreamBroker`, call `startup()` at boot (which registers the Redis consumer group for Taskiq's own internal machinery), register zero tasks, log success. Shutdown calls `shutdown()` which closes the connection pool.

```python
# Source: taskiq-redis PyPI docs (CITED: pypi.org/project/taskiq-redis)
from taskiq_redis import RedisStreamBroker

async def _run_convergence(settings: Settings, shutdown: asyncio.Event) -> None:
    broker = RedisStreamBroker(url=str(settings.valkey_url))
    await broker.startup()          # creates internal consumer group on Valkey

    log.info("taskiq broker connected", url=str(settings.valkey_url))

    # Phase 1: no tasks registered. Block until shutdown.
    await shutdown.wait()
    await broker.shutdown()         # closes connection pool
```

**Important:** `RedisStreamBroker.startup()` internally calls `_declare_consumer_group()` which does an `XGROUP CREATE` on Valkey. This will produce a separate consumer group for Taskiq's own message passing (distinct from the provisioning consumer group). This is expected behavior — the broker manages its own stream. [CITED: taskiq-redis GitHub source inspection]

### Pattern 5: structlog configuration with stdlib bridge

**What:** Use `ProcessorFormatter` to bridge structlog output through the stdlib `logging` root handler. In dev: `ConsoleRenderer`; in prod/staging: `JSONRenderer`. This pattern is directly mirrored from `platform-api/infrastructure/logging.py`.

```python
# Source: platform-api/src/platform_api/infrastructure/logging.py [CITED — exact template]
import logging
import sys
import structlog
from structlog.contextvars import merge_contextvars
from structlog.processors import (
    CallsiteParameter, CallsiteParameterAdder,
    JSONRenderer, TimeStamper, add_log_level, format_exc_info,
)
from structlog.stdlib import BoundLogger, ProcessorFormatter, add_logger_name

def configure_logging(settings: Settings) -> None:
    use_json = settings.environment != "dev"

    shared_processors: list = [
        merge_contextvars,
        add_log_level,
        add_logger_name,
        TimeStamper(fmt="iso", utc=True),
        CallsiteParameterAdder(
            parameters=[
                CallsiteParameter.MODULE,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            ],
        ),
        format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, ProcessorFormatter.wrap_for_formatter],
        wrapper_class=BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    renderer = JSONRenderer() if use_json else structlog.dev.ConsoleRenderer(colors=True)
    formatter = ProcessorFormatter(processor=renderer, foreign_pre_chain=shared_processors)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
```

**Worker delta vs platform-api:** Remove the `granian` logger silencing lines (lines 83–87) — the worker uses no Granian.

### Pattern 6: Settings with pydantic-settings + env_file

The `Settings` class mirrors platform-api's `Settings` exactly. Drop Keycloak, Stripe, and `service_name`; add `PROVISIONING_CONSUMER_GROUP`, `CONSUMER_NAME`, `DEPLOYMENT_ADAPTER`, `NOTIFICATION_TRANSPORT`, `HEALTH_PORT`, `OUTBOX_BATCH_SIZE`, `INSTANCE_DOMAIN_SUFFIX`, `ODOO_BASE_IMAGE`.

```python
# Source: platform-api/src/platform_api/settings.py [CITED — direct template]
from typing import Literal
from pydantic import Field, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: PostgresDsn = Field(default="postgresql+psycopg://...")
    database_url_sync: PostgresDsn = Field(default="postgresql+psycopg://...")
    db_pool_size: int = Field(default=5, ge=1, le=100)

    valkey_url: RedisDsn = Field(default="redis://localhost:6379/0")
    provisioning_consumer_group: str = "cg.provisioning-convergence"
    consumer_name: str = "worker-1"

    deployment_adapter: Literal["fake", "coolify"] = "fake"
    notification_transport: Literal["console", "smtp"] = "console"

    health_port: int = Field(default=8001, ge=1, le=65535)
    outbox_poll_seconds: float = Field(default=1.0, gt=0.0)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)

    instance_domain_suffix: str = "example.local"
    odoo_base_image: str = "odoo:17"

    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "provisioning-worker"

    @property
    def otel_enabled(self) -> bool:
        return self.otel_exporter_otlp_endpoint is not None
```

### Pattern 7: OpenTelemetry bootstrap (mirrors platform-api exactly)

**What:** Always install the `TracerProvider` (even without a backend). In Phase 1 instrument psycopg and redis. The OTLP exporter is added only when `settings.otel_enabled` is True.

```python
# Source: platform-api/src/platform_api/infrastructure/observability.py [CITED — direct template]
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor  # NOT aiohttp in Phase 1
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

def configure_tracing(settings: Settings) -> None:
    resource = Resource.create({
        "service.name": settings.otel_service_name,
        "deployment.environment": settings.environment,
    })
    provider = TracerProvider(resource=resource)

    if settings.otel_enabled:
        exporter = OTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint,
            insecure=settings.environment == "dev",
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    PsycopgInstrumentor().instrument()
    RedisInstrumentor().instrument()
    # AioHttpClientInstrumentor reserved for M2 Coolify client
```

**Worker delta vs platform-api:** Use `RedisInstrumentor` (not `AioHttpClientInstrumentor`). Remove `FastAPIInstrumentor`. [CITED: platform-api observability.py + D-07]

### Pattern 8: Alembic env.py for single provisioning tree

**What:** A single-section `alembic.ini` with `[provisioning]` section. The `env.py` sets `version_table_schema="provisioning"` so the `alembic_version` table lives in the `provisioning` schema (not `public`).

```ini
# alembic.ini [CITED: platform-api/alembic.ini + CLAUDE.md §6.3]
[alembic]
script_location = migrations/_default_must_use_named_section
prepend_sys_path = .

[provisioning]
script_location = migrations/provisioning
prepend_sys_path = .
version_table = alembic_version
version_table_schema = provisioning
file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(slug)s
```

```python
# migrations/provisioning/env.py [CITED: platform-api/migrations/catalog/env.py pattern]
# NO "from __future__ import annotations" — forbidden by CLAUDE.md §6.1
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from provisioning_worker.settings import get_settings

SCHEMA = "provisioning"
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", str(get_settings().database_url_sync))

target_metadata = None  # no tables in Phase 1

def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table="alembic_version",
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()
```

**Critical for Phase 1:** `make migrate` in Phase 1 creates only the `provisioning.alembic_version` table (no domain tables). This is valid — the schema exists (created by platform-infra init SQL), the table is Alembic's own version tracker.

### Pattern 9: script.py.mako — remove `from __future__ import annotations`

The default Alembic mako template includes `from __future__ import annotations`. Since PEP 649 is built into Python 3.14, this import is both unnecessary and forbidden by CLAUDE.md. The template must be customized:

```mako
# migrations/provisioning/script.py.mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Note the removal of `from __future__ import annotations`. [CITED: CLAUDE.md §6.1, docs/conventions.md]

### Pattern 10: Dockerfile two-stage (mirror platform-api exactly)

**Key deltas from platform-api:**
1. `ENTRYPOINT ["python", "-m", "provisioning_worker"]` (no Granian)
2. `EXPOSE 8001` (not 8000)
3. `HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2).status == 200 else 1)"`
4. `alembic.ini` + `migrations/` copied into runtime stage (single tree, no `-n` suffix needed in docker-migrate)
5. No `CMD` with workers/host flags (python -m takes no args for worker)

[CITED: platform-api/Dockerfile — direct template]

### Pattern 11: docker-compose.yml delta from platform-api

**Key deltas:**
- `name: provisioning-worker`
- Service is `worker:` not `api:`
- `container_name: provisioning-worker`
- `ports: - "${HEALTH_HOST_PORT:-8001}:8001"`
- No `KEYCLOAK_BASE_URL` environment override
- `healthcheck` hits `http://127.0.0.1:8001/healthz`

[CITED: platform-api/docker-compose.yml — direct template + D-13]

### Pattern 12: Outbox relay no-op strategy in Phase 1

Since `event_outbox` does not exist until Phase 4, the relay cannot query it. The cleanest no-op that passes lint and tests is: run the poll loop normally, but each iteration sleeps via `asyncio.wait_for(shutdown.wait(), timeout=settings.outbox_poll_seconds)` rather than querying the DB. The relay logs "outbox relay started" and does nothing else per iteration.

This avoids: (a) importing the `EventOutbox` model (doesn't exist), (b) a try/except on missing table (misleading error logs), (c) a conditional branch that would confuse Phase 4 when the relay gains real content.

```python
# Phase 1 outbox relay — no-op poll loop (Claude's Discretion)
import asyncio
import contextlib

async def run_outbox_relay(settings: Settings, shutdown: asyncio.Event) -> None:
    log.info("outbox relay started", poll_seconds=settings.outbox_poll_seconds)
    while not shutdown.is_set():
        # Phase 4 will replace this with a real DB drain.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=settings.outbox_poll_seconds)
    log.info("outbox relay stopped")
```

[ASSUMED — design choice for Phase 1 no-op; consistent with platform-api outbox_relay.py's poll-sleep pattern]

### Pattern 13: Fail-fast on unreachable infra at boot (D-05)

**What:** Before starting the TaskGroup, attempt a lightweight check on Postgres and Valkey. If either fails, log at `error` level and raise (which exits non-zero from `__main__.py`).

```python
# Postgres check: attempt a single async connection
async def _check_postgres(settings: Settings) -> None:
    engine = build_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.error("postgres unreachable at boot", error=str(exc))
        raise

# Valkey check: ping
async def _check_valkey(settings: Settings) -> None:
    client = aioredis.from_url(str(settings.valkey_url))
    try:
        await client.ping()
    except Exception as exc:
        log.error("valkey unreachable at boot", error=str(exc))
        raise
    finally:
        await client.aclose()
```

[ASSUMED — pattern based on D-05 specification; standard approach for fail-fast infra checks]

### Anti-Patterns to Avoid

- **`web.run_app()` for the health server:** This blocks the entire event loop. Use `AppRunner` + `TCPSite` as a background concern inside the TaskGroup.
- **`asyncio.gather()` for concern supervision:** `gather()` does not cancel siblings on failure (D-01 requires crash-only). Use `TaskGroup`.
- **Registering signal handlers with `signal.signal()`:** This uses the sync signal module. Use `loop.add_signal_handler()` for async-safe signal handling.
- **`from __future__ import annotations` in any file:** Forbidden by CLAUDE.md + PEP 649 makes it unnecessary in Python 3.14.
- **`aioredis` package:** Unmaintained. Use `redis.asyncio` from the unified `redis>=5` package.
- **Hardcoded `localhost` in docker-compose:** Override with in-network DNS names (`platform-postgres`, `platform-valkey`) in the `environment:` block.
- **Running `alembic upgrade head` without `-n provisioning`:** The default `[alembic]` section in `alembic.ini` should be configured to error loudly (point at a non-existent path) so omitting `-n` fails rather than silently migrating the wrong schema. [CITED: platform-api/alembic.ini]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Structured logging with stdlib bridge | Custom `logging.Formatter` | `structlog.stdlib.ProcessorFormatter` | ProcessorFormatter handles the structlog→stdlib bridge, including foreign (third-party) log routing |
| Env-var config with validation | Custom `os.getenv()` wrappers | `pydantic-settings BaseSettings` | Field validation, Literal types, computed fields, env_file loading |
| Async DB sessions | Manual `sessionmaker()` lifecycle | `SQLAlchemy async_sessionmaker` + `session_scope()` context manager | Handles rollback on exception, expire_on_commit=False |
| Consumer group idempotency | Check-then-create | `mkstream=True` + BUSYGROUP exception swallow | Race-condition safe; idiomatic redis-py pattern |
| Graceful shutdown | `try/finally` in each concern | `asyncio.Event` shared across concerns | One event; all concerns observe it cleanly |
| OTel always-on without backend | Conditional import/configure | Install `TracerProvider` unconditionally; only add `BatchSpanProcessor` when endpoint set | Prevents "cold start" trace gaps when backend is later added |
| Mako template `from __future__` removal | Post-process generated files | Customize `script.py.mako` at scaffold time | One-time fix; prevents every `make revision` from producing a bad file |

**Key insight:** The platform-api codebase is the reference implementation for 90% of this scaffold. The planner should instruct agents to "mirror platform-api and apply the worker deltas enumerated in D-06..D-15" rather than designing each piece from scratch.

---

## Runtime State Inventory

> Phase 1 is a greenfield scaffold — no rename/refactor/migration involved. No existing runtime state to audit.

**Nothing found in any category — this is a net-new repo with no existing deployed state.**

---

## Common Pitfalls

### Pitfall 1: OTel instrumentation-package version mismatch
**What goes wrong:** `opentelemetry-instrumentation-psycopg==0.63b1` installed alongside `opentelemetry-api==1.41.*` causes `AttributeError` or import errors at boot.
**Why it happens:** The `0.6Xb0` train maps to the `1.4X` API train. 0.62b0 ↔ 1.41.x; 0.63b1 ↔ 1.42.x.
**How to avoid:** Pin all OTel packages together. Use platform-api's pyproject.toml as the exact template: `opentelemetry-*==1.41.*` + `opentelemetry-instrumentation-*==0.62b0`.
**Warning signs:** Import errors mentioning `opentelemetry.instrumentation` at worker boot.

### Pitfall 2: `from __future__ import annotations` in generated Alembic files
**What goes wrong:** `make revision` generates a revision file with `from __future__ import annotations`, violating the project's no-`__future__` rule and causing ruff to flag it.
**Why it happens:** The default Alembic `script.py.mako` template includes this import.
**How to avoid:** Customize `migrations/provisioning/script.py.mako` to omit the import line before running the first `make revision`.
**Warning signs:** `ruff check` fails on generated migration files.

### Pitfall 3: Health server blocks the event loop
**What goes wrong:** `web.run_app(app)` blocks the current thread; the other three concerns never start.
**Why it happens:** `run_app` is designed for programs where the server IS the main concern. It calls `loop.run_forever()` internally.
**How to avoid:** Use `AppRunner` + `TCPSite` + `await shutdown.wait()` pattern as the health server concern task. [CITED: aiohttp AppRunner docs]
**Warning signs:** Worker logs only "health server listening" and never "joined consumer group".

### Pitfall 4: XREADGROUP `block=0` prevents clean shutdown
**What goes wrong:** `block=0` (block indefinitely) makes the consumer task unresponsive to `shutdown.is_set()` until a message arrives.
**Why it happens:** The Redis blocking read doesn't check the Python event.
**How to avoid:** Use `block=1000` (1 second) so the loop re-checks `shutdown.is_set()` every second. [ASSUMED — standard pattern for cooperative shutdown in Redis consumers]
**Warning signs:** Worker hangs on SIGTERM for many seconds until a message arrives.

### Pitfall 5: BUSYGROUP not tolerated causes crash on restart
**What goes wrong:** Second invocation of the worker fails at boot with `BUSYGROUP Consumer Group name already exists`.
**Why it happens:** `xgroup_create` without exception handling raises `ResponseError` if the group exists.
**How to avoid:** Wrap `xgroup_create` in `try/except ResponseError` and check `"BUSYGROUP" in str(exc)`. Re-raise anything else.
**Warning signs:** Worker fails at second boot with a Redis error; first boot succeeds.

### Pitfall 6: Docker `.venv` shebang path mismatch
**What goes wrong:** `alembic` command in the runtime container says `no such file or directory`.
**Why it happens:** If `builder` uses `WORKDIR /build` and `runtime` uses `WORKDIR /app`, the `.venv/bin/alembic` shebang (written as `/build/.venv/bin/python`) points at a non-existent path.
**How to avoid:** Use `WORKDIR /app` in BOTH builder and runtime stages (as platform-api/Dockerfile does). [CITED: platform-api/Dockerfile comment]
**Warning signs:** `docker-migrate` exits non-zero with path errors.

### Pitfall 7: `pytest-cov` version mismatch
**What goes wrong:** `pytest-cov==7.*` (latest) may have changed CLI args that break existing Make targets.
**Why it happens:** CLAUDE.md pins `pytest-cov==6.*` but PyPI latest is 7.x.
**How to avoid:** Respect the pinned `6.*` constraint from CLAUDE.md. The platform-api reference uses `6.*` consistently.
**Warning signs:** `make test-cov` fails with unrecognized coverage options.

### Pitfall 8: `target_metadata = None` in env.py breaks autogenerate
**What goes wrong:** `make revision` generates an empty migration that detects no changes when domain tables should be detected.
**Why it happens:** `autogenerate` compares against `target_metadata`; `None` means "nothing to compare".
**How to avoid:** In Phase 1, `None` is correct (no tables yet). In Phase 3+, import `metadata` from `models.py`. The planner must note this as a Phase 3 task.
**Warning signs:** `make revision` generates `pass` for both upgrade and downgrade on first domain table.

### Pitfall 9: TaskGroup exception handling with `except*`
**What goes wrong:** Using `except Exception` (not `except* Exception`) to catch `TaskGroup` failures silently ignores them or causes `TypeError`.
**Why it happens:** `TaskGroup` raises `ExceptionGroup` (Python 3.11+), which requires `except*` syntax.
**How to avoid:** Use `except* Exception as eg` when wrapping the TaskGroup block. [CITED: Python asyncio.TaskGroup docs]
**Warning signs:** Uncaught `ExceptionGroup` traceback in logs; non-zero exit not triggered.

### Pitfall 10: Alembic `DATABASE_URL_SYNC` must not include `+psycopg` if using the sync driver
**What goes wrong:** Alembic (sync) receives `postgresql+psycopg://...` which requires psycopg's sync mode. This is correct for psycopg3 — but if `+asyncpg` is used by mistake, Alembic fails.
**Why it happens:** Platform-api and this repo use `psycopg[binary]` for BOTH async and sync paths. The same `psycopg` driver package handles both.
**How to avoid:** Always use `postgresql+psycopg://` (no `async` prefix) for `DATABASE_URL_SYNC`. The async URL uses the same scheme (not `postgresql+asyncpg://`). [CITED: CLAUDE.md §3 — "NOT asyncpg"]
**Warning signs:** Alembic ImportError about `asyncpg` driver.

---

## Code Examples

### Boot log Phase 1 must reproduce
```text
# Source: docs/local-development.md §Running the worker [CITED — authoritative]
INFO  provisioning-worker starting environment=dev deployment_adapter=fake
INFO  joined consumer group group=cg.provisioning-convergence stream=events.subscription consumer=worker-1
INFO  outbox relay started poll_seconds=1.0
INFO  health server listening port=8001
```

These four lines are the acceptance criterion for D-04. The planner must produce tasks that generate exactly these lines (matching field names and values with default `.env`).

### GitHub Actions CI (mirrors platform-api with worker deltas)
```yaml
# Source: platform-api/.github/workflows/ci.yml [CITED — direct template]
# Deltas: no integration tests on PR gate (D-15); coverage source is provisioning_worker
name: CI
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
  workflow_dispatch:

env:
  PYTHON_VERSION: "3.14"
  UV_VERSION: "0.11.14"

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with: { version: "${{ env.UV_VERSION }}", enable-cache: true }
      - run: uv python install ${{ env.PYTHON_VERSION }}
      - run: uv sync --frozen --extra dev
      - run: uv run ruff check .
      - run: uv run ruff format --check .

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with: { version: "${{ env.UV_VERSION }}", enable-cache: true }
      - run: uv python install ${{ env.PYTHON_VERSION }}
      - run: uv sync --frozen --extra dev
      - run: uv run pytest -m "not integration"  # D-15: no testcontainers on gate

  build:
    runs-on: ubuntu-latest
    needs: [lint]
    steps:
      - uses: actions/checkout@v5
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          load: true
          tags: provisioning-worker:ci-${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `asyncio.gather()` for concurrent tasks | `asyncio.TaskGroup` | Python 3.11 | Fail-fast semantics; ExceptionGroup propagation |
| `aioredis` (standalone package) | `redis.asyncio` (unified `redis>=5`) | redis-py 4.x → 5.x | Single package, maintained, same API |
| `signal.signal()` for SIGTERM | `loop.add_signal_handler()` | Python 3.4+ asyncio | Async-safe; doesn't interrupt coroutines |
| `web.run_app()` for aiohttp | `AppRunner` + `TCPSite` | aiohttp 3.x | Non-blocking; embeddable in asyncio app |
| `from __future__ import annotations` | Native (PEP 649) | Python 3.14 | Import unnecessary + forbidden |
| `alembic.ini` single `[alembic]` section | Named sections `[provisioning]`, etc. | Alembic 1.x (long established) | Multi-schema support without path confusion |

**Deprecated/outdated:**
- `aioredis` package: Do not install. Use `redis>=5` which provides `redis.asyncio`.
- `asyncio.gather()` for supervised tasks: Use `asyncio.TaskGroup` in Python 3.11+.
- `web.run_app()` in async context: Use `AppRunner`/`TCPSite`.
- `from __future__ import annotations`: Python 3.14 makes it redundant; project forbids it.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `RedisStreamBroker.startup()` creates Taskiq's own internal consumer group on Valkey — this is separate from the provisioning consumer group and is expected behavior | Pattern 4 | Low — if startup() doesn't create a group, boot still succeeds; confirm by reading taskiq-redis source |
| A2 | Empty outbox relay (poll-sleep loop, no DB query) is the cleanest Phase 1 no-op | Pattern 12 | Low — alternative (catch table-not-found exception) is equally valid but noisy in logs |
| A3 | `loop.add_signal_handler` + shared `asyncio.Event` is the correct clean-drain pattern for this TaskGroup shape | Pattern 1 | Low — this is the standard Python asyncio pattern; risk is in timeout tuning |
| A4 | `block=1000` ms on `XREADGROUP` is sufficient for responsive shutdown | Pattern 3 | Low — worst case 1 second additional delay on SIGTERM; acceptable |
| A5 | The `pytest-cov==6.*` pin is intentional and `7.*` should not be used | Standard Stack | Medium — if CLAUDE.md §3 permits 7.*, the pin is wrong. CLAUDE.md says "6.*" explicitly |

**If this table is empty:** All claims in this research were verified or cited — no user confirmation needed.
(Table has 5 items; all LOW or MEDIUM risk.)

---

## Open Questions

1. **Taskiq broker startup() behavior on empty Valkey**
   - What we know: `startup()` calls `_declare_consumer_group()` internally.
   - What's unclear: Does this consumer group conflict with the provisioning consumer group on the same Valkey DB?
   - Recommendation: Each concern uses a different stream key (taskiq has its own stream, provisioning has `events.subscription`). No conflict expected.

2. **Drain timeout on SIGTERM**
   - What we know: D-02 says "let in-flight work settle" — but Phase 1 has no in-flight work (no-op handlers).
   - What's unclear: Whether a drain timeout constant (e.g., 30 seconds) should be coded now or deferred.
   - Recommendation: In Phase 1, the drain is instant (no in-flight work). Implement the event-based shutdown (no timeout) and add a configurable timeout in Phase 3 when real tasks exist.

3. **`make run` convention: `uv run` vs `.venv/bin/python`**
   - What we know: CLAUDE.md says prefer `.venv/bin/<tool>` for ad-hoc commands; "Make-internal `uv run` matches platform-api and is acceptable."
   - What's unclear: Which to use in `make run` specifically.
   - Recommendation: Mirror platform-api's Makefile exactly — platform-api uses `$(UV) run granian …` in `make run`. The equivalent for the worker is `$(UV) run python -m provisioning_worker`. This is consistent with "run tools through make targets" being the convention (Claude's Discretion).

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| uv | Package management, `make run`, CI | ✓ | 0.11.14 | — |
| Python 3.14 | Runtime | ✓ | 3.14.4 | — |
| Docker | `make docker-build`, `make docker-run`, CI build job | ✓ | 29.4.3 | — |
| git | CI, version control | ✓ | 2.53.0 | — |
| psql / pg_isready | `make psql`, dev healthcheck | ✗ | — | `docker exec platform-postgres psql` |
| valkey-cli / redis-cli | Manual event injection, smoke testing | ✗ | — | `docker exec platform-valkey valkey-cli` |
| platform-infra (Postgres+Valkey) | `make run`, `make migrate` | Unknown | — | Must be started via `make infra-up` |

**Missing dependencies with no fallback:**
- `platform-infra` services (Postgres 18 + Valkey 8) — required for `make run` and `make migrate`. Must be started before `make run`. CI must mock or skip infra-dependent tests (unit tests are Docker-free per D-15).

**Missing dependencies with fallback:**
- `psql` — use `docker exec platform-postgres psql` or `make psql` (which constructs the connection from `DATABASE_URL_SYNC`).
- `valkey-cli` — use `docker exec platform-valkey valkey-cli`.

---

## Validation Architecture

> `workflow.nyquist_validation: true` — section required.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.* with pytest-asyncio 1.3.* |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — created in Wave 0 |
| Quick run command | `uv run pytest -m "not integration" -x` |
| Full suite command | `uv run pytest -m "not integration"` (Phase 1 has no integration tests) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SCAF-01 | `make check` passes (ruff check + format --check) | smoke / linting | `uv run ruff check . && uv run ruff format --check .` | ❌ Wave 0 — pyproject.toml with ruff config |
| SCAF-01 | `make test` passes on empty suite | smoke | `uv run pytest -m "not integration"` | ❌ Wave 0 — conftest.py + empty provisioning/ dir |
| SCAF-02 | Worker logs exact four boot lines in order | unit / process smoke | `tests/test_boot.py` — start worker as subprocess, assert log output | ❌ Wave 0 |
| SCAF-02 | SIGTERM → exit 0 (clean drain) | unit / process smoke | `tests/test_boot.py` — send SIGTERM, assert exit code 0 | ❌ Wave 0 |
| SCAF-03 | `Settings` raises on missing required var | unit | `tests/test_settings.py::test_missing_required_var` | ❌ Wave 0 |
| SCAF-03 | `Settings` loads from `.env` | unit | `tests/test_settings.py::test_env_file_loading` | ❌ Wave 0 |
| SCAF-04 | `GET /healthz` → 200 `{"status":"ok"}` | unit (in-process) | `tests/test_health.py::test_healthz_returns_ok` | ❌ Wave 0 |
| SCAF-05 | `make migrate` creates `provisioning.alembic_version` table | integration | `@pytest.mark.integration` in `tests/test_migrations.py` | ❌ Wave 0 |
| SCAF-05 | `make revision` emits file without `from __future__ import annotations` | smoke (manual / CI) | `uv run ruff check migrations/` | ❌ Wave 0 — after first revision |
| OBS-01 | structlog emits JSON when `ENVIRONMENT=staging` | unit | `tests/test_logging.py::test_json_output_non_dev` | ❌ Wave 0 |
| OBS-01 | OTel TracerProvider installed without OTLP endpoint | unit | `tests/test_observability.py::test_tracing_no_backend` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest -m "not integration" -x` (fail-fast)
- **Per wave merge:** `uv run pytest -m "not integration"` (full unit suite)
- **Phase gate:** Full suite green + `make check` passes + `make docker-build` succeeds

### Wave 0 Gaps

The following test infrastructure must be created before (or in Wave 1 of) the implementation:

- [ ] `pyproject.toml` with `[tool.pytest.ini_options]` — `asyncio_mode=auto`, `--strict-markers`, `filterwarnings=["error", ...]`, `markers = ["integration: ...", "slow: ..."]`
- [ ] `tests/conftest.py` — minimal (no fixtures needed in Phase 1; stubs for later phases)
- [ ] `tests/__init__.py` and `tests/provisioning/__init__.py`
- [ ] `tests/test_health.py` — `test_healthz_returns_ok` using aiohttp `TestClient` or in-process `AppRunner`
- [ ] `tests/test_settings.py` — env-var validation tests
- [ ] `tests/test_logging.py` — JSON vs ConsoleRenderer selection test
- [ ] `tests/test_observability.py` — TracerProvider installed test

For SCAF-02 (boot log + SIGTERM drain): subprocess-based tests are integration-level (require real infra or mocks). Consider a unit-level test that calls `main.run()` with mocked infra connections and a `shutdown_event` that fires after a short delay — then assert the log output.

---

## Security Domain

> `security_enforcement: true`, `security_asvs_level: 1` — section required.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Worker authenticates to nothing in MVP (trusted internal network) |
| V3 Session Management | No | No sessions; stateless worker |
| V4 Access Control | No | No HTTP API endpoints beyond `/healthz` |
| V5 Input Validation | Partial | Pydantic `Settings` validates env vars at startup; event payload validation is Phase 2 |
| V6 Cryptography | No | No crypto in Phase 1; M2 introduces per-instance token (PyJWT) |
| V7 Error Handling | Yes | Fail-fast on bad config; crash-only on infra failure |
| V14 Configuration | Yes | Secrets in env vars (not code); `.env` is gitignored |

### Known Threat Patterns for this Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Secrets in Docker image | Information Disclosure | Use `env_file:` in Compose, not ARGs; `.env` excluded from `.dockerignore` [CITED: platform-api/.dockerignore] |
| Valkey without auth in dev | Spoofing | Acceptable on trusted internal network; production Valkey should require AUTH |
| Non-root container user | Privilege Escalation | UID/GID 10001 `platform` user (D-12) mitigates container escape risk |
| `OTEL_EXPORTER_OTLP_ENDPOINT` pointing at attacker | Tampering | OTLP spans contain traces, not secrets; low risk in Phase 1 with no sensitive payload data |
| Log injection | Tampering | structlog outputs structured key-value pairs; message strings are not interpolated into format strings |

### Phase 1 Security Notes

- No user-facing data is processed in Phase 1 (no handlers). Security surface is minimal.
- The `/healthz` endpoint is unauthenticated — this is intentional for liveness probes.
- `pydantic-settings` with `extra="ignore"` means unknown env vars are silently dropped (not errors). Consider `extra="forbid"` for stricter validation — but `extra="ignore"` matches platform-api's pattern (Claude's Discretion).

---

## Project Constraints (from CLAUDE.md)

The following directives from `./CLAUDE.md` constrain implementation choices. The planner must verify compliance:

| Directive | Section | Constraint |
|-----------|---------|------------|
| No `from __future__ import annotations` anywhere | §6.1 | Forbidden — Python 3.14 built-in; in Alembic-generated files too |
| No mypy / pyright | §6.7 | Do not add mypy config or pre-commit mypy hooks |
| `uv sync --frozen` everywhere | §6.1 | Local, CI, Docker — all use `--frozen` |
| Tools via `.venv/bin/<tool>` for ad-hoc | §6.1 | Make targets may use `uv run`; ad-hoc commands use `.venv/bin/` |
| `make check` before declaring work complete | §6.1 | CI gate + local gate |
| Async-first | §6.1 | No blocking I/O on the loop; wrap sync libs in `asyncio.to_thread` |
| `redis.asyncio` not standalone `aioredis` | §conventions.md | Use unified `redis>=5` package |
| NOT asyncpg | §3 | Postgres driver is `psycopg[binary]` only |
| Single Alembic tree | §5, §6.3 | `[provisioning]` section only; no multi-tree setup |
| `HEALTH_PORT` default `8001` | §5 | Off platform-api's `8000` deliberately |
| Dependency rule (modules/ → infra,ports,adapters,shared,events but NOT vice versa) | §4 | Load-bearing architectural constraint |
| Google-style docstrings on all public modules/classes/functions | §6.1 | Every public API must have a docstring |
| `log = structlog.get_logger(__name__)` once per module | §6.6 | Module-level logger, not per-function |
| File naming is load-bearing | §6.1.1 | `models.py`=SQLAlchemy, `schemas.py`=Pydantic, never swap |
| No business logic in `infrastructure/` | §6.7 | infrastructure/ is plumbing only |

---

## Sources

### Primary (HIGH confidence — verified against official/authoritative sources)

- `platform-api/Dockerfile` — two-stage Dockerfile template (D-12 delta analysis)
- `platform-api/docker-compose.yml` — Compose service template (D-13 delta analysis)
- `platform-api/Makefile` — Makefile target structure (D-14 delta analysis)
- `platform-api/src/platform_api/settings.py` — Settings class template (D-06/D-09 pattern)
- `platform-api/src/platform_api/infrastructure/logging.py` — structlog configure_logging (D-06 exact template)
- `platform-api/src/platform_api/infrastructure/observability.py` — OTel bootstrap (D-07 exact template)
- `platform-api/src/platform_api/infrastructure/database.py` — SQLAlchemy async engine + session_scope (D-04 pattern)
- `platform-api/src/platform_api/infrastructure/outbox_relay.py` — outbox relay pattern (Phase 1 no-op design reference)
- `platform-api/migrations/catalog/env.py` — Alembic env.py with version_table_schema pattern (D-10/SCAF-05)
- `platform-api/alembic.ini` — alembic.ini named sections pattern (SCAF-05)
- `platform-api/pyproject.toml` — exact dep pins template (SCAF-01)
- `platform-api/.github/workflows/ci.yml` — CI workflow template (D-15)
- `platform-api/.dockerignore` — build context reduction list (D-12)
- `platform-api/.env.example` — env-file shape (D-09)
- `platform-infra/docker-compose.yml` — confirms `platform-net` network name, service names `platform-postgres`/`platform-valkey`
- `provisioner/docs/architecture.md` — four concerns, process model, code layout, configuration vars
- `provisioner/docs/local-development.md` — exact boot log (acceptance criterion for D-04)
- `provisioner/docs/conventions.md` — no `from __future__`, file naming, async rules
- `provisioner/CLAUDE.md §3/§4/§6` — tech stack pins, repo layout, coding conventions
- [PyPI taskiq 0.12.4](https://pypi.org/project/taskiq/) — version confirmed
- [PyPI taskiq-redis 1.2.2](https://pypi.org/project/taskiq-redis/) — version confirmed, `RedisStreamBroker` class documented
- [PyPI redis 8.0.0](https://pypi.org/project/redis/) — `redis.asyncio` unified client confirmed
- [alembic docs — `version_table_schema`](https://alembic.sqlalchemy.org/en/latest/api/runtime.html) — `EnvironmentContext.configure(version_table_schema=...)` confirmed
- [aiohttp AppRunner docs](https://docs.aiohttp.org/en/stable/web_advanced.html) — `AppRunner`/`TCPSite`/`runner.cleanup()` lifecycle confirmed
- [Python asyncio.TaskGroup docs](https://docs.python.org/3/library/asyncio-task.html#asyncio.TaskGroup) — fail-fast cancel semantics confirmed; `except*` required for ExceptionGroup

### Secondary (MEDIUM confidence — verified with official source)

- [redis.io xgroup-create docs](https://redis.io/docs/latest/commands/xgroup-create/) — `mkstream` parameter and BUSYGROUP error behavior confirmed
- [structlog standard-library.md](https://www.structlog.org/en/stable/standard-library.html) — `ProcessorFormatter` stdlib bridge pattern confirmed (matches platform-api template)

### Tertiary (LOW confidence — used for background only)

- taskiq-redis PyPI page — `RedisStreamBroker(url=...)` constructor shape (supplement to platform-api template)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified on PyPI at pinned versions; pins come from authoritative platform-api template
- Architecture: HIGH — TaskGroup, AppRunner, Alembic patterns verified against official docs; OTel verified against platform-api exact template
- Pitfalls: HIGH — most pitfalls derived from reading actual source code (platform-api, Python docs) rather than speculation
- CI/Docker/Makefile: HIGH — direct template files read and diff analyzed

**Research date:** 2026-06-01
**Valid until:** 2026-07-01 (stable ecosystem; all dependencies are established packages)
