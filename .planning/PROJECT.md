# provisioning-worker — Odoo Entitlements SaaS Provisioner

## What This Is

`provisioner` (the **provisioning-worker**) is the backend service that turns a
paid subscription into a running, dedicated Odoo instance. It is a long-running
Taskiq / Valkey-Streams-consumer worker — **not** an HTTP service (it serves
only `GET /healthz`). It consumes `subscription.*` events from `platform-api`,
converges each customer's dedicated Odoo deployment to the state their
subscription entitles them to via a pluggable **deployment adapter** (Coolify in
v1), owns the `provisioning` Postgres schema, and produces `instance.*` events
back onto the bus.

It is one of three Python deployables; the others are `platform-api` (the
FastAPI control plane that emits the events this worker consumes) and
`telemetry-worker` (not yet built). See `docs/overview.md` and
`docs/architecture.md` — those repo docs are authoritative and this planning
layer references them rather than restating.

## Core Value

A paid subscription becomes a running, correctly-entitled, dedicated Odoo
instance — automatically, idempotently, and observably — by consuming the
subscription lifecycle and converging the instance through a pluggable
deployment adapter. **If this path doesn't work, customers pay and get
nothing.** Milestone 1 proves the entire pipeline against an in-memory fake
adapter so the convergence logic, registry, idempotency, and event contract are
correct *before* the real-orchestrator risk is taken on.

## Requirements

### Validated

<!-- Capabilities confirmed in the codebase via tests. Greenfield — none yet. -->

- _(none — the repo is being scaffolded; see Active.)_

### Active

Milestone 1 — the full provisioning pipeline against the in-memory
`FakeDeploymentAdapter` + `ConsoleNotificationTransport`. Grouped requirements
(full text in `REQUIREMENTS.md`):

**Scaffold & runtime**
- [ ] **SCAF-01..05**: repo scaffold (pinned `pyproject.toml`, `uv.lock`,
  `Makefile`, ruff/pytest, Dockerfile, CI), the `python -m provisioning_worker`
  entrypoint booting the four concerns, typed `Settings`, the `/healthz` probe,
  and the single-tree Alembic wiring.
- [ ] **OBS-01**: structlog JSON logging with bound event/instance context and
  an OTel bootstrap.

**Event consumption**
- [ ] **CONS-01..04**: the Valkey Streams consumer on `events.subscription`
  (`cg.provisioning-convergence`), the re-implemented envelope + consumed
  `subscription.*` payload models, transactional idempotency on
  `(envelope.id, consumer_group)`, and poison-message handling.

**Registry, convergence & deployment adapter**
- [ ] **PROV-01..08**: the `provisioning.instance` + `provisioning_task` tables,
  the `DeploymentAdapter` port + `FakeDeploymentAdapter`, the create path
  (`subscription.activated` → `ready`), the `InstanceSpec` builder, Taskiq
  retry/backoff, the update/suspend/reinstate/deprovision paths, and credential
  delivery via the `NotificationTransport` port.
- [ ] **SNAP-01**: the `provisioning.enforcement_snapshot` table + snapshot
  computation/versioning (the table exists in M1 so platform-api's read seam is
  real; *serving* it to a live plugin is M2).

**Event production**
- [ ] **EVT-01..02**: the `provisioning.event_outbox` + relay publishing to
  `events.instance`, and the produced `instance.*` payload catalog
  (`provisioned`/`updated`/`suspended`/`reinstated`/`failed`/`deprovisioned`),
  with `instance.deprovisioned` field-symmetric to platform-api.

### Out of Scope

<!-- Explicit boundaries. Most of these are milestone 2 or other repos. -->

