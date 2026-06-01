# Phase 1: Repo scaffold & worker skeleton - Context

**Gathered:** 2026-06-01
**Status:** Ready for planning

<domain>
## Phase Boundary

A fresh checkout can `uv sync --frozen --extra dev`, `make migrate` against the
empty `provisioning` schema, `make run` to boot `python -m provisioning_worker`
(four concurrent concerns under one asyncio loop — Streams consumer, convergence
+ Taskiq, outbox relay, `/healthz`), serve `GET /healthz` → 200
`{"status":"ok"}`, drain cleanly on SIGTERM, and pass `make check` + an empty
`make test` with green CI. **No real handlers, no state machine, no event
parsing, no instance rows** — those are Phase 2+. This phase delivers the
skeleton the rest of the milestone fills in.

**In scope:** SCAF-01..05 + OBS-01 (logging/OTel bootstrap only; **metrics are
Phase 5**). Pinned `pyproject.toml` + committed `uv.lock`, `Makefile`,
ruff/pytest config, `.env.example`, `.gitignore`, `.dockerignore`, the
`python -m provisioning_worker` entrypoint + composition root, typed `Settings`,
the `/healthz` aiohttp server, the structlog + OTel bootstrap, the single
`provisioning` Alembic tree (wired, no domain tables yet), the Dockerfile +
`docker-compose.yml` + GitHub Actions CI.

**Explicitly NOT in scope:** envelope/payload models, the consumer's
dispatch-on-type + idempotency (Phase 2), any `provisioning.*` domain tables and
the state machine (Phase 3), the outbox schema + real relay publishing
(Phase 4), the `instance.*` catalog (Phase 4/5), metrics (Phase 5), Coolify/SMTP
(M2).

</domain>

<decisions>
## Implementation Decisions

### Concern supervision & lifecycle
- **D-01:** The four concerns run inside a single `asyncio.TaskGroup` started
  from the composition root (`main.py`). **Crash-only**: if any concern raises a
  fatal error, the TaskGroup cancels the other three, the DB/Valkey pools close,
  and the process exits **non-zero** — the orchestrator (or `make run`)
  restarts it. No in-process per-concern restart loops.
- **D-02:** SIGTERM / Ctrl-C triggers a **clean drain**: stop accepting new work,
  let in-flight work settle, close the Valkey/Postgres pools, exit **0**. Install
  signal handling via `loop.add_signal_handler` (or equivalent) in `main.py`.
- **D-03:** This is the load-bearing skeleton decision — `main.py`'s
  TaskGroup-based supervision shape must hold unchanged through Phases 2–5
  (the concerns gain bodies, not a new supervision model).

