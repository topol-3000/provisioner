# Roadmap: provisioning-worker

## Overview

`provisioner` is greenfield — the repo holds only `docs/`, `CLAUDE.md`,
`README.md`, and this planning layer. **Milestone 1** delivers the entire
provisioning pipeline against the in-memory `FakeDeploymentAdapter` in five
vertical phases, so the convergence logic, registry, idempotency, and the
`instance.*` event contract are correct before the real-orchestrator risk is
taken on. Phase 1 lays down the worker scaffold (entrypoint, settings, health
probe, Alembic, CI). Phase 2 builds the Valkey Streams consumer + idempotency
against the re-implemented event contract (handlers are no-op stubs that prove
delivery + dedupe). Phase 3 delivers the first real value — `subscription.activated`
drives a `provisioning.instance` to `ready` through the fake adapter, with
retry/backoff and console credential delivery. Phase 4 makes that observable to
the rest of the platform via the transactional outbox → relay and the first
produced event (`instance.provisioned`). Phase 5 completes the lifecycle —
update / suspend / reinstate / deprovision, the full `instance.*` catalog,
enforcement-snapshot computation, and observability polish.

**Beyond milestone 1** (separate milestone, see `.planning/seeds/`): a Coolify
API spike, then the real `CoolifyAdapter` + Odoo stack template, the per-instance
bearer token + serving `enforcement_snapshot` to a live plugin,
`SmtpNotificationTransport`, and operator-triggered retry.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): planned milestone work.
- Decimal phases (2.1, 2.2): urgent insertions (marked INSERTED).

- [ ] **Phase 1: Repo scaffold & worker skeleton** — `pyproject`/`uv.lock`/`Makefile`/CI, the `python -m provisioning_worker` entrypoint (four concerns), typed `Settings`, `/healthz`, the single `provisioning` Alembic tree, structlog/OTel bootstrap
- [ ] **Phase 2: Event consumption & idempotency** — the `events.subscription` consumer (`cg.provisioning-convergence`), the re-implemented envelope + `subscription.*` payloads, transactional `processed_event` dedupe, poison-message handling
- [ ] **Phase 3: Registry & create-path (fake adapter)** — `provisioning.instance`/`provisioning_task`/`enforcement_snapshot` tables, the `DeploymentAdapter` port + `FakeDeploymentAdapter`, `subscription.activated` → `ready`, `InstanceSpec`, Taskiq retry/backoff, console credential delivery
- [ ] **Phase 4: Event production (outbox → relay)** — `provisioning.event_outbox` + relay to `events.instance`, the envelope publisher, and `instance.provisioned` emitted atomically on first `ready`
- [ ] **Phase 5: Full lifecycle convergence** — `lines_changed` / `suspended` / `reinstated` / `cancelled` (incl. `at_period_end` grace), the rest of the `instance.*` catalog, enforcement-snapshot computation/versioning, and observability (metrics) polish

## Phase Details

### Phase 1: Repo scaffold & worker skeleton

