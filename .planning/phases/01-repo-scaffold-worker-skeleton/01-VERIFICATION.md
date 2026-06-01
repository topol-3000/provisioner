---
phase: 01-repo-scaffold-worker-skeleton
verified: 2026-06-01T16:00:00Z
status: human_needed
score: 5/5
overrides_applied: 0
human_verification:
  - test: "Run: make migrate (against a running platform-infra postgres)"
    expected: "Exit 0; make psql shows provisioning.alembic_version table exists"
    why_human: "Requires live Postgres (platform-infra); @pytest.mark.integration, off the PR gate per 01-VALIDATION.md and D-15"
  - test: "Push a PR branch to GitHub and check the Actions run"
    expected: "All three CI jobs pass: lint (ruff check + ruff format --check), test (10 unit tests, no testcontainers), build (docker/build-push-action, push: false)"
    why_human: "Requires GitHub Actions runner; cannot reproduce in local unit suite (01-VALIDATION.md Manual-Only Verifications)"
  - test: "Run: make run (with platform-infra up) and observe the four boot log lines, then curl http://localhost:8001/healthz and send Ctrl-C"
    expected: "Four INFO lines in order: 'provisioning-worker starting', 'joined consumer group', 'outbox relay started', 'health server listening'; /healthz returns 200 {'status':'ok'}; Ctrl-C causes clean drain and exit 0"
    why_human: "Requires live Postgres and Valkey (fail-fast infra checks block boot without them); the in-process tests cover all four behaviors but a live end-to-end run has not been confirmed with real infra yet"
---

# Phase 1: Repo Scaffold & Worker Skeleton — Verification Report

**Phase Goal:** A fresh checkout can `uv sync`, `make migrate` against a clean `provisioning` schema, `make run` to boot the worker (which logs `starting`, serves `/healthz`, and shuts down cleanly), and pass `make check` + an empty `make test` — with CI green.
**Verified:** 2026-06-01T16:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `uv sync --frozen --extra dev` installs the pinned stack from a committed `uv.lock`; `make check` passes | VERIFIED | `uv sync --frozen --extra dev` exit 0, 63 packages; `make check` exit 0 (ruff check all passed, 23 files formatted) |
| 2 | `make run` boots, logs structured `starting` line, `/healthz` answers 200 `{"status":"ok"}` on `HEALTH_PORT`, Ctrl-C drains and exits 0 | VERIFIED (automated) + human_needed (live infra) | test_health.py pass (in-process /healthz); test_boot.py passes (mock infra, SIGTERM drain); log.info("provisioning-worker starting") in main.py; live end-to-end run requires platform-infra |
| 3 | The four concerns start (consumer / convergence+Taskiq / outbox relay / health) even though handlers not implemented — each logs that it started | VERIFIED | "joined consumer group" in main.py _run_consumer; "taskiq broker connected" in _run_convergence; "outbox relay started" in outbox_relay.py; "health server listening" in health_server.py; test_boot.py 2/2 tests pass confirming clean run() with four concerns |
| 4 | `make migrate` succeeds against empty `provisioning` schema; `make revision` emits file with no `from __future__ import annotations` | VERIFIED (static) + human_needed (live migrate) | `grep -c "from __future__" migrations/provisioning/script.py.mako` = 0; `grep -c "from __future__" migrations/provisioning/env.py` = 0; alembic.ini `version_table_schema = provisioning` confirmed; live `make migrate` requires platform-infra |
| 5 | CI runs lint + test + a Docker build and is green | VERIFIED (static) + human_needed (runner) | `.github/workflows/ci.yml` valid YAML; 3 jobs (lint/test/build); `push: false`; test uses `-m "not integration"`; actual CI runner execution requires GitHub Actions environment |

**Score:** 5/5 truths verified (3 fully automated, 2 partially automated with human verification required for live-infra and CI-runner components)

### Deferred Items

