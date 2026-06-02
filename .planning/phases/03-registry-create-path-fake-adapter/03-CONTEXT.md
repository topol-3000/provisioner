# Phase 3: Registry & create-path (fake adapter) - Context

**Gathered:** 2026-06-02
**Status:** Ready for planning

<domain>
## Phase Boundary

A `subscription.activated` event drives a real `provisioning.instance` row from
`pending → deploying → configuring → ready` through the in-memory
`FakeDeploymentAdapter`, with exponential-backoff retry on injected failure and
console credential delivery on first `ready`. This is the first phase that
produces **domain value** (a registered, "running" instance) — Phase 2 proved
delivery+dedupe with no-op handlers; here `handle_subscription_activated` gains
a real body.

**In scope (PROV-01, PROV-02, PROV-03, PROV-04, PROV-08, SNAP-01 table):**
- The `provisioning.instance`, `provisioning.provisioning_task`, and
  `provisioning.enforcement_snapshot` tables (single Alembic tree), shaped to
  `docs/architecture.md §Postgres schema`. `instance.subscription_id` UNIQUE
  (1:1:1).
- The `DeploymentAdapter` **port** (`ports/deployment_adapter.py`) +
  `FakeDeploymentAdapter` adapter (in-memory, fault-injection mode), with the
  frozen `InstanceSpec` dataclass and an opaque `InstanceHandle`.
- The `NotificationTransport` **port** + `ConsoleNotificationTransport`.
- A new `EntitlementResolver` **port** + a default M1 resolver (see D-01..D-03).
- The `InstanceSpec` builder (`spec.py`) and the convergence service +
  create-path state machine (`service.py`), with adapter calls running as a
  retryable Taskiq `create` task (`tasks.py`).
- Console credential delivery on first `ready`; a minimal v1
  `enforcement_snapshot` row written at `configuring`.

**Explicitly NOT in scope (deferred to later phases):**
- Any **produced `instance.*` event** (`instance.provisioned`/`failed`/…) and
  the `event_outbox` + relay — **Phase 4** (this phase sets the DB columns the
  events will later read; it emits nothing onto `events.instance`).
- `lines_changed` / `suspended` / `reinstated` / `cancelled` convergence and
  the rest of the state machine — **Phase 5** (those handlers stay no-op here).
- `enforcement_snapshot` **recompute/versioning logic** (monotonic bump on each
  converging change) — **Phase 5**; Phase 3 writes only the initial v1 row.
- The real `CoolifyAdapter`, the per-instance bearer token, and
  `instance_credential` storage (hash) — **milestone 2**.
- Cross-schema read-back of the real entitled set — **milestone 2** (the
  `EntitlementResolver` seam is what makes that swap non-invasive).

</domain>

<decisions>
## Implementation Decisions

### Entitlement resolution → InstanceSpec (PROV-03)
- **D-01:** **M1 placeholder spec.** `subscription.activated` carries only
  `line_count` + `total_amount`, **not** the entitled module/seat/resource set
  (confirmed in `events/subscription.py`). Build a **deterministic**
  `InstanceSpec` from the activated fields + `Settings` defaults rather than
  reading the real entitlement picture. The fake adapter installs nothing real,
  so this is correct for M1 and avoids cross-schema coupling. `docs/events.md
  §Resolving entitlements` flags this as the M1 open contract question — this
  decision settles it for M1 as "placeholder now, read-back in M2".
- **D-02:** **Introduce an `EntitlementResolver` port now.** A `Protocol` in
  `ports/` with a default M1 implementation; milestone 2 swaps in the
  cross-schema read-back adapter **without touching `spec.py`**. Same
  ports/adapters discipline as `DeploymentAdapter`; the M1→M2 swap becomes a
  one-line wiring change in `main.py`.
