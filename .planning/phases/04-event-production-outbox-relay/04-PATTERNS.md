# Phase 4: Event Production (Outbox → Relay) — Pattern Map

**Mapped:** 2026-06-03
**Files analyzed:** 13 (7 new, 6 extended)
**Analogs found:** 13 / 13

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `events/instance.py` (NEW) | model | request-response | `events/subscription.py` (this repo) | exact |
| `events/envelope.py` (EXTEND: add `build()`) | model | request-response | `../platform-api/events/envelope.py` | exact |
| `events/__init__.py` (EXTEND: `envelope_class_for` + exports) | registry | request-response | `events/__init__.py` + `../platform-api/events/__init__.py` | exact |
| `ports/message_bus.py` (NEW) | port/protocol | pub-sub | `ports/event_consumer.py` (this repo) | role-match |
| `adapters/valkey_streams_bus.py` (NEW) | adapter | pub-sub | `adapters/valkey_streams.py` (this repo) + `../platform-api/adapters/valkey_streams_bus.py` | exact |
| `shared/strings.py` (NEW) | utility | transform | `../platform-api/shared/strings.py` | exact |
| `modules/provisioning/models.py` (EXTEND: `EventOutbox`) | model | CRUD | `modules/provisioning/models.py` — `ProvisioningTask` class | exact |
| `modules/provisioning/repository.py` (EXTEND: `OutboxRepo`) | repository | CRUD | `../platform-api/modules/billing/repository.py` — `OutboxRepo` | exact |
| `modules/provisioning/service.py` (EXTEND: `emit_instance_provisioned`) | service | event-driven | `service.py` — `write_enforcement_snapshot` method | role-match |
| `modules/provisioning/tasks.py` (EXTEND: step 4 + hostname fix) | task | event-driven | `tasks.py` — `_run_convergence` step 3 (existing pattern) | exact |
| `infrastructure/outbox_relay.py` (REPLACE body) | infrastructure | pub-sub | `../platform-api/infrastructure/outbox_relay.py` | exact |
| `main.py` (EXTEND: bus construction + relay wiring) | config | request-response | `main.py` — existing `_run_consumer` concern wiring | role-match |
| `migrations/provisioning/versions/YYYYMMDD_add_event_outbox.py` (NEW) | migration | CRUD | `migrations/provisioning/versions/20260602_1233_add_instance_tables.py` | role-match |

---

## Pattern Assignments

### `events/instance.py` (NEW — model, request-response)

**Analog:** `src/provisioning_worker/events/subscription.py` (lines 1-32)

**Imports pattern** (subscription.py lines 17-31):
```python
from datetime import datetime  # noqa: TC003 — runtime-typed Pydantic field
from uuid import UUID  # noqa: TC003 — runtime-typed Pydantic field

from pydantic import BaseModel, ConfigDict

__all__ = [
    "InstanceProvisionedPayload",
]
```

**Core payload pattern** — copy byte-for-byte from `docs/events.md §instance.provisioned`:
```python
class InstanceProvisionedPayload(BaseModel):
    """Payload for ``instance.provisioned`` (v1).

    Emitted when an Odoo instance first reaches ``ready`` status.
    Copied verbatim from ``docs/events.md §Events this service PRODUCES``
    (CLAUDE.md §6.2 — no shared contracts package). No credentials
    (D-12 Phase 3, D-09 Phase 4).
    """

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

**Module docstring pattern** (subscription.py lines 1-15):
- Reference `docs/events.md` as the contract
- Note "re-implemented per repo — no cross-repo import"
- Note "no credentials" explicitly

---

### `events/envelope.py` (EXTEND: add `build()` classmethod)

**Analog:** `../platform-api/src/platform_api/events/envelope.py` (lines 60-99)

**New import to add at top of file** (currently missing from provisioner's envelope.py):
```python
from datetime import UTC, datetime  # promote from TYPE_CHECKING to runtime
from ulid import ULID
```

Note: provisioner's current `envelope.py` line 15 has `from datetime import datetime` as a `# noqa: TC003` runtime import. The `UTC` constant and `ULID` import must be added alongside it. Remove `from __future__ import annotations` if accidentally generated (project forbids it).

