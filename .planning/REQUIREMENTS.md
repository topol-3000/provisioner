# Requirements: provisioning-worker

**Defined:** 2026-06-01
**Core Value:** A paid subscription becomes a running, correctly-entitled,
dedicated Odoo instance — automatically, idempotently, and observably — by
consuming `subscription.*` lifecycle events and converging the instance through
a pluggable deployment adapter.

## Milestone 1 — the fake-adapter pipeline

The smallest end-to-end slice that proves the model: consume the subscription
lifecycle, converge a dedicated Odoo instance through the full state machine
against an in-memory `FakeDeploymentAdapter`, persist the registry, and emit the
`instance.*` event catalog — **no Coolify and no real Odoo**. All items are
unit-testable on the fast (Docker-free) path. Authoritative detail lives in the
repo `docs/` (referenced per requirement); this file is the checklist the
roadmap must cover. `[ ]` = active scope, `[x]` = shipped.

### Scaffold & runtime

- [x] **SCAF-01**: The repo builds and checks clean — `pyproject.toml` with the
  pinned stack (per `docs/conventions.md` / `CLAUDE.md` §3), committed
  `uv.lock`, `Makefile` (help/dev/run/test/test-integration/lint/lint-fix/
  format/check/migrate/revision/psql/infra-up), ruff + pytest config,
  `.env.example`, `.gitignore`, `Dockerfile`, and CI (lint + test + build).
  `make check` and `make test` pass on an empty suite.
- [ ] **SCAF-02**: `python -m provisioning_worker` (`make run`) boots the four
  concurrent concerns on one asyncio loop — Streams consumer, convergence +
  Taskiq, outbox relay, `/healthz` — logs a structured `starting` line, and
  drains cleanly on SIGTERM. (`docs/architecture.md` §Process model.)
- [ ] **SCAF-03**: A typed `Settings` (pydantic-settings) validates every env
  var at startup and fails fast on a missing one; `.env.example` lists each var
  with the milestone-1 defaults (`docs/local-development.md`).
- [ ] **SCAF-04**: `GET /healthz` (aiohttp, `HEALTH_PORT`, default 8001) returns
  200 `{"status":"ok"}`.
- [x] **SCAF-05**: A single Alembic tree for the `provisioning` schema
  (`alembic.ini` section `provisioning`, `version_table_schema=provisioning`)
  is wired; `make migrate` runs clean against the empty schema; `make revision
  name="..."` emits a clean revision (no `from __future__ import annotations`).

### Observability

- [ ] **OBS-01**: structlog emits JSON outside dev; handlers bind
  `envelope_id` / `subscription_id` / `instance_id` / `correlation_id` via
  `bind_contextvars`; an OTel bootstrap is in place (OTLP optional). Consumer
  lag, convergence duration, and outbox backlog are exposed as metrics.

### Event consumption

- [ ] **CONS-01**: A Valkey Streams consumer reads `events.subscription` via
  consumer group `cg.provisioning-convergence` (`XREADGROUP`, `XACK`,
  `XAUTOCLAIM` for stuck entries) and dispatches on the envelope `type`.
- [ ] **CONS-02**: The `EventEnvelope` and the consumed `subscription.*` payload
  models (`activated`, `lines_changed`, `suspended`, `reinstated`, `cancelled`)
  are re-implemented here (frozen, `extra="forbid"`) byte-matching
  `docs/events.md` / platform-api's contract.
- [ ] **CONS-03**: Handlers are idempotent — a replayed `envelope.id`
  short-circuits via `provisioning.processed_event(event_id, consumer_group)`
  inserted in the **same transaction** as the state change.
- [ ] **CONS-04**: A malformed envelope (bad JSON or unknown field) is logged at
  `error` and `XACK`'d as a poison message — it never crashes the consumer and
  never creates or advances an instance.

### Registry, convergence & deployment adapter

- [ ] **PROV-01**: The `provisioning.instance` and `provisioning.provisioning_task`
  tables exist (Alembic), with `instance` shaped to the read model platform-api
  documents (`docs/architecture.md` §Postgres schema). `subscription_id` is
  unique (1:1:1).
- [ ] **PROV-02**: `subscription.activated` creates an `instance` row (`pending`)
  + a `create` task and converges `pending → deploying → configuring → ready`
  against the `DeploymentAdapter` port via `FakeDeploymentAdapter`.
- [ ] **PROV-03**: An `InstanceSpec` builder (`spec.py`) turns the entitlement
  picture (module set, seat cap, resource caps) into the orchestrator-agnostic
  spec; the entitlement-resolution approach (reconstruct-from-deltas vs
  read-back) is decided and documented (`docs/events.md` §Resolving
  entitlements).
- [ ] **PROV-04**: A failed convergence step records `last_error` on the task,
  sets the instance `failed_step`/`failure_reason`, and schedules an
  exponential-backoff Taskiq retry — the consumer keeps running. The fake
  adapter's fault-injection mode drives this deterministically.
- [ ] **PROV-05**: `subscription.lines_changed` converges an `update` (diff
  desired-vs-current), idempotent on `change_set_id` via
  `UNIQUE (instance_id, change_set_id)`.
- [ ] **PROV-06**: `subscription.suspended` soft-suspends and
  `subscription.reinstated` reinstates the instance.
- [ ] **PROV-07**: `subscription.cancelled` deprovisions — `immediate` now,
  `at_period_end` scheduled for `grace_until` (delayed Taskiq job) — generating
  a backup ref and tearing down via the adapter.
- [ ] **PROV-08**: On first `ready`, customer credentials are delivered via the
  `NotificationTransport` port (`ConsoleNotificationTransport` in dev);
  credentials are never placed in events or logs.

### Enforcement snapshot

- [ ] **SNAP-01**: The `provisioning.enforcement_snapshot` table exists and a
  versioned snapshot (`module_set`, `seat_cap`, `resource_caps`,
  `feature_flags`, monotonic `version`) is computed on convergence. (Serving it
  to a live Odoo plugin is milestone 2; the table + computation exist in M1 so
  platform-api's cross-schema read seam is real.)

### Event production

- [ ] **EVT-01**: A `provisioning.event_outbox` + relay publishes `instance.*`
  envelopes to `events.instance` (`XADD`, single `envelope` field,
  `MAXLEN ~ 100000`), with the outbox row written in the **same transaction** as
  the state change (`UNIQUE(envelope_id)`).
- [ ] **EVT-02**: The produced `instance.*` payload catalog is authored and
  emitted — `provisioned`, `updated`, `suspended`, `reinstated`, `failed`,
  `deprovisioned` (frozen, `extra="forbid"`, `producer="provisioning-worker"`,
  `causation_id` = triggering envelope id). `instance.deprovisioned`'s field set
  is symmetric with platform-api's `docs/events.md`.

## Out of scope (milestone 2 and beyond)

See `PROJECT.md` §Out of Scope and `.planning/seeds/`. Headlines: the real
`CoolifyAdapter` + Odoo stack template + per-instance Postgres strategy; the
per-instance bearer-token mechanism + serving `enforcement_snapshot` to a live
plugin; `SmtpNotificationTransport`; operator-triggered retry; hard suspension;
resource-cap enforcement beyond seats; a dead-letter stream. Telemetry/usage is
`telemetry-worker`.
