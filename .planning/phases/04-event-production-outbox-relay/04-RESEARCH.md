# Phase 4: Event Production (Outbox → Relay) — Research

**Researched:** 2026-06-03
**Domain:** Transactional outbox pattern, Valkey Streams publish, SQLAlchemy async, Pydantic event envelope
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** First-ready guard is the PRIMARY exactly-once mechanism. The outbox row is enqueued inside the same `session_scope()` as the `ready_at null→now` transition + `record_task_success`. That transition already happens exactly once per instance (guarded by `ready_at IS NULL`). Emit MUST join that existing transaction.
- **D-02:** Fresh ULID at emit; `UNIQUE(envelope_id)` is a backstop. `EventEnvelope.build()` mints a fresh random ULID. `ON CONFLICT DO NOTHING` is defense-in-depth.
- **D-03:** Retry forever — no cap, no dead-letter. A publish failure records `last_error` (truncated ~2000 chars), bumps `attempt_count`, leaves `sent_at` NULL, retried on next poll indefinitely. `SKIP LOCKED` ensures a stuck row never blocks the batch.
- **D-04:** Transport failure and deserialize/validation failure are treated identically. Any exception during rebuild-or-publish records `last_error` + bumps `attempt_count`. Retries next poll.
- **D-05:** `SELECT … FOR UPDATE SKIP LOCKED`, batched, one transaction per drain. Each `_drain_once` selects up to `settings.outbox_batch_size` unsent rows ordered by `created_at ASC` with `with_for_update(skip_locked=True)`, processes them inside one session/transaction, then commits.
- **D-06:** Relay rebuilds the typed envelope before publishing. `envelope_class_for(row.envelope_type).model_validate(row.payload)` then `bus.publish()` which re-serializes via `model_dump_json()`. Requires produced-side `envelope_class_for` registry (M1: `{"instance.provisioned": …}`).
- **D-07:** `service.py` mints, `OutboxRepo` enqueues, `tasks.py` calls inside the ready txn. `ProvisioningService.emit_instance_provisioned(session, instance, causation_id)` builds envelope via `EventEnvelope.build()` and calls `OutboxRepo.enqueue(session, envelope)`. The outbox INSERT lives in the repository, never in `infrastructure/`.
- **D-08:** `hostname = f"{spec.slug}.{settings.instance_domain_suffix}"`, `url = f"https://{hostname}"`. Fix the Phase-3 placeholder `url=f"https://{spec.slug}"`. Single derivation helper for both the `instance.url` column and the payload.
- **D-09:** Payload field mapping: `provisioned_at = ready_at`, `causation_id = task.source_event_id`, `snapshot_version = instance.snapshot_version`, `admin_email = spec.admin_email`, `instance_id`/`subscription_id`/`customer_id` from instance row. No credentials.

### Claude's Discretion

- The exact `MessageBus` Protocol surface (publish-only) and `ValkeyStreamsBus` constants (`_MAXLEN=100_000`, `approximate=True`) — mirror platform-api.
- Whether `OutboxRepo` is a small class (platform-api parity) or a module function in `repository.py`; the `event_outbox` PK type (uuid7) and index choices beyond `UNIQUE(envelope_id)`.
- Where `EventEnvelope.build()` lives and its exact signature.
- The `stream` column derivation (`stream_for_envelope_type` at enqueue vs at publish).
- How `task.source_event_id` is made available at the emit point in step 4 given `expire_on_commit=True` detaches ORM objects (CR-01 pattern from Phase 3).
- Test boundary: fast in-memory unit vs `@pytest.mark.integration`.

### Deferred Ideas (OUT OF SCOPE)

- The rest of the produced `instance.*` catalog (`updated`, `suspended`, `reinstated`, `failed`, `deprovisioned`) — Phase 5.
- Max-attempts cap / terminal "poison outbox row" state and dead-letter stream — not milestone 1.
- `events.instance` consumption (port stays publish-only) — platform-api Phase 6.
- Metrics (outbox backlog depth, publish failure counts) — Phase 5.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EVT-01 | `provisioning.event_outbox` + relay publishes `instance.*` envelopes to `events.instance` (`XADD`, single `envelope` field, `MAXLEN ~ 100000`), with outbox row written in the **same transaction** as the state change (`UNIQUE(envelope_id)`). | Outbox table shape from platform-api `EventOutbox` model; relay loop from platform-api `run_outbox_relay`; same-txn guarantee via `session_scope()` in `tasks.py` step 4. |
| EVT-02 | The produced `instance.provisioned` payload (frozen, `extra="forbid"`, `producer="provisioning-worker"`, `causation_id` = triggering envelope id) is authored and emitted. | `InstanceProvisionedPayload` copied byte-for-byte from `docs/events.md`; field mapping from D-09. |
</phase_requirements>

---

## Summary

