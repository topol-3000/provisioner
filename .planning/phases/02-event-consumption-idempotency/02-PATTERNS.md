# Phase 2: Event Consumption & Idempotency — Pattern Map

**Mapped:** 2026-06-02
**Files analyzed:** 12
**Analogs found:** 12 / 12

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `src/provisioning_worker/events/envelope.py` | model | request-response | `../platform-api/src/platform_api/events/envelope.py` | exact (drop `build()`) |
| `src/provisioning_worker/events/subscription.py` | model | request-response | `../platform-api/src/platform_api/events/subscription.py` | exact (swap `MoneyDecimal` → `Decimal`) |
| `src/provisioning_worker/events/__init__.py` | config/registry | request-response | `../platform-api/src/platform_api/events/__init__.py` | role-match (consume-only subset) |
| `src/provisioning_worker/ports/event_consumer.py` | port (Protocol) | event-driven | `src/provisioning_worker/ports/__init__.py` + platform-api `ports/message_bus.py` | role-match |
| `src/provisioning_worker/adapters/valkey_streams.py` | adapter | event-driven | `src/provisioning_worker/main.py:_run_consumer` (lines 77–128) | exact extract |
| `src/provisioning_worker/shared/event_consumer.py` | utility/wrapper | event-driven | `src/provisioning_worker/infrastructure/db.py:session_scope` | partial (session contract reused) |
| `src/provisioning_worker/modules/provisioning/handlers.py` | handler | event-driven | `src/provisioning_worker/main.py:_run_consumer` no-op inner loop | role-match |
| `src/provisioning_worker/modules/provisioning/models.py` | model (ORM) | CRUD | `src/provisioning_worker/infrastructure/db.py` (engine/session pattern) | partial (first ORM model in module) |
| `src/provisioning_worker/settings.py` | config | — | `src/provisioning_worker/settings.py` (modify existing) | exact (add one field) |
| `migrations/provisioning/versions/<rev>_add_processed_event.py` | migration | CRUD | `migrations/provisioning/script.py.mako` | exact (hand-authored body) |
| `tests/events/test_envelope.py` + `test_subscription_payloads.py` | test | request-response | `../platform-api/tests/events/test_subscription_payload.py` + `test_envelope_registry.py` | exact (no cross-repo import) |
| `tests/provisioning/test_idempotency.py` + `test_handlers.py` | test | event-driven | `tests/test_boot.py` (mock/patch pattern) + `tests/test_health.py` | role-match |

---

## Pattern Assignments

---

### `src/provisioning_worker/events/envelope.py` (model, request-response)

**Analog:** `/home/yevhenii/projects/saas/platform-api/src/platform_api/events/envelope.py`

**Imports pattern** (lines 1–7 of analog):
```python
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID
```
Note: consume-only re-implementation drops the `ULID` import (no `build()`) and the `UTC`/`datetime` import can be simplified to just `datetime` since we validate inbound, not mint.

**Core model pattern** (lines 22–58 of analog — keep all fields, drop `build()`):
```python
class EventEnvelope[P: BaseModel](BaseModel):
    """Wire envelope for every domain event consumed by this service.

    Re-implemented per repo (no shared package — CLAUDE.md §6.2).
    Frozen, extra="forbid" — contract drift is a validation failure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=26, max_length=26)
    type: str                         # str, NOT Literal — forward-compat (D-05, Pitfall 4)
    version: int = Field(..., ge=1)
    occurred_at: datetime
    producer: Literal["platform-api", "provisioning-worker", "telemetry-worker"]
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: P

    # NOTE: No build() classmethod — producer concern, Phase 4+.
```

**`stream_for_envelope_type` helper** (lines 102–119 of analog — copy verbatim):
```python
def stream_for_envelope_type(envelope_type: str) -> str:
    """Return the Valkey stream name for a given envelope type."""
    return f"events.{envelope_type.split('.', 1)[0]}"
```

---

### `src/provisioning_worker/events/subscription.py` (model, request-response)

**Analog:** `/home/yevhenii/projects/saas/platform-api/src/platform_api/events/subscription.py`

**Key divergence from analog:** Platform-api uses `MoneyDecimal` (an `Annotated[Decimal, PlainSerializer(str)]` alias from `modules/subscription/schemas.py`). Consume-only path uses plain `Decimal` — Pydantic 2.13 coerces the JSON string `"129.99"` to `Decimal` and serializes back to `"129.99"` in JSON mode (VERIFIED in RESEARCH.md).