**`build()` classmethod** (adapt from platform-api lines 60-99 — change producer literal only):
```python
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
    """Mint a new envelope with a fresh ULID and the provisioning-worker producer.

    This is the only sanctioned way to construct an envelope from producer
    code — direct ``EventEnvelope(...)`` calls risk forgetting the producer
    literal or the ULID id.

    Args:
        type: Dotted event type matching ``docs/events.md``.
        version: Payload schema version (``>= 1``).
        payload: The typed payload instance.
        correlation_id: Upstream request id (optional).
        causation_id: Preceding event id when this event is part of a chain;
            ``None`` for root events.

    Returns:
        A frozen :class:`EventEnvelope` with ``id`` = new ULID,
        ``occurred_at`` = ``datetime.now(tz=UTC)``,
        ``producer`` = ``"provisioning-worker"``.
    """
    return cls(
        id=str(ULID()),
        type=type,
        version=version,
        occurred_at=datetime.now(tz=UTC),
        producer="provisioning-worker",   # ← differs from platform-api's "platform-api"
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=payload,
    )
```

**Add to `__all__`:** `build` is a classmethod so no `__all__` change needed; `EventEnvelope` is already exported.

---

### `events/__init__.py` (EXTEND: produced-side registry + exports)

**Analog (consume-side pattern in this file):** lines 45-77 of `events/__init__.py`
**Analog (produced-side pattern):** `../platform-api/src/platform_api/events/__init__.py` lines 60-103

**New import to add:**
```python
from provisioning_worker.events.instance import InstanceProvisionedPayload
```

**New produced-side registry** (add after existing `_PAYLOAD_REGISTRY`):
```python
# Produced-side registry: dotted envelope type → parameterised EventEnvelope class.
#
# Used by the outbox relay to reconstruct typed envelopes from JSONB rows
# before publishing. Values are ``EventEnvelope[PayloadClass]`` (not bare payload
# classes) because the relay calls .model_validate(row.payload) on the full envelope
# shape, not just the payload. See platform-api events/__init__.py CR-01 note.
# Grows with the Phase-5 catalog (instance.updated, .suspended, etc.).
_ENVELOPE_REGISTRY: dict[str, type[EventEnvelope[Any]]] = {
    "instance.provisioned": EventEnvelope[InstanceProvisionedPayload],
}


def envelope_class_for(envelope_type: str) -> type[EventEnvelope[Any]]:
    """Return the parameterised EventEnvelope class for ``envelope_type``.

    Called by the outbox relay before ``model_validate(row.payload)`` so the
    rebuilt envelope carries the concrete payload class and a real
    ``SchemaSerializer`` (see platform-api VERIFICATION.md CR-01).

    Args:
        envelope_type: Dotted event-type string, e.g. ``"instance.provisioned"``.

    Returns:
        Parameterised generic class, e.g. ``EventEnvelope[InstanceProvisionedPayload]``.

    Raises:
        UnknownEnvelopeType: If the type is not in the produced-side registry.
    """
    try:
        return _ENVELOPE_REGISTRY[envelope_type]
    except KeyError as exc:
        raise UnknownEnvelopeType(
            f"no envelope class registered for envelope_type={envelope_type!r}"
        ) from exc
```

**`__all__` additions:**
```python
"envelope_class_for",
"InstanceProvisionedPayload",
```

**New import needed at top:** `from typing import Any` (required for `dict[str, type[EventEnvelope[Any]]]`).

---

### `ports/message_bus.py` (NEW — port, pub-sub)

**Analog:** `../platform-api/src/platform_api/ports/message_bus.py` (verbatim mirror)
**Symmetric counterpart in this repo:** `ports/event_consumer.py` (lines 1-67)

