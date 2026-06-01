# Architecture

This document describes how `provisioner` (the **provisioning-worker**) is
organized internally: its process model, modules, the ports/adapters at its
external seams, the `provisioning` Postgres schema it owns, the instance
state machine it drives, and the cross-cutting concerns (event consumption,
idempotency, event production, observability, migrations).

It is the architectural source of truth for this repo. Cross-repo decisions
(the three-service split, the Valkey Streams event bus, per-customer
dedicated Odoo instances) are **restated** here where they constrain this
service — there is no shared platform HLD on disk to point at. The contracts
this service shares with `platform-api` are human-authored docs, not a shared
package: see [events.md](events.md) (the event contract) and platform-api's
`docs/api.md` (the instance read model and plugin API).

## Service position

```text
        platform-api (FastAPI control plane)
                 │
        publishes subscription.* events
                 │
                 ▼
        ┌─────────────────────────┐        Valkey Streams
        │   events.subscription   │  ◄──── (consumer group
        └─────────────────────────┘         cg.provisioning-convergence)
                 │ XREADGROUP
                 ▼
   ┌────────────────────────────────────────────────────────┐
   │              provisioning-worker (this repo)            │
   │  - Valkey Streams consumer (events.subscription)        │
   │  - provisioning convergence service (state machine)     │
   │  - Taskiq jobs (retryable provisioning steps, backoff)  │
   │  - transactional outbox → relay (events.instance)       │
   │  - tiny aiohttp /healthz (liveness only)                │
   └───────┬───────────────────┬───────────────────┬─────────┘
           │                   │                   │
   SQL (provisioning  DeploymentAdapter      publishes events
    schema, shared     (Fake v1 →            (events.instance,
    Postgres cluster)  Coolify later)         producer=
           │                   │              provisioning-worker)
           ▼                   ▼                   │
       Postgres          Customer Odoo             ▼
    provisioning.*        instance         events.instance ──► platform-api
                       (+ enforcement                         (cg.subscription-
                         plugin)                               convergence, its
                                                               Phase 6)
```

The dependency arrow is **one-way**. The worker consumes what `platform-api`
publishes and publishes events back onto the bus; it does **not** call
`platform-api`'s HTTP API in MVP, and `platform-api` does **not** call this
worker. The only shared state is the Postgres cluster (this service writes the
`provisioning` schema; platform-api reads it) and the Valkey event bus.

## Process model

Unlike `platform-api`, this service exposes **no request API**. The process is
a long-running worker with four concurrent concerns, started together from the
`__main__` entry point and supervised under one `asyncio` event loop:

| Concern | What it does | Implementation |
|---|---|---|
| **Stream consumer** | `XREADGROUP` on `events.subscription` via consumer group `cg.provisioning-convergence`; one handler per `subscription.*` type; acks with `XACK`; reclaims stuck entries with `XAUTOCLAIM`. | `adapters/valkey_streams.py` (consume side) + `shared/event_consumer.py` (idempotency wrapper) |
| **Convergence + tasks** | Each event opens/updates a `provisioning_task` and advances the instance state machine. Long or failure-prone steps run as Taskiq jobs with exponential backoff. | `modules/provisioning/service.py`, `tasks.py` |
| **Outbox relay** | Polls `provisioning.event_outbox` for unsent rows and publishes them to `events.instance`; marks `sent_at`. Mirrors platform-api's relay so instance events are emitted **atomically with** the DB transition that produced them. | `infrastructure/outbox_relay.py` |
| **Health probe** | A minimal `aiohttp.web` server exposing `GET /healthz` → `{"status":"ok"}` for the orchestrator's liveness check. No domain endpoints. | `infrastructure/health_server.py` |

`python -m provisioning_worker` boots all four and blocks until SIGTERM, then
drains in-flight tasks, `XACK`s nothing new, and closes the Valkey/Postgres
pools cleanly. There is no Granian/ASGI app — `aiohttp.web` (already a
dependency for the Coolify client) serves the health probe.

> **Why an outbox on the worker too.** Emitting `instance.provisioned` must be
> atomic with marking the instance `ready`; a crash between "DB committed" and
> "event published" must not lose or duplicate the event. The transactional
> outbox (write the event row in the same transaction as the state change, a
> relay publishes later, consumers dedupe on `envelope.id`) gives
> exactly-the-contract semantics. This is the same pattern platform-api uses
> for `billing.event_outbox`.

## Code layout