**Imports pattern** (adapt from analog lines 10–18):
```python
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
```

**Model pattern** (adapt all six classes from analog lines 31–225; substitute `MoneyDecimal` → `Decimal`):
```python
class SubscriptionActivatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    quote_id: UUID
    stripe_subscription_id: str
    billing_cycle: Literal["monthly", "annual"]
    currency: str = Field(..., min_length=3, max_length=3)
    line_count: int = Field(..., ge=1)
    total_amount: Decimal              # plain Decimal, NOT MoneyDecimal
    activated_at: datetime
    current_period_start: datetime
    current_period_end: datetime
```
The remaining five classes (`LineDelta`, `SubscriptionLinesChangedPayload`, `SubscriptionSuspendedPayload`, `SubscriptionReinstatedPayload`, `SubscriptionCancelledPayload`) have zero drift — copy field-for-field from analog lines 77–225.

---

### `src/provisioning_worker/events/__init__.py` (registry, request-response)

**Analog:** `/home/yevhenii/projects/saas/platform-api/src/platform_api/events/__init__.py`

**Key divergence from analog:** Platform-api's registry maps type→`EventEnvelope[Payload]` (parameterised class) for the outbox relay. Consume-side registry maps type→payload model class (simpler, used in two-phase parse). The `UnknownEnvelopeType` exception and `envelope_class_for`-style lookup helper are kept.

**Registry pattern** (adapt from analog lines 43–103):
```python
from pydantic import BaseModel

from provisioning_worker.events.envelope import EventEnvelope, stream_for_envelope_type
from provisioning_worker.events.subscription import (
    LineDelta,
    SubscriptionActivatedPayload,
    SubscriptionCancelledPayload,
    SubscriptionLinesChangedPayload,
    SubscriptionReinstatedPayload,
    SubscriptionSuspendedPayload,
)

__all__ = [
    "EventEnvelope",
    "LineDelta",
    "SubscriptionActivatedPayload",
    "SubscriptionCancelledPayload",
    "SubscriptionLinesChangedPayload",
    "SubscriptionReinstatedPayload",
    "SubscriptionSuspendedPayload",
    "UnknownEnvelopeType",
    "payload_class_for",
    "stream_for_envelope_type",
]


class UnknownEnvelopeType(Exception):
    """Raised when envelope.type is not in the consume-side registry."""


# Consume-side registry: type string → payload model class.
# Do NOT use EventEnvelope[Payload] as values here — this registry
# is used in two-phase parse (type unknown until outer envelope is read).
_PAYLOAD_REGISTRY: dict[str, type[BaseModel]] = {
    "subscription.activated": SubscriptionActivatedPayload,
    "subscription.lines_changed": SubscriptionLinesChangedPayload,
    "subscription.suspended": SubscriptionSuspendedPayload,
    "subscription.reinstated": SubscriptionReinstatedPayload,
    "subscription.cancelled": SubscriptionCancelledPayload,
}


def payload_class_for(envelope_type: str) -> type[BaseModel]:
    """Return the payload model class for envelope_type, or raise UnknownEnvelopeType."""
    try:
        return _PAYLOAD_REGISTRY[envelope_type]
    except KeyError as exc:
        raise UnknownEnvelopeType(
            f"no payload class registered for envelope_type={envelope_type!r}"
        ) from exc
```

---

### `src/provisioning_worker/ports/event_consumer.py` (port/Protocol, event-driven)

**Analog:** `src/provisioning_worker/ports/__init__.py` (stub only — docstring says "Protocol interfaces for the deployment adapter and notification transport ports"). No body to copy.

**Secondary analog shape:** Platform-api's `ports/message_bus.py` (publish-side Protocol). Mirror the Protocol pattern but for the consume side.

