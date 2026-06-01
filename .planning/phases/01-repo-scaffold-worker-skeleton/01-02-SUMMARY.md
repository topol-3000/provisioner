---
phase: "01-repo-scaffold-worker-skeleton"
plan: 2
subsystem: "worker-skeleton"
tags: ["settings", "structlog", "opentelemetry", "aiohttp", "asyncio-taskgroup", "taskiq", "redis-streams", "pydantic-settings"]
dependency_graph:
  requires:
    - "01-01: pyproject.toml, uv.lock, Makefile, alembic.ini"
  provides:
    - "python -m provisioning_worker boots four asyncio concerns"
    - "GET /healthz returns 200 {\"status\":\"ok\"}"
    - "SIGTERM drains cleanly via asyncio.Event + TaskGroup"
    - "typed Settings with full M1 var set and otel_enabled property"
    - "structlog ConsoleRenderer (dev) / JSONRenderer (non-dev)"
    - "always-on OTel TracerProvider + psycopg + redis instrumentors"
    - "D-10 placeholder module tree (ports/adapters/shared/events/modules/provisioning/*)"
    - "10 passing unit tests (no Docker required)"
  affects:
    - "Phase 2 (handlers): imports provisioning_worker.main._run_consumer, settings, shared"
    - "Phase 3 (domain): fills modules/provisioning/*.py placeholders"
    - "Phase 4 (outbox): replaces outbox_relay.py loop body"
tech_stack:
  added:
    - "asyncio.TaskGroup supervision (D-01, D-02, D-03)"
    - "aiohttp AppRunner + TCPSite for /healthz (SCAF-04)"
    - "structlog ProcessorFormatter + ConsoleRenderer/JSONRenderer (D-06, OBS-01)"
    - "opentelemetry TracerProvider + PsycopgInstrumentor + RedisInstrumentor (D-07, OBS-01)"
    - "pydantic-settings BaseSettings with full M1 var set (D-09, SCAF-03)"
    - "redis.asyncio XREADGROUP consumer loop with BUSYGROUP tolerance (D-04)"
    - "taskiq RedisStreamBroker connect-only (D-08)"
    - "SQLAlchemy async engine + session_scope context manager"
  patterns:
    - "TYPE_CHECKING guards for annotation-only imports (PEP 649 compatible)"
    - "asyncio.wait_for + contextlib.suppress(TimeoutError) poll-sleep idiom"
    - "Fail-fast infra checks before TaskGroup starts (D-05)"
    - "except* Exception as eg ExceptionGroup handling (TaskGroup Pitfall 9)"
    - "lru_cache(maxsize=1) for get_settings()"
key_files:
  created:
    - "src/provisioning_worker/__init__.py"
    - "src/provisioning_worker/__main__.py"
    - "src/provisioning_worker/main.py"
    - "src/provisioning_worker/settings.py"
    - "src/provisioning_worker/infrastructure/__init__.py"
    - "src/provisioning_worker/infrastructure/db.py"
    - "src/provisioning_worker/infrastructure/logging.py"
    - "src/provisioning_worker/infrastructure/observability.py"
    - "src/provisioning_worker/infrastructure/health_server.py"
    - "src/provisioning_worker/infrastructure/outbox_relay.py"
    - "src/provisioning_worker/ports/__init__.py"
    - "src/provisioning_worker/adapters/__init__.py"
    - "src/provisioning_worker/shared/__init__.py"
    - "src/provisioning_worker/events/__init__.py"
    - "src/provisioning_worker/modules/__init__.py"
    - "src/provisioning_worker/modules/provisioning/__init__.py"
    - "src/provisioning_worker/modules/provisioning/models.py"
    - "src/provisioning_worker/modules/provisioning/schemas.py"
    - "src/provisioning_worker/modules/provisioning/repository.py"
    - "src/provisioning_worker/modules/provisioning/service.py"
    - "src/provisioning_worker/modules/provisioning/handlers.py"
    - "src/provisioning_worker/modules/provisioning/tasks.py"
    - "src/provisioning_worker/modules/provisioning/spec.py"
    - "tests/__init__.py"
    - "tests/conftest.py"
    - "tests/provisioning/__init__.py"
    - "tests/test_health.py"
    - "tests/test_settings.py"
    - "tests/test_logging.py"
    - "tests/test_observability.py"
    - "tests/test_boot.py"
  modified: []
