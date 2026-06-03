# Phase 4: Event production (outbox → relay) - Context

**Gathered:** 2026-06-03
**Status:** Ready for planning

<domain>
## Phase Boundary

When an instance **first reaches `ready`**, an `instance.provisioned` envelope is
written to `provisioning.event_outbox` **in the same transaction** as the `ready`
state change, and a relay loop drains unsent rows to `events.instance` via `XADD`
(`MAXLEN ~ 100000`), marking them sent — with publish failures recorded
(`last_error`, `attempt_count`) and retried on the next poll, never lost or
duplicated. This is the first phase that **produces** an `instance.*` event; it
makes "instance reached `ready`" and "`instance.provisioned` emitted" atomic.

This phase is **MVP mode** (vertical slice). The slice is the full
ready → outbox → relay → `events.instance` path for the single `instance.provisioned`
event, observable end-to-end via `valkey-cli XRANGE events.instance - +`.

**In scope (EVT-01, EVT-02 `instance.provisioned`):**
- The `provisioning.event_outbox` table (single Alembic tree), mirroring
  platform-api's `billing.event_outbox` shape: `id`, `envelope_type`,
  `envelope_id` (UNIQUE), `stream`, `payload` (JSONB), `created_at`
  (server_default now()), `sent_at` (nullable), `last_error` (nullable),
  `attempt_count` (default 0).
- The produced `InstanceProvisionedPayload` model (frozen, `extra="forbid"`,
  no credentials) in `events/`, copied byte-for-byte from `docs/events.md`.
- A producer-side `EventEnvelope.build(...)` classmethod (restored — the
  consume-side dropped it in Phase 2 D-03) that mints a fresh ULID,
  `producer="provisioning-worker"`, UTC `occurred_at`, and accepts
  `causation_id`.
- A `MessageBus` **port** (publish-only) + `ValkeyStreamsBus` **adapter**
  (the only place `redis.asyncio` publish/`XADD` lives), mirroring
  platform-api's `ports/message_bus.py` + `adapters/valkey_streams_bus.py`.
- An `OutboxRepo.enqueue(session, envelope)` repository writer
  (`ON CONFLICT (envelope_id) DO NOTHING`).
- A `ProvisioningService.emit_instance_provisioned(...)` method — the single
  place an `instance.*` event is emitted (CLAUDE.md §6.1.1).
- The real relay loop body in `infrastructure/outbox_relay.py` replacing the
  Phase-1 no-op: `SELECT … FOR UPDATE SKIP LOCKED` batched drain → rebuild typed
  envelope via a produced-side registry → `bus.publish()` → mark sent / record
  failure.
- A produced-side `envelope_class_for(...)` registry (one entry in M1:
  `instance.provisioned`).
- Relay + bus wiring into the composition root (`main.py`) — the relay concern
  gains a real `session_factory` + `bus`.

**Explicitly NOT in scope (deferred):**
- The rest of the produced `instance.*` catalog (`updated`, `suspended`,
  `reinstated`, `failed`, `deprovisioned`) and their emit paths — **Phase 5**
  (and platform-api Phase 6 consumes `deprovisioned`). EVT-02's full catalog is
  scoped here to `instance.provisioned` only, per ROADMAP Phase 4.
- A max-attempts cap / terminal "poison outbox row" state and a dead-letter
  stream — **not milestone 1** (mirrors the Phase-2 DLQ deferral).
- `events.instance` **consumption** (the `MessageBus` stays publish-only;
  platform-api adds the consumer in its Phase 6).
- Metrics (outbox backlog depth, publish failure counts) — **Phase 5** (OBS-01
  metrics half).

</domain>

<decisions>
## Implementation Decisions

### Exactly-once emission (EVT-01)
- **D-01:** **First-ready guard is the PRIMARY exactly-once mechanism.** The
  outbox row is enqueued inside the **same** `session_scope()` that performs the
  `ready_at null→now` transition + `record_task_success`
  ([tasks.py step 4](../../../src/provisioning_worker/modules/provisioning/tasks.py)). That transition already happens exactly once per
  instance (guarded by `ready_at IS NULL`, D-13), so the event is emitted exactly
  once. The emit MUST join that existing transaction — not a new one — so the
  state change and the outbox insert commit together or not at all.