**Protocol pattern to author** (no direct analog body, use RESEARCH.md §Architectural Responsibility Map):
```python
from typing import Protocol, runtime_checkable
import asyncio
from collections.abc import Callable, Awaitable
from pydantic import BaseModel


HandlerFn = Callable[[BaseModel], Awaitable[None]]


@runtime_checkable
class EventConsumer(Protocol):
    """Port for the Valkey Streams consume side.

    Only adapters import redis.asyncio (CLAUDE.md §4 dependency rule).
    Domain code and shared/event_consumer.py talk to this Protocol.
    """

    async def start(self) -> None:
        """Create consumer group idempotently; tolerate BUSYGROUP on restart."""
        ...

    async def run(
        self,
        handlers: dict[str, HandlerFn],
        shutdown: asyncio.Event,
    ) -> None:
        """Poll loop — dispatch to handlers until shutdown is set."""
        ...

    async def close(self) -> None:
        """Release the underlying connection pool."""
        ...
```

---

### `src/provisioning_worker/adapters/valkey_streams.py` (adapter, event-driven)

**Analog:** `src/provisioning_worker/main.py` lines 77–128 (`_run_consumer`) — extract verbatim, add parse/dispatch/dedupe/poison/XAUTOCLAIM.

**Secondary analog:** `/home/yevhenii/projects/saas/platform-api/src/platform_api/adapters/valkey_streams_bus.py` — client construction pattern (`aioredis.from_url`, `decode_responses`, `close()`).

**Imports pattern** (adapt from `main.py` lines 8–12 + platform-api bus lines 17–23):
```python
import asyncio
import json
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from pydantic import ValidationError

from provisioning_worker.events import UnknownEnvelopeType, payload_class_for
from provisioning_worker.events.envelope import EventEnvelope

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)
```

**Client construction pattern** (from `main.py` line 90 + platform-api bus lines 61–64):
```python
# decode_responses=True: fields["envelope"] arrives as str, not bytes
# (platform-api bus uses decode_responses=False for publish; consume side uses True)
self._client = aioredis.from_url(
    str(settings.valkey_url),
    decode_responses=True,
)
```

**`start()` / XGROUP CREATE pattern** (from `main.py` lines 92–101 — transfer verbatim):
```python
async def start(self) -> None:
    try:
        await self._client.xgroup_create(
            name="events.subscription",
            groupname=self._group,
            id="0",
            mkstream=True,
        )
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    log.info(
        "joined consumer group",
        group=self._group,
        stream="events.subscription",
        consumer=self._consumer,
    )
```

**XREADGROUP poll loop** (from `main.py` lines 110–127 — extract and extend):
```python
while not shutdown.is_set():
    results = await self._client.xreadgroup(
        groupname=self._group,
        consumername=self._consumer,
        streams={"events.subscription": ">"},
        count=10,
        block=1000,
    )
    if results:
        for _stream, messages in results:
            for msg_id, fields in messages:
                await self._dispatch(msg_id, fields, handlers)
    poll_count += 1
    if poll_count % self._reclaim_every_n == 0:
        await self._reclaim(handlers)
```

**XAUTOCLAIM reclaim pattern** (from RESEARCH.md §XAUTOCLAIM — 3-element unpack is critical):
```python
cursor = "0-0"
while True:
    result = await self._client.xautoclaim(
        name="events.subscription",
        groupname=self._group,
        consumername=self._consumer,
        min_idle_time=self._reclaim_min_idle_ms,
        start_id=cursor,
        count=10,
    )
    cursor, messages, _deleted_ids = result   # 3-element list — NOT 2
    for msg_id, fields in (messages or []):
        await self._dispatch(msg_id, fields, handlers)
    if cursor == "0-0":
        break
```

**Poison/dispatch pattern** (from RESEARCH.md §Poison vs Unknown-Type Policy):
```python
async def _dispatch(self, msg_id: str, fields: dict[str, str], handlers) -> None:
    try:
        raw_data = json.loads(fields["envelope"])
    except (json.JSONDecodeError, KeyError):
        log.error("poison message — bad JSON or missing envelope field", msg_id=msg_id)
        await self._client.xack("events.subscription", self._group, msg_id)
        return

    try:
        raw_env = _RawEnvelope.model_validate(raw_data)
    except ValidationError:
        log.error("poison message — envelope validation failed",
                  msg_id=msg_id, envelope_type=raw_data.get("type", "<unknown>"))
        await self._client.xack("events.subscription", self._group, msg_id)
        return

    try:
        payload_cls = payload_class_for(raw_env.type)
    except UnknownEnvelopeType:
        log.warning("unknown envelope type — skipping", msg_id=msg_id, envelope_type=raw_env.type)
        await self._client.xack("events.subscription", self._group, msg_id)
        return

    try:
        payload = payload_cls.model_validate(raw_env.payload)
    except ValidationError:
        log.error("poison message — payload validation failed",
                  msg_id=msg_id, envelope_type=raw_env.type)
        await self._client.xack("events.subscription", self._group, msg_id)
        return

    # Happy path: idempotency wrapper in shared/event_consumer.py handles dedupe + DB write
    handler = handlers.get(raw_env.type)
    if handler is not None:
        await handler(raw_env, payload)
    # XACK after successful commit (D-06 ordering — commit happens inside handler wrapper)
    await self._client.xack("events.subscription", self._group, msg_id)
```

