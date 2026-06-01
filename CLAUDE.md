# CLAUDE.md — guidance for Claude Code agents in this repo

This file is loaded automatically by Claude Code at the start of every
session. It tells Claude what this repo is, how it's organised, where
the authoritative specs live, and what conventions matter when writing
or modifying code here.

If you are a human reader, this also doubles as the onboarding doc for
a new engineer — keep it lean and accurate; outdated guidance here will
mislead both Claude and your colleagues.

---

## 1. What this repo is

`provisioner` is the **provisioning-worker** for the **Odoo Entitlements
SaaS Platform**: a long-running Taskiq / stream-consumer worker, not an
HTTP service. It is one of three Python deployables; the other two are
`platform-api` (the FastAPI control plane that emits the events this
worker consumes) and `telemetry-worker`.

Its single responsibility is **convergence**: drive each customer's
dedicated Odoo instance to the state their subscription entitles them
to. Concretely it:

- **consumes** `subscription.*` events from `events.subscription`
  (Valkey Streams, consumer group `cg.provisioning-convergence`);
- **produces** `instance.*` events on `events.instance` via a
  transactional outbox + relay (producer literal
  `provisioning-worker`);
- **owns** the `provisioning` Postgres schema (tables `instance`,
  `provisioning_task`, `enforcement_snapshot`, `instance_credential`,
  `event_outbox`, `processed_event`);
- **drives** a `DeploymentAdapter` port (`FakeDeploymentAdapter` in
  milestone 1, `CoolifyAdapter` in milestone 2) and a
  `NotificationTransport` port (`ConsoleNotificationTransport` in dev)
  to email credentials.

