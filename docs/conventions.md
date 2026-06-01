# Coding conventions

The shortest version of the conventions that aren't already enforced by
`ruff` or the type system. CLAUDE.md is the agent-facing version of this;
this file is for human reviewers and onboarding. Architectural conventions
(modules, ports, schemas, the state machine, idempotency, sessions) live in
[architecture.md](architecture.md); the event/envelope contract lives in
[events.md](events.md); the deployment seam lives in
[deployment-adapter.md](deployment-adapter.md).

This service has **no request API** — it's a long-running consumer
([architecture.md](architecture.md) §Process model). Where platform-api's
conventions are about HTTP handlers, ours are about event handlers, Taskiq
tasks, and the outbox.

## Tooling and Python version

- **Python 3.14**, managed by **uv** with a committed `uv.lock`
  (`uv sync --frozen --extra dev`). Pins live in `pyproject.toml` — mirror
  platform-api's, minus FastAPI / Granian / Stripe.
- **No `from __future__ import annotations`.** Python 3.14 evaluates
  annotations lazily (PEP 649), so the import is unnecessary — and the
  project forbids it. Freshly generated Alembic templates and revisions must
  be clean of it too.
- Invoke tools through the `make` targets, or directly via `.venv/bin/<tool>`
  (e.g. `.venv/bin/pytest`, `.venv/bin/ruff`) — prefer these over
  `uv run <tool>` for ad-hoc commands, per project convention.
- **No mypy / pyright.** Type hints are documentation for humans; there is no
  static type-check gate (a deliberate decision shared with platform-api).

## Imports

- Absolute imports only inside `provisioning_worker.*`. No `from ..foo import
  bar` — ruff's `TID252` would flag relative parent imports.