decisions:
  - "D-01: asyncio.TaskGroup crash-only supervision — any concern crash cancels all others"
  - "D-02: SIGTERM via loop.add_signal_handler → shutdown Event → all concerns exit → exit 0"
  - "D-03: main.py TaskGroup shape locked through Phases 2-5"
  - "D-04: real infra connections at boot + no-op consumer dispatch"
  - "D-05: fail-fast _check_postgres + _check_valkey before TaskGroup"
  - "D-06: structlog ConsoleRenderer in dev / JSONRenderer otherwise"
  - "D-07: always-on TracerProvider + psycopg + redis instrumentors + endpoint-gated OTLP"
  - "D-08: taskiq RedisStreamBroker.startup() connect-only, zero tasks registered"
  - "D-09: typed Settings full M1 var set + otel_enabled property"
  - "D-10: full module tree as docstring-only placeholders"
metrics:
  duration_seconds: 811
  completed_date: "2026-06-01"
  tasks_completed: 2
  tasks_total: 2
  files_created: 31
  files_modified: 0
---

# Phase 01 Plan 02: Walking Skeleton Summary

**One-liner:** asyncio.TaskGroup composition root with SIGTERM drain, aiohttp /healthz, structlog + OTel bootstrap, typed pydantic-settings, no-op Valkey Streams consumer, taskiq broker connect-only, and D-10 placeholder module tree — all with 10 passing unit tests.

## What Was Built

This plan delivers the walking skeleton: the complete boot path is real (all four asyncio concerns make real infrastructure connections), while the domain module tree is in place as docstring-only placeholders waiting to be filled in by Phases 2–5.

### Task 1: Settings, infrastructure (db, logging, observability, health_server, outbox_relay)