**`close()` pattern** (from platform-api bus line 108):
```python
async def close(self) -> None:
    await self._client.aclose()
```

---

### `src/provisioning_worker/shared/event_consumer.py` (utility/wrapper, event-driven)

**Analog:** `src/provisioning_worker/infrastructure/db.py` — `session_scope()` at lines 86–103 is the transaction boundary this wrapper runs inside.

**`session_scope()` contract** (from `infrastructure/db.py` lines 86–103):
```python
@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
# Caller must call await session.commit() explicitly — no auto-commit.
```

**Idempotency wrapper pattern** (from RESEARCH.md §Same-Transaction Dedupe):
```python
from provisioning_worker.infrastructure.db import session_scope

async def _handle_with_dedupe(
    raw_env,
    payload,
    handler_fn,
    consumer_group: str,
) -> None:
    """Run handler inside session_scope(); dedupe on (event_id, consumer_group).

    Crash semantics:
      - Crash BEFORE commit → re-delivered → reprocesses cleanly.
      - Crash AFTER commit, BEFORE xack → re-delivered → short-circuits on
        existing processed_event row.
    """
    async with session_scope() as session:
        existing = await _select_processed_event(session, raw_env.id, consumer_group)
        if existing is not None:
            log.debug("dedupe short-circuit", envelope_id=raw_env.id)
            return
        await handler_fn(raw_env, payload, session)
        await _insert_processed_event(session, raw_env.id, consumer_group)
        await session.commit()
    # XACK happens in the adapter AFTER this function returns.
```

**DB query pattern** (follow same `select()` style used in `infrastructure/db.py`; `session.execute(select(...).where(...))`):
```python
from sqlalchemy import select
from provisioning_worker.modules.provisioning.models import ProcessedEvent

async def _select_processed_event(
    session: AsyncSession,
    event_id: str,
    consumer_group: str,
) -> ProcessedEvent | None:
    result = await session.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.event_id == event_id,
            ProcessedEvent.consumer_group == consumer_group,
        )
    )
    return result.scalar_one_or_none()
```

---

### `src/provisioning_worker/modules/provisioning/handlers.py` (handler, event-driven)

**Analog:** `src/provisioning_worker/main.py` lines 120–126 (the inner no-op loop body) for structlog pattern; CLAUDE.md §6.1.1 ("handlers.py: thin: validate → dedupe → return").

**Handler pattern** (one per consumed type; module-level functions per CLAUDE.md §6.1.1):
```python
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from provisioning_worker.events.subscription import SubscriptionActivatedPayload

log = structlog.get_logger(__name__)


async def handle_subscription_activated(raw_env, payload: SubscriptionActivatedPayload, session: AsyncSession) -> None:
    """No-op handler for subscription.activated (Phase 2 stub).

    Phase 3 will open a provisioning.instance row and a create task here.
    """
    structlog.contextvars.bind_contextvars(
        envelope_id=raw_env.id,
        subscription_id=str(payload.subscription_id),
        correlation_id=raw_env.correlation_id,
    )
    log.debug("subscription.activated received (no-op)", subscription_id=str(payload.subscription_id))
```
Repeat the same pattern for the other four handler functions.

---

### `src/provisioning_worker/modules/provisioning/models.py` (ORM model, CRUD)

**Analog:** `src/provisioning_worker/infrastructure/db.py` — engine/session setup; no existing ORM mapped class in this repo yet (Phase 1 left `models.py` as a stub). The `DeclarativeBase` + `mapped_column` pattern follows SQLAlchemy 2.0 async conventions consistent with the Phase-1 engine setup.