**Full file pattern** (adapt from platform-api, replacing package references):
```python
"""MessageBus port — domain-event publisher abstraction.

Publish-only in Phase 4; a consume surface is deferred until the port is needed.
Only the adapter (``adapters/valkey_streams_bus.py``) imports the Valkey client —
domain code talks to this port exclusively (CLAUDE.md §4 dependency rule).
Symmetric to the consume-side :class:`~provisioning_worker.ports.event_consumer.EventConsumer`
Protocol.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from provisioning_worker.events.envelope import EventEnvelope


@runtime_checkable
class MessageBus(Protocol):
    """Domain-event publisher abstraction (publish-only in Phase 4).

    Adapters guarantee at-least-once delivery to the appropriate Valkey
    Stream (one stream per envelope-type prefix). Callers must construct
    envelopes via :meth:`EventEnvelope.build` — never direct construction.

    A ``consume(...)`` method will be added if/when an ``events.instance``
    consumer is needed. Keeping the surface minimal matches the Phase-4 scope.
    """

    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish a domain event to its stream.

        Args:
            envelope: A fully-built :class:`EventEnvelope`. Use
                :meth:`EventEnvelope.build` upstream — the adapter does
                not mint ids or timestamps.

        Raises:
            Exception: The adapter re-raises the underlying transport error
                (e.g. ``redis.RedisError``) so the outbox relay can record
                ``last_error`` on the originating row and retry.
        """
        ...
```

---

### `adapters/valkey_streams_bus.py` (NEW — adapter, pub-sub)

**Analog:** `../platform-api/src/platform_api/adapters/valkey_streams_bus.py` (verbatim mirror)
**Symmetric counterpart in this repo:** `adapters/valkey_streams.py` (consume-side adapter)

**Imports pattern** (adapt platform-api lines 1-29, replacing package references):
```python
from typing import TYPE_CHECKING, Final

import redis.asyncio as aioredis
import structlog

from provisioning_worker.events import EventEnvelope, stream_for_envelope_type
from provisioning_worker.ports.message_bus import MessageBus

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)
```

**Constants** (platform-api lines 32-37):
```python
_MAXLEN: Final[int] = 100_000
_APPROXIMATE: Final[bool] = True
```

**Class body** (adapt platform-api lines 40-108):
```python
class ValkeyStreamsBus(MessageBus):
    def __init__(self, settings: Settings) -> None:
        self._client = aioredis.from_url(
            str(settings.valkey_url),
            encoding="utf-8",
            decode_responses=False,
        )

    async def publish(self, envelope: EventEnvelope) -> None:
        stream = stream_for_envelope_type(envelope.type)
        serialized = envelope.model_dump_json().encode("utf-8")
        await self._client.xadd(
            name=stream,
            fields={b"envelope": serialized},
            maxlen=_MAXLEN,
            approximate=_APPROXIMATE,
        )
        log.debug(
            "event published",
            stream=stream,
            envelope_id=envelope.id,
            envelope_type=envelope.type,
        )

    async def close(self) -> None:
        await self._client.aclose()
```

---

### `shared/strings.py` (NEW — utility, transform)

**Analog:** `../platform-api/src/platform_api/shared/strings.py` (verbatim copy, adapt package name in docstring)

```python
"""Shared text-shaping utilities (truncation, sanitisation). No I/O.

Helpers here are deliberately private (``_``-prefixed) — callers import via
``from provisioning_worker.shared.strings import _truncate`` so each call
site is grep-able.
"""


def _truncate(s: str, *, max_len: int) -> str:
    """Return ``s`` truncated to ``max_len`` characters with an ellipsis.

    The ellipsis (``"…"``) occupies the final character of the truncated
    output so the return value is exactly ``max_len`` characters when
    shortening occurs. Inputs already at or under ``max_len`` are unchanged.

    Used by the outbox relay (D-03 Phase 4 — ``event_outbox.last_error``)
    to bound exception context stored in the JSONB column.

    Args:
        s: The input string. Must not be ``None``.
        max_len: Maximum allowed length of the return value. Must be ``>= 1``.

    Returns:
        ``s`` unchanged if ``len(s) <= max_len``; otherwise
        ``s[: max_len - 1] + "…"``.
    """
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"
```

---

### `modules/provisioning/models.py` (EXTEND: add `EventOutbox`)

**Analog (in this file):** `ProvisioningTask` class (lines 183-239) — same schema, same TIMESTAMP type convention
**Analog (column set):** `../platform-api/src/platform_api/modules/billing/models.py` `EventOutbox`

**New imports needed** (already present in file: `TIMESTAMP`, `Text`, `Integer`, `UniqueConstraint`, `func`, `JSONB`, `PG_UUID`, `UUID`, `uuid7`):
```python
from sqlalchemy import text  # needed for server_default=text("0") on attempt_count
```