- `TYPE_CHECKING` for cycles. Don't pollute runtime with imports used only in
  annotations (ruff's `TCH` group enforces moving them).
- The dependency rule is load-bearing: `modules/` may import
  `infrastructure/` / `ports/` / `adapters/` / `shared/` / `events/`; nothing
  in `infrastructure/` / `ports/` / `adapters/` may import `modules/`. Only
  adapters import third-party clients (`redis.asyncio`, `aiohttp`, `taskiq`).
  See [architecture.md](architecture.md) §Code layout.

## Async

- Every I/O-bound function is `async` — DB access, Valkey reads/writes,
  adapter calls, the health server. Wrap any sync library call in
  `asyncio.to_thread` if no async option exists, but treat that as a smell to
  revisit.
- No `time.sleep` in `async` functions; use `asyncio.sleep` (the outbox poll
  loop, backoff waits). Ruff's `ASYNC` group flags the sync call.
- aiohttp `ClientSession`s are reused at module/adapter scope, never created
  per call — a new session per request is a leak waiting to happen. The
  `CoolifyAdapter` (milestone 2) owns one session for its lifetime and closes
  it on shutdown.
- Use `redis.asyncio` (the modern unified `redis>=5` client) for the Streams
  consumer and publisher — **not** the unmaintained standalone `aioredis`
  package. This is for parity with platform-api's `ValkeyStreamsBus`.

## Errors

- Domain failures raise typed exceptions from `shared/errors.py`:
  `ProvisioningError` and subclasses such as `DeploymentFailed`,
  `InvalidTransition`, `AdapterTimeout`. Never raise bare `Exception` or
  re-raise a third-party error into the domain.
- Translate adapter-level exceptions (`redis.RedisError`,
  `aiohttp.ClientError`, a Coolify HTTP error) into a `ProvisioningError`
  **at the adapter boundary** — they never leak into `service.py`.
- **Never crash the consumer on a single failed event.** A failed convergence
  step records `last_error` on the `provisioning_task`, sets the instance
  `failed_step` / `failure_reason`, emits `instance.failed` (via the outbox),
  and schedules a backoff retry. The consumer loop keeps running and the next
  event is processed. See [architecture.md](architecture.md) §Errors.
- New error types go in `shared/errors.py`, never as ad-hoc string codes at
  call sites. `instance.failed`'s `failure_code` is derived from the
  exception type, not invented per raise site (see [events.md](events.md)).

## Pydantic models

- Event payloads and command/result models use `ConfigDict(extra="forbid")`
  (and `frozen=True` for envelopes and payloads) so unknown keys are rejected
  at the boundary. This is what makes the cross-repo contract safe to evolve.
- `Decimal` for money; never `float`. Always carry a sibling `currency`
  field (e.g. `subscription.activated` carries `total_amount` + `currency`).
- Datetimes are UTC, timezone-aware; serialization yields RFC 3339 with `Z`.
  Mint produced timestamps with `datetime.now(tz=UTC)`. Parse inbound ones as
  aware datetimes — never compare naive to aware.
- Keep SQLAlchemy in `models.py` and Pydantic in `schemas.py` /
  `events/` — the file name is the type signal
  ([architecture.md](architecture.md) §Code layout).

## Events

- The `EventEnvelope` is **re-implemented in this repo** (`events/`), not
  imported from a shared package — there is none. Match the shape in
  [events.md](events.md) byte-for-byte so envelopes round-trip across
  services.
- Handlers MUST be idempotent on `envelope.id`. Dedupe via
  `provisioning.processed_event(event_id, consumer_group)`, inserted **in the
  same transaction** as the state change; `shared/event_consumer.py` wraps
  every registration with this guard. `subscription.lines_changed`
  additionally dedupes on `change_set_id`. See
  [architecture.md](architecture.md) §Event consumption and idempotency.
- **Emit `instance.*` events only through the outbox** — write the
  `event_outbox` row inside the same transaction as the state change; the
  relay publishes it. Never `XADD` directly from a handler or a task. Set
  `producer="provisioning-worker"`, a fresh 26-char ULID `id`, and
  `causation_id` = the triggering envelope's `id` ([events.md](events.md)).
- Follow the evolution discipline in [events.md](events.md): additive changes
  keep the `version`; breaking changes author `vN+1` beside `vN`. Consumers
  treat unknown enum values as a no-op — never parse inbound enums strictly.

## Migrations

- A **single** Alembic tree for the `provisioning` schema (`alembic.ini`
  section `provisioning`, `version_table_schema=provisioning`), simpler than
  platform-api's three trees. Use `make revision name="..."` / `make migrate`.
- One concept per migration. Adding a table AND a column to another table is
  two revisions.
- Always review the autogenerated SQL before committing. Alembic's
  autogenerate is "almost right" — it loses CHECK constraints, gets enum
  changes wrong, and sometimes drops things it shouldn't. The `instance`
  status enum and `provisioning_task` constraints in particular need hand
  review.
- Forward-only. The `downgrade()` function exists but we don't rely on it in
  prod.

## Logging

```python
import structlog

log = structlog.get_logger(__name__)

async def handle_subscription_activated(...):
    log.info("instance create requested", subscription_id=str(payload.subscription_id))
```

- Fields are passed as kwargs, not interpolated into the message.
- Bind the cross-cutting ids once at the top of each handler via
  `structlog.contextvars.bind_contextvars(...)` — `envelope_id`,
  `subscription_id`, `instance_id`, and `correlation_id` (carried from the
  inbound envelope) — instead of threading them through the call chain.
- `info` for state transitions; `warning` for retried/backed-off operations;
  `error` only for things that need a human. Never log credentials or the
  per-instance bearer token (milestone 2).

## Tests

- One file per unit of behavior — `test_<thing>.py`. Pytest collects them
  automatically; the test file name mirrors what it tests.
- Default `make test` runs `-m "not integration"` and must stay fast and
  Docker-free. Mark anything that needs Postgres/Valkey via testcontainers
  `@pytest.mark.integration`; mark genuinely slow cases `@pytest.mark.slow`.
  Markers are `--strict-markers`, so register new ones in `pyproject.toml`.
- Unit-test convergence against the `FakeDeploymentAdapter` + an in-memory
  bus — never reach a real Valkey, Coolify, or Odoo on the fast path. The
  fake's fault-injection mode (`fail_on={"create"}`, latency, partial
  failure) drives the retry/backoff and `instance.failed` paths
  deterministically. See [deployment-adapter.md](deployment-adapter.md).
- `asyncio_mode="auto"` is set, so async tests need no per-test decorator.
  Inject a deterministic `Clock` / `IdGenerator` rather than asserting on
  wall-clock time or random ULIDs.

## Comments

- Comment why, not what. The code says what.
- Reference the docs in comments next to non-obvious decisions:
  `# Per architecture.md "Idempotency", dedupe on (envelope.id, consumer_group)`.
- Flag milestone-2 / open items explicitly in code where a milestone-1
  shortcut is taken (e.g. `# milestone 2: real CoolifyAdapter; fake for now`),
  matching how this repo's docs call out deferred work.