Phase 4 wires the full ready → outbox → relay → `events.instance` path for `instance.provisioned`. This is the first time the provisioner produces an event; the goal is that "instance reached `ready`" and "`instance.provisioned` emitted on `events.instance`" are atomic.

The implementation is a **direct mirror** of the sibling repo `platform-api`'s outbox → relay stack, which has been read in full and verified. Every new artifact in this phase has a verbatim reference counterpart in platform-api. The only differences are (a) schema name (`provisioning` not `billing`), (b) producer literal (`"provisioning-worker"` not `"platform-api"`), and (c) the `envelope_class_for` registry has one entry (`instance.provisioned`) instead of seven.

The existing codebase has six extension points and two new files (`events/instance.py`, `ports/message_bus.py`) plus three new files mirroring platform-api (`adapters/valkey_streams_bus.py`, `shared/strings.py`, and the Alembic revision). None of the convergence machinery needs to be redesigned — only extended. The Phase-1 no-op relay body is replaced, and the ready-transition step 4 in `tasks.py` gets an emit call plus a hostname derivation fix.

**Primary recommendation:** Mirror platform-api verbatim. The six reference files have been read; copy the patterns, adapt only schema name, producer literal, and registry contents.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Envelope minting (ULID, producer literal, occurred_at) | `events/envelope.py` — `EventEnvelope.build()` | `service.py` (caller) | Single mint site; service calls it |
| Payload model definition | `events/instance.py` (new) | — | Mirrors consume-side `events/subscription.py` structure |
| Produced-side envelope type registry | `events/__init__.py` | — | Symmetric with consume-side `payload_class_for`; relay imports `envelope_class_for` |
| Outbox row write | `modules/provisioning/repository.py` — `OutboxRepo.enqueue()` | `service.py` — `emit_instance_provisioned()` orchestrates | CLAUDE.md §6.1.1: repository owns SQL, service owns domain emit logic |
| Transaction boundary (atomic commit) | `modules/provisioning/tasks.py` step 4 | — | Caller owns the commit; outbox write joins the existing ready transaction (D-01) |
| Relay drain (SELECT FOR UPDATE SKIP LOCKED, mark sent/failed) | `infrastructure/outbox_relay.py` | — | Infrastructure plumbing only — no domain logic |
| Stream publish (XADD) | `adapters/valkey_streams_bus.py` | — | Only place `redis.asyncio` publish lives; domain talks to `MessageBus` port |
| MessageBus port definition | `ports/message_bus.py` | — | Protocol-based DI; relay + future callers depend on port, not adapter |

---

## Standard Stack

### Core (all already pinned — no new packages required)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `redis.asyncio` (via `redis>=5`) | `uv.lock` pinned | `XADD` to `events.instance` | Already used for consume-side; same client |
| `sqlalchemy[asyncio]` 2.0.* | pinned | `SELECT … FOR UPDATE SKIP LOCKED`, JSONB column writes | All existing DB work uses it |
| `pydantic` 2.13.* | pinned | `InstanceProvisionedPayload`, `EventEnvelope.build()` | frozen + extra="forbid" contract model |
| `python-ulid` 3.1.* | pinned | `str(ULID())` in `EventEnvelope.build()` | 26-char ULID; already imported on consume side |
| `sqlalchemy.dialects.postgresql.insert` (`pg_insert`) | part of SQLAlchemy | `ON CONFLICT DO NOTHING` enqueue | Platform-api pattern |
| `testcontainers[redis]` 4.14.* | pinned (dev) | Integration test: Valkey container for relay round-trip | Already in `pyproject.toml` |

**No new packages required.** This phase is purely structural extension of already-installed libraries.

`fakeredis` is NOT installed in this project. Integration tests for the relay XADD round-trip must use `testcontainers[redis]` (which IS pinned) rather than fakeredis.

### Package Legitimacy Audit

> No new packages are installed in this phase. Existing packages are already in `uv.lock` and have been vetted.

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

---

## Architecture Patterns

### System Architecture Diagram

```
subscription.activated
        │
        ▼
[handlers.py] → [service.open_instance] → instance(pending) + task(pending)
                                                   │
                                               [tasks.py]
                            create_instance_task steps 1–3 (deploying→configuring)
                                                   │
                                          step 4: session_scope()
                                                   │
                    ┌──────────────────────────────┼──────────────────────┐
                    │                              │                      │
             update_instance_status(ready)  OutboxRepo.enqueue(env)  record_task_success
                    │                              │                      │
                    └──────────────────────────────┼──────────────────────┘
                                              session.commit()  ← atomic
                                                   │
                                    provisioning.event_outbox row (sent_at=NULL)
                                                   │
                                       [infrastructure/outbox_relay.py]
                                         run_outbox_relay (poll loop)
                                              _drain_once()
                                    SELECT … FOR UPDATE SKIP LOCKED
                                    envelope_class_for(type).model_validate(payload)
                                                   │
                                       ┌───────────┴──────────┐
                                  bus.publish(envelope)    exception?
                                       │                      │
                              row.sent_at = now()    last_error / attempt_count++
                              session.commit()        session.commit()  (retry next poll)
                                       │
                               XADD events.instance
                               * envelope <json-bytes>
                               MAXLEN ~ 100000
```