**`EventOutbox` class** (add after `EnforcementSnapshot`):
```python
class EventOutbox(Base):
    """Transactional outbox — one row per produced domain event.

    Written inside the same ``session_scope()`` transaction as the state change
    that triggers it; the relay polls ``sent_at IS NULL`` rows and publishes
    them to ``events.instance`` via ``XADD``. Exactly mirrors platform-api's
    ``billing.event_outbox`` column set (D-02, D-05).

    ``UNIQUE(envelope_id)`` is a backstop against pathological double-enqueue;
    the primary exactly-once mechanism is the ``ready_at IS NULL`` first-ready
    guard in ``tasks.py`` (D-01).
    """

    __tablename__ = "event_outbox"
    __table_args__: ClassVar[tuple] = (
        UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id"),
        {"schema": _SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    envelope_type: Mapped[str] = mapped_column(Text, nullable=False)
    envelope_id: Mapped[str] = mapped_column(Text, nullable=False)
    stream: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
```

**Key difference from platform-api:** Use `TIMESTAMP(timezone=True)` (not `DateTime(timezone=True)`) to match all existing mapped classes in this file (lines 117-120, 164-175, etc.).

**`__all__` addition:**
```python
"EventOutbox",
```

---

### `modules/provisioning/repository.py` (EXTEND: add `OutboxRepo`)

**Analog:** `../platform-api/src/platform_api/modules/billing/repository.py` (lines 32-88)
**Pattern within this file:** `insert_enforcement_snapshot` (lines 272-303) — same session-receives, no-commit convention

**New imports needed at top of file:**
```python
from datetime import UTC, datetime  # add to TYPE_CHECKING section or runtime
from sqlalchemy.dialects.postgresql import insert as pg_insert
from provisioning_worker.events.envelope import EventEnvelope  # TYPE_CHECKING
from provisioning_worker.modules.provisioning.models import EventOutbox
```

**`OutboxRepo` class** (add at bottom of file, after `update_snapshot_version`):
```python
class OutboxRepo:
    """Persistence for ``provisioning.event_outbox`` — transactional outbox writer.

    Constructed with an open ``AsyncSession``; the caller owns the transaction
    boundary and commits. Used by :meth:`ProvisioningService.emit_instance_provisioned`
    inside the ready-transition ``session_scope()`` (D-07).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, envelope: EventEnvelope) -> None:
        """INSERT one row into ``provisioning.event_outbox`` for ``envelope``.

        Idempotent on ``envelope.id`` via ``ON CONFLICT DO NOTHING`` on
        ``envelope_id`` — a second enqueue with the same ULID is a no-op
        (D-02). Does NOT commit — caller owns the transaction.

        Args:
            envelope: The :class:`EventEnvelope` to enqueue. ``envelope.id``
                (ULID) is the dedup key; ``envelope.type`` drives stream
                selection via :func:`stream_for_envelope_type`.
        """
        from provisioning_worker.events.envelope import stream_for_envelope_type

        stmt = (
            pg_insert(EventOutbox)
            .values(
                envelope_type=envelope.type,
                envelope_id=envelope.id,
                stream=stream_for_envelope_type(envelope.type),
                payload=envelope.model_dump(mode="json"),  # JSON-native types for JSONB
                created_at=datetime.now(tz=UTC),
            )
            .on_conflict_do_nothing(index_elements=["envelope_id"])
        )
        await self._session.execute(stmt)
        await self._session.flush()
```

**`__all__` addition:**
```python
"OutboxRepo",
```

Note: platform-api's `OutboxRepo.enqueue` uses `id=uuid.uuid7()` explicitly because its `EventOutbox` PK has no Python-side `default`. This repo's `EventOutbox` model sets `default=uuid7` on the PK column, so the explicit `id=` kwarg in `.values(...)` can be omitted — SQLAlchemy fires the default at flush.

---

### `modules/provisioning/service.py` (EXTEND: add `emit_instance_provisioned`)

**Analog within this file:** `write_enforcement_snapshot` method (lines 184-209) — same shape: async, accepts session, delegates to repo, no commit
**Key rule (CLAUDE.md §6.1.1):** This is the ONLY place an `instance.*` event is emitted via the outbox.