**`ProcessedEvent` ORM model pattern** (first mapped class in this repo; follow SQLAlchemy 2.0 style):
```python
from datetime import datetime
from typing import Final

from sqlalchemy import String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_SCHEMA: Final[str] = "provisioning"


class Base(DeclarativeBase):
    pass


class ProcessedEvent(Base):
    """Idempotency ledger row — one row per (event_id, consumer_group) pair.

    Inserted in the same transaction as any handler side-effects. The
    composite primary key makes duplicate-delivery a safe no-op: the
    INSERT will conflict and session.commit() will roll back, causing the
    dedupe guard to short-circuit on re-delivery.
    """

    __tablename__ = "processed_event"
    __table_args__ = {"schema": _SCHEMA}

    event_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    consumer_group: Mapped[str] = mapped_column(Text, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(server_default=func.now())
```

**Note on `env.py` update:** Once `ProcessedEvent` is defined, `env.py` line 27 (`target_metadata = None`) must be updated to `target_metadata = Base.metadata` so autogenerate works for future migrations. This is the Path A migration approach from RESEARCH.md.

---

### `src/provisioning_worker/settings.py` (config — modify existing)

**Analog:** `src/provisioning_worker/settings.py` itself — add one field following the existing `Field(default=..., description=...)` convention.

**Addition pattern** (after line 54, `consumer_name: str = "worker-1"`):
```python
consumer_reclaim_min_idle_ms: int = Field(
    default=60_000,
    ge=1_000,
    description="XAUTOCLAIM min-idle-time in milliseconds (~60s default).",
)
```
Also add `CONSUMER_RECLAIM_MIN_IDLE_MS=60000` to `.env.example`.

---

### `migrations/provisioning/versions/<rev>_add_processed_event.py` (migration, CRUD)

**Analog:** `migrations/provisioning/script.py.mako` — the template scaffold (lines 1–24) provides the exact file structure.

**Scaffold shape** (from `script.py.mako` lines 1–24):
```python
"""add processed_event

Revision ID: <generated>
Revises: None
Create Date: <generated>
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "<generated>"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
```

**Hand-authored `upgrade()` body** (from RESEARCH.md §processed_event Migration):
```python
def upgrade() -> None:
    op.create_table(
        "processed_event",
        sa.Column("event_id", sa.String(26), nullable=False),
        sa.Column("consumer_group", sa.Text(), nullable=False),
        sa.Column(
            "processed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id", "consumer_group"),
        schema="provisioning",   # CRITICAL — Pitfall 5
    )


def downgrade() -> None:
    op.drop_table("processed_event", schema="provisioning")
```

**Critical rules:** No `from __future__ import annotations` (Pitfall 8); always `schema="provisioning"` (Pitfall 5).

---

### `tests/events/test_envelope.py` + `tests/events/test_subscription_payloads.py` (test, request-response)

**Analog:** `/home/yevhenii/projects/saas/platform-api/tests/events/test_subscription_payload.py` and `test_envelope_registry.py` — copy the field-pinning + round-trip pattern. No cross-repo import (D-09).

**Field-pinning pattern** (from platform-api `test_subscription_payload.py` lines 22–50):
```python
_ENVELOPE_FIELDS: frozenset[str] = frozenset({
    "id", "type", "version", "occurred_at", "producer",
    "correlation_id", "causation_id", "payload",
})

_ACTIVATED_FIELDS: frozenset[str] = frozenset({
    "subscription_id", "customer_id", "quote_id",
    "stripe_subscription_id", "billing_cycle", "currency",
    "line_count", "total_amount", "activated_at",
    "current_period_start", "current_period_end",
})
```

**Round-trip test pattern** (from platform-api `test_subscription_payload.py` lines 70–130):
```python
def test_subscription_activated_round_trip() -> None:
    """Payload round-trips from wire JSON; total_amount parses from string."""
    env = EventEnvelope[SubscriptionActivatedPayload].model_validate(_ACTIVATED_FIXTURE)
    assert env.type == "subscription.activated"
    assert env.payload.total_amount == Decimal("129.99")
    dumped = env.model_dump(mode="json")
    assert set(dumped.keys()) == _ENVELOPE_FIELDS
    assert set(dumped["payload"].keys()) == _ACTIVATED_FIELDS
    assert isinstance(dumped["payload"]["total_amount"], str)  # Decimal → JSON string
    re_env = EventEnvelope[SubscriptionActivatedPayload].model_validate(dumped)
    assert re_env == env
```