### Recommended Project Structure (files touched / created)

```
src/provisioning_worker/
├── events/
│   ├── envelope.py          # EXTEND: add EventEnvelope.build() classmethod
│   ├── instance.py          # NEW: InstanceProvisionedPayload
│   └── __init__.py          # EXTEND: envelope_class_for registry + InstanceProvisionedPayload export
├── ports/
│   └── message_bus.py       # NEW: publish-only MessageBus Protocol
├── adapters/
│   └── valkey_streams_bus.py # NEW: ValkeyStreamsBus (XADD, close())
├── shared/
│   └── strings.py           # NEW: _truncate helper (mirrors platform-api)
├── modules/provisioning/
│   ├── models.py            # EXTEND: EventOutbox mapped class
│   ├── repository.py        # EXTEND: OutboxRepo class + enqueue()
│   ├── service.py           # EXTEND: emit_instance_provisioned()
│   └── tasks.py             # EXTEND: step 4 — emit call + hostname derivation fix (D-08)
├── infrastructure/
│   └── outbox_relay.py      # REPLACE: real _drain_once + new run_outbox_relay signature
└── main.py                  # EXTEND: construct ValkeyStreamsBus, pass to run_outbox_relay
migrations/
└── provisioning/versions/
    └── YYYYMMDD_HHMM_add_event_outbox.py  # NEW: Alembic revision
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| `SELECT … FOR UPDATE SKIP LOCKED` | Custom row-locking query | SQLAlchemy `.with_for_update(skip_locked=True)` | Pattern-matched from platform-api; multi-replica safe at zero cost |
| `ON CONFLICT DO NOTHING` outbox enqueue | Custom idempotency logic | `pg_insert(EventOutbox).on_conflict_do_nothing(index_elements=["envelope_id"])` | Postgres-native; handles concurrent double-insert race |
| ULID minting | `uuid.uuid4()` | `str(ULID())` from `python-ulid` | 26-char ULID required by `EventEnvelope.id` field constraint (min_length=26, max_length=26) |
| String truncation for `last_error` | Manual slicing | `_truncate(repr(exc), max_len=2000)` from `shared/strings.py` | Already solved in platform-api; preserves readability with ellipsis |
| Stream name derivation | Hard-coded `"events.instance"` | `stream_for_envelope_type(envelope.type)` | Already implemented in `events/envelope.py`; shared routing rule |
| XADD with MAXLEN | Unbounded stream append | `xadd(…, maxlen=100_000, approximate=True)` | Retention limit from `docs/events.md §Retention`; approximate trim is O(1) |

**Key insight:** Every non-trivial pattern in this phase is already implemented in platform-api. Mirror, don't invent.

---

## Existing Code State (what to extend vs create)

### `infrastructure/outbox_relay.py` — Phase-1 no-op [VERIFIED: file read]

Current signature: `run_outbox_relay(settings: Settings, shutdown: asyncio.Event)` — only two args. The body runs the `asyncio.wait_for` sleep loop with an empty body (`# Phase 4 will replace this with: await _drain_once(...)`). The function is already wired into `main.py`'s TaskGroup.

**What changes:** Add `session_factory` and `bus` parameters; add `_drain_once` inner function; replace the comment with the real drain call.

### `events/envelope.py` — consume-only, no `build()` [VERIFIED: file read]

Phase-2 D-03 deliberately dropped `build()`. The `stream_for_envelope_type` helper is already present. No `from ulid import ULID` import yet.