**New imports needed:**
```python
# Add to TYPE_CHECKING block:
from provisioning_worker.modules.provisioning.repository import OutboxRepo
from provisioning_worker.events.envelope import EventEnvelope
from provisioning_worker.events.instance import InstanceProvisionedPayload
```

**New method** (add after `write_enforcement_snapshot`):
```python
async def emit_instance_provisioned(
    self,
    session: AsyncSession,
    instance: Instance,
    causation_id: str,
    correlation_id: str | None = None,
) -> None:
    """Enqueue an ``instance.provisioned`` outbox row inside the caller's session.

    CLAUDE.md §6.1.1: this is the ONLY place an ``instance.*`` event is
    emitted via the outbox. Constructs the payload from the instance row,
    mints a fresh envelope via :meth:`EventEnvelope.build`, and delegates
    the INSERT to :class:`OutboxRepo` — which uses ``ON CONFLICT DO NOTHING``
    as a backstop (D-02). Does NOT commit — caller owns the transaction (D-01).

    Args:
        session: The open async session (the same ready-transition session).
        instance: The Instance ORM object in ``ready`` state. All fields
            needed for the payload must already be set (``hostname``,
            ``url``, ``admin_email``, ``snapshot_version``, ``ready_at``).
        causation_id: The ULID of the triggering ``subscription.activated``
            envelope — set from ``task.source_event_id`` (D-09).
        correlation_id: Optional upstream trace/request id (passthrough).
    """
    payload = InstanceProvisionedPayload(
        instance_id=instance.id,
        subscription_id=instance.subscription_id,
        customer_id=instance.customer_id,
        hostname=instance.hostname,
        url=instance.url,
        admin_email=instance.admin_email,
        snapshot_version=instance.snapshot_version,
        provisioned_at=instance.ready_at,
    )
    envelope = EventEnvelope.build(
        type="instance.provisioned",
        version=1,
        payload=payload,
        causation_id=causation_id,
        correlation_id=correlation_id,
    )
    outbox = OutboxRepo(session)
    await outbox.enqueue(envelope)
    log.info(
        "instance.provisioned enqueued to outbox",
        instance_id=str(instance.id),
        envelope_id=envelope.id,
    )
```

---

### `modules/provisioning/tasks.py` (EXTEND: step 4 hostname fix + emit)

**Analog within this file:** existing step 4 block (lines 228-278) — add to it, not replace it

**Change 1 — capture `source_event_id` in first `session_scope()` (CR-01 fix, lines 181-183):**

Current (line 182):
```python
        task_payload = task.payload
```

Add immediately after:
```python
        source_event_id = task.source_event_id  # CR-01: capture before session exits
```

**Change 2 — step 4: hostname derivation + `hostname=` kwarg + emit call:**

Current step 4 block (lines 228-246):
```python
    async with session_scope() as session:
        instance = await repository.get_instance_by_id(session, instance_id)
        if instance is None:
            log.error("instance disappeared before ready transition")
            return

        is_first_ready = instance.ready_at is None
        ready_at = clock.now()

        service.validate_transition(InstanceStatus.configuring, InstanceStatus.ready)
        await repository.update_instance_status(
            session,
            instance_id,
            InstanceStatus.ready,
            ready_at=ready_at,
            url=f"https://{spec.slug}",          # ← placeholder, D-08 fix required
        )
        await repository.record_task_success(session, task_id)
        await session.commit()
```

Replace with (D-08 hostname fix + D-01/D-07 emit):
```python
    async with session_scope() as session:
        instance = await repository.get_instance_by_id(session, instance_id)
        if instance is None:
            log.error("instance disappeared before ready transition")
            return

        is_first_ready = instance.ready_at is None
        ready_at = clock.now()

        # D-08: single derivation for both the instance column and the payload.
        hostname = f"{spec.slug}.{settings.instance_domain_suffix}"
        url = f"https://{hostname}"

        service.validate_transition(InstanceStatus.configuring, InstanceStatus.ready)
        await repository.update_instance_status(
            session,
            instance_id,
            InstanceStatus.ready,
            ready_at=ready_at,
            hostname=hostname,
            url=url,
        )
        await repository.record_task_success(session, task_id)

        # D-01 / D-07: emit is inside the same transaction as the ready transition.
        # Guarded by is_first_ready so a task retry never double-enqueues
        # (ON CONFLICT DO NOTHING is the backstop, not the primary guard — D-02).
        if is_first_ready:
            # Re-load the instance so ready_at / snapshot_version are populated
            # (they were just written via update_instance_status above).
            refreshed = await repository.get_instance_by_id(session, instance_id)
            if refreshed is not None:
                await service.emit_instance_provisioned(
                    session,
                    refreshed,
                    causation_id=source_event_id,
                )

        await session.commit()
```