```text
provisioner/
├── alembic.ini                          # single section: `provisioning`
├── migrations/
│   └── provisioning/{env.py, versions/}
├── src/provisioning_worker/
│   ├── __init__.py
│   ├── __main__.py                      # `python -m provisioning_worker` entry
│   ├── main.py                          # composition root: wire adapters, start the 4 concerns
│   ├── settings.py                      # Pydantic Settings, env-driven
│   ├── infrastructure/                  # DB engine, logging, OTel, health_server, outbox_relay
│   ├── ports/                           # Protocol interfaces (one file each)
│   ├── adapters/                        # v1 implementations of the ports
│   ├── shared/                          # envelope, event_consumer, errors, ids
│   ├── events/                          # event payload models (consumed + produced) + envelope
│   └── modules/
│       └── provisioning/                # the one domain module
│           ├── models.py                # SQLAlchemy mapped classes
│           ├── schemas.py               # Pydantic command/result models
│           ├── repository.py            # async data access (ORM/SQL only)
│           ├── service.py               # convergence + state machine (business rules)
│           ├── handlers.py              # one handler per consumed event type
│           ├── tasks.py                 # Taskiq tasks (retryable steps)
│           └── spec.py                  # InstanceSpec builder (entitlements → desired state)
└── tests/
    ├── conftest.py                      # Fake adapters + testcontainers fixtures
    ├── provisioning/
    └── test_health.py
```

**Dependency rule** (identical to platform-api): `modules/` may import from
`infrastructure/`, `ports/`, `adapters/`, `shared/`, `events/`. Nothing in
`infrastructure/`, `ports/`, or `adapters/` may import from `modules/`. This
keeps the deployment adapter and the bus swappable and the domain testable.

File naming is load-bearing and matches platform-api: `models.py` =
SQLAlchemy, `schemas.py` = Pydantic, `repository.py` / `service.py` /
`handlers.py` / `tasks.py`. See [conventions.md](conventions.md).

## Modules

This service has a single domain module — **`provisioning`** — because it owns
exactly one responsibility: converging a customer's dedicated Odoo instance to
the state their subscription entitles them to.

- **`service.py`** holds the convergence logic and the instance state machine.
  It is the only place that decides a transition is legal and emits the
  corresponding `instance.*` event (via the outbox).
- **`handlers.py`** maps each consumed `subscription.*` event to a convergence
  intent (create / update / suspend / reinstate / deprovision). Handlers are
  thin: validate → dedupe → open/advance a `provisioning_task` → return.
- **`tasks.py`** runs the actual adapter calls (which can be slow or fail) as
  Taskiq jobs so they get retried with backoff without blocking the consumer.
- **`spec.py`** translates the entitlement picture (module set, seat cap,
  resource caps) into an orchestrator-agnostic `InstanceSpec` (see
  [deployment-adapter.md](deployment-adapter.md)).

## Ports and adapters

External systems and swappable strategies are reached through `Protocol`
interfaces in `ports/`. Each has a fake for tests and one real v1 adapter.

| Port | v1 adapter(s) | Used by | Status |
|---|---|---|---|
| `DeploymentAdapter` | `FakeDeploymentAdapter` (in-memory) → `CoolifyAdapter` (aiohttp) | `provisioning` service/tasks | **Fake is milestone 1; Coolify is milestone 2** |
| `MessageBus` (publish) | `ValkeyStreamsBus` | outbox relay | milestone 1 |
| `EventConsumer` (consume) | `ValkeyStreamsConsumer` | stream consumer loop | milestone 1 |
| `NotificationTransport` | `ConsoleNotificationTransport` (dev) → `SmtpNotificationTransport` | `provisioning` service (credentials email) | Console milestone 1; SMTP later |
| `Clock` / `IdGenerator` | system / ULID+uuid7 | everywhere (injected for deterministic tests) | milestone 1 |

The **deployment adapter is the load-bearing seam** (PRD §8.3). Provisioning
logic must be byte-for-byte identical whether it runs against the in-memory
fake or the real Coolify adapter — that is the acceptance test for the
abstraction. Its full contract lives in
[deployment-adapter.md](deployment-adapter.md).

Only adapters import third-party clients (`redis.asyncio`, `aiohttp`,
`taskiq`). Domain code talks to the ports.

## Postgres schema