### Boot fidelity (the Phase-1 / Phase-2 line)
- **D-04:** At boot the four concerns make **real** infrastructure connections,
  reproducing the documented boot log (`docs/local-development.md` §Running):
  - SQLAlchemy async engine opened (psycopg driver).
  - Consumer does `XGROUP CREATE … MKSTREAM` (idempotent; `BUSYGROUP` tolerated)
    on `events.subscription` for `cg.provisioning-convergence`, then enters the
    `XREADGROUP` loop — but **dispatches to no-op handlers** (logs the receipt,
    `XACK`s; no parsing, no instance creation). Handler bodies are Phase 2.
  - Outbox relay loop runs and polls the (empty) outbox at `OUTBOX_POLL_SECONDS`.
    (The `event_outbox` **table** does not exist until Phase 4 — in Phase 1 the
    relay either polls nothing or short-circuits gracefully; the planner decides
    the cleanest no-op that doesn't require the table.)
  - taskiq-redis broker constructed from `VALKEY_URL` and `startup()`-ed (see
    D-08).
- **D-05:** **Fail-fast on unreachable infra.** If Postgres or Valkey can't be
  reached at boot, log an `error` and exit **non-zero** — do **not** retry-wait.
  Consistent with crash-only (D-01): the supervisor/orchestrator owns restart.
  This pairs with `Settings` failing fast on missing/invalid env (SCAF-03).

### Observability bootstrap (OBS-01, bootstrap only)
- **D-06:** `Settings.environment: Literal["dev","staging","prod"] = "dev"`
  drives structlog rendering — `ConsoleRenderer` in `dev`, JSON renderer
  otherwise. `LOG_LEVEL: Literal["DEBUG","INFO","WARNING","ERROR"] = "INFO"`.
  **No dedicated `LOG_FORMAT` var** (mirrors platform-api). `bind_contextvars`
  helper wiring is present; per-handler binding of
  `envelope_id`/`subscription_id`/`instance_id`/`correlation_id` lands with the
  handlers (Phase 2+).
- **D-07:** `infrastructure/observability.py` mirrors platform-api's
  `infrastructure/observability.py`: **always** install the `TracerProvider`
  (resource = `OTEL_SERVICE_NAME`, default `provisioning-worker`) and instrument
  **now even without a backend** — psycopg + redis instrumentors enabled in
  Phase 1; the **aiohttp-client** instrumentor is reserved for the M2 Coolify
  client (the aiohttp `/healthz` *server* is not instrumented). The OTLP
  `BatchSpanProcessor` exporter is added **only** when
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set (an `otel_enabled` computed property on
  `Settings`). **Metrics (consumer lag, convergence duration, outbox backlog)
  are explicitly Phase 5** — not wired here.

### Taskiq wiring depth
- **D-08:** **Broker connect-only.** The "convergence + Taskiq" concern
  constructs the taskiq-redis broker from `VALKEY_URL`, calls `broker.startup()`
  at boot, registers **zero** tasks, and logs that the broker connected. The
  in-process Taskiq listener/receiver and the real retry/backoff tasks land in
  **Phase 3** (where PROV-04 needs them). (platform-api is HTTP-only and gave no
  worker-loop precedent to mirror — this is our call.)

### Settings & .env.example scope
- **D-09:** **Full M1 var set now.** `settings.py` defines the complete
  milestone-1 environment (with defaults + `Literal` validation where the value
  space is closed) so Phases 2–5 only consume, never redefine:
  `DATABASE_URL`, `DATABASE_URL_SYNC`, `VALKEY_URL`,
  `PROVISIONING_CONSUMER_GROUP` (default `cg.provisioning-convergence`),
  `CONSUMER_NAME`, `DEPLOYMENT_ADAPTER: Literal["fake","coolify"] = "fake"`,
  `NOTIFICATION_TRANSPORT: Literal["console","smtp"] = "console"`,
  `HEALTH_PORT = 8001`, `OUTBOX_POLL_SECONDS`, `OUTBOX_BATCH_SIZE`,
  `INSTANCE_DOMAIN_SUFFIX`, `ODOO_BASE_IMAGE`, `ENVIRONMENT`, `LOG_LEVEL`,
  `OTEL_*`. `.env.example` lists them with the M1 defaults; **M2/secret vars
  (`COOLIFY_API_URL`, `COOLIFY_API_TOKEN`, `SMTP_*`, per-instance token config)
  are commented out**. `pydantic-settings` loads `env_file=".env"`.

### Repo / scaffold shape
- **D-10:** **Full module tree as docstring-only placeholders.** Lay down the
  entire layout from `docs/architecture.md` §Code layout now —
  `infrastructure/`, `ports/`, `adapters/`, `shared/`, `events/`, and
  `modules/provisioning/{models,schemas,repository,service,handlers,tasks,spec}.py`
  — as modules containing only a Google-style module docstring (no premature
  `Protocol`, model, or function definitions; those land in their owning phase).
  `tests/` mirrors the tree. This makes the dependency rule visible from day one
  and gives later phases a clear home. The boot path itself
  (`__main__.py`, `main.py`, `settings.py`,
  `infrastructure/{logging,observability,db,health_server,outbox_relay}.py`) is
  real, not a placeholder.

### Docker (mirror platform-api exactly)
- **D-11:** `make run` stays **local** (host `.venv`, `python -m
  provisioning_worker`). Docker is the **parallel** container path, not the
  default — mirroring platform-api (which keeps `make run` local + `make up` /
  `docker-*` for the container).
- **D-12:** **Dockerfile** mirrors `platform-api/Dockerfile`: two-stage
  (`builder` → `runtime`), `python:3.14-slim-trixie`, uv copied from
  `ghcr.io/astral-sh/uv:0.11`, `WORKDIR /app` in **both** stages (so the baked
  `.venv` shebangs stay valid), `uv sync --frozen --no-install-project --no-dev`
  (cached) then `uv sync --frozen --no-dev`, non-root user `platform` uid/gid
  **10001**, `PATH=/app/.venv/bin`. **Differences from platform-api:**
  `ENTRYPOINT ["python","-m","provisioning_worker"]` (no Granian, no ASGI),
  `EXPOSE 8001`, `HEALTHCHECK` hits `http://127.0.0.1:8001/healthz`, and
  `alembic.ini` + `migrations/` are copied in so the image can run the migration
  one-shot.
- **D-13:** **docker-compose.yml** mirrors `platform-api/docker-compose.yml`:
  `name: provisioning-worker`, one `worker` service (`build: .`, `image:
  provisioning-worker:dev`, `container_name: provisioning-worker`,
  `restart: unless-stopped`, `env_file: .env`), attached to the **external**
  `platform-net` network (`external: true` — platform-infra owns its lifecycle),
  with `environment:` overrides to in-network DNS:
  `DATABASE_URL`/`DATABASE_URL_SYNC` → `platform-postgres:5432`,
  `VALKEY_URL` → `redis://platform-valkey:6379/0`. **No `KEYCLOAK_BASE_URL`
  override** — the worker uses no Keycloak realm. Publish
  `${HEALTH_HOST_PORT:-8001}:8001`; healthcheck on `/healthz`.
- **D-14:** **Make docker targets** mirror platform-api:
  `docker-build` (`docker compose build worker`), `docker-run`
  (`compose up worker`), `docker-up` (`-d`), `docker-down`, `docker-logs`,
  `docker-migrate` (one-shot `compose run --rm --entrypoint alembic worker
  upgrade head` — a **single** tree, not platform-api's four), and `up:
  infra-up docker-build docker-migrate docker-run`. `infra-up/-down/-ps`
  delegate to `$(MAKE) -C ../platform-infra`.

### CI (GitHub Actions)
- **D-15:** GitHub Actions (`.github/workflows/`), mirroring platform-api's
  provider. The **PR gate** runs `make check` (ruff check + `ruff format
  --check`) + `make test` (Docker-free unit suite, `-m "not integration"`) + a
  **build-only** Docker job (proves the image builds; **no push** to any
  registry in M1). `make test-integration` (testcontainers) is **not** on the
  PR gate — it runs locally / on-demand. CI uses `uv sync --frozen` so lockfile
  drift fails the build.

### Claude's Discretion
- Exact `Makefile` mechanics (e.g. `include .env`/`export`, `uv run` vs
  `.venv/bin` inside targets — note the project convention prefers `.venv/bin`
  for *ad-hoc* commands, but Make-internal `uv run` matches platform-api and is
  acceptable since "run tools through make targets" is itself the convention),
  the `make help` awk one-liner, `make psql` running against the host DB
  (`DATABASE_URL_SYNC` minus `+psycopg`), and `make revision name="..."`.
- The precise drain-timeout (if any) on SIGTERM, and the structlog processor
  chain (timestamper, log-level, contextvars merge, renderer).
- The empty-relay no-op strategy in Phase 1 given `event_outbox` doesn't exist
  until Phase 4 (poll-nothing vs short-circuit-with-log).
- ruff/pytest config specifics within the pins (line-length 100, target py314,
  `asyncio_mode=auto`, `--strict-markers --strict-config`, `filterwarnings`),
  `.gitignore`/`.dockerignore` contents, `.python-version`.
- Dev-only health smoke test (`tests/test_health.py`) shape.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### This repo — authoritative specs
- `docs/architecture.md` §Process model — the four concerns, "boots all four and
  blocks until SIGTERM, then drains … closes pools cleanly".
- `docs/architecture.md` §Code layout — the exact `src/provisioning_worker/`
  tree and the dependency rule (D-10).
- `docs/architecture.md` §Configuration — the authoritative env var list and
  naming (D-09).
- `docs/architecture.md` §Observability — structlog/OTel expectations; metrics
  list (deferred to Phase 5).
- `docs/architecture.md` §Migrations — single `provisioning` Alembic tree,
  `version_table_schema=provisioning`, forward-only, review autogenerated SQL.
- `docs/local-development.md` §Running the worker — the **exact boot log**
  Phase 1 must reproduce (D-04); §Adding a migration — `make revision`/`make
  migrate` behavior; the M1 `.env` var table (D-09).
- `docs/conventions.md` — coding standards not enforced by ruff (file naming,
  encapsulation, docstrings).
- `docs/python-style.md` — Python 3.14 style/design rules (no
  `from __future__ import annotations`; modern typing; SOLID; module structure).
- `CLAUDE.md` §3 — the exact tech-stack pins for `pyproject.toml`.
- `CLAUDE.md` §4 — repository layout + dependency rule.
- `CLAUDE.md` §6.1 — code style, tooling (`uv sync --frozen`, `.venv/bin` vs
  `uv run`), `make check` gate.
- `.planning/REQUIREMENTS.md` — SCAF-01..05, OBS-01 (the Phase-1 checklist).
- `.planning/ROADMAP.md` §Phase 1 — goal + the 5 success criteria.

### Sibling repo — the convention to MIRROR (read before writing Docker/CI/Settings)
- `../platform-api/Dockerfile` — the multi-stage uv build to mirror (D-12).
- `../platform-api/docker-compose.yml` — external `platform-net`, in-network DNS
  overrides, healthcheck shape (D-13).
- `../platform-api/.dockerignore` — build-context reduction list to mirror.
- `../platform-api/Makefile` — target structure incl. `docker-*`, `up`,
  `infra-*` delegation (D-14, D-15).
- `../platform-api/.env.example` — the env-file shape (ENVIRONMENT/LOG_LEVEL/
  OUTBOX_* live, secrets commented) to mirror (D-09).
- `../platform-api/src/platform_api/settings.py` — `environment` Literal,
  `otel_enabled` computed property, `env_file=".env"` (D-06, D-09).
- `../platform-api/src/platform_api/infrastructure/observability.py` — the
  OTel bootstrap to mirror: always-on TracerProvider + instrument-now +
  endpoint-gated exporter (D-07).
- `../platform-infra/docker-compose.yml` — confirms service/container names
  (`platform-postgres`, `platform-valkey`, `platform-keycloak`) and the
  `platform-net` external network (D-13).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **platform-api is a near-complete template.** Its `Dockerfile`,
  `docker-compose.yml`, `.dockerignore`, `Makefile`, `pyproject.toml`,
  `settings.py`, and `infrastructure/observability.py` are the direct models for
  this phase. Copy-and-adapt rather than design from scratch; the deltas are
  enumerated in D-06..D-15 (drop FastAPI/Granian/Stripe/Keycloak; swap
  ENTRYPOINT to `python -m provisioning_worker`; single Alembic tree; port 8001).

### Established Patterns
- **Hexagonal / ports & adapters** (`docs/architecture.md` §Ports and adapters)
  — Phase 1 lays down the `ports/`+`adapters/` dirs (placeholders) so the seam
  is visible; the real Protocols arrive with their phases.
- **Single Alembic tree** (vs platform-api's four) — `alembic.ini` has one
  `provisioning` section; the `migrate`/`revision`/`docker-migrate` targets carry
  **no** schema suffix.
- **`uv sync --frozen` everywhere** (local, CI, Docker) so lockfile drift fails
  the build, not the laptop.

### Integration Points
- **platform-infra** provides Postgres 18 + Valkey 8 on the external
  `platform-net` bridge; the `provisioning` schema already exists **empty**
  (created by `platform-infra/postgres/init/01-init.sql`) — this phase's Alembic
  tree creates tables *into* it (none in Phase 1 beyond the alembic version
  table).
- The worker connects to the **same** Valkey/Postgres as platform-api; it reads
  `events.subscription` (consumer) and will write `events.instance` (Phase 4).
  No HTTP call to platform-api, ever.

</code_context>

<specifics>
## Specific Ideas

- **"Run this repo in Docker as we do it for platform-api"** (user's explicit
  ask) — captured verbatim as D-11..D-14: mirror platform-api's container
  workflow exactly, minus Keycloak, with the worker ENTRYPOINT and port 8001.
  The reference files in `<canonical_refs>` are the literal templates.
- The documented boot log in `docs/local-development.md` §Running the worker is
  the **acceptance shape** for D-04 — Phase 1's `make run` should produce those
  four `INFO` lines (`starting … deployment_adapter=fake`, `joined consumer
  group …`, `outbox relay started poll_seconds=1.0`, `health server listening
  port=8001`).

</specifics>

<deferred>
## Deferred Ideas

- **Metrics** (consumer lag, convergence duration per `task_type`, outbox
  backlog, task failure/retry counts) — OBS-01's metrics half is **Phase 5**,
  not Phase 1. Phase 1 ships only the logging + tracing bootstrap.
- **In-process Taskiq listener + real retry/backoff tasks** — Phase 3 (PROV-04).
- **Envelope + `subscription.*` payload models, dispatch-on-type, idempotency,
  poison-message handling** — Phase 2 (CONS-01..04). Phase 1's consumer loop
  dispatches to no-op handlers only.
- **`provisioning.*` domain tables + state machine** — Phase 3 (PROV-01..).
- **`event_outbox` table + real relay publishing + `instance.*` catalog** —
  Phase 4 (EVT-01..02).
- **testcontainers integration tests on the CI PR gate** — kept off the gate in
  M1 (D-15); revisit if integration coverage needs enforcing pre-merge.
- **Distroless final image stage** — considered; rejected in favor of
  `slim-trixie` to match platform-api and keep the container debuggable.
- **In-process per-concern restart loops** — considered; rejected in favor of
  crash-only (D-01). Revisit only if a concern proves flaky in a way the
  orchestrator restart can't absorb.

</deferred>

---

*Phase: 1-Repo scaffold & worker skeleton*
*Context gathered: 2026-06-01*