**Credential-delivery block below** (lines 256-278) — no change needed to the `if is_first_ready:` guard or its `try/except`; just update the placeholder URL reference:
```python
# Both references to f"https://{spec.slug}" in transport.send_credentials
# must be updated to use the already-computed `url` variable (D-08).
instance_url=url,
```

---

### `infrastructure/outbox_relay.py` (REPLACE body — real drain)

**Analog:** `../platform-api/src/platform_api/infrastructure/outbox_relay.py` (verbatim mirror)
**Current no-op:** lines 1-39 of this repo's file

**Full replacement** (adapt platform-api, replacing package references and adding `session_factory` + `bus` params):

**Module docstring:** adapt from platform-api, referencing `provisioning.event_outbox` instead of `billing.event_outbox`.

**Imports:**
```python
import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from provisioning_worker.events import envelope_class_for
from provisioning_worker.modules.provisioning.models import EventOutbox
from provisioning_worker.shared.strings import _truncate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from provisioning_worker.ports.message_bus import MessageBus
    from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)

_MAX_LAST_ERROR_LEN = 2000
```

**`run_outbox_relay` signature** (new — adds `session_factory` and `bus`):
```python
async def run_outbox_relay(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bus: MessageBus,
    shutdown: asyncio.Event,
) -> None:
```

**Loop body** (mirror platform-api lines 74-84 verbatim):
```python
    log.info(
        "outbox relay starting",
        poll_seconds=settings.outbox_poll_seconds,
        batch_size=settings.outbox_batch_size,
    )
    while not shutdown.is_set():
        try:
            await _drain_once(settings, session_factory, bus)
        except Exception:  # the relay must never die
            log.exception("outbox relay iteration crashed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=settings.outbox_poll_seconds)
    log.info("outbox relay stopped")
```

**`_drain_once` body** (mirror platform-api lines 87-135 verbatim — only model name differs):
```python
async def _drain_once(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bus: MessageBus,
) -> int:
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
                log.warning(
                    "outbox publish failed",
                    envelope_id=row.envelope_id,
                    envelope_type=row.envelope_type,
                    attempt_count=row.attempt_count,
                    error=str(exc),
                )
        await session.commit()
        return len(rows)
```

**Critical:** The relay uses the injected `session_factory()` — never calls `session_scope()` (Pitfall 4). This enables integration tests to inject a test session factory.

---

### `main.py` (EXTEND: bus construction + relay wiring)

**Analog within this file:** `_run_consumer` concern wiring (lines 103-134) — same pattern of constructing an adapter, using it, calling `close()` in `finally`.

**New imports to add** (after existing adapter imports, lines 21-28):
```python
from provisioning_worker.adapters.valkey_streams_bus import ValkeyStreamsBus
from provisioning_worker.infrastructure.db import get_session_factory
```

(Note: `get_session_factory` is already imported as `session_scope` on line 24; add `get_session_factory` to that import.)

**Change in `run()` function** (lines 91-100):

Add bus construction before the TaskGroup (after `get_engine(settings)`):
```python
    get_engine(settings)
    bus = ValkeyStreamsBus(settings)  # ← new
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_run_consumer(settings, shutdown), name="consumer")
            tg.create_task(_run_convergence(settings, shutdown), name="convergence")
            tg.create_task(
                run_outbox_relay(settings, get_session_factory(), bus, shutdown),  # ← changed
                name="outbox_relay",
            )
            tg.create_task(run_health_server(settings, shutdown), name="health_server")
    except* Exception as eg:
        raise SystemExit(1) from eg.exceptions[0]
    finally:
        await bus.close()    # ← new (Pitfall 7)
        await dispose_engine()
```