**Extra-field rejection pattern** (from platform-api `test_envelope_registry.py` pattern):
```python
def test_extra_field_rejected() -> None:
    from pydantic import ValidationError
    bad = {**_ACTIVATED_FIXTURE["payload"], "unexpected_field": "x"}
    with pytest.raises(ValidationError):
        SubscriptionActivatedPayload.model_validate(bad)
```

**Canonical fixture format** (from RESEARCH.md §Round-trip test fixtures, D-09):
```python
_ACTIVATED_FIXTURE = {
    "id": "01JZQABCDE12345678901234AB",   # 26-char ULID
    "type": "subscription.activated",
    "version": 1,
    "occurred_at": "2026-06-01T00:00:00Z",
    "producer": "platform-api",
    "correlation_id": None,
    "causation_id": None,
    "payload": {
        "subscription_id": "018efa2c-0000-7000-8000-000000000001",
        "customer_id": "018efa2c-0000-7000-8000-000000000002",
        "quote_id": "018efa2c-0000-7000-8000-000000000003",
        "stripe_subscription_id": "sub_test_abc123",
        "billing_cycle": "monthly",
        "currency": "USD",
        "line_count": 2,
        "total_amount": "129.99",   # string on wire — Pydantic coerces to Decimal
        "activated_at": "2026-06-01T00:00:00Z",
        "current_period_start": "2026-06-01T00:00:00Z",
        "current_period_end": "2026-07-01T00:00:00Z",
    },
}
```

---

### `tests/provisioning/test_idempotency.py` + `tests/provisioning/test_handlers.py` (test, event-driven)

**Analog:** `tests/test_boot.py` — unit test with `unittest.mock.patch` and `asyncio.Event` patterns; `tests/test_health.py` — async test + `asyncio.create_task` pattern.

**Unit test pattern** (handler dispatch — no DB — from `tests/test_boot.py` lines 39–74):
```python
async def test_handler_dispatched_and_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_subscription_activated is called and does not raise."""
    from provisioning_worker.modules.provisioning.handlers import handle_subscription_activated
    from unittest.mock import AsyncMock, MagicMock

    mock_session = AsyncMock()
    mock_env = MagicMock(id="01JZQABCDE12345678901234AB", correlation_id=None)
    mock_payload = MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")

    await handle_subscription_activated(mock_env, mock_payload, mock_session)
    # No-op handler: assert no DB writes occurred
    mock_session.execute.assert_not_called()
```

**Integration test pattern** (`@pytest.mark.integration`, real Postgres via testcontainers — follow `tests/test_health.py` async pattern):
```python
@pytest.mark.integration
async def test_dedupe_replay_short_circuits(pg_session) -> None:
    """Second delivery of same envelope.id does not insert a second processed_event row."""
    # First delivery
    await _handle_with_dedupe(raw_env_1, payload_1, _noop_handler, "cg.provisioning-convergence")
    # Second delivery (same envelope.id)
    await _handle_with_dedupe(raw_env_1, payload_1, _noop_handler, "cg.provisioning-convergence")
    count = await pg_session.scalar(select(func.count()).select_from(ProcessedEvent))
    assert count == 1
```

---

### `src/provisioning_worker/main.py` (modify existing — wiring change)

**Analog:** `src/provisioning_worker/main.py` itself. `_run_consumer` (lines 77–128) shrinks to wiring: construct `ValkeyStreamsConsumer`, register five handlers, call `consumer.run(handlers, shutdown)`.

**New `_run_consumer` shape** (replace lines 77–128 with):
```python
async def _run_consumer(settings: Settings, shutdown: asyncio.Event) -> None:
    """Wire and run the Valkey Streams consumer (Phase 2+).

    Constructs ValkeyStreamsConsumer, registers the five subscription.*
    handlers wrapped by the idempotency guard, and runs the poll loop
    until shutdown is set.
    """
    from provisioning_worker.adapters.valkey_streams import ValkeyStreamsConsumer
    from provisioning_worker.shared.event_consumer import make_handler_registry
    from provisioning_worker.modules.provisioning import handlers

    consumer = ValkeyStreamsConsumer(settings)
    await consumer.start()

    handler_map = make_handler_registry(
        settings.provisioning_consumer_group,
        {
            "subscription.activated": handlers.handle_subscription_activated,
            "subscription.lines_changed": handlers.handle_subscription_lines_changed,
            "subscription.suspended": handlers.handle_subscription_suspended,
            "subscription.reinstated": handlers.handle_subscription_reinstated,
            "subscription.cancelled": handlers.handle_subscription_cancelled,
        },
    )

    try:
        await consumer.run(handler_map, shutdown)
    finally:
        await consumer.close()
```