**What changes:** Add `EventEnvelope.build(...)` classmethod. The producer literal must be `"provisioning-worker"` (differs from platform-api's `"platform-api"`). Add `from ulid import ULID` import and `from datetime import UTC, datetime` (already in `TYPE_CHECKING` block — needs to be promoted to runtime import since `build()` calls `datetime.now(tz=UTC)`).

### `events/__init__.py` — consume-side only [VERIFIED: file read]

Currently exports: `EventEnvelope`, five `subscription.*` payloads, `UnknownEnvelopeType`, `payload_class_for`, `stream_for_envelope_type`.

**What changes:**
1. Import `InstanceProvisionedPayload` from `events/instance.py`.
2. Add produced-side `_ENVELOPE_REGISTRY` dict (separate from consume-side `_PAYLOAD_REGISTRY`).
3. Add `envelope_class_for(envelope_type)` function — raises `UnknownEnvelopeType` (already defined).
4. Export `InstanceProvisionedPayload` and `envelope_class_for` in `__all__`.

### `modules/provisioning/models.py` — no `EventOutbox` yet [VERIFIED: file read]

Currently maps `ProcessedEvent`, `Instance`, `ProvisioningTask`, `EnforcementSnapshot`. All use `TIMESTAMP(timezone=True)` (not `DateTime(timezone=True)` as in platform-api).

**What changes:** Add `EventOutbox` mapped class. Use `TIMESTAMP(timezone=True)` for consistency with all existing mapped classes. Mirror platform-api's column set exactly.

### `modules/provisioning/repository.py` — no `OutboxRepo` yet [VERIFIED: file read]

**What changes:** Add `OutboxRepo` class. Pattern: small class (constructor takes `session`). Add `OutboxRepo` to `__all__`. The `flush()` call after `execute` matches platform-api.

### `modules/provisioning/service.py` — no `emit_instance_provisioned` yet [VERIFIED: file read]

`ProvisioningService` already has `open_instance`, `validate_transition`, `write_enforcement_snapshot`. Constructor takes `entitlement_resolver`.

**What changes:** Add `emit_instance_provisioned(session, instance, causation_id, correlation_id)`. This is the ONLY place an `instance.*` event is emitted via the outbox (CLAUDE.md §6.1.1).

### `modules/provisioning/tasks.py` step 4 — placeholder hostname + no emit [VERIFIED: file read]

Current step 4 issues:
1. `url=f"https://{spec.slug}"` — placeholder, missing domain suffix (D-08)
2. No `hostname=` kwarg passed to `update_instance_status` — column stays NULL
3. No `emit_instance_provisioned` call
4. `task.source_event_id` not captured in the first `session_scope()` block (CR-01 gap for this field)

**What changes:** (a) Capture `source_event_id = task.source_event_id` in first session_scope alongside `task_payload`; (b) compute `hostname` and `url` in step 4; (c) pass `hostname=hostname` to `update_instance_status`; (d) call `service.emit_instance_provisioned` inside the same session when `is_first_ready`.

**Important:** `_UPDATABLE_INSTANCE_COLUMNS` in `repository.py` already includes `"hostname"` — no change to the allowlist needed. [VERIFIED: repository.py line 57]

### `settings.py` — confirm fields exist [VERIFIED: file read]

- `outbox_poll_seconds: float = 1.0` ✓
- `outbox_batch_size: int = 100` ✓
- `instance_domain_suffix: str = "example.local"` ✓

No settings changes needed.

### `main.py` — needs ValkeyStreamsBus construction + relay wiring [VERIFIED: file read]

Current call: `run_outbox_relay(settings, shutdown)` — only two args. The `_check_valkey` function already creates and pings a transient client, so Valkey reachability is already tested at boot.

**What changes:**
1. Import `ValkeyStreamsBus` from `provisioning_worker.adapters.valkey_streams_bus`.
2. Import `get_session_factory` from `provisioning_worker.infrastructure.db`.
3. In the `run` function before TaskGroup: `bus = ValkeyStreamsBus(settings)`.
4. Change relay task call: `run_outbox_relay(settings, get_session_factory(), bus, shutdown)`.
5. In `finally`: `await bus.close()` after `await dispose_engine()`.

---

## Reference Implementation — Platform-API Extracts

All patterns below are `[VERIFIED: file read]` from `../platform-api/`.

### `run_outbox_relay` + `_drain_once` (platform-api, mirror verbatim)

```python
# Source: ../platform-api/src/platform_api/infrastructure/outbox_relay.py
_MAX_LAST_ERROR_LEN = 2000

async def run_outbox_relay(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bus: MessageBus,
    shutdown: asyncio.Event,
) -> None:
    log.info("outbox relay starting", poll_seconds=settings.outbox_poll_seconds, ...)
    while not shutdown.is_set():
        try:
            await _drain_once(settings, session_factory, bus)
        except Exception:  # relay must never die
            log.exception("outbox relay iteration crashed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=settings.outbox_poll_seconds)
    log.info("outbox relay stopped")

async def _drain_once(settings, session_factory, bus) -> int:
    async with session_factory() as session:
        stmt = (
            select(EventOutbox)
            .where(EventOutbox.sent_at.is_(None))
            .order_by(EventOutbox.created_at.asc())
            .limit(settings.outbox_batch_size)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        for row in rows:
            try:
                envelope = envelope_class_for(row.envelope_type).model_validate(row.payload)
                await bus.publish(envelope)
                row.sent_at = datetime.now(tz=UTC)
            except Exception as exc:
                row.last_error = _truncate(repr(exc), max_len=_MAX_LAST_ERROR_LEN)
                row.attempt_count = row.attempt_count + 1
                log.warning("outbox publish failed", ...)
        await session.commit()
        return len(rows)
```

### `MessageBus` Protocol (platform-api, mirror verbatim)

```python
# Source: ../platform-api/src/platform_api/ports/message_bus.py
@runtime_checkable
class MessageBus(Protocol):
    async def publish(self, envelope: EventEnvelope) -> None: ...
```

Single-method publish-only. Add Google-style docstring matching docs/events.md §Wire format.

### `ValkeyStreamsBus` (platform-api, mirror verbatim)

```python
# Source: ../platform-api/src/platform_api/adapters/valkey_streams_bus.py
_MAXLEN: Final[int] = 100_000
_APPROXIMATE: Final[bool] = True

class ValkeyStreamsBus(MessageBus):
    def __init__(self, settings: Settings) -> None:
        self._client = aioredis.from_url(
            str(settings.valkey_url), encoding="utf-8", decode_responses=False,
        )

    async def publish(self, envelope: EventEnvelope) -> None:
        stream = stream_for_envelope_type(envelope.type)
        serialized = envelope.model_dump_json().encode("utf-8")
        await self._client.xadd(
            name=stream, fields={b"envelope": serialized},
            maxlen=_MAXLEN, approximate=_APPROXIMATE,
        )

    async def close(self) -> None:
        await self._client.aclose()
```

### `OutboxRepo.enqueue` (platform-api, adapt schema name)

```python
# Source: ../platform-api/src/platform_api/modules/billing/repository.py
# Adaptation: EventOutbox from provisioning schema, stream_for_envelope_type from events/envelope.py
class OutboxRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, envelope: EventEnvelope) -> None:
        stmt = (
            pg_insert(EventOutbox)
            .values(
                id=uuid7(),
                envelope_type=envelope.type,
                envelope_id=envelope.id,
                stream=stream_for_envelope_type(envelope.type),
                payload=envelope.model_dump(mode="json"),
                created_at=datetime.now(tz=UTC),
            )
            .on_conflict_do_nothing(index_elements=["envelope_id"])
        )
        await self._session.execute(stmt)
        await self._session.flush()
```

### `EventEnvelope.build()` (platform-api, adapt producer literal)

```python
# Source: ../platform-api/src/platform_api/events/envelope.py
# Adaptation: producer="provisioning-worker" (not "platform-api")
@classmethod
def build(
    cls,
    *,
    type: str,
    version: int,
    payload: P,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> "EventEnvelope[P]":
    return cls(
        id=str(ULID()),
        type=type,
        version=version,
        occurred_at=datetime.now(tz=UTC),
        producer="provisioning-worker",
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
    )
```

### `envelope_class_for` registry (platform-api pattern, one entry in M1)

```python
# Source: ../platform-api/src/platform_api/events/__init__.py (pattern)
# Adaptation: one entry for instance.provisioned; grows in Phase 5
_ENVELOPE_REGISTRY: dict[str, type[EventEnvelope[Any]]] = {
    "instance.provisioned": EventEnvelope[InstanceProvisionedPayload],
}

def envelope_class_for(envelope_type: str) -> type[EventEnvelope[Any]]:
    try:
        return _ENVELOPE_REGISTRY[envelope_type]
    except KeyError as exc:
        raise UnknownEnvelopeType(
            f"no envelope class registered for envelope_type={envelope_type!r}"
        ) from exc
```

### `InstanceProvisionedPayload` — byte-for-byte from `docs/events.md` [VERIFIED: file read]

```python
# Source: docs/events.md §Events this service PRODUCES → instance.provisioned
class InstanceProvisionedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    hostname: str
    url: str
    admin_email: str
    snapshot_version: int
    provisioned_at: datetime
```

### `EventOutbox` mapped class (platform-api, adapt schema + TIMESTAMP type)

```python
# Source: ../platform-api/src/platform_api/modules/billing/models.py (EventOutbox)
# Adaptation: schema="provisioning"; TIMESTAMP(timezone=True) not DateTime(timezone=True)
class EventOutbox(Base):
    __tablename__ = "event_outbox"
    __table_args__ = (
        UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id"),
        {"schema": _SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    envelope_type: Mapped[str] = mapped_column(Text, nullable=False)
    envelope_id: Mapped[str] = mapped_column(Text, nullable=False)
    stream: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
```

---

## Common Pitfalls

### Pitfall 1: `source_event_id` not captured in first session_scope (CR-01 gap)

**What goes wrong:** The first `session_scope()` in `_run_convergence` already captures `task_payload = task.payload` but NOT `source_event_id = task.source_event_id`. Accessing `task.source_event_id` after that session exits raises `MissingGreenlet` / `DetachedInstanceError` (the CR-01 pattern).

**How to avoid:** Add `source_event_id = task.source_event_id` on the line after `task_payload = task.payload` in the first session block. Both are plain string scalars, safe to hold.

**Warning signs:** `sqlalchemy.exc.MissingGreenlet` in test logs after wiring the emit call.

### Pitfall 2: `emit_instance_provisioned` called when `is_first_ready` is False

**What goes wrong:** If a retry re-runs step 4 after `ready_at` was already committed (worker died before Taskiq ACK), `is_first_ready` is `False`. Emitting unconditionally writes a second outbox row with a different ULID — a duplicate event on `events.instance`. `ON CONFLICT DO NOTHING` does NOT help here because each call mints a fresh ULID.

**How to avoid:** Guard the emit call with `if is_first_ready:`, exactly as credential delivery is already guarded. [VERIFIED: tasks.py line 256 — `if is_first_ready:` already scopes the credential delivery call]

### Pitfall 3: Alembic autogenerate drops `server_default`

**What goes wrong:** `make revision name="add_event_outbox"` generates migration code. Autogenerate may omit `server_default=sa.text("0")` on `attempt_count` and `server_default=sa.text("now()")` on `created_at`. It may also silently drop the `UniqueConstraint` name.

**How to avoid:** After `make revision`, open the generated file and hand-verify:
- `attempt_count` has `server_default=sa.text("0")`
- `created_at` has `server_default=sa.text("now()")`
- `UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id")` is present
- No `from __future__ import annotations` (forbidden by CLAUDE.md)

### Pitfall 4: Relay uses global `session_scope()` instead of injected `session_factory`

**What goes wrong:** The existing `session_scope()` in `infrastructure/db.py` is a module-level singleton-backed helper. Using it directly in the relay couples it to the module-level engine, making integration tests unable to inject a test `session_factory`.

**How to avoid:** Pass `session_factory` as an explicit parameter to `run_outbox_relay` and `_drain_once`, exactly as platform-api does. The relay never calls `session_scope()`.

### Pitfall 5: `model_dump(mode="json")` in repo vs `model_dump_json()` in bus — must be consistent

**What goes wrong:** `model_dump()` (without `mode="json"`) serializes UUID fields as `UUID` objects (not strings), which cannot be stored in JSONB as JSON-native types. `model_validate` on the relay side then reconstructs from JSON-native types (strings), and the mismatch causes a `ValidationError`.

**How to avoid:** Repo MUST use `envelope.model_dump(mode="json")` for the JSONB write. Bus uses `envelope.model_dump_json().encode("utf-8")` for the stream value. Both produce JSON-native types — they are consistent. Platform-api uses this exact pattern. [VERIFIED: platform-api billing/repository.py line 82]

### Pitfall 6: `instance.snapshot_version` is NULL in step 4

**What goes wrong:** `snapshot_version` is written by `write_enforcement_snapshot` in step 3 in its own `session_scope()`. The step-4 session opens fresh and calls `get_instance_by_id` which SELECT-loads the row — this picks up `snapshot_version=1` committed by step 3. If step 3 is somehow not committed yet, `snapshot_version` is NULL and the `InstanceProvisionedPayload` field (typed `int`, not `int | None`) raises `ValidationError`.

**How to avoid:** Confirm step 3 commits before step 4 begins. [VERIFIED: tasks.py lines 222–225 — step 3 is a separate `session_scope()` with `await session.commit()`] This is already correct.

### Pitfall 7: `ValkeyStreamsBus.close()` not called on shutdown

**What goes wrong:** If `bus.close()` is not called in `main.py`'s `finally` block, the redis connection pool is not released. This causes `ResourceWarning` in integration tests and a potential leak in production.

**How to avoid:** Add `await bus.close()` in `run()`'s `finally` block after `await dispose_engine()`.

### Pitfall 8: `hostname` column not added in migration but expected by `emit_instance_provisioned`

**What goes wrong:** `hostname` is mapped in `models.py` but if the migration autogenerate omitted it from the `CREATE TABLE`, the column doesn't exist in a real Postgres and `update_instance_status(… hostname=…)` silently writes to a non-existent column.

**How to avoid:** Verify the generated migration includes `sa.Column("hostname", sa.Text(), nullable=True)` in the `event_outbox` table definition. Also add `hostname` to the `instance` table migration update if it was omitted from the Phase-3 migration (check `20260602_1233_add_instance_tables.py` — it should be there since `models.py` has the column).

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Direct `XADD` on state change | Transactional outbox → relay | Industry standard since ~2018 | Atomicity between DB and event bus; relay retries on publish failure without re-running domain logic |
| `SELECT … FOR UPDATE` (blocks all waiters) | `SELECT … FOR UPDATE SKIP LOCKED` | Postgres 9.5+ (2016) | Zero-contention multi-replica relay; stuck rows never block the batch |
| `aioredis` standalone package | `redis.asyncio` via `redis>=5` | `aioredis` deprecated 2022 | Unified client, maintained, same API; already pinned in this repo |
| `from __future__ import annotations` in Alembic files | Python 3.14 native PEP 649 deferral | Python 3.14 | Project forbids the import; generated migration files must NOT include it |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `expire_on_commit=False` is set on `get_session_factory()` (so step-4 ORM objects accessible after commit) | tasks.py step 4 nuances | If True, accessing `instance.snapshot_version` after step-3 commit raises; but db.py line 82 confirms `expire_on_commit=False` [VERIFIED] |
| A2 | `hostname` column was included in the Phase-3 migration for `provisioning.instance` | models.py / Alembic | If missing, `update_instance_status(hostname=…)` would silently fail until the next migration; low risk since models.py maps it and conftest.py uses `create_all` |

**If the table is empty:** Both claims were verified directly in source files read — no user confirmation needed.

---

## Open Questions (RESOLVED)

> All three resolved inline with adopted recommendations; Phase 4 plans implement each (`shared/strings.py`, `events/instance.py`, `OutboxRepo` class).

1. **Where should `_truncate` live?**
   - What we know: Platform-api puts it in `shared/strings.py`. This repo has only one call site in Phase 4 (relay). Phase 5 may add more (e.g. recording relay errors for future payloads).
   - Recommendation: Create `shared/strings.py` with `_truncate` now — mirrors platform-api, tiny cost, ready for Phase 5.

2. **Where should `InstanceProvisionedPayload` live?**
   - What we know: Phase 5 adds five more produced payloads. Platform-api has `events/subscription.py`, `events/payment.py` per domain.
   - Recommendation: Create `events/instance.py` now with `InstanceProvisionedPayload`. Phase 5 adds remaining payloads to the same file.

3. **Should `OutboxRepo` be a class or a module-level function?**
   - What we know: Platform-api uses a class (constructor takes `session`). The relay constructs it per drain; `service.py` constructs it in `emit_instance_provisioned`.
   - Recommendation: Use a class for parity with platform-api. It also makes future testing (inject a mock `OutboxRepo`) easier.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `redis.asyncio` | `ValkeyStreamsBus` | ✓ (uv.lock) | redis>=5 | — |
| `testcontainers[redis]` | Integration test: relay XADD round-trip | ✓ (pyproject.toml) | 4.14.* | — |
| `testcontainers[postgres]` | Integration test: outbox same-txn guarantee | ✓ (pyproject.toml) | 4.14.* | — |
| `fakeredis` | Fast unit test Valkey mock | NOT installed | — | Use testcontainers[redis] for relay tests |
| PostgreSQL 18 container | Integration tests | ✓ (used Phase 3) | postgres:18 | — |

**Missing dependencies with no fallback:** none — all required deps are available.

**Note on fakeredis:** It is not installed. The relay round-trip test (assert event appears on `events.instance` after drain) MUST use `@pytest.mark.integration` + `testcontainers[redis]`. Fast unit tests can mock `MessageBus.publish` as an `AsyncMock`.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.* + pytest-asyncio 1.3.* |
| Config file | `pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `.venv/bin/pytest -m "not integration" tests/` |
| Full suite command | `.venv/bin/pytest tests/` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EVT-01 | Outbox row written in same txn as `ready` transition | integration | `.venv/bin/pytest -m integration tests/provisioning/test_outbox.py::test_outbox_row_written_atomically -x` | ❌ Wave 0 |
| EVT-01 | Relay `_drain_once` marks row `sent_at`, calls `bus.publish` | unit | `.venv/bin/pytest tests/provisioning/test_outbox.py::test_drain_once_marks_sent -x` | ❌ Wave 0 |
| EVT-01 | Relay records `last_error` + bumps `attempt_count` on publish failure | unit | `.venv/bin/pytest tests/provisioning/test_outbox.py::test_drain_once_records_failure -x` | ❌ Wave 0 |
| EVT-01 | `ON CONFLICT DO NOTHING` — double `enqueue` is no-op | integration | `.venv/bin/pytest -m integration tests/provisioning/test_outbox.py::test_enqueue_idempotent -x` | ❌ Wave 0 |
| EVT-01 | Relay XADD round-trip: event appears on `events.instance` | integration | `.venv/bin/pytest -m integration tests/provisioning/test_outbox.py::test_relay_xadd_roundtrip -x` | ❌ Wave 0 |
| EVT-02 | `InstanceProvisionedPayload` fields all present, `causation_id` = triggering envelope id | unit | `.venv/bin/pytest tests/provisioning/test_tasks.py::test_emit_instance_provisioned_fields -x` | ❌ Wave 0 (extends existing file) |
| EVT-02 | `hostname` = `{slug}.{domain_suffix}`, `url` = `https://{hostname}` | unit | `.venv/bin/pytest tests/provisioning/test_tasks.py::test_hostname_derivation -x` | ❌ Wave 0 (extends existing file) |
| EVT-01 | emit guarded by `is_first_ready` — no duplicate on retry | unit | `.venv/bin/pytest tests/provisioning/test_tasks.py::test_no_duplicate_emit_on_retry -x` | ❌ Wave 0 (extends existing file) |

**Fast-unit vs integration boundary:**

- **Unit (Docker-free):** `test_drain_once_*` — mock `session_factory` + `AsyncMock` bus; assert row mutations. `test_emit_instance_provisioned_*` — mock `OutboxRepo.enqueue` + assert envelope fields. `test_hostname_derivation` — assert `instance.url`/`instance.hostname` values.
- **Integration (testcontainers Postgres + Redis):** Same-txn guarantee (outbox row appears only when `ready` commits); relay XADD round-trip (event appears on `events.instance` after drain); idempotent enqueue (duplicate ULID → no row inserted).

### Sampling Rate

- **Per task commit:** `.venv/bin/pytest -m "not integration" tests/provisioning/test_outbox.py tests/provisioning/test_tasks.py`
- **Per wave merge:** `.venv/bin/pytest -m "not integration" tests/`
- **Phase gate:** `.venv/bin/pytest tests/` (full suite including integration) before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/provisioning/test_outbox.py` — covers EVT-01 unit tests (drain, failure recording, idempotency) and EVT-01 integration tests (same-txn, round-trip)
- [ ] EVT-02 test additions to `tests/provisioning/test_tasks.py` — `test_emit_instance_provisioned_fields`, `test_hostname_derivation`, `test_no_duplicate_emit_on_retry`

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Worker talks to Valkey/Postgres on trusted internal network |
| V3 Session Management | no | Worker, not HTTP service |
| V4 Access Control | no | No access control surface |
| V5 Input Validation | yes | `InstanceProvisionedPayload` validated via Pydantic (`frozen=True`, `extra="forbid"`) before write to outbox; `envelope_class_for(...).model_validate(row.payload)` validates on relay read-back |
| V6 Cryptography | no | No crypto in this phase |

### Known Threat Patterns for Outbox + Event Bus

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Credential in event payload | Information Disclosure | `InstanceProvisionedPayload` has no credentials (CLAUDE.md §6.6, D-12 Phase 3); credentials go via `NotificationTransport` only |
| Duplicate event on `events.instance` | Tampering | `is_first_ready` guard + `UNIQUE(envelope_id)` backstop + consumer-side dedupe on `envelope.id` |
| Relay logs exception containing sensitive data | Information Disclosure | `log.warning("outbox publish failed", error=str(exc))` — `str(exc)` on a `redis.RedisError` contains no secrets; avoid `repr(exc)` in log output (use it only for `last_error` column) |
| SSRF via `valkey_url` | Elevation of Privilege | `valkey_url` is a `RedisDsn`-typed pydantic field; validated at startup; internal network only |

---

## Sources

### Primary (HIGH confidence)

- Platform-api source files read directly: `infrastructure/outbox_relay.py`, `ports/message_bus.py`, `adapters/valkey_streams_bus.py`, `modules/billing/models.py`, `modules/billing/repository.py`, `events/envelope.py`, `events/__init__.py` — all patterns and signatures verified.
- Provisioner source files read directly: `infrastructure/outbox_relay.py`, `events/envelope.py`, `events/__init__.py`, `modules/provisioning/tasks.py`, `modules/provisioning/service.py`, `modules/provisioning/repository.py`, `modules/provisioning/models.py`, `settings.py`, `main.py`, `infrastructure/db.py`, `shared/errors.py`, `tests/conftest.py`.
- `docs/events.md` — `InstanceProvisionedPayload` field set, envelope shape, stream routing, retention.
- `docs/architecture.md` — Event production section, idempotency section, database sessions section.
- `docs/deployment-adapter.md` — `InstanceSpec.slug` + `INSTANCE_DOMAIN_SUFFIX` hostname derivation.
- `CLAUDE.md` — §6.1.1 module layout, §6.2 contracts, §6.3 database, §6.4 idempotency, §6.6 logging.
- `.planning/phases/04-event-production-outbox-relay/04-CONTEXT.md` — D-01..D-09 locked decisions.

### Secondary (MEDIUM confidence)

- `pyproject.toml` — confirmed `testcontainers[redis]` pinned; `fakeredis` absent.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; all existing deps verified in uv.lock
- Architecture: HIGH — direct mirror of platform-api; both reference implementation and existing code verified by file read
- Pitfalls: HIGH — derived from direct code inspection (CR-01 gap, IS_FIRST_READY guard, Alembic autogenerate)
- Test strategy: HIGH — existing test infrastructure confirmed; wave 0 gaps explicitly identified

**Research date:** 2026-06-03
**Valid until:** 2026-07-03 (stable stack; platform-api reference implementation is stable)