- **D-03:** **Settings defaults populate the placeholder.** Typed `Settings`
  supply `odoo_image`, `instance_domain_suffix` (for
  `{slug}.{INSTANCE_DOMAIN_SUFFIX}`), `default_seat_cap`,
  `default_resource_caps` (JSON), and the default `module_set` (empty or a small
  base set). Deterministic and trivially unit-testable; real values arrive with
  read-back in M2. Do **not** invent a `line_count → seat_cap` mapping (the
  contract doesn't define one).

### Convergence execution (PROV-02)
- **D-04:** **One `create` Taskiq task drives all edges.** A single `create`
  `provisioning_task` walks `pending → deploying → configuring → ready`; on
  failure the **same** task re-runs and converges idempotently (diff
  desired-vs-current). Matches the `task_type` enum
  (`create|update|suspend|reinstate|delete` = one row per operation) and
  `docs/architecture.md §Task lifecycle` ("a retried create that already
  half-built an instance converges rather than duplicating"). No task-per-edge.
- **D-05:** **Handler opens, the job converges.** `handle_subscription_activated`
  (inside the Phase-2 dedupe `session_scope()` txn): insert the `instance` row
  (`pending`) + the `provisioning_task` (`pending`) + the `processed_event` row,
  commit, then enqueue the Taskiq `create` job. The Taskiq job performs **all**
  adapter calls and transitions (`deploying → configuring → ready`) + the
  credential notification. Keeps the consumer non-blocking; all slow/fail-prone
  work runs on the retry path (`docs/architecture.md §Process model`).
- **D-06:** **Injected `Clock` + poll `get_instance_status`.** The job polls
  `adapter.get_instance_status(handle)` until healthy with clock-driven waits;
  the fake transitions `deploying → healthy` instantly (or on an injected short
  timer) so unit tests are deterministic with no real sleep. The same poll code
  runs unchanged against a real adapter — this is the abstraction's acceptance
  test (`docs/deployment-adapter.md`). Add a `Clock` port if one isn't already
  present.
- **D-07:** **Write a minimal v1 `enforcement_snapshot` at `configuring`.** Write
  one `enforcement_snapshot` row (`module_set`/`seat_cap`/`resource_caps` from
  the spec, `version=1`) and set `instance.snapshot_version`, so Phase 4's
  `instance.provisioned` carries a **real** `snapshot_version` and the
  platform-api read seam is genuinely populated. Recompute/version-bump logic is
  **Phase 5**. (Resolves the roadmap's "SNAP-01 (table)" vs the architecture's
  "configuring writes snapshot" tension in favour of a single initial write.)

### Retry & failure semantics (PROV-04)
- **D-08:** **Settings-tunable exponential backoff.** `max_attempts`,
  `base_delay_s`, `multiplier`, `cap_s` as typed `Settings` — proposed defaults
  **5 attempts / 2s base / ×2 / 60s cap** (→ 2, 4, 8, 16, 32s). The
  fault-injection test overrides these to near-zero for fast, deterministic
  runs. Consistent with the `consumer_reclaim_min_idle_ms` Settings tunable from
  Phase 2.
- **D-09:** **`failed` on each attempt, re-enter on retry.** Each failed step
  sets `instance.status=failed` + `failed_step` + `failure_reason`; the
  scheduled retry transitions the instance back into `deploying` and clears the
  failure fields on success. Matches `docs/architecture.md §State machines`
  ("any step failure → status=failed, failed_step set"). **Note:** the
  `instance.failed` **event** emission itself is Phase 4+ — Phase 3 only sets the
  DB columns/status.
- **D-10:** **`provisioning_task` is the durable backoff ledger.**
  `attempt_count` / `max_attempts` / `next_attempt_at` / `last_error` are
  persisted in the converging transaction; the retry is a **delayed Taskiq
  re-kick** scheduled to `next_attempt_at`. Survives worker restart and is
  exactly the ledger operator-retry (M2) will read. Do **not** rely on Taskiq's
  in-memory retry middleware as the source of truth (it double-books the
  durability the columns already provide).

### Credential delivery (PROV-08)
- **D-11:** **Adapter generates secrets, returns them in the create-result.** The
  fake adapter generates db/admin passwords and returns them via a
  `CreateResult` (the opaque `InstanceHandle` **plus** the secrets), per
  `docs/deployment-adapter.md` ("secrets … are generated by the adapter and
  returned via the create result, never placed in the spec"). Same contract for
  the real Coolify adapter in M2. The fake returns a **stable** secret for the
  same spec so an idempotent re-run stays consistent.
- **D-12:** **In-memory pass-through, never persisted.** The `create` job carries
  the plaintext secret in-memory from the adapter result straight to the
  `NotificationTransport` call, then drops it. **Never** written to a column,
  **never** in an event, **never** through `structlog`
  (`docs/architecture.md §Observability`: "never log secrets"). All credential
  *storage* (`instance_credential` hash) is **milestone 2**.
- **D-13:** **Deliver once, guarded on first `ready_at`.** Send the credentials
  notification exactly when the instance first transitions to `ready`
  (`ready_at` goes `null → now` in this convergence). A re-converge or a retry
  that re-reaches `ready` does **not** re-notify. (Future `lines_changed →
  configuring → ready` re-converges in Phase 5 must not re-send credentials.)

### Claude's Discretion
- The exact `DeploymentAdapter` Protocol surface beyond the documented methods,
  and whether `create_instance` returns a combined `CreateResult` dataclass vs a
  `(handle, secrets)` tuple — keep it frozen and orchestrator-agnostic.
- The `DeploymentStatus` enum shape (how fake "deploying/healthy/…" maps onto the
  poll) and the poll interval/visibility constants within the clock-driven loop.
- ID strategy per `docs/architecture.md §schema` (`instance.id` uuid7,
  `processed_event.event_id` the 26-char ULID) — already constrained by the docs;
  wire it the documented way.
- The `EntitlementResolver` Protocol method signature and where the default M1
  impl lives (adapter vs a small default class) — honour the dependency rule.
- The `NotificationTransport` method name + the `CredentialNotification` shape
  (recipient_email, instance_url, admin_login, admin_password, instance_id);
  Console impl writes to stdout directly (not via `structlog`), marked dev-only.
- Test boundary: which create/retry coverage is fast in-memory unit (fake
  adapter + fakeredis) vs `@pytest.mark.integration` (testcontainers Postgres for
  the real instance/task rows). Keep the fast path Docker-free.
- The new Alembic revision content (review autogenerated SQL — it drops CHECK
  constraints / mishandles enums; the status enums need hand-checking).
- structlog `bind_contextvars` now also binds `instance_id` (available once the
  row is opened — it was deferred in Phase 2).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### This repo — authoritative specs
- `docs/deployment-adapter.md` — the **load-bearing seam**: the
  `DeploymentAdapter` Protocol (method signatures, idempotency contract), the
  frozen `InstanceSpec` dataclass shape, the opaque `InstanceHandle`, the
  `FakeDeploymentAdapter` + fault-injection mode, and "secrets generated by the
  adapter, returned in the create result" (grounds D-01, D-04..D-06, D-11). The
  acceptance test: identical convergence code against fake and real adapter.
- `docs/architecture.md §Postgres schema` — column shapes for `instance`,
  `provisioning_task`, `enforcement_snapshot` (grounds D-07, D-10; the
  `instance` read model mirrors platform-api's `Instance`).
- `docs/architecture.md §State machines` — the 8-status instance lifecycle and
  the `pending→deploying→configuring→ready` create path, "any step failure →
  status=failed", and the `provisioning_task pending→running→(succeeded|failed)`
  + backoff loop (grounds D-04, D-06, D-09, D-10).
- `docs/architecture.md §Process model` / §Modules — handler is thin
  (validate → dedupe → open/advance a task); long/fail-prone steps run as Taskiq
  jobs (grounds D-05).
- `docs/architecture.md §Errors` — typed `shared/errors.py` exceptions
  (`DeploymentFailed`, `AdapterTimeout`, `InvalidTransition`); adapter
  exceptions translated at the boundary, never leak into `service.py`.
- `docs/architecture.md §Observability` — bind `instance_id` now; never log
  secrets (grounds D-09, D-12, D-13).
- `docs/events.md §Resolving entitlements` — the reconstruct-vs-read-back open
  question this phase settles for M1 (grounds D-01..D-03); §Events this service
  CONSUMES `subscription.activated` — the payload's field set (no entitled set).
- `docs/events.md` (produced `instance.provisioned`) — **read-ahead for Phase 4**:
  the `snapshot_version` / `url` / `hostname` fields are why D-07 writes a real
  snapshot and sets the columns now.
- `CLAUDE.md §6.1.1` — module file layout (`models.py` SQLAlchemy, `schemas.py`
  Pydantic, `repository.py`, `service.py`, `handlers.py`, `tasks.py`, `spec.py`);
  §6.3 single Alembic tree + review autogenerate; §6.4 the three idempotency
  layers; §6.5 fake adapter fault-injection; §6.6 never log secrets.
- `docs/python-style.md` / `docs/conventions.md` — frozen dataclasses for value
  objects (`InstanceSpec`), Protocol-based DI, no `from __future__ import
  annotations`, Google docstrings.
- `.planning/REQUIREMENTS.md` — PROV-01..04, PROV-08, SNAP-01 (the Phase-3
  checklist).
- `.planning/ROADMAP.md §Phase 3` — goal + the 5 success criteria (the
  done-state this phase is judged against).

### Prior phase — binding decisions
- `.planning/phases/02-event-consumption-idempotency/02-CONTEXT.md` — Phase-2
  decisions Phase 3 builds on: **D-06** (the dedupe wrapper owns the
  `session_scope()`; the real state change joins that **same** transaction —
  grounds D-05), D-01 (the `EventConsumer`/`ValkeyStreamsConsumer` seam handlers
  register against), D-07 (`processed_event` schema), D-08 (`XAUTOCLAIM` reclaim
  through the same path). **Heads-up:** STATE.md notes a Phase-2 CONS-03
  IntegrityError gap on the concurrent/reclaim race — confirm it's closed before
  relying on the same-transaction guarantee here.

### Existing source to extend (not greenfield)
- `src/provisioning_worker/modules/provisioning/handlers.py` — the no-op
  `handle_subscription_activated` to replace with the D-05 body.
- `src/provisioning_worker/events/subscription.py` — the
  `SubscriptionActivatedPayload` the spec builder reads (confirms D-01: no
  entitled set on the wire).
- `src/provisioning_worker/modules/provisioning/models.py` — currently holds
  `ProcessedEvent`; add `Instance` / `ProvisioningTask` / `EnforcementSnapshot`.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Phase-2 dedupe wrapper** (`shared/event_consumer.py`) — already opens the
  `session_scope()` and inserts `processed_event`; the create handler's row+task
  inserts join this open session (D-05). No new transaction machinery needed.
- **`ports/` + `adapters/` pattern** (`event_consumer.py` +
  `valkey_streams.py`) — the established seam shape to mirror for
  `DeploymentAdapter`, `NotificationTransport`, `EntitlementResolver`, and
  `Clock`.
- **Typed `Settings`** (`settings.py`) — already the home for env-driven
  tunables (e.g. `consumer_reclaim_min_idle_ms`); extend with the D-03 spec
  defaults and D-08 backoff knobs.
- **`models.py`** already wires `Base.metadata` for Alembic autogenerate (Phase
  2) — the three new tables land in the same tree.

### Established Patterns
- **Same-transaction commit unit** (Phase 2 D-06) — state change +
  `processed_event` commit together; a crash before `XACK` re-delivers and
  short-circuits. The instance row + task insert extend this exact unit.
- **Frozen, `extra="forbid"` models** for contracts; **frozen dataclasses** for
  value objects (`InstanceSpec`).
- **Adapter-boundary error translation** — `redis`/`aiohttp` errors become
  `shared/errors.py` domain types; `service.py` only sees domain errors.

### Integration Points
- `main.py` composition root — wire `FakeDeploymentAdapter`,
  `ConsoleNotificationTransport`, the default `EntitlementResolver`, and `Clock`
  into the convergence service; register the (now-real) activated handler.
- Taskiq broker (booted as one of the four concerns in Phase 1) — the `create`
  job + its delayed re-kick register here.
- `instance` / `enforcement_snapshot` tables — platform-api's read seam (it
  reads these read-only); shapes must track `docs/architecture.md`.

</code_context>

<specifics>
## Specific Ideas

- Proposed backoff schedule: **5 attempts / 2s base / ×2 / 60s cap** (2, 4, 8,
  16, 32s) — a starting point, Settings-tunable (D-08).
- The fault-injection test is the canonical PROV-04 proof: `fail_on={"create"}`
  fails the first attempt(s), records `last_error`/`failed_step`, then the
  backoff retry succeeds — the consumer never crashes (ROADMAP success
  criterion 3).
- Credentials notification content: recipient email, instance URL, admin login +
  one-time admin password — emitted to stdout by the console transport, never
  through `structlog`.

</specifics>

<deferred>
## Deferred Ideas

- **Cross-schema entitlement read-back** (the real module/seat/resource set) —
  **milestone 2**, slotted behind the `EntitlementResolver` port introduced here
  (D-02).
- **`instance_credential` (hash storage) + per-instance bearer token** —
  **milestone 2**; M1 delivers credentials in-memory only (D-12).
- **`enforcement_snapshot` recompute + monotonic version bump** on each
  converging change — **Phase 5**; Phase 3 writes only the initial v1 row (D-07).
- **`instance.provisioned` / `instance.failed` event emission** + the
  `event_outbox` → relay — **Phase 4** (Phase 3 sets the columns those events
  will read but emits nothing).
- **`lines_changed` / `suspended` / `reinstated` / `cancelled` convergence** +
  the rest of the `instance.*` catalog — **Phase 5**.
- **Operator-triggered retry** (reads the `provisioning_task` ledger D-10
  produces) — **milestone 2**; M1 ships automatic backoff only.
- **`change_set_id` second-layer dedupe** (`UNIQUE (instance_id, change_set_id)`
  on `provisioning_task`) — the column/constraint may be created with the table
  here, but it's only **exercised** by `lines_changed` in **Phase 5**.

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 3-registry-create-path-fake-adapter*
*Context gathered: 2026-06-02*