This service **owns** the `provisioning` schema in the shared `platform`
cluster (created empty by `platform-infra/postgres/init/01-init.sql`; this
repo's Alembic tree creates the tables). No cross-schema foreign keys;
references to `subscription_id` / `customer_id` are **opaque UUIDs** enforced
at the application layer. `platform-api` reads `provisioning.instance` and
`provisioning.enforcement_snapshot` read-only.

| Table | Purpose |
|---|---|
| `instance` | The instance registry. One row per subscription (1:1:1 invariant). Read by platform-api's `/api/instance*` and `/api/ops/instances*`. |
| `provisioning_task` | The unit-of-work / retry ledger. One row per create/update/suspend/reinstate/delete attempt; drives backoff and operator retry. |
| `enforcement_snapshot` | The current entitlement snapshot per instance, served by platform-api's `plugin_api` to the in-Odoo enforcement plugin (polled with `If-None-Match` on `version`). |
| `instance_credential` | Hash of the per-instance bearer token the Odoo plugin uses (never plaintext). Milestone 2. |
| `event_outbox` | Outbound `instance.*` events awaiting publication to `events.instance`. |
| `processed_event` | Consumer idempotency ledger: `(event_id, consumer_group)` of every successfully handled inbound event. |

### `instance` (the registry — platform-api's read model)

Column shapes track platform-api's documented `Instance` resource
(`docs/api.md`), so cross-schema reads need no translation:

- `id` UUID PK (uuid7)
- `subscription_id` UUID **UNIQUE** (opaque) · `customer_id` UUID (opaque)
- `status` — the lifecycle enum (see state machine below)
- `hostname` text? · `url` text? (the live Odoo URL; set at `ready`)
- `admin_email` text?
- `desired_seat_cap` int? · `desired_resource_caps` JSONB
- `deployment_handle` JSONB? — adapter-specific handle (e.g. Coolify project/app/db ids); opaque to the domain
- `failed_step` text? · `failure_reason` text?
- `ready_at` timestamptz? · `last_status_check_at` timestamptz?
- `snapshot_version` int? — current `enforcement_snapshot.version`
- `version` int (optimistic-concurrency counter, bumped on every transition)
- `created_at` / `updated_at` timestamptz

### `provisioning_task`

- `id` UUID PK · `instance_id` UUID (FK within schema is allowed)
- `task_type` — `create | update | suspend | reinstate | delete`
- `status` — `pending | running | succeeded | failed`
- `source_event_id` text — ULID of the triggering event (idempotency)
- `change_set_id` UUID? — for `update`, the `subscription.lines_changed`
  idempotency key; a `UNIQUE (instance_id, change_set_id)` makes re-delivery a
  no-op
- `attempt_count` int · `max_attempts` int · `next_attempt_at` timestamptz?
  (exponential backoff)
- `last_error` text? · `payload` JSONB (the desired spec / deltas)
- `created_at` / `updated_at`

### `enforcement_snapshot`

Matches platform-api's `EntitlementSnapshot` (`docs/api.md`): `instance_id`
PK, monotonic `version` int, `computed_at`, `module_set` JSONB (`list[str]`),
`seat_cap` int, `resource_caps` JSONB, `feature_flags` JSONB
(`dict[str, bool]`). Milestone-2 concern (it only matters once a real Odoo
plugin polls it), but the table is created in milestone 1 so the cross-schema
read seam exists.

## State machines

### Instance lifecycle (owned by this service)

```text
            subscription.activated
                 │ create instance row
                 ▼
            ┌─────────┐  enqueue create task
            │ pending │
            └────┬────┘
                 │ adapter.createInstance
                 ▼
            ┌───────────┐  wait healthy
            │ deploying │
            └────┬──────┘
                 │ install modules · set caps · create admin
                 ▼                · mint per-instance token · write snapshot
            ┌──────────────┐
            │ configuring  │
            └────┬─────────┘
                 │ emit instance.provisioned · send credentials email
                 ▼
            ┌────────┐   subscription.lines_changed → (update) → configuring → ready
            │ ready  │◄──────────────────────────────────────────────────────┐
            └────┬───┘   subscription.reinstated → (reinstate) ───────────────┘
                 │
                 │ subscription.suspended → adapter.suspendInstance (soft)
                 ▼
            ┌───────────┐  subscription.reinstated → ready
            │ suspended │
            └────┬──────┘
                 │ subscription.cancelled (immediate, or at grace_until)
                 ▼
            ┌────────────────┐  backup → adapter.deleteInstance
            │ deprovisioning │
            └────┬───────────┘
                 │ emit instance.deprovisioned
                 ▼
            ┌──────────────┐
            │ deprovisioned│
            └──────────────┘

   Any step failure → status=failed, failed_step set, instance.failed emitted,
   task scheduled for backoff retry. Operator can force a retry (milestone 2).
```

The eight statuses — `pending`, `deploying`, `configuring`, `ready`,
`suspended`, `failed`, `deprovisioning`, `deprovisioned` — are exactly the set
platform-api's `/api/instance*` documents. The customer portal renders six of
them (it treats `deprovisioning`/`deprovisioned` as terminal/gone) and polls
every 10s while the status is non-terminal (`pending`/`deploying`/
`configuring`). The mapping from finer internal step detail (image pull, db
init, module install) onto these public statuses is this service's
responsibility — keep internal step detail in `provisioning_task`, expose only
the public status on `instance`.

`subscription.cancelled` carries `cancellation_kind` and `grace_until`:
`immediate` deprovisions now; `at_period_end` schedules deprovision for
`grace_until` (a delayed Taskiq job), generating the data-export/backup within
the window per PRD §10.4.

### Task lifecycle

`provisioning_task` rows go `pending → running → (succeeded | failed)`. A
`failed` task with `attempt_count < max_attempts` is re-scheduled at
`next_attempt_at` with exponential backoff. Convergence is **idempotent**: a
task re-runs by diffing desired-vs-current and applying only the missing
operations, so a retried `create` that already half-built an instance
converges rather than duplicating.

## Event consumption and idempotency

The worker reads `events.subscription` with `XREADGROUP GROUP
cg.provisioning-convergence <consumer-name>`. Each stream entry stores the full
envelope JSON under a single field `envelope` (platform-api's wire format);
the consumer reads that field, parses it into a local `EventEnvelope`
(re-implemented per repo — no shared package), and dispatches on `type`.

Delivery is **at-least-once**. Every handler is idempotent on `envelope.id`:
before doing work it checks `processed_event (event_id, consumer_group)`; the
insert of that row commits **in the same transaction** as the state change, so
a crash before `XACK` re-delivers the event and the handler short-circuits.
`shared/event_consumer.py` wraps every registered handler with this guard
(mirrors the helper platform-api's `docs/events.md` describes). Stuck entries
(consumer died mid-batch) are reclaimed with `XAUTOCLAIM` after a visibility
timeout.

A **malformed** entry — one that fails to JSON-parse or violates the
envelope's `extra="forbid"` — is a poison message: retrying cannot fix it. The
consumer logs it at `error`, `XACK`s it so it cannot block the group, and
increments a metric; it never creates or advances an instance. (A dead-letter
stream for forensic replay is a later addition, not milestone 1.)

For `subscription.lines_changed`, idempotency additionally keys on
`change_set_id` (the documented downstream-provisioning idempotency key) via
the `UNIQUE (instance_id, change_set_id)` on `provisioning_task`.

Events are best-effort-ordered within a stream and not ordered across streams.
The convergence model (diff desired-vs-current, named `*-convergence` on both
sides) tolerates reordering: an out-of-order `lines_changed` before
`activated` parks until the instance row exists, rather than corrupting state.

## Event production

Instance events are written to `provisioning.event_outbox` inside the
transaction that performs the state change, then published to `events.instance`
by the relay. Envelopes use the **shared envelope shape** with
`producer="provisioning-worker"`, a fresh 26-char ULID `id`, `occurred_at` in
UTC, and `causation_id` set to the triggering `subscription.*` envelope's `id`.
Routing follows the shared rule `events.<prefix-before-first-dot>`, so
`instance.*` types land on `events.instance`. The full produced/consumed
catalog and payload schemas are in [events.md](events.md).

## Cross-cutting concerns

### Authentication

This service authenticates to nothing in MVP: it reads Valkey and writes
Postgres on the trusted internal network, and does not call platform-api.
There is no Keycloak client for the worker.

The one credential it **issues** is the **per-instance bearer token** the Odoo
enforcement plugin uses to call platform-api's `/api/instance/{id}/*` plugin
endpoints. The worker mints a high-entropy opaque token at provisioning time,
stores only its hash in `instance_credential` (with an issue/rotate/expiry
timestamp; 24h rotation grace), hands the plaintext to the instance once, and
platform-api's `require_instance` validates by hashing the presented token and
matching the stored hash via a cross-schema read. (A signed-JWT alternative is
viable but introduces a shared signing key — defer that choice and **coordinate
the exact mechanism with platform-api's `plugin_api` before building it**.)
This is a **milestone-2** concern.

### Errors

Domain failures raise typed exceptions from `shared/errors.py`
(`ProvisioningError` and subclasses such as `DeploymentFailed`,
`InvalidTransition`, `AdapterTimeout`). Adapter-level exceptions
(`redis.RedisError`, `aiohttp.ClientError`) are translated to domain errors at
the adapter boundary — they never leak into `service.py`. A failed convergence
step records `last_error` on the task, sets the instance `failed_step` /
`failure_reason`, emits `instance.failed`, and schedules a retry; it does not
crash the consumer.

### Idempotency

Three layers, all required: inbound events dedupe on
`(envelope.id, consumer_group)` in `processed_event`; line changes dedupe on
`change_set_id`; outbound events dedupe at the consumer via `envelope.id`
(the outbox `UNIQUE(envelope_id)` prevents double-enqueue). Adapter operations
are themselves idempotent (diff desired-vs-current; re-creating an existing
instance converges).

### Observability

- **Logs** — `structlog` JSON outside dev. Bind `envelope_id`,
  `subscription_id`, `instance_id`, and `correlation_id` (carried from the
  inbound envelope) via `bind_contextvars` at the top of each handler; never
  thread them through call chains.
- **Tracing** — OpenTelemetry SDK + OTLP exporter (`OTEL_*` env), optional
  backend in MVP. Continue the inbound `correlation_id` as the trace so a
  customer request → platform-api → worker → Odoo is one trace.
- **Metrics** — at minimum: consumer lag (stream length − last-acked),
  convergence duration per `task_type`, task failure/retry counts, outbox
  backlog depth. RED-style.

### Database sessions

Async SQLAlchemy 2.0 (psycopg driver). Workers use an explicit
`session_scope()` async context manager (platform-api exposes the same for its
worker-style code); the handler owns the transaction boundary and commits
explicitly — the state change, the `processed_event` insert, and the
`event_outbox` insert all commit together or not at all.

### Migrations

A **single** Alembic tree for the `provisioning` schema (`alembic.ini` section
`provisioning`, `version_table_schema=provisioning`), simpler than
platform-api's multi-tree setup. Forward-only; review autogenerated SQL before
committing (autogenerate loses CHECK constraints and mishandles enums). Use
`make revision name="..."` / `make migrate`.

### Configuration

Pydantic-settings, env-driven, validated at startup. Key vars (mirroring
platform-api naming where shared):

`DATABASE_URL` (postgresql+psycopg async) · `DATABASE_URL_SYNC` (Alembic) ·
`VALKEY_URL` (`redis://…:6379/0`, same instance as platform-api) ·
`PROVISIONING_CONSUMER_GROUP` (default `cg.provisioning-convergence`) ·
`CONSUMER_NAME` (per-replica) · `OUTBOX_POLL_SECONDS` · `OUTBOX_BATCH_SIZE` ·
`DEPLOYMENT_ADAPTER` (`fake | coolify`) · `NOTIFICATION_TRANSPORT`
(`console | smtp`) · `HEALTH_PORT` (default `8001`, off platform-api's
`8000`) · `INSTANCE_DOMAIN_SUFFIX` ·
`ODOO_BASE_IMAGE` · `OTEL_EXPORTER_OTLP_ENDPOINT` ·
`OTEL_SERVICE_NAME` (default `provisioning-worker`) · `LOG_LEVEL`.
Milestone 2 adds `COOLIFY_API_URL`, `COOLIFY_API_TOKEN`, `SMTP_*`, and the
per-instance token signing/hashing config.

## What this service does NOT do

- **No HTTP request API.** It serves only `/healthz`. Customer/operator views
  of instance status are served by **platform-api** reading `provisioning.*`.
- **No Stripe / billing logic.** It reacts to `subscription.*` events; it never
  talks to Stripe.
- **No subscription state.** It never writes the `subscription` schema; it
  reconciles its own `provisioning` schema from events and, when its
  `instance.deprovisioned` is consumed (platform-api Phase 6), platform-api
  closes out the subscription.
- **No telemetry / usage polling.** Per-instance health and usage belong to
  `telemetry-worker` (`telemetry` schema). The portal's "recent usage" comes
  from there, not from this service.
- **No shared contracts package.** Event/envelope/payload models are
  re-implemented here against [events.md](events.md) and platform-api's
  `docs/events.md`; drift is caught by review and the evolution discipline.

## MVP milestones (scope at a glance)

- **Milestone 1 — pipeline against the fake adapter.** Stream consumer +
  idempotency, the `provisioning` schema, the convergence service + instance
  state machine, the Taskiq retry/backoff path, the outbox + relay, and the
  full `instance.*` event catalog — all driven by `FakeDeploymentAdapter` and
  `ConsoleNotificationTransport`. Entirely unit-testable; no Coolify, no real
  Odoo. Unblocks platform-api Phase 5/6 reads and portal capability A.8 against
  a real registry.
- **Milestone 2 — real deployment.** A Coolify-API spike, then `CoolifyAdapter`
  + the Odoo stack template, the `enforcement_snapshot` served to a real
  plugin, the per-instance token mechanism, SMTP notifications, and operator
  retry.

See [deployment-adapter.md](deployment-adapter.md) for the adapter contract and
[events.md](events.md) for the event contract.