- **CoolifyAdapter, the Odoo stack template, and the per-instance Postgres
  strategy** — milestone 2, gated on a Coolify-API spike (PRD's #1 risk). M1
  uses the in-memory fake adapter.
- **Per-instance bearer-token mechanism** and **serving `enforcement_snapshot`
  to a live Odoo plugin** — milestone 2 (needs a real instance + coordination
  with platform-api's `plugin_api`).
- **`SmtpNotificationTransport`** — milestone 2; M1 uses the console transport.
- **Operator-triggered retry** (`POST /api/ops/instances/{id}/retry`) — needs a
  platform-api→worker signalling channel that doesn't exist yet; M1 ships
  automatic backoff retry only.
- **Hard suspension** and **resource-cap enforcement beyond seats** — deferred
  (PRD).
- **Health / usage polling** — owned by `telemetry-worker`, not this repo.
- **A dead-letter stream** for poison messages — later; M1 logs + acks them.
- **A shared contracts package** — never; payload models are re-implemented per
  repo against `docs/events.md`.
- **Any HTTP request API** — the worker serves only `/healthz`; customer/operator
  instance views are platform-api reading `provisioning.*`.

## Context

**Product model.** The platform sells per-line entitlements on a Stripe
subscription; each paying customer gets a dedicated Odoo instance (1:1:1
customer→subscription→instance). This worker is the piece that creates,
configures, suspends, reinstates, and tears down that instance in response to
the subscription lifecycle. Full business context: platform `docs/PRD.md` and
`platform-api/docs/overview.md`.

**Architecture.** Hexagonal / Ports & Adapters, event-driven. A single domain
module (`modules/provisioning/`) holds the convergence service + the 8-state
instance machine; external seams (`DeploymentAdapter`, `MessageBus`/consumer,
`NotificationTransport`) are `Protocol`s with v1 adapters. The worker owns the
`provisioning` schema in the shared Postgres cluster (no cross-schema FKs;
opaque UUIDs). Reliable event production uses a transactional outbox → relay,
mirroring platform-api. See `docs/architecture.md`.

**Cross-repo state.** `platform-api` already publishes the `subscription.*`
events this worker consumes (implemented today via its own outbox → relay) and
will consume `instance.deprovisioned` in its Phase 6. `platform-infra` runs the
shared Postgres 18 + Valkey 8 + Keycloak 26; the `provisioning` schema exists
empty there and this repo's Alembic tree creates the tables. **Coolify is not
configured anywhere yet** — the M1 fake adapter is what lets us build the whole
pipeline before that risk is taken on.

**Starting state.** Greenfield: the repo currently holds only `docs/`,
`CLAUDE.md`, `README.md`, and this `.planning/`. Phase 1 lays down the scaffold.

## Constraints

- **Tech stack** (mirrors platform-api, minus FastAPI / Granian / Stripe):
  Python 3.14; uv 0.11 + committed `uv.lock` (`uv sync --frozen`); Pydantic
  2.13 / pydantic-settings 2.7; SQLAlchemy 2.0 async + `psycopg[binary]` 3.3
  (**not** asyncpg); Alembic 1.18 (a **single** `provisioning` tree); Taskiq
  0.12 + taskiq-redis 1.2; `redis.asyncio` (`redis>=5`) for the Streams
  consumer/publisher (**not** standalone `aioredis`); aiohttp 3 (Coolify client
  + `/healthz` server); python-ulid 3.1; PyJWT 2.10 (per-instance tokens, M2);
  structlog 25; OpenTelemetry 1.41; ruff 0.15; pytest 9 + pytest-asyncio
  (`asyncio_mode=auto`) + testcontainers 4.14. All pins in `pyproject.toml`.
- **No mypy / pyright** — type hints are documentation for humans (deliberate,
  shared with platform-api).
- **No `from __future__ import annotations`** — Python 3.14 defers annotation
  evaluation (PEP 649); the import is forbidden, including in generated Alembic
  files.
- **Tooling** — run tools through `make` targets or `.venv/bin/<tool>`; prefer
  that over `uv run <tool>` for ad-hoc commands (project convention).
- **Async-first** — no blocking I/O on the event loop; the consumer, relay,
  adapters, and DB sessions are all async.
- **Worker, not a service** — no HTTP request API; only `GET /healthz`. No
  Granian/ASGI/FastAPI.
- **Single Postgres schema** (`provisioning`), no cross-schema FKs; cross-service
  references are opaque UUIDs validated at the app layer. One Alembic tree.
- **Idempotency is load-bearing** — at-least-once delivery; every handler
  dedupes on `(envelope.id, consumer_group)` (and `change_set_id` for line
  changes), inserted in the same transaction as the state change.
- **Contracts are per-repo** — no shared `platform-contracts` package; envelope
  + payload models are re-implemented against `docs/events.md`. The
  `instance.deprovisioned` field set must stay symmetric with platform-api.
- **Module file naming** is load-bearing: `models.py` (SQLAlchemy), `schemas.py`
  (Pydantic), `repository.py`, `service.py`, `handlers.py`, `tasks.py`,
  `spec.py`. Dependency rule: `modules/` may import infra/ports/adapters/shared/
  events; never the reverse.
- **External dependency** — `platform-infra` (Postgres + Valkey) must be running
  for local dev and integration tests.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| **Fake-adapter-first** (M1 builds the whole pipeline against `FakeDeploymentAdapter`; real Coolify is M2 after a spike) | De-risks the PRD's #1 risk (Coolify API gaps) and makes the entire convergence/idempotency/event contract unit-testable with no Coolify or Odoo; unblocks platform-api Phase 5/6 reads + portal A.8 against a real registry sooner | — Pending validation across M1 phases |
| **Pure worker + tiny `/healthz`** (no FastAPI/Granian; custom Valkey Streams consumer + Taskiq jobs + `aiohttp.web` health probe) | "Workers never expose HTTP" while still giving the orchestrator a standard liveness check; the worker is the first repo to build a real Streams consumer (platform-api's `MessageBus` is publish-only) | — Pending validation in Phase 1/2 |
| **Worker owns credential delivery** via a `NotificationTransport` port (`Console` in dev, `Smtp` in M2) | Simplest end-to-end MVP; keeps the ports/adapters pattern and the swap path | — Pending validation in Phase 3 |
| **Transactional outbox → relay** for `instance.*` production (mirrors platform-api's `billing.event_outbox`) | Makes "instance reached `ready`" and "`instance.provisioned` emitted" atomic; survives crashes; consumers dedupe on `envelope.id` | — Pending validation in Phase 4 |
| **Postgres `processed_event` dedup** (vs a Valkey set) | Lets the idempotency insert commit in the same transaction as the state change — stronger exactly-the-contract semantics than an out-of-band Valkey set | — Pending validation in Phase 2 |
| **Consumer group `cg.provisioning-convergence`** on `events.subscription` | Group name describes what the consumer does (convergence), per the platform's stream/group convention; reconciliation model tolerates event reordering | — Pending validation in Phase 2 |
| **No shared contracts package** | Matches platform-api; avoids monorepo coupling; drift caught by review + the evolution discipline in `docs/events.md` | ✓ Established platform-wide |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-progress` / transition):
1. Requirements validated? → move to Validated with phase reference.
2. Requirements invalidated? → move to Out of Scope with reason.
3. New requirements emerged? → add to Active.
4. Decisions to log? → add to Key Decisions.

**After milestone 1** (via `/gsd-complete-milestone`):
1. Full review; confirm Core Value still the priority.
2. Promote the milestone-2 seed (Coolify spike → `CoolifyAdapter` + Odoo
   template + per-instance token + served snapshot + SMTP + operator retry) into
   the next milestone's Active scope.

---
*Last updated: 2026-06-01 — project initialized from the repo `docs/` set; milestone 1 (fake-adapter pipeline) defined across 5 phases. No code yet; Phase 1 is the scaffold.*
