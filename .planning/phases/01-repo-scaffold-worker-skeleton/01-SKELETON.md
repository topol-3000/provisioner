# Walking Skeleton — provisioning-worker

**Phase:** 1
**Generated:** 2026-06-01

## Capability Proven End-to-End

A developer can `uv sync --frozen --extra dev`, `make migrate` against the empty `provisioning` schema,
`make run` to boot the worker (which logs the four documented startup lines, serves `/healthz` → 200
`{"status":"ok"}` on port 8001), and send SIGTERM to confirm clean drain (exit 0) — with CI and Docker
also green. No domain logic exists; the skeleton proves the full async lifecycle works before business
code is added.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Process model | `python -m provisioning_worker` — single asyncio event loop, no ASGI app | Worker consumes events; it has no request API. No FastAPI, no Granian, no Stripe. |
| Concern supervision | `asyncio.TaskGroup` (crash-only, D-01/D-03) | Fail-fast: any concern crash cancels the others and the process exits non-zero. The orchestrator owns restart. This shape is locked through Phase 5 — concerns gain bodies, not a new supervisor. |
| SIGTERM drain | `loop.add_signal_handler` + shared `asyncio.Event` (D-02) | Each concern's loop checks the event; SIGTERM sets it; all concerns exit cleanly before pool disposal. |
| Health probe | aiohttp `AppRunner` + `TCPSite` on `HEALTH_PORT=8001` (SCAF-04) | aiohttp is already a dependency (M2 Coolify client). AppRunner is non-blocking — does not occupy the event loop. `/healthz` is the only HTTP surface; no auth, intentional. |
| Event bus client | `redis.asyncio` from `redis>=5` | Unified client (maintained); `aioredis` is abandoned. Used for Valkey Streams consumer, outbox relay (`XADD`), and taskiq-redis broker. |
| Database | SQLAlchemy 2.0 async + psycopg[binary] 3.3 | Async ORM with session_scope context manager. Single `provisioning` schema owned by this repo's Alembic tree. NOT asyncpg (CLAUDE.md §3). |
| Migrations | Alembic 1.18 — single `[provisioning]` section, `version_table_schema=provisioning` | One repo, one schema, one tree. Simpler than platform-api's four-tree setup. `make migrate` and `make revision` carry no suffix. |
| Background jobs | taskiq-redis `RedisStreamBroker` — broker-connect-only in Phase 1 (D-08) | Phase 1 proves broker connectivity. In-process listener + real tasks land Phase 3. |
| Settings | pydantic-settings `BaseSettings`, `env_file=".env"` (D-09) | Full M1 var set defined now so Phases 2–5 only consume. Fail-fast on missing/invalid vars at startup. |
| Observability | structlog 25 (JSON prod, ConsoleRenderer dev) + OpenTelemetry 1.41 always-on (D-06/D-07) | Mirrors platform-api. TracerProvider installed unconditionally; OTLP exporter gated on endpoint. Metrics are Phase 5. |
| Container | Two-stage Dockerfile: `python:3.14-slim-trixie` + `ghcr.io/astral-sh/uv:0.11` (D-12) | Same base as platform-api. Non-root user `platform` uid/gid 10001. WORKDIR `/app` in both stages (shebang stability). |
| Docker Compose | External `platform-net` network; `env_file: .env`; in-network DNS overrides (D-13) | Worker shares infra with platform-api on the same Compose network. Secrets never in Dockerfile ARGs. |
| Package manager | `uv 0.11.*` — `uv sync --frozen` everywhere (local, CI, Docker) | Lock-file drift fails the build, not the laptop. `.venv/bin/<tool>` for ad-hoc; `uv run` inside Makefile targets. |
| Linting / formatting | ruff 0.15, line-length 100, target py314 | Same config as platform-api. `make check` is the CI gate. |
| Testing | pytest 9 + pytest-asyncio 1.3 (`asyncio_mode=auto`) — Docker-free unit suite on CI (D-15) | testcontainers (`@pytest.mark.integration`) run locally/on-demand, never on the PR gate in M1. |
| Directory layout | CLAUDE.md §4: `src/provisioning_worker/{infrastructure,ports,adapters,shared,events,modules/provisioning}` | Dependency rule enforced from day one: `modules/` imports infra/ports/adapters/shared/events, never the reverse. |
| Python | 3.14.* (PEP 649 native) | `from __future__ import annotations` is forbidden everywhere (unnecessary + project rule). |

## Stack Touched in Phase 1

- [x] Project scaffold (pyproject.toml + uv.lock + Makefile + ruff + pytest config)
- [x] Routing — GET /healthz returns 200 `{"status":"ok"}` on aiohttp
- [x] Database — SQLAlchemy async engine opened at boot; `make migrate` creates `provisioning.alembic_version`
- [x] Event bus — `XGROUP CREATE` + `XREADGROUP` no-op loop on `events.subscription`; taskiq broker connected
- [x] Deployment — `make run` for local native; `make docker-build` + `make docker-up` for container path

## Out of Scope (Deferred to Later Slices)

- **Phase 2:** Envelope + `subscription.*` payload models, dispatch-on-type, `processed_event` idempotency, poison-message handling
- **Phase 3:** `provisioning.instance` / `provisioning_task` tables, `DeploymentAdapter` port + `FakeDeploymentAdapter`, `subscription.activated` → `ready` convergence, Taskiq retry/backoff, `ConsoleNotificationTransport`
- **Phase 4:** `event_outbox` table + real outbox relay publishing to `events.instance`, `instance.provisioned` event
- **Phase 5:** Full lifecycle (`lines_changed`, `suspended`, `reinstated`, `cancelled`), `enforcement_snapshot`, consumer-lag/convergence-duration/outbox-backlog metrics
- **Milestone 2:** `CoolifyAdapter` + Odoo stack template, per-instance bearer token, `SmtpNotificationTransport`, operator-triggered retry

## Subsequent Slice Plan

Each later phase adds one vertical slice on top of this skeleton without altering its architectural decisions:

- Phase 2: A `subscription.*` envelope can be consumed, parsed, deduped, and ACK'd (handlers are observable no-op stubs)
- Phase 3: `subscription.activated` drives a real `provisioning.instance` row to `ready` through `FakeDeploymentAdapter` with retry
- Phase 4: Reaching `ready` atomically emits `instance.provisioned` on `events.instance` via the transactional outbox
- Phase 5: Full lifecycle convergence + enforcement snapshot + metrics — all six `instance.*` events produced