**Goal**: A fresh checkout can `uv sync`, `make migrate` against a clean
`provisioning` schema, `make run` to boot the worker (which logs `starting`,
serves `/healthz`, and shuts down cleanly), and pass `make check` + an empty
`make test` — with CI green.
**Mode:** mvp
**Depends on**: `platform-infra` running (Postgres + Valkey).
**Requirements**: SCAF-01, SCAF-02, SCAF-03, SCAF-04, SCAF-05, OBS-01
**Plans:** 1/3 plans executed
**Success Criteria** (what must be TRUE):

  1. `uv sync --frozen --extra dev` installs the pinned stack from a committed `uv.lock`; `make check` (ruff check + format --check) passes.
  2. `make run` boots `python -m provisioning_worker`; it logs a structured `starting` line, the `/healthz` server answers 200 `{"status":"ok"}` on `HEALTH_PORT`, and `Ctrl-C` drains and exits 0.
  3. The four concerns start (consumer / convergence+Taskiq / outbox relay / health) even though handlers are not implemented yet — each logs that it started.
  4. `make migrate` succeeds against the empty `provisioning` schema; `make revision name="..."` emits a revision with no `from __future__ import annotations`.
  5. CI runs lint + test + a Docker build and is green.

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Project toolchain scaffold: pyproject.toml (pinned deps, ruff, pytest), uv.lock, Makefile, alembic.ini, migrations/provisioning tree, .env.example, .gitignore, .dockerignore

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 01-02-PLAN.md — Worker source + test suite: settings.py, infrastructure/*.py (real boot path), __main__.py, main.py (TaskGroup + SIGTERM), placeholder module tree, all unit tests

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 01-03-PLAN.md — Container + CI: Dockerfile (two-stage, uv, non-root), docker-compose.yml (external platform-net), .github/workflows/ci.yml (lint + test + build-only)

### Phase 2: Event consumption & idempotency

**Goal**: The worker reads `subscription.*` envelopes off `events.subscription`,
parses them into typed models, dedupes replays, and survives malformed messages
— with handlers as observable no-op stubs.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: CONS-01, CONS-02, CONS-03, CONS-04
**Success Criteria** (what must be TRUE):

  1. With the worker running, `valkey-cli XADD events.subscription … envelope '{…subscription.activated…}'` is picked up via `XREADGROUP` on `cg.provisioning-convergence`, parsed into a frozen `extra="forbid"` model, dispatched on `type`, and `XACK`'d.
  2. Re-publishing the **same** `envelope.id` is a no-op: the handler short-circuits on `provisioning.processed_event`, inserted in the same transaction as the (stub) state change.
  3. A malformed envelope (bad JSON / unknown field) is logged at `error` and `XACK`'d without crashing the consumer; a valid envelope published afterward is still processed.
  4. All five consumed payload models round-trip a platform-api-shaped envelope (verified by unit tests against fixtures matching `docs/events.md`).

### Phase 3: Registry & create-path (fake adapter)

**Goal**: A `subscription.activated` event drives a real `provisioning.instance`
row from `pending` to `ready` through the fake deployment adapter, with retry on
injected failure and console credential delivery.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: PROV-01, PROV-02, PROV-03, PROV-04, PROV-08, SNAP-01 (table)
**Success Criteria** (what must be TRUE):

  1. `subscription.activated` opens a `pending` `instance` + a `create` `provisioning_task` and converges `pending → deploying → configuring → ready` via `FakeDeploymentAdapter`; the row ends at `ready` with a populated `url`.
  2. The same convergence code runs unchanged against the fake adapter and (by construction of the port) would against a real one — verified by the in-memory fake being the only deployment dependency on the fast test path.
  3. An injected adapter failure (`fail_on={"create"}`) records `last_error`, sets `failed_step`, and schedules a backoff retry that later succeeds — the consumer never crashes.
  4. On first `ready`, `ConsoleNotificationTransport` emits the credentials notification; no credential value appears in any event or log line.
  5. The `instance`, `provisioning_task`, and `enforcement_snapshot` tables exist via the single Alembic tree.

### Phase 4: Event production (outbox → relay)

**Goal**: Reaching `ready` reliably emits an `instance.provisioned` envelope on
`events.instance`, atomically with the state change.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: EVT-01, EVT-02 (`instance.provisioned`)
**Success Criteria** (what must be TRUE):

  1. The transition to `ready` writes an `instance.provisioned` row to `provisioning.event_outbox` in the **same transaction** as the instance update.
  2. The relay publishes unsent outbox rows to `events.instance` via `XADD` (single `envelope` field, `MAXLEN ~ 100000`) and marks them sent; `valkey-cli XRANGE events.instance - +` shows the envelope with `producer="provisioning-worker"`, a fresh ULID `id`, and `causation_id` = the triggering `subscription.activated` id.
  3. A relay/publish failure leaves the row unsent (records `last_error`, bumps `attempt_count`) and is retried on the next poll — no event is lost or duplicated (consumer-side dedupe on `envelope.id`).
  4. The produced `InstanceProvisionedPayload` is frozen + `extra="forbid"` and carries no credentials.

### Phase 5: Full lifecycle convergence

**Goal**: Every `subscription.*` lifecycle event drives the correct instance
transition and emits the correct `instance.*` event, the enforcement snapshot is
computed and versioned, and the worker exposes operational metrics.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: PROV-05, PROV-06, PROV-07, SNAP-01 (computation), EVT-02 (full catalog), OBS-01 (metrics)
**Success Criteria** (what must be TRUE):

  1. `subscription.lines_changed` converges an `update` (diff desired-vs-current), idempotent on `change_set_id`, and emits `instance.updated`.
  2. `subscription.suspended` → soft `suspended` (+ `instance.suspended`); `subscription.reinstated` → `ready` (+ `instance.reinstated`).
  3. `subscription.cancelled` deprovisions — `immediate` now, `at_period_end` at `grace_until` via a delayed job — produces a backup ref, ends at `deprovisioned`, and emits `instance.deprovisioned` with a field set symmetric to platform-api.
  4. A failed step emits `instance.failed` (with `will_retry`); the enforcement snapshot is recomputed with a bumped monotonic `version` on each converging change.
  5. Consumer lag, convergence duration per `task_type`, and outbox backlog are exposed as metrics; logs carry the bound event/instance context.

## Beyond milestone 1

Captured as a seed (`.planning/seeds/`), promoted to the next milestone after M1
completes:

- **Coolify spike** — prototype the riskiest operations (volumes, networking,
  per-instance Postgres, suspension) against the live Coolify API (PRD §16 #1
  risk).

- **`CoolifyAdapter` + Odoo stack template** — the real deployment path behind
  the unchanged `DeploymentAdapter` port; the Odoo base image (with the
  `platform_entitlements` plugin) build + registry.

- **Per-instance bearer token + served `enforcement_snapshot`** — mint/rotate
  the token, serve the snapshot to the live Odoo plugin via platform-api's
  `plugin_api` (coordinate the exact token-validation mechanism).

- **`SmtpNotificationTransport`** and **operator-triggered retry** (needs a
  platform-api → worker signalling channel).