- `settings.py`: Full M1 var set (14 vars per D-09): `DATABASE_URL`, `DATABASE_URL_SYNC`, `VALKEY_URL`, `PROVISIONING_CONSUMER_GROUP`, `CONSUMER_NAME`, `DEPLOYMENT_ADAPTER`, `NOTIFICATION_TRANSPORT`, `HEALTH_PORT`, `OUTBOX_POLL_SECONDS`, `OUTBOX_BATCH_SIZE`, `INSTANCE_DOMAIN_SUFFIX`, `ODOO_BASE_IMAGE`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`; `otel_enabled` property; `lru_cache` `get_settings()`
- `infrastructure/db.py`: Lazy `get_engine()` singleton, `get_session_factory()`, `session_scope()` asynccontextmanager, `dispose_engine()` — mirrors platform-api/infrastructure/database.py without FastAPI `get_session` dep
- `infrastructure/logging.py`: `configure_logging()` with shared_processors, `ConsoleRenderer` in dev / `JSONRenderer` otherwise; no Granian silencing block (worker delta)
- `infrastructure/observability.py`: `_configured` idempotency guard, always-on `TracerProvider` + `PsycopgInstrumentor` + `RedisInstrumentor`; `BatchSpanProcessor` only when `otel_enabled`
- `infrastructure/health_server.py`: `AppRunner + TCPSite` (not `web.run_app`); `GET /healthz → 200 {"status":"ok"}`; logs "health server listening port=8001"
- `infrastructure/outbox_relay.py`: No-op poll loop with `asyncio.wait_for + contextlib.suppress(TimeoutError)`; logs "outbox relay started poll_seconds=1.0"
- Tests: `test_health.py` (in-process aiohttp smoke), `test_settings.py` (defaults + otel_enabled), `test_logging.py` (renderer type assertions), `test_observability.py` (TracerProvider installed + idempotent)

### Task 2: Entrypoint, composition root, placeholder modules, test_boot

- `__main__.py`: `asyncio.run(run(get_settings()))` wrapped in `except*`; `sys.exit(1)` on failure
- `main.py`: `run()` composition root — `configure_logging` → `configure_tracing` → log "provisioning-worker starting" → `_check_postgres` → `_check_valkey` → install signal handlers → `asyncio.TaskGroup` with four named tasks; `except* Exception as eg` ExceptionGroup handling; `finally: await dispose_engine()`
  - `_run_consumer`: XGROUP CREATE with BUSYGROUP tolerance + XREADGROUP loop (`block=1000`) + no-op XACK; logs "joined consumer group"
  - `_run_convergence`: `RedisStreamBroker.startup()` + `shutdown.wait()` + `broker.shutdown()`; logs "taskiq broker connected"
  - `_check_postgres`: `engine.connect()` + `SELECT 1`; fail-fast on error
  - `_check_valkey`: `client.ping()` + `aclose()`; fail-fast on error
- D-10 placeholder modules (docstring-only, zero imports): `ports/__init__.py`, `adapters/__init__.py`, `shared/__init__.py`, `events/__init__.py`, `modules/__init__.py`, `modules/provisioning/__init__.py`, `models.py`, `schemas.py`, `repository.py`, `service.py`, `handlers.py`, `tasks.py`, `spec.py`
- `tests/test_boot.py`: `main.run()` with patched infra checks and consumer/convergence concerns; `asyncio.Event` patched with auto-shutdown; asserts clean return

## Verification Evidence

1. `make test` → 10 passed in 0.74s (all unit tests, no Docker)
2. `make check` → All checks passed; 23 files already formatted
3. `grep -rn "from __future__" src/ tests/` → 0 matches
4. `grep -c "asyncio.TaskGroup" main.py` → 3 (declaration + TaskGroup block + two related references)
5. `grep -c "except\* Exception" main.py` → 1
6. `grep -c "web.run_app" health_server.py` → 0 (uses AppRunner)
7. `grep -c "block=1000" main.py` → 1 (XREADGROUP block timeout)
8. `grep -l "^import\|^from" modules/provisioning/*.py` → empty (all are docstring-only)

## Deviations from Plan

### Rule 1 (Auto-fix) — structlog ProcessorFormatter attribute name

**Found during:** Task 1 (test_logging.py authoring)
**Issue:** The initial test asserted `formatter.processor` but `ProcessorFormatter` in structlog 25.* stores the renderer as the last element of `formatter.processors` (tuple), not a `.processor` attribute.
**Fix:** Updated test assertions to use `formatter.processors[-1]` to check the renderer type.
**Files modified:** `tests/test_logging.py`
**Commit:** 0366f94

### Rule 1 (Auto-fix) — Multiple ruff lint fixes

**Found during:** Both tasks (ruff check passes)
**Issues:** `TC001`/`TC002`/`TC003` (imports used only in annotations should be in TYPE_CHECKING with PEP 649); `UP037` (unquoted annotations when using TYPE_CHECKING); `E501` line-too-long in placeholder docstrings; `PLC0415` imports at function scope in tests; `S104` binding to 0.0.0.0; `PLR2004` magic values in tests.
**Fix:** Restructured all infrastructure files to use TYPE_CHECKING properly (annotation-only imports), moved test imports to top-level, added `_DEFAULT_HEALTH_PORT` and `_HTTP_OK` named constants, added `# noqa: S104` for the intentional 0.0.0.0 binding, trimmed long docstrings.
**Files modified:** All infrastructure + test files
**Commit:** 0366f94, 7b53d61

## Known Stubs

- `infrastructure/outbox_relay.py`: poll loop body is a no-op comment `# Phase 4 will replace this with...` — intentional. Phase 4 will add `event_outbox` table and the real drain. Documented in docstring.
- `modules/provisioning/*.py`: all seven domain files contain only module docstrings. Intentional per D-10. Phase 2 fills handlers.py, Phase 3 fills models/schemas/repository/service/tasks/spec.py.
- `main._run_consumer`: dispatches to no-op logging + XACK (no envelope parsing, no instance creation). Intentional Phase 1 placeholder per D-04. Phase 2 adds real dispatch.
- `main._run_convergence`: broker connect-only, zero Taskiq tasks registered. Intentional per D-08. Phase 3 adds real tasks.

## Threat Flags

No new security-relevant surface beyond what the threat model anticipated:
- T-01-04 mitigated: `block=1000` in XREADGROUP loop
- T-01-05 mitigated: AppRunner + TCPSite (not web.run_app)
- T-01-06 mitigated: structlog structured key-value; no format-string interpolation
- T-01-08 accepted: /healthz unauthenticated by design (internal liveness probe)
- T-01-09 mitigated: `except* Exception as eg` handles ExceptionGroup correctly
- T-01-10 mitigated: `_check_postgres` + `_check_valkey` fail-fast at boot

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | 0366f94 | feat(01-02): add settings, infrastructure, and test suite (Task 1) |
| Task 2 | 7b53d61 | feat(01-02): add entrypoint, composition root, placeholder modules, test_boot (Task 2) |

## Self-Check: PASSED
