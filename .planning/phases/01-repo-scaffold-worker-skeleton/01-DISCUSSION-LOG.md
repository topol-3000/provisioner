# Phase 1: Repo scaffold & worker skeleton - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-01
**Phase:** 1-repo-scaffold-worker-skeleton
**Areas discussed:** Supervision & shutdown, Startup connectivity, CI pipeline shape, Scaffold shape, Taskiq wiring depth, Settings & .env scope, OTel bootstrap depth, Env detection & log format, Docker (run like platform-api)

---

## Concern supervision & shutdown

| Option | Description | Selected |
|--------|-------------|----------|
| Crash-only (TaskGroup) | All four in one asyncio.TaskGroup; any concern raising → cancel the rest, close pools, exit non-zero, orchestrator restarts. SIGTERM drains + exit 0. | ✓ |
| In-process restart loops | Each concern wrapped in its own restart-with-backoff loop; a crashed concern restarts in-process. | |

**User's choice:** Crash-only (TaskGroup)
**Notes:** Idiomatic 12-factor worker; matches the documented "blocks until SIGTERM → drain → close pools". Becomes the load-bearing main.py shape for Phases 2–5.

---

## Startup connectivity / boot fidelity

| Option | Description | Selected |
|--------|-------------|----------|
| Real conns, fail-fast | Real Valkey/Postgres connections at boot (XGROUP CREATE MKSTREAM + XREADGROUP→no-op handlers, relay polls empty outbox, taskiq broker connects); infra down → log + exit non-zero. Reproduces the documented boot log. | ✓ |
| Real conns, retry-wait | Same real connections, but retry-with-backoff and wait for infra instead of exiting. | |
| Pure log-and-idle stubs | Each concern just logs "started" and idles; no real I/O until Phase 2. | |

**User's choice:** Real conns, fail-fast
**Notes:** Pairs with crash-only — supervisor/orchestrator owns restart. Draws the Phase-1/Phase-2 line at "connections real, handler bodies in Phase 2".

---

## CI pipeline shape

| Option | Description | Selected |
|--------|-------------|----------|
| GH Actions, unit + build | PR runs `make check` + `make test` (Docker-free) + build-only Docker job (no push); integration on-demand. | ✓ |
| GH Actions, full gate | PR also runs `make test-integration` (testcontainers) + image build every PR. | |
| Match platform-api | Mirror whatever provider/shape platform-api uses. | (effectively chosen too — platform-api uses GitHub Actions) |

**User's choice:** GH Actions, unit + build
**Notes:** platform-api confirmed to use GitHub Actions, so this both mirrors the sibling and keeps PRs fast.

---

## Scaffold shape (tree depth + Docker base)

| Option | Description | Selected |
|--------|-------------|----------|
| Full tree + slim | Full module tree as docstring-only placeholders + tests mirror; python:3.14-slim multi-stage, uv, non-root. | ✓ |
| Minimal tree + slim | Only files needed to boot; grow tree per phase. Same Dockerfile. | |
| Full tree + distroless | Full tree but distroless final stage. | |

**User's choice:** Full tree + slim
**Notes:** Makes the dependency rule visible from day one; slim-trixie matches platform-api and stays debuggable.

---

## Taskiq wiring depth

| Option | Description | Selected |
|--------|-------------|----------|
| Broker connect-only | Construct taskiq-redis broker, `broker.startup()`, register zero tasks, log connected. Listener + tasks in Phase 3. | ✓ |
| Broker + listener | Wire broker AND start an in-process listener now (no tasks registered). | |
| Defer to Phase 3 | No broker connection at all until Phase 3. | |

**User's choice:** Broker connect-only
**Notes:** platform-api is HTTP-only — no worker-loop precedent to mirror. Connect-only is the leanest thing that still exercises the dependency at boot (consistent with fail-fast).

---

## OTel bootstrap depth

| Option | Description | Selected |
|--------|-------------|----------|
| Mirror platform-api | Always install TracerProvider; instrument psycopg + redis now; OTLP exporter only when endpoint set. | ✓ |
| Fully gated (no-op unless configured) | Install nothing unless OTEL_EXPORTER_OTLP_ENDPOINT set. | |

**User's choice:** Mirror platform-api
**Notes:** Matches `platform-api/src/platform_api/infrastructure/observability.py` ("instrument now even without a backend"). Metrics stay deferred to Phase 5.

---

## Settings & .env.example scope

| Option | Description | Selected |
|--------|-------------|----------|
| Full M1 set now | settings.py defines the complete M1 env set with defaults + Literal validation; M2/secrets commented in .env.example. | ✓ |
| Minimal boot-only | Only what Phase-1 code reads at boot; add the rest per phase. | |

**User's choice:** Full M1 set now
**Notes:** Mirrors platform-api's .env.example shape; later phases only consume, never redefine.

---

## Docker (run like platform-api)

| Option | Description | Selected |
|--------|-------------|----------|
| Mirror platform-api | `make run` local; add docker-compose.yml (worker on external platform-net, in-network DB/Valkey overrides, no Keycloak, HEALTH_PORT published, healthcheck on /healthz); Dockerfile ENTRYPOINT python -m provisioning_worker, EXPOSE 8001; Make docker-* + `up` targets. | ✓ |
| Docker as primary run | `make run` itself drives docker compose up. | |

**User's choice:** Mirror platform-api
**Notes:** User's explicit addition ("I want to run this repo in docker as we do it for platform-api"). Read platform-api's Dockerfile / docker-compose.yml / .dockerignore / Makefile and platform-infra's compose to mirror exactly, minus Keycloak.

---

## Env detection & log format (decided by mirroring — not separately questioned)

**Decision:** `environment: Literal["dev","staging","prod"] = "dev"` drives structlog ConsoleRenderer(dev)/JSON(otherwise); `LOG_LEVEL` Literal; `env_file=".env"`; no dedicated `LOG_FORMAT` var. Mirrors `platform-api/src/platform_api/settings.py`. Presented as a stated mirror decision; user did not object.

---

## Claude's Discretion

- Exact Makefile mechanics (`include .env`/`export`, `uv run` vs `.venv/bin` inside targets, `make help` awk, `make psql` against host DB, `make revision`).
- SIGTERM drain timeout; the structlog processor chain.
- Phase-1 empty-relay no-op strategy (event_outbox table doesn't exist until Phase 4).
- ruff/pytest config specifics within pins; .gitignore/.dockerignore/.python-version contents.
- tests/test_health.py shape.

## Deferred Ideas

- Metrics (OBS-01 metrics half) → Phase 5.
- In-process Taskiq listener + retry/backoff tasks → Phase 3.
- Envelope/payload models, dispatch, idempotency, poison handling → Phase 2.
- Domain tables + state machine → Phase 3; event_outbox + relay publishing + instance.* catalog → Phase 4.
- testcontainers integration tests on the CI PR gate → kept off the gate in M1.
- Distroless final image stage → rejected in favor of slim-trixie.
- In-process per-concern restart loops → rejected in favor of crash-only.