Items not yet fully met but explicitly addressed in later milestone phases. These are parts of the OBS-01 requirement that extend beyond Phase 1 scope.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | Handlers bind `envelope_id`/`subscription_id`/`instance_id`/`correlation_id` via `bind_contextvars` | Phase 2 | Phase 2 goal: "handlers are observable no-op stubs that prove delivery + dedupe"; real handler code not written until Phase 2 |
| 2 | Consumer lag, convergence duration, outbox backlog exposed as metrics | Phase 5 | Phase 5 ROADMAP: "Requirements: OBS-01 (metrics)"; Phase 5 SC-5: "Consumer lag, convergence duration per task_type, and outbox backlog are exposed as metrics; logs carry the bound event/instance context" |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `pyproject.toml` | Build backend + runtime deps + ruff + pytest config | VERIFIED | hatchling>=1.27; pydantic==2.13.*, redis>=5, taskiq==0.12.*, taskiq-redis==1.2.*, ruff==0.15.*; asyncio_mode=auto; strict-markers/config |
| `uv.lock` | Committed lock file; `uv sync --frozen` works | VERIFIED | 63 packages resolved from CPython 3.14.4; uv sync exit 0 |
| `.python-version` | Contains "3.14" | VERIFIED | Single line `3.14` |
| `Makefile` | run, test, check, migrate, revision, docker-build, docker-migrate, psql | VERIFIED | All targets present: run, test, test-integration, check, migrate, revision, docker-build, docker-migrate, psql |
| `alembic.ini` | Single [provisioning] section with version_table_schema=provisioning | VERIFIED | `version_table_schema = provisioning` confirmed; default [alembic] section points at non-existent path (forces -n) |
| `migrations/provisioning/script.py.mako` | No `from __future__ import annotations` | VERIFIED | `grep -c "from __future__"` = 0 |
| `migrations/provisioning/env.py` | Imports get_settings, SCHEMA="provisioning", target_metadata=None | VERIFIED | All three confirmed present |
| `src/provisioning_worker/main.py` | asyncio.TaskGroup with four concerns + SIGTERM drain | VERIFIED | 3 references to asyncio.TaskGroup; 1 `except* Exception as eg`; run_health_server and run_outbox_relay both referenced twice (import + tg.create_task) |
| `src/provisioning_worker/settings.py` | Full M1 var set (14 vars), otel_enabled property, lru_cache get_settings | VERIFIED | All 14 vars present; `otel_enabled` property present (2 references); lru_cache pattern present |
| `src/provisioning_worker/infrastructure/health_server.py` | AppRunner + TCPSite, GET /healthz, no web.run_app | VERIFIED | AppRunner present (1 ref); web.run_app = 0 matches; test_health.py passes |
| `src/provisioning_worker/infrastructure/logging.py` | configure_logging, ConsoleRenderer (dev) / JSONRenderer (non-dev) | VERIFIED | configure_logging present; JSONRenderer and ConsoleRenderer both in file; test_logging.py passes |
| `src/provisioning_worker/infrastructure/observability.py` | configure_tracing, _configured guard, PsycopgInstrumentor, RedisInstrumentor | VERIFIED | All four confirmed; test_observability.py passes |
| `src/provisioning_worker/infrastructure/outbox_relay.py` | asyncio.wait_for + contextlib.suppress, logs "outbox relay started" | VERIFIED | Both patterns confirmed in source |
| `src/provisioning_worker/__main__.py` | asyncio.run(run(get_settings())) | VERIFIED | Exact call confirmed |
| `Dockerfile` | Two-stage, python:3.14-slim-trixie, ENTRYPOINT python -m provisioning_worker, EXPOSE 8001, non-root uid 10001 | VERIFIED | All confirmed: ENTRYPOINT ["python", "-m", "provisioning_worker"]; EXPOSE 8001; USER platform; groupadd --gid 10001 |
| `docker-compose.yml` | platform-net (external), env_file, port 8001 | VERIFIED | platform-net external confirmed; env_file present; port 8001 |
| `.github/workflows/ci.yml` | Three jobs (lint/test/build), push: false, no testcontainers | VERIFIED | Jobs: lint, test, build; push: false; test uses `-m "not integration"` |
| `.env.example` | 14 M1 vars with defaults | VERIFIED | All 14 vars confirmed: ENVIRONMENT, LOG_LEVEL, DATABASE_URL, DATABASE_URL_SYNC, VALKEY_URL, PROVISIONING_CONSUMER_GROUP, CONSUMER_NAME, DEPLOYMENT_ADAPTER, NOTIFICATION_TRANSPORT, HEALTH_PORT, OUTBOX_POLL_SECONDS, OUTBOX_BATCH_SIZE, INSTANCE_DOMAIN_SUFFIX, ODOO_BASE_IMAGE |
| `tests/test_health.py` | In-process /healthz → 200 {"status":"ok"} | VERIFIED | 1 test passes |
| `tests/test_settings.py` | Missing-var raises; otel_enabled true/false | VERIFIED | 3 tests pass |
| `tests/test_logging.py` | JSON vs ConsoleRenderer selection | VERIFIED | 2 tests pass |
| `tests/test_observability.py` | TracerProvider installed, idempotent | VERIFIED | 2 tests pass |
| `tests/test_boot.py` | main.run() clean return with mocked infra | VERIFIED | 2 tests pass |
| `src/provisioning_worker/modules/provisioning/*.py` (7 files) | Docstring-only placeholders, zero imports | VERIFIED | `grep -l "^import\|^from"` on modules/provisioning/*.py returns empty (exit 1 = no matches) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `alembic.ini [provisioning]` | `migrations/provisioning/` | `script_location = migrations/provisioning` | WIRED | Confirmed in alembic.ini |
| `migrations/provisioning/env.py` | `provisioning_worker.settings.get_settings` | `config.set_main_option(sqlalchemy.url, get_settings())` | WIRED | `from provisioning_worker.settings import get_settings` + call confirmed |
| `src/provisioning_worker/__main__.py` | `src/provisioning_worker/main.py run()` | `asyncio.run(run(get_settings()))` | WIRED | Exact call confirmed |
| `src/provisioning_worker/main.py` | `infrastructure/health_server.py` | `tg.create_task(run_health_server(...))` | WIRED | Import + tg.create_task both confirmed |
| `src/provisioning_worker/main.py` | `infrastructure/outbox_relay.py` | `tg.create_task(run_outbox_relay(...))` | WIRED | Import + tg.create_task both confirmed |
| `infrastructure/observability.py` | `opentelemetry.instrumentation.psycopg.PsycopgInstrumentor` | `PsycopgInstrumentor().instrument()` | WIRED | Import + call confirmed |
| `infrastructure/observability.py` | `opentelemetry.instrumentation.redis.RedisInstrumentor` | `RedisInstrumentor().instrument()` | WIRED | Import + call confirmed |
| `docker-compose.yml worker service` | `Dockerfile` | `build: context: .` | WIRED | `build:` confirmed in docker-compose.yml |
| `.github/workflows/ci.yml build job` | `Dockerfile` | `docker/build-push-action context: .` | WIRED | `build-push-action` confirmed in ci.yml |

### Data-Flow Trace (Level 4)

Not applicable — Phase 1 is a walking skeleton. No domain data flows through the system; the consumer loop is a no-op (no envelope parsing, no state changes). The data-flow trace applies to phases that render dynamic domain data.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `uv sync --frozen --extra dev` installs pinned stack | `uv sync --frozen --extra dev` | exit 0, 63 packages | PASS |
| `make check` (ruff check + format --check) | `make check` | exit 0, all checks passed, 23 files | PASS |
| `make test` (10 unit tests, no Docker) | `make test` | 10 passed in 0.74s | PASS |
| No `from __future__ import annotations` in src/ or tests/ | `grep -rn "from __future__" src/ tests/` | 0 matches | PASS |
| No `from __future__ import annotations` in migrations/ | `grep -c "from __future__" migrations/provisioning/script.py.mako` | 0 | PASS |
| asyncio.TaskGroup present in main.py | `grep -c "asyncio.TaskGroup" main.py` | 3 | PASS |
| `except* Exception` ExceptionGroup handler present | `grep -c "except\* Exception" main.py` | 1 | PASS |
| health_server.py uses AppRunner (not web.run_app) | `grep -c "web.run_app" health_server.py` | 0 | PASS |
| XREADGROUP block=1000 present | `grep -c "block=1000" main.py` | 1 | PASS |
| Placeholder modules contain zero imports | `grep -l "^import\|^from" modules/provisioning/*.py` | empty (exit 1 = no files match) | PASS |
| CI YAML valid | `python3 -c "import yaml; yaml.safe_load(...ci.yml)"` | no error, 3 jobs | PASS |
| `push: false` in CI | `grep "push: false" ci.yml` | found | PASS |
| `.env` in `.gitignore` | `grep "^\.env$" .gitignore` | `.env` | PASS |
| All 14 M1 vars in `.env.example` | `grep -E "ENVIRONMENT|LOG_LEVEL|..." .env.example` | 14 vars confirmed | PASS |
| All 14 M1 settings fields in `settings.py` | `grep -E "provisioning_consumer_group|..." settings.py` | all 14 confirmed | PASS |
| No TBD/FIXME/XXX debt markers in src/tests/migrations | `grep -rn "TBD\|FIXME\|XXX" src/ tests/ migrations/` | 0 matches | PASS |

### Probe Execution

No probe scripts defined for this phase. Phase 1 is a toolchain scaffold — the spot-checks above serve the equivalent function.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SCAF-01 | 01-01, 01-03 | Repo builds clean: pyproject.toml, uv.lock, Makefile, ruff, pytest, Dockerfile, CI | SATISFIED | `make check` exit 0; `make test` 10 passed; ci.yml present and valid; Dockerfile present with correct ENTRYPOINT |
| SCAF-02 | 01-02 | `make run` boots four concerns, logs starting line, SIGTERM drains clean | SATISFIED (automated) | test_boot.py 2/2 pass; "provisioning-worker starting" + four concern log lines in source; live run human-verification pending |
| SCAF-03 | 01-02 | Typed Settings, fail-fast on missing var, .env.example | SATISFIED | test_settings.py 3/3 pass; 14 M1 fields in settings.py; .env.example complete |
| SCAF-04 | 01-02 | GET /healthz → 200 {"status":"ok"} | SATISFIED | test_health.py 1/1 pass; AppRunner/TCPSite confirmed |
| SCAF-05 | 01-01 | Single Alembic tree, version_table_schema=provisioning, no from __future__, make migrate | SATISFIED (static) | alembic.ini confirmed; script.py.mako 0 __future__ lines; live migrate human-verification pending |
| OBS-01 | 01-02 | structlog JSON (non-dev), merge_contextvars in shared_processors, OTel bootstrap (OTLP optional) | SATISFIED (Phase 1 scope) | test_logging.py 2/2 pass; test_observability.py 2/2 pass; bind_contextvars in handlers and metrics deferred to Phase 2 and Phase 5 per ROADMAP |

Note on OBS-01: REQUIREMENTS.md marks OBS-01 as `[x]` shipped. Phase 1 delivers the bootstrap (structlog JSON renderer selection, merge_contextvars processor in shared_processors chain, always-on TracerProvider + OTLP gated exporter). The per-handler `bind_contextvars` calls and operational metrics (consumer lag, convergence duration, outbox backlog) are explicitly addressed in Phase 2 (handlers) and Phase 5 (OBS-01 metrics sub-requirement per ROADMAP Phase 5 SC-5 and Requirements row). This is not a Phase 1 gap.

### Anti-Patterns Found

No blockers. All WARNING/INFO findings are from the code review (01-REVIEW.md) and are advisory per the verification instructions.

| File | Finding | Severity | Impact |
|------|---------|----------|--------|
| `src/provisioning_worker/__main__.py:14-15` | WR-01: `except* Exception` never catches `SystemExit(1)` from `run()` — dead code on the crash path (exit 1 still occurs via propagation, so exit contract is correct) | WARNING | Advisory — exit code behavior is correct by accident; fix recommended for clarity |
| `src/provisioning_worker/main.py:55-74` | WR-02: `_check_postgres`/`_check_valkey` called before `try/finally` that disposes engine — engine leaked on fail-fast exit (process exits anyway) | WARNING | Advisory — low impact since process exits; fix recommended per clean shutdown contract |
| `src/provisioning_worker/main.py:56-72` | WR-03: `except*` would not catch plain exceptions from connectivity checks if moved inside the try | WARNING | Advisory — related to WR-02; no current behavioral bug |
| `src/provisioning_worker/main.py:90-128` | WR-04: Consumer redis client leaked if xreadgroup/xack raises mid-loop | WARNING | Advisory — crash-only process; `filterwarnings=["error"]` could surface this in future tests |
| `src/provisioning_worker/main.py:143-149` | WR-05: `_run_convergence` broker leaked if `broker.startup()` raises | WARNING | Advisory — crash-only process; same pattern as WR-04 |
| `tests/test_boot.py:52-64` | WR-06: `patch("asyncio.Event", _AutoShutdownEvent)` is process-wide and couples test to single-Event assumption in run() | WARNING | Advisory — test works today; fragile if run() ever constructs a second Event |
| `migrations/provisioning/env.py:66` | IN-03: `run_migrations_online` hardcodes `version_table="alembic_version"` instead of reading from config section (no behavioral bug since values match) | INFO | Cosmetic — safe to defer |

No TBD, FIXME, or XXX markers found in any source file (confirmed by grep — 0 matches). No stub classifications apply to the placeholder modules in `modules/provisioning/` — they are intentionally docstring-only per D-10 and are not in the rendering/data path.

### Human Verification Required

#### 1. make migrate (live Postgres)

**Test:** With platform-infra running, execute `make migrate` against the empty `provisioning` schema, then `make psql` and run `\dt provisioning.*`
**Expected:** `make migrate` exits 0; `\dt provisioning.*` shows `alembic_version` table exists in the `provisioning` schema
**Why human:** Requires live Postgres (platform-infra docker-compose). This is `@pytest.mark.integration` and is explicitly off the PR gate per D-15 and 01-VALIDATION.md "Manual-Only Verifications"

#### 2. CI GitHub Actions run

**Test:** Push a PR branch to GitHub and observe the Actions workflow run
**Expected:** All three CI jobs pass: lint (ruff check + ruff format --check), test (10 passed, no testcontainers), build (docker/build-push-action, push: false, exit 0)
**Why human:** Requires GitHub Actions runner environment. Explicitly a manual verification item per 01-VALIDATION.md

#### 3. Live end-to-end make run (optional, full smoke)

**Test:** With platform-infra running (Postgres + Valkey), execute `make run` and observe the boot log, then `curl http://localhost:8001/healthz`, then `Ctrl-C`
**Expected:** Four INFO lines in order: "provisioning-worker starting environment=dev deployment_adapter=fake", "joined consumer group group=cg.provisioning-convergence stream=events.subscription consumer=worker-1", "outbox relay started poll_seconds=1.0", "health server listening port=8001"; curl returns 200 `{"status":"ok"}`; Ctrl-C causes clean drain and exit 0
**Why human:** Fail-fast infra checks block boot without live Postgres and Valkey. The in-process tests (test_boot.py, test_health.py) cover all four boot behaviors with mocked/in-process infrastructure, but a live end-to-end run confirms the complete integration path

### Gaps Summary

No gaps. All automated must-haves are VERIFIED. The two outstanding human verification items (live `make migrate` and CI runner) are explicit integration/CI-gate exceptions per 01-VALIDATION.md "Manual-Only Verifications" — they are not code deficiencies. The phase goal is substantively achieved for everything automatable without live infrastructure.

The review findings (WR-01 through WR-06, IN-01 through IN-05) are advisory warnings from 01-REVIEW.md and do not block the phase goal — they are cleanup items for Phase 2 or beyond.

---

_Verified: 2026-06-01T16:00:00Z_
_Verifier: Claude (gsd-verifier)_