- **D-02:** **Fresh ULID at emit; `UNIQUE(envelope_id)` is a backstop, not the
  primary mechanism.** `EventEnvelope.build()` mints a fresh random ULID exactly
  like platform-api. No deterministic-id scheme. The outbox `UNIQUE(envelope_id)`
  + `OutboxRepo.enqueue` using `ON CONFLICT DO NOTHING` is defense-in-depth
  against a pathological double-insert; in practice the first-ready guard means
  it never fires. Consumer-side dedup on `envelope.id` (`docs/events.md`) is the
  downstream safety net.

### Relay failure policy (EVT-01, Success Criterion 3)
- **D-03:** **Retry forever — no cap, no dead-letter.** A publish failure records
  `last_error` (truncated, ~2000 chars), bumps `attempt_count`, leaves
  `sent_at` NULL, and the row is retried on every subsequent poll indefinitely.
  No max-attempts cap, no terminal state, no DLQ in M1. Mirrors platform-api's
  relay verbatim and matches the Phase-2 "log + survive, no DLQ" poison
  deferral. `SKIP LOCKED` ensures a stuck row never blocks the rest of the batch.
- **D-04:** **Transport failure and deserialize/validation failure are treated
  identically.** Any exception during rebuild-or-publish records
  `last_error` + bumps `attempt_count` + retries next poll — no special-casing of
  permanent errors. A malformed stored payload is near-impossible since the
  envelope is written from an already-validated model in the same process; if one
  ever churns, `SKIP LOCKED` keeps it harmless.

### Relay claim strategy + body (EVT-01, Success Criterion 2)
- **D-05:** **`SELECT … FOR UPDATE SKIP LOCKED`, batched, one transaction per
  drain.** Each `_drain_once` selects up to `settings.outbox_batch_size` unsent
  rows ordered by `created_at ASC` with `with_for_update(skip_locked=True)`,
  processes them inside one session/transaction, then commits — so row locks
  always release at the iteration boundary whether publish succeeded or raised.
  Multi-replica-safe (disjoint row sets) at zero cost for the M1 single replica;
  future horizontal scaling needs no rewrite. Direct mirror of platform-api's
  `_drain_once`.
- **D-06:** **The relay rebuilds the typed envelope before publishing.** The
  outbox stores `payload` as JSONB; at publish time the relay does
  `envelope_class_for(row.envelope_type).model_validate(row.payload)` and the bus
  re-serializes via `model_dump_json()`. This requires a small **produced-side**
  `envelope_class_for` registry (M1: `{"instance.provisioned": …}`), kept
  symmetric with the consume-side `payload_class_for` registry and ready to grow
  with Phase 5's catalog. Validates on the way out (mirrors platform-api).