It has **no request API**. The only HTTP it serves is `GET /healthz`
(a tiny `aiohttp.web` server for the orchestrator's liveness check).
Customer/operator views of instance status are served by `platform-api`
reading the `provisioning` schema — not by this worker.

`python -m provisioning_worker` boots **four** concurrent concerns on
one `asyncio` loop: (1) the Valkey Streams consumer
(`XREADGROUP` / `XACK` / `XAUTOCLAIM`), (2) the convergence service +
8-state instance machine + Taskiq retry/backoff jobs, (3) the outbox
relay publishing `events.instance`, and (4) the `/healthz` server. No
Granian, no ASGI app, no FastAPI, no Stripe.

## 2. Source-of-truth docs (read these first)

The docs in this repo are scoped to the provisioning-worker
specifically (not the whole platform). When working on a feature,
reference the canonical section rather than your memory:

- `docs/overview.md` — what this service is, MVP scope, where it sits
  between platform-api and the customer Odoo instance. Read first.
- [docs/architecture.md](docs/architecture.md) — process model (the
  four concerns), modules, ports/adapters, the `provisioning` schema,
  the instance + task state machines, cross-cutting concerns (auth,
  errors, idempotency, observability, sessions, migrations).
- [docs/events.md](docs/events.md) — the events this service consumes
  and produces: envelope, payload schemas, evolution rules,
  idempotency. **This is the human-authored event contract.**
- [docs/deployment-adapter.md](docs/deployment-adapter.md) — the
  `DeploymentAdapter` Protocol, the `InstanceSpec`, the fake and the
  (milestone-2) Coolify adapter. The load-bearing seam of this repo.
- `docs/conventions.md` — coding standards not enforced by ruff.
- `docs/local-development.md` — getting set up and the iteration loop.
- `docs/python-style.md` — Python 3.14 style and design rules (typing,
  data modeling, SOLID, module structure, error handling, etc.).
  Loaded automatically: @docs/python-style.md

GSD is the workflow for in-flight feature work. Phases, plans,
roadmaps, and codebase maps live under `.planning/`. See
`.planning/PROJECT.md` for the project state and `gsd-help` for the
command index.

**Important — contracts are per-repo, not shared.** Each Python service
defines its own Pydantic models for events, the envelope, ports, and
shared types inside its own `src/<pkg>/` tree. There is **no** shared
`platform-contracts` package. [docs/events.md](docs/events.md) is the
human-authored event contract; the consumed `subscription.*` payloads
are re-implemented here against platform-api's `docs/events.md`, and the
produced `instance.*` payloads are authored here and re-implemented by
platform-api on its consumer side. Drift is caught by review and the
schema-evolution discipline in `docs/events.md`. In particular,
`instance.deprovisioned` must match platform-api's field set exactly —
both envelopes are `extra="forbid"`.

## 3. Tech stack pins

| Concern             | Choice                                                  |
|---------------------|---------------------------------------------------------|
| Python              | 3.14.*                                                  |
| Process model       | `python -m provisioning_worker` — worker, no ASGI app   |
| Health server       | aiohttp 3.* (`/healthz` only; also the Coolify client)  |
| Validation          | Pydantic 2.13.*, pydantic-settings 2.7.*                |
| ORM                 | SQLAlchemy 2.0.* async + psycopg[binary] 3.3.* (NOT asyncpg) |
| Migrations          | Alembic 1.18.* — a SINGLE tree/section `provisioning`   |
| Bg jobs             | Taskiq 0.12.* + taskiq-redis 1.2.*                      |
| Event bus client    | `redis.asyncio` (unified `redis>=5`) — NOT standalone `aioredis` |
| ID generation       | python-ulid 3.1.* (26-char ULID idempotency keys)       |
| Auth (M2)           | PyJWT 2.10.* `[crypto]` (per-instance bearer tokens)    |
| Logging             | structlog 25.* (JSON in non-dev)                        |
| Tracing             | OpenTelemetry 1.41.* (opt-in via OTLP env)              |
| Package manager     | uv 0.11.* (commit `uv.lock`, use `--frozen`)            |
| Build backend       | hatchling (`packages=["src/provisioning_worker"]`)      |
| Lint / format       | ruff 0.15.* (line-length 100, target py314)             |
| Test runner         | pytest 9.* with pytest-asyncio 1.3.* (`asyncio_mode=auto`) |
| Coverage            | pytest-cov 6.*                                           |
| Integration tests   | testcontainers 4.14.* `[postgres,redis]`                |

This mirrors platform-api's pins **minus FastAPI, Granian, and
Stripe** — this is a worker, it reacts to `subscription.*` events and
never talks to Stripe. Milestone 2 adds the Coolify client config
(`COOLIFY_API_URL`, `COOLIFY_API_TOKEN`), SMTP, and the per-instance
token config.

**Do not** introduce mypy. It's a deliberate decision (same as
platform-api). Type hints are written and read by humans; we don't run
a static checker.

## 4. Repository layout