---

### `tests/conftest.py` (modify existing — add fixtures)

**Analog:** `tests/conftest.py` itself (currently a near-empty stub with a docstring). `tests/test_health.py` shows the `asyncio.create_task` + `asyncio.Event` + teardown pattern for async fixtures.

**Async session fixture pattern** (testcontainers Postgres — add to `conftest.py`):
```python
import pytest
from testcontainers.postgres import PostgresContainer
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

@pytest.fixture(scope="session")
def postgres_container():
    """Start a real Postgres container for integration tests."""
    with PostgresContainer("postgres:18") as pg:
        yield pg

@pytest.fixture
async def pg_session(postgres_container):
    """Yield an async session against the test Postgres container."""
    engine = create_async_engine(postgres_container.get_connection_url().replace("psycopg2", "psycopg"))
    async with async_sessionmaker(engine)() as session:
        yield session
    await engine.dispose()
```

---

## Shared Patterns

### Transaction boundary / session_scope
**Source:** `src/provisioning_worker/infrastructure/db.py` lines 86–103
**Apply to:** `shared/event_consumer.py` (dedupe guard), `modules/provisioning/handlers.py` (future Phase 3 side-effects)
```python
@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
# session.commit() is always the caller's responsibility — no auto-commit.
```

### structlog `bind_contextvars` in handlers
**Source:** `src/provisioning_worker/main.py` lines 103–108 (`log.info(...)`) + CLAUDE.md §6.6
**Apply to:** All five handler functions in `modules/provisioning/handlers.py`
```python
structlog.contextvars.bind_contextvars(
    envelope_id=raw_env.id,
    subscription_id=str(payload.subscription_id),
    correlation_id=raw_env.correlation_id,
)
# instance_id NOT bound here — only available Phase 3+
```

### XACK ordering (commit-then-ack)
**Source:** RESEARCH.md §Same-Transaction Dedupe + §Pitfall 1
**Apply to:** `adapters/valkey_streams.py` `_dispatch()` — XACK must happen after the `_handle_with_dedupe()` call returns (i.e., after `session.commit()` inside the wrapper).
```python
await self._handle_with_dedupe(raw_env, payload, handler)
# Only xack AFTER commit succeeded:
await self._client.xack("events.subscription", self._group, msg_id)
```

### No `from __future__ import annotations`
**Source:** CLAUDE.md §6.1, RESEARCH.md §Pitfall 8
**Apply to:** All new files, including the Alembic migration.
Python 3.14 PEP 649 makes this import unnecessary and the project forbids it.

### Pydantic `ConfigDict(frozen=True, extra="forbid")`
**Source:** `/home/yevhenii/projects/saas/platform-api/src/platform_api/events/envelope.py` line 49, `subscription.py` lines 57, 96, 121, 154, 180, 215
**Apply to:** `events/envelope.py`, `events/subscription.py` (all six models)

### Schema-qualified Alembic operations
**Source:** `migrations/provisioning/env.py` lines 15–16, RESEARCH.md §Pitfall 5
**Apply to:** The `processed_event` migration — always pass `schema="provisioning"` to `op.create_table()` and `op.drop_table()`.

---

## No Analog Found

All files have sufficient analogs. No files require falling back to RESEARCH.md patterns exclusively — RESEARCH.md §Code Examples provide supplemental detail, but all analogs are grounded in real codebase files.

---

## Metadata

**Analog search scope:** `src/provisioning_worker/` (all subdirs), `migrations/provisioning/`, `tests/`, `../platform-api/src/platform_api/events/`, `../platform-api/src/platform_api/adapters/`, `../platform-api/tests/events/`
**Files scanned:** 27 (provisioner) + 7 (platform-api analogs) = 34
**Pattern extraction date:** 2026-06-02