### Emit seam (EVT-01, CLAUDE.md §6.1.1)
- **D-07:** **`service.py` mints, `OutboxRepo` enqueues, `tasks.py` calls inside
  the ready txn.** Add `ProvisioningService.emit_instance_provisioned(session,
  instance, causation_id)` that builds the envelope via `EventEnvelope.build()`
  and calls `OutboxRepo.enqueue(session, envelope)` (repository layer,
  `ON CONFLICT DO NOTHING`). `tasks.py` invokes this inside the **same**
  `session_scope()` that commits the `ready` transition (D-01). Honors CLAUDE.md
  §6.1.1 ("service.py is the only place a transition is judged legal **and** the
  matching `instance.*` event is emitted via the outbox") and mirrors
  platform-api's `OutboxRepo`. The outbox INSERT lives in the repository
  (`repository.py` / a dedicated `OutboxRepo`), never in `infrastructure/`.

### Payload field mapping (EVT-02)
- **D-08:** **`hostname = f"{spec.slug}.{settings.instance_domain_suffix}"`,
  `url = f"https://{hostname}"`.** Fix the Phase-3 placeholder
  `url=f"https://{spec.slug}"` ([tasks.py ready transition](../../../src/provisioning_worker/modules/provisioning/tasks.py)) so both the
  `instance.url` column and the payload carry the full FQDN. Use a single
  derivation helper for the ready transition and the payload so they never drift.
- **D-09:** **Remaining payload fields map straight off the `Instance` row /
  convergence:** `provisioned_at = ready_at` (the `clock.now()` used for the
  ready transition), `causation_id = task.source_event_id` (the triggering
  `subscription.activated` ULID, already persisted on the task —
  `provisioning_task.source_event_id`), `snapshot_version` =
  `instance.snapshot_version` (written at `configuring`, D-07 Phase 3),
  `admin_email` = `instance.admin_email`/`spec.admin_email`, and
  `instance_id`/`subscription_id`/`customer_id` from the instance row. The
  payload carries **no credentials** (D-12 Phase 3; `docs/events.md`).

### Claude's Discretion
- The exact `MessageBus` Protocol surface (publish-only) and `ValkeyStreamsBus`
  constants (`_MAXLEN=100_000`, `approximate=True`) — mirror platform-api.
- Whether `OutboxRepo` is a small class (platform-api parity) or a module
  function in `repository.py`; the `event_outbox` PK type (uuid7) and index
  choices beyond `UNIQUE(envelope_id)`.
- Where `EventEnvelope.build()` lives and its exact signature (payload, type,
  version, causation_id, correlation_id passthrough) — keep it the producer
  mint helper; the consume path stays as-is.
- The `stream` column derivation (`stream_for_envelope_type(envelope.type)` at
  enqueue vs at publish) — keep the routing rule shared between repo and bus.
- How `task.source_event_id` is made available at the emit point in step 4
  (capture-while-open vs re-read in the ready session) given
  `expire_on_commit=True` detaches ORM objects (CR-01 pattern from Phase 3).
- Test boundary: fast in-memory unit (fakeredis + the outbox row asserted in a
  testcontainers Postgres only where the same-txn / `SKIP LOCKED` guarantee
  needs a real engine) vs `@pytest.mark.integration`. Keep the fast path
  Docker-free; the relay drain + `XADD` round-trip is the natural integration
  test.
- The new Alembic revision content — review autogenerated SQL (it drops CHECK
  constraints / mishandles defaults; `server_default` on `attempt_count` and
  `created_at` need hand-checking).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### This repo — authoritative specs
- `docs/events.md §Events this service PRODUCES` → `instance.provisioned` — the
  exact `InstanceProvisionedPayload` field set to copy byte-for-byte (frozen,
  `extra="forbid"`, no credentials) (grounds D-08, D-09); §Envelope + §Wire
  format — the produced envelope shape (`producer="provisioning-worker"`, fresh
  ULID `id`, `causation_id`, single `envelope` field) and `model_dump_json()`
  wire format (grounds D-02, D-06); §Stream routing — `events.<prefix>` →
  `events.instance` (grounds D-05, D-06); §Retention — `MAXLEN ~ 100000`,
  approximate trim (grounds D-06); §Producer/consumer summary — only we produce
  `instance.provisioned`, no consumer in M1.
- `docs/architecture.md §Event production` — outbox written **inside the
  state-change transaction**, then relay publishes; shared envelope shape;
  `causation_id` = triggering `subscription.*` id (grounds D-01, D-07, D-09).
- `docs/architecture.md §Idempotency` — outbound dedup via `envelope.id`; outbox
  `UNIQUE(envelope_id)` prevents double-enqueue (grounds D-02).
- `docs/architecture.md §Database sessions` — `session_scope()`; the state
  change + `processed_event` + `event_outbox` insert all commit together
  (grounds D-01, D-07).
- `docs/architecture.md §Errors` / §Observability — adapter errors translated at
  the boundary; never log secrets; the relay never dies (catch + log per
  iteration) (grounds D-03, D-04).
- `CLAUDE.md §6.1.1` — module file layout: `service.py` is the only place an
  `instance.*` event is emitted via the outbox; `repository.py` holds the SQL;
  `infrastructure/` is plumbing only (grounds D-07). §6.2 — copy produced payload
  + envelope from `docs/events.md`, never publish directly (outbox → relay).
  §6.3 single Alembic tree + review autogenerate. §6.4 the three idempotency
  layers (layer 3 = outbound). §6.6 never log secrets/tokens.
- `docs/deployment-adapter.md` — `InstanceSpec.slug` + the domain-suffix source
  for D-08's hostname derivation.
- `docs/python-style.md` / `docs/conventions.md` — frozen `extra="forbid"`
  models, Protocol-based DI, no `from __future__ import annotations`, Google
  docstrings, async-first.
- `.planning/REQUIREMENTS.md` — EVT-01, EVT-02 (the Phase-4 checklist).
- `.planning/ROADMAP.md §Phase 4` — goal + the 4 success criteria (the done-state
  this phase is judged against; note EVT-02 is scoped to `instance.provisioned`).

### Sibling repo — the reference implementation to MIRROR (read before writing)
- `../platform-api/src/platform_api/infrastructure/outbox_relay.py` — the
  `run_outbox_relay` + `_drain_once` loop to mirror verbatim: `FOR UPDATE SKIP
  LOCKED`, rebuild-validate-publish, mark sent / record `last_error` + bump
  `attempt_count`, relay-never-dies (grounds D-03, D-04, D-05, D-06).
- `../platform-api/src/platform_api/ports/message_bus.py` — the publish-only
  `MessageBus` Protocol to re-implement (grounds the port).
- `../platform-api/src/platform_api/adapters/valkey_streams_bus.py` — the
  `ValkeyStreamsBus` adapter (`XADD MAXLEN ~ 100000`, approximate trim,
  `redis.asyncio`, propagate transport errors) (grounds D-04, D-06).
- `../platform-api/src/platform_api/modules/billing/models.py` →
  `EventOutbox` — the table shape to mirror (`envelope_id` UNIQUE, `payload`
  JSONB, `created_at` server_default, `sent_at`/`last_error`/`attempt_count`).
- `../platform-api/src/platform_api/modules/billing/repository.py` →
  `OutboxRepo.enqueue` — the `pg_insert(...).on_conflict_do_nothing
  (index_elements=["envelope_id"])` writer (grounds D-02, D-07).
- `../platform-api/src/platform_api/events/envelope.py` → `EventEnvelope.build`
  — the producer mint classmethod to restore here (grounds D-02).

### Existing source to extend (not greenfield)
- `src/provisioning_worker/infrastructure/outbox_relay.py` — the Phase-1 no-op
  poll loop whose body this phase replaces with the real drain (D-05); already
  wired into `main.py`'s TaskGroup, but only with `settings` — add
  `session_factory` + `bus`.
- `src/provisioning_worker/events/envelope.py` — add the producer `build()`
  (Phase-2 D-03 deliberately dropped it).
- `src/provisioning_worker/events/__init__.py` — add the produced-side
  `envelope_class_for` registry + `InstanceProvisionedPayload` export (D-06).
- `src/provisioning_worker/modules/provisioning/tasks.py` — the ready transition
  (step 4) where the emit call joins the existing `session_scope()` (D-01, D-07)
  and the `url=` derivation is fixed (D-08).
- `src/provisioning_worker/modules/provisioning/service.py` — add
  `emit_instance_provisioned` (D-07).
- `src/provisioning_worker/modules/provisioning/repository.py` /
  `models.py` — `EventOutbox` model + `OutboxRepo.enqueue` (D-07).
- `src/provisioning_worker/settings.py` — `outbox_poll_seconds`,
  `outbox_batch_size` already present; `instance_domain_suffix` exists (D-03
  Phase 3) for D-08.
- `src/provisioning_worker/main.py` — composition root: construct
  `ValkeyStreamsBus`, pass `session_factory` + `bus` into `run_outbox_relay`.

### Prior phase — binding decisions
- `.planning/phases/03-registry-create-path-fake-adapter/03-CONTEXT.md` — D-07
  (snapshot_version set at `configuring` — read by D-09 here), D-12/D-13
  (credentials never in events / first-ready guard — grounds D-01), D-03
  (`instance_domain_suffix` setting — grounds D-08), the CR-01
  capture-while-session-open pattern.
- `.planning/phases/02-event-consumption-idempotency/02-CONTEXT.md` — D-03
  (consume-side envelope dropped `build()` — this phase restores the producer
  side), the `session_scope()` same-transaction commit unit the emit joins, and
  the "no DLQ in M1" deferral (grounds D-03).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Phase-1 no-op relay** (`infrastructure/outbox_relay.py`) — already runs the
  `asyncio.wait_for(shutdown.wait(), timeout=poll_seconds)` poll-sleep pattern
  and is wired into `main.py`'s TaskGroup; only the loop body needs the real
  drain (D-05).
- **platform-api outbox→relay stack** — a near-verbatim template for every new
  artifact (model, port, adapter, repo, relay body, envelope `build()`).
- **`session_scope()`** (`infrastructure/db.py`) — the explicit async txn
  boundary the emit joins (D-01, D-07).
- **`provisioning_task.source_event_id`** — already persists the triggering
  envelope ULID for `causation_id` (D-09).
- **Typed `Settings`** — `outbox_poll_seconds`, `outbox_batch_size`,
  `instance_domain_suffix` already defined.

### Established Patterns
- **Hexagonal ports/adapters** — new `MessageBus` port + `ValkeyStreamsBus`
  adapter is the only place `redis.asyncio` publish lives; domain talks to the
  port. Symmetric to the consume-side `EventConsumer`/`ValkeyStreamsConsumer`.
- **Single `provisioning` Alembic tree** — `make revision` for `event_outbox`;
  review autogenerated SQL (server_defaults, no CHECK loss).
- **Frozen `extra="forbid"` contract models** copied from `docs/events.md`.
- **CR-01 capture-while-open** — read scalars before `session_scope()` exits
  (`expire_on_commit=True`).

### Integration Points
- Writes to `events.instance` — the stream platform-api will consume in its
  Phase 6 (`cg.subscription-convergence`) for `instance.deprovisioned`; M1 has no
  consumer, so the smoke check is `valkey-cli XRANGE events.instance - +`.
- The emit joins the **existing** ready-transition transaction in `tasks.py` —
  no new transaction machinery.
- `event_outbox` rows are private to this worker; platform-api reads
  `provisioning.instance` / `enforcement_snapshot`, not the outbox.

</code_context>

<specifics>
## Specific Ideas

- **Smoke/done-state shape (ROADMAP success criteria):** drive a
  `subscription.activated` through to `ready`; assert one `instance.provisioned`
  row in `event_outbox` written in the same txn as the `ready` update; the relay
  publishes it to `events.instance` and sets `sent_at`; `valkey-cli XRANGE
  events.instance - +` shows the envelope with `producer="provisioning-worker"`,
  a fresh ULID `id`, and `causation_id` = the triggering `subscription.activated`
  id. A `bus.publish` failure (inject a Valkey error) leaves the row unsent with
  `last_error`/`attempt_count` bumped and succeeds on the next drain — no
  duplicate on `events.instance`.
- The relay must **never die**: each iteration is wrapped in try/except + log,
  mirroring platform-api (`"outbox relay iteration crashed"`).
- A credential/notification-transport failure already does NOT re-fail a
  converged instance (Phase-3 WR-06) — the outbox emit is upstream of that and
  inside the ready txn, so it is unaffected.

</specifics>

<deferred>
## Deferred Ideas

- **The rest of the produced `instance.*` catalog** (`updated`, `suspended`,
  `reinstated`, `failed`, `deprovisioned`) + their emit paths — **Phase 5**;
  platform-api consumes `deprovisioned` in its Phase 6. The produced-side
  `envelope_class_for` registry created here grows then.
- **Max-attempts cap / terminal "poison outbox row" + dead-letter stream** —
  **not milestone 1** (mirrors Phase-2 DLQ deferral); revisit if publish-failure
  volume warrants replay tooling.
- **`events.instance` consumption** (a `MessageBus.consume` surface) —
  platform-api adds the consumer in its Phase 6; the port stays publish-only here.
- **Metrics** (outbox backlog depth, publish failure/retry counts) — **Phase 5**
  (OBS-01 metrics half).

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 4-event-production-outbox-relay*
*Context gathered: 2026-06-03*