```
provisioner/
├── alembic.ini                          # single section: `provisioning`
├── docs/                                # Engineer-facing docs (architecture, events, …)
├── migrations/
│   └── provisioning/{env.py, versions/} # one tree; version_table_schema=provisioning
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

**Dependency rule** (identical to platform-api): `modules/` may import
from `infrastructure/`, `ports/`, `adapters/`, `shared/`, `events/`.
Nothing in `infrastructure/`, `ports/`, or `adapters/` may import from
`modules/`. Only adapters import third-party clients (`redis.asyncio`,
`aiohttp`, `taskiq`); domain code talks to the ports. This keeps the
deployment adapter and the bus swappable and the domain testable. If
you add a new cross-cutting helper, decide its home by that rule.

## 5. Running locally

The repo depends on `platform-infra` being up (Postgres 18 + Valkey 8 +
Keycloak 26 via docker-compose — though the worker uses **neither**
Keycloak realm). The `provisioning` schema already exists empty in the
shared `platform` database (created by
`platform-infra/postgres/init/01-init.sql`); this repo's Alembic tree
creates the tables. With infra running:

```bash
cp .env.example .env             # adjust if your platform-infra ports differ
uv sync --frozen --extra dev
make migrate                     # create the provisioning.* tables
make run                         # python -m provisioning_worker
```

Smoke check (done-state for the milestone-1 pipeline against the fake
adapter — no Coolify, no real Odoo):

1. `curl http://localhost:8001/healthz` → 200 `{"status":"ok"}`
   (port is `HEALTH_PORT`, default `8001` — deliberately off
   platform-api's `8000`).
2. Feed a `subscription.activated` envelope onto the bus by hand:

   ```bash
   valkey-cli XADD events.subscription '*' envelope \
     '{"id":"01J0...26CHARS","type":"subscription.activated","version":1,
       "occurred_at":"2026-06-01T00:00:00Z","producer":"platform-api",
       "payload":{ ...SubscriptionActivatedPayload... }}'
   ```

3. The consumer picks it up, opens a `pending` `provisioning.instance`
   row + a `create` task, and converges through
   `deploying → configuring → ready` against the `FakeDeploymentAdapter`.
   Expect a `provisioning.instance` row at `ready` **and** an
   `instance.provisioned` envelope on `events.instance`
   (`valkey-cli XRANGE events.instance - +`).

`make help` lists every Make target. The most-used:

| Target            | Use                                                     |
|-------------------|---------------------------------------------------------|
| `make run`        | `python -m provisioning_worker` (the four concerns)     |
| `make test`       | unit tests, `-m "not integration"` (no Docker required) |
| `make test-integration` | testcontainers-backed tests (needs Docker)        |
| `make lint`       | `ruff check`                                            |
| `make check`      | `ruff check` + `ruff format --check` (CI gate)          |
| `make migrate`    | upgrade head on the single `provisioning` tree          |
| `make psql`       | psql shell on the `platform` DB (inspect `provisioning.*`) |
| `make revision name="..."` | new Alembic revision in the `provisioning` tree |

## 6. Conventions Claude should follow

### 6.1 Code style

- Run `make check` before declaring work complete. `make lint-fix`
  applies trivial fixes; `make format` rewrites formatting. Ruff is
  configured line-length 100, target `py314`, quote-style double, with
  lint groups `E,W,F,I,B,C4,UP,RUF,ASYNC,S,PT,SIM,TCH,PL,ERA`.
- Type hints everywhere, but **do not** run mypy/pyright as part of CI.
  Hints are documentation for humans.
- **Async-first.** This is an async worker end to end — the consumer
  loop, the relay, the adapters, the DB sessions. No blocking I/O on
  the loop. If a library has only a sync API, wrap it in
  `asyncio.to_thread` or find an async alternative.
- **Encapsulation.** Internal-only attributes, methods, and module
  functions are prefixed with `_`. Public APIs are deliberately small.
- **Docstrings.** Every public module, class, function, and method has
  a **Google-style** docstring. Private `_helpers` only need one when
  the logic isn't obvious. Don't restate the name.
- **Python 3.14, no `from __future__ import annotations`.** 3.14 defers
  annotation evaluation (PEP 649), so the import is unnecessary — and the
  project forbids it, including in generated Alembic files.
- **Tooling.** `uv` manages deps (`uv sync --frozen`). Run tools through
  the `make` targets or directly via `.venv/bin/<tool>` (e.g.
  `.venv/bin/pytest`, `.venv/bin/ruff`); prefer that over `uv run <tool>`
  for ad-hoc commands, per project convention.

### 6.1.1 Module file layout

Within `modules/provisioning/`, file names are load-bearing — they
signal what kind of code lives there. Stick to these:

| File             | Holds                                                          |
|------------------|----------------------------------------------------------------|
| `models.py`      | SQLAlchemy mapped classes (the DB layer). Never `orm.py`.      |
| `schemas.py`     | Pydantic command/result models. Never `models.py` for Pydantic. |
| `repository.py`  | Async SQLAlchemy data-access. ORM + SQL only, no Pydantic.     |
| `service.py`     | Convergence + state machine. The only place a transition is judged legal and the matching `instance.*` event is emitted (via the outbox). |
| `handlers.py`    | One handler per consumed `subscription.*` type. Thin: validate → dedupe → open/advance a `provisioning_task` → return. |
| `tasks.py`       | Taskiq tasks — the actual adapter calls (slow / fail-prone) with backoff. |
| `spec.py`        | The `InstanceSpec` builder (entitlements → desired state).     |

Test files mirror the name: `tests/provisioning/test_<thing>.py`.

### 6.2 Contracts

- When implementing a consumed payload, copy the Pydantic model from
  [docs/events.md](docs/events.md) exactly (frozen, `extra="forbid"`).
  When producing an event, copy the produced payload model from the
  same doc. JSON keys are `snake_case`, timestamps are RFC 3339 UTC.
- The envelope is re-implemented here (no shared package): 26-char ULID
  `id`, dotted `type`, `version >= 1`, UTC `occurred_at`,
  `producer="provisioning-worker"` on what we emit, `causation_id` set
  to the triggering `subscription.*` envelope's `id`, frozen +
  `extra="forbid"`.
- Produced events are **never published directly.** Write the envelope
  to `provisioning.event_outbox` in the same transaction as the state
  change; the relay publishes it (`XADD events.instance`). This is what
  makes "instance reached `ready`" and "`instance.provisioned` emitted"
  atomic.
- Domain failures raise typed exceptions from `shared/errors.py`
  (`ProvisioningError`, `DeploymentFailed`, `InvalidTransition`,
  `AdapterTimeout`, …). Adapter-level exceptions
  (`redis.RedisError`, `aiohttp.ClientError`) are translated at the
  adapter boundary and never leak into `service.py`.

### 6.3 Database

- All tables live under the `provisioning` schema. No cross-schema
  foreign keys; `subscription_id` / `customer_id` are **opaque UUIDs**
  enforced at the application layer. (FKs *within* the schema, e.g.
  `provisioning_task.instance_id → instance.id`, are fine.)
- A **single** Alembic tree (`alembic.ini` section `provisioning`,
  `version_table_schema=provisioning`). Scaffold with
  `make revision name="..."`; review the autogenerated SQL before
  committing (autogenerate loses CHECK constraints and mishandles
  enums). Forward-only.
- Sessions use an explicit `session_scope()` async context manager. The
  handler owns the transaction boundary and commits explicitly — the
  state change, the `processed_event` insert, and the `event_outbox`
  insert all commit together or not at all.

### 6.4 Idempotency

Delivery is **at-least-once**; handlers MUST be idempotent. Three
layers, all required:

- inbound events dedupe on `(event_id, consumer_group)` in
  `provisioning.processed_event`, inserted in the same transaction as
  the state change (so a crash before `XACK` re-delivers and the
  handler short-circuits). `shared/event_consumer.py` wraps every
  registered handler with this guard.
- `subscription.lines_changed` additionally dedupes on `change_set_id`
  via `UNIQUE (instance_id, change_set_id)` on `provisioning_task`.
- outbound events dedupe at the consumer on `envelope.id`; the outbox
  `UNIQUE(envelope_id)` prevents double-enqueue.

Adapter operations are themselves idempotent (diff desired-vs-current;
re-creating an existing instance converges rather than duplicating).

### 6.5 Tests

- Default `make test` (`-m "not integration"`) must stay fast and
  Docker-free. Use the `FakeDeploymentAdapter` + an in-memory bus for
  unit tests; mark anything needing real Postgres/Valkey
  `@pytest.mark.integration` (testcontainers).
- `asyncio_mode = "auto"`; `addopts` include `--strict-markers
  --strict-config`; `filterwarnings = ["error", ...]`.
- The `FakeDeploymentAdapter` has a fault-injection mode
  (`fail_on={"create"}`, latency, partial-failure) — use it to exercise
  the retry/backoff and `instance.failed` paths deterministically.

### 6.6 Logging

- Use `structlog.get_logger(__name__)` once per module; never `print`.
- Bind context via `structlog.contextvars.bind_contextvars(...)` at the
  top of each handler: `envelope_id`, `subscription_id`, `instance_id`,
  `correlation_id` (carried from the inbound envelope). Never thread
  them through call chains.
- Log `info` for state transitions, `warning` for retried operations,
  `error` only for things a human must look at. Never log secrets,
  tokens, or admin passwords.

### 6.7 What NOT to do

- **No business logic in `infrastructure/`.** That layer is plumbing
  (DB engine, logging, OTel, health server, outbox relay).
- **No orchestrator concepts in the domain.** `modules/provisioning/`
  calls only the `DeploymentAdapter` Protocol — never imports a Coolify
  SDK, never constructs an HTTP client, never references Coolify
  "applications" or K8s "manifests". All of that lives behind an
  adapter. The acceptance test for the abstraction: the same
  convergence code runs unchanged against the fake and the real
  Coolify adapter.
- **No Stripe, no subscription state, no telemetry.** The worker reacts
  to `subscription.*` events; it never writes the `subscription` schema
  and never polls instance usage (that's `telemetry-worker`).
- **Don't extract a shared contracts package.** Re-implement against
  [docs/events.md](docs/events.md).
- **No mypy.** Don't add mypy config or pre-commit mypy hooks.

## 7. Common requests you'll get

This section is a prompt-engineering aid: when the user asks something
here, this is the expected shape of the response (file touches).

**"Add a consumed event handler"** (e.g. a new `subscription.*` type) —
add the payload model in `src/provisioning_worker/events/`; add a
handler in `modules/provisioning/handlers.py` (validate → dedupe →
open/advance a `provisioning_task`); register it with the consumer in
`main.py`; add convergence logic in `service.py`; tests under
`tests/provisioning/`. Match the payload in
[docs/events.md](docs/events.md).

**"Add a produced instance event"** — add the payload model in
`src/provisioning_worker/events/`; emit it from `service.py` by writing
the envelope to `provisioning.event_outbox` inside the converging
transaction (never publish directly); update
[docs/events.md](docs/events.md) and the producer/consumer summary
table.

**"Add a deployment-adapter method"** — add the async method to the
`DeploymentAdapter` Protocol in `ports/deployment_adapter.py`;
implement it in `FakeDeploymentAdapter` (and, milestone 2, the
`CoolifyAdapter`); call it from `modules/provisioning/tasks.py`
(not `service.py` directly — adapter calls run as retryable Taskiq
jobs); update [docs/deployment-adapter.md](docs/deployment-adapter.md).
Keep it idempotent and orchestrator-agnostic.

**"Add an alembic migration"** — `make revision name="..."`. Never
write the file by hand; review the autogenerated SQL before committing
(autogenerate drops CHECK constraints and mishandles enums). One tree
only.

## 8. Pointers outside this repo

- `platform-infra/` — Postgres 18, Valkey 8, Keycloak 26 in Compose.
  Must be running for local dev (reach it at `localhost:5432/6379/8080`
  or in-network hostnames `postgres` / `valkey` / `keycloak` on the
  `platform-net` bridge). The `provisioning` schema is created empty
  there; this repo Alembic-creates its tables. The worker uses **no**
  Keycloak realm.
- `platform-api/` — the FastAPI control plane. **Publishes** the
  `subscription.*` events this worker consumes (already implemented and
  emitting today via its own outbox → relay) and **reads**
  `provisioning.instance` / `provisioning.enforcement_snapshot`
  read-only. It will **consume** `instance.deprovisioned` in its
  Phase 6 (`cg.subscription-convergence`) to close out the
  subscription — so that payload's field set must stay symmetric with
  platform-api's `docs/events.md`.
- `telemetry-worker/` — per-instance health/usage polling, alert
  evaluation. Owns the `telemetry` schema. (Not yet built.)
- `operator-console/`, `customer-portal/` — Nuxt SPAs that render
  instance status via platform-api, not this worker. (Operator console
  not yet built.)

---

End of CLAUDE.md. Keep this file current when conventions change.
