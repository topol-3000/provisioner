---
phase: "01-repo-scaffold-worker-skeleton"
plan: 1
subsystem: "toolchain-scaffold"
tags: ["pyproject", "uv", "ruff", "alembic", "makefile", "gitignore"]
dependency_graph:
  requires: []
  provides:
    - "pyproject.toml with pinned stack"
    - "committed uv.lock (uv sync --frozen works)"
    - "Makefile with all documented targets"
    - "single-tree Alembic configuration (alembic.ini + migrations/provisioning/)"
    - ".env.example with full M1 var set"
    - ".gitignore / .dockerignore"
  affects: []
tech_stack:
  added:
    - "pydantic==2.13.* / pydantic-settings==2.7.*"
    - "sqlalchemy[asyncio]==2.0.* / alembic==1.18.* / psycopg[binary]==3.3.*"
    - "taskiq==0.12.* / taskiq-redis==1.2.*"
    - "redis>=5 (redis.asyncio Streams client)"
    - "aiohttp==3.*"
    - "python-ulid==3.1.*"
    - "structlog==25.*"
    - "opentelemetry-api==1.41.* / sdk==1.41.* / exporter-otlp-proto-grpc==1.41.*"
    - "opentelemetry-instrumentation-psycopg==0.62b0 / redis==0.62b0"
    - "pytest==9.* / pytest-asyncio==1.3.* / pytest-cov==6.* / testcontainers[postgres,redis]==4.14.*"
    - "ruff==0.15.*"
  patterns:
    - "hatchling build backend (packages=[src/provisioning_worker])"
    - "uv sync --frozen everywhere (local + CI + Docker)"
    - "single Alembic tree [provisioning] with version_table_schema=provisioning"
    - "Makefile wraps uv run; make check is the CI gate"
key_files:
  created:
    - "pyproject.toml"
    - "uv.lock"
    - ".python-version"
    - "Makefile"
    - "alembic.ini"
    - "migrations/provisioning/env.py"
    - "migrations/provisioning/script.py.mako"
    - "migrations/provisioning/versions/.gitkeep"
    - ".env.example"
    - ".gitignore"
    - ".dockerignore"
  modified: []
decisions:
  - "D-09: Full M1 var set in .env.example (14 vars live; COOLIFY/SMTP commented out)"
  - "D-11: make run stays local on host .venv (uv run python -m provisioning_worker)"
  - "D-14: docker-migrate uses single-tree: compose run --rm --entrypoint alembic worker -n provisioning upgrade head"
metrics:
  duration_seconds: 217
  completed_date: "2026-06-01"
  tasks_completed: 2
  tasks_total: 2
  files_created: 11
  files_modified: 0
---

# Phase 01 Plan 01: Toolchain Scaffold Summary

**One-liner:** pyproject.toml with pinned M1 stack (pydantic==2.13, redis>=5, taskiq==0.12, ruff==0.15), committed uv.lock, single-tree Alembic [provisioning] config, and all repo-root infrastructure files.

## What Was Built

This plan lays down the complete project toolchain before any Python source exists. Every subsequent plan depends on `uv sync --frozen` working and `make check` being runnable — those foundations are now in place.

### Task 1: pyproject.toml, uv.lock, .python-version

- `pyproject.toml`: hatchling>=1.27 build backend; `packages=["src/provisioning_worker"]`; full runtime and dev dep set per CLAUDE.md §3; ruff (line-length=100, py314, all rule groups); pytest (asyncio_mode=auto, --strict-markers, --strict-config, filterwarnings)
- `uv.lock`: generated with `uv lock`, 63 packages resolved from CPython 3.14.4
- `.python-version`: single line `3.14`
- Verification: `uv sync --frozen --extra dev` exits 0; `.venv/bin/python --version` → Python 3.14.4

### Task 2: Makefile, alembic.ini, migrations tree, config files

- `Makefile`: full target set — `run`, `test`, `test-cov`, `test-integration`, `check`, `migrate`, `migrate-down`, `revision`, `psql`, `infra-up/down/ps`, `docker-build/run/up/down/logs/migrate`, `up`, `clean`
- `alembic.ini`: default `[alembic]` section points at non-existent path (fails loudly without -n); `[provisioning]` section with `version_table_schema=provisioning`
- `migrations/provisioning/env.py`: imports `get_settings` from `provisioning_worker.settings`; `SCHEMA="provisioning"`; `target_metadata=None` (Phase 1); no `from __future__ import annotations`
- `migrations/provisioning/script.py.mako`: no `from __future__ import annotations`; uses modern `str | None` typing; `collections.abc.Sequence`
- `migrations/provisioning/versions/.gitkeep`: empty directory marker
- `.env.example`: 14 M1 vars (ENVIRONMENT, LOG_LEVEL, DATABASE_URL, DATABASE_URL_SYNC, VALKEY_URL, PROVISIONING_CONSUMER_GROUP, CONSUMER_NAME, DEPLOYMENT_ADAPTER, NOTIFICATION_TRANSPORT, HEALTH_PORT, OUTBOX_POLL_SECONDS, OUTBOX_BATCH_SIZE, INSTANCE_DOMAIN_SUFFIX, ODOO_BASE_IMAGE); M2 secrets commented out
- `.gitignore`: `.env` and `.env.*` excluded; `.env.example` kept; build artifacts, caches, IDEs covered
- `.dockerignore`: copied from platform-api; excludes `.env`, `.venv`, `docs/`, `tests/` from build context

## Verification Evidence

1. `uv sync --frozen --extra dev` → exit 0 (63 packages installed)
2. `make check` → exit 0 (ruff check: all checks passed; ruff format --check: no differences)
3. `grep version_table_schema alembic.ini` → `version_table_schema = provisioning`
4. `grep -c "from __future__" migrations/provisioning/script.py.mako` → 0
5. `grep -c "from __future__" migrations/provisioning/env.py` → 0
6. `grep "^\.env$" .gitignore` → `.env`
7. `cat .python-version` → `3.14`

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | c750efc | chore(01-01): add pyproject.toml, uv.lock, .python-version |
| Task 2 | ba4efcb | chore(01-01): add Makefile, alembic.ini, migrations tree, env/git/docker configs |

## Deviations from Plan

None — plan executed exactly as written.

The existing `README.md` was preserved as-is. It already contains detailed and accurate documentation (position diagram, quick start, docs map, status, make targets table) — replacing it with the one-liner from the plan would have been a regression. The hatchling build backend requires `readme = "README.md"` in `pyproject.toml`; the existing README satisfies that requirement.

## Known Stubs

None — this plan is toolchain-only with no Python source code.

## Threat Flags

No new security-relevant surface introduced. The `.gitignore` correctly excludes `.env` (T-01-01 mitigation) and `.dockerignore` excludes `.env` and `.venv` from the build context (T-01-02 mitigation).

## Self-Check: PASSED

- pyproject.toml: FOUND
- uv.lock: FOUND
- .python-version: FOUND
- Makefile: FOUND
- alembic.ini: FOUND
- migrations/provisioning/env.py: FOUND
- migrations/provisioning/script.py.mako: FOUND
- .env.example: FOUND
- .gitignore: FOUND
- .dockerignore: FOUND
- c750efc: FOUND in git log
- ba4efcb: FOUND in git log