---

### `migrations/provisioning/versions/YYYYMMDD_add_event_outbox.py` (NEW — migration)

**Analog:** `migrations/provisioning/versions/20260602_1233_add_instance_tables.py` — same tree, same `version_table_schema=provisioning` convention

**Generate via:** `make revision name="add_event_outbox"` then hand-verify the autogenerated file.

**Mandatory post-generation checks (Pitfalls 3 and 8 from RESEARCH.md):**

1. No `from __future__ import annotations` (project forbids it).
2. `attempt_count` column has `server_default=sa.text("0")`.
3. `created_at` column has `server_default=sa.text("now()")`.
4. `UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id")` is present in `op.create_table(...)`.
5. The `provisioning` schema prefix is present on the table name.

**Expected `op.create_table` shape:**
```python
op.create_table(
    "event_outbox",
    sa.Column("id", sa.UUID(), nullable=False),
    sa.Column("envelope_type", sa.Text(), nullable=False),
    sa.Column("envelope_id", sa.Text(), nullable=False),
    sa.Column("stream", sa.Text(), nullable=False),
    sa.Column("payload", postgresql.JSONB(), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("last_error", sa.Text(), nullable=True),
    sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id"),
    schema="provisioning",
)
```

---

## Shared Patterns

### Transaction Boundary (no-commit convention)
**Source:** `modules/provisioning/repository.py` module docstring (lines 1-17) and every function
**Apply to:** `OutboxRepo.enqueue`, `ProvisioningService.emit_instance_provisioned`
```
All repo functions and service methods accept an open AsyncSession and do NOT commit.
The task or handler that opens session_scope() owns the commit.
```

### CR-01: Capture Scalars While Session Is Open
**Source:** `modules/provisioning/tasks.py` lines 176-183
**Apply to:** `tasks.py` step 4 — capture `source_event_id` in the first session block
```python
# In _run_convergence, first async with session_scope() block:
current_status = instance.status
task_payload = task.payload
source_event_id = task.source_event_id  # ← add this line
```

### `is_first_ready` Guard
**Source:** `modules/provisioning/tasks.py` lines 234, 256
**Apply to:** `tasks.py` step 4 — emit call must be inside `if is_first_ready:`
```python
if is_first_ready:
    # ... existing credential delivery ...
    # AND the new emit call (same guard, same condition)
```

### Structlog Context Binding
**Source:** `modules/provisioning/tasks.py` line 126; `service.py` line 153
**Apply to:** `ValkeyStreamsBus.publish` (debug log), `ProvisioningService.emit_instance_provisioned` (info log)
```python
log.info("instance.provisioned enqueued to outbox", instance_id=str(instance.id), envelope_id=envelope.id)
log.debug("event published", stream=stream, envelope_id=envelope.id, envelope_type=envelope.type)
```

### Relay Never Dies
**Source:** `../platform-api/infrastructure/outbox_relay.py` lines 77-78
**Apply to:** `infrastructure/outbox_relay.py`
```python
except Exception:  # the relay must never die
    log.exception("outbox relay iteration crashed")
```

### `model_dump(mode="json")` for JSONB, `model_dump_json()` for Stream
**Source:** `../platform-api/modules/billing/repository.py` line 82; `../platform-api/adapters/valkey_streams_bus.py` line 87
**Apply to:** `OutboxRepo.enqueue` (JSONB write) and `ValkeyStreamsBus.publish` (stream write)
```python
# Repo — JSONB column:
payload=envelope.model_dump(mode="json"),

# Bus — stream field bytes:
serialized = envelope.model_dump_json().encode("utf-8")
```
These two serialization calls round-trip identically for UUID/datetime fields (Pitfall 5).

---

## No Analog Found

All files have a close match. No entries.

---

## Metadata

**Analog search scope:**
- `src/provisioning_worker/` — all existing modules read
- `../platform-api/src/platform_api/infrastructure/`, `ports/`, `adapters/`, `modules/billing/`, `events/`, `shared/` — reference implementation read

**Files scanned:** 20 source files read directly
**Pattern extraction date:** 2026-06-03
