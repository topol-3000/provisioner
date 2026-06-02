# Phase 3: Registry & Create-Path (Fake Adapter) — Pattern Map

**Mapped:** 2026-06-02
**Files analyzed:** 22 new/modified files
**Analogs found:** 22 / 22

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `modules/provisioning/models.py` (extend) | model | CRUD | `modules/provisioning/models.py:ProcessedEvent` (same file) | exact |
| `modules/provisioning/schemas.py` (new body) | model | request-response | `events/subscription.py:SubscriptionActivatedPayload` | role-match |
| `modules/provisioning/repository.py` (new body) | service | CRUD | `shared/event_consumer.py:_select_processed_event` | role-match |
| `modules/provisioning/service.py` (new body) | service | event-driven | `shared/event_consumer.py:handle_with_dedupe` | partial-match |
| `modules/provisioning/spec.py` (new body) | utility | transform | `events/subscription.py:SubscriptionActivatedPayload` | partial-match |
| `modules/provisioning/handlers.py` (extend) | handler | request-response | `modules/provisioning/handlers.py` (same file) | exact |
| `modules/provisioning/tasks.py` (new body) | service | event-driven | `adapters/valkey_streams.py` (retry/error patterns) | partial-match |
| `ports/deployment_adapter.py` (new) | middleware | request-response | `ports/event_consumer.py` | exact |
| `ports/notification_transport.py` (new) | middleware | request-response | `ports/event_consumer.py` | exact |
| `ports/entitlement_resolver.py` (new) | middleware | transform | `ports/event_consumer.py` | exact |
| `ports/clock.py` (new) | utility | request-response | `ports/event_consumer.py` | exact |
| `adapters/fake_deployment.py` (new) | adapter | request-response | `adapters/valkey_streams.py` | role-match |
| `adapters/console_notification.py` (new) | adapter | request-response | `adapters/valkey_streams.py` | role-match |
| `adapters/m1_entitlement_resolver.py` (new) | adapter | transform | `adapters/valkey_streams.py` | role-match |
| `adapters/system_clock.py` (new) | adapter | request-response | `adapters/valkey_streams.py` | role-match |
| `shared/errors.py` (new) | utility | request-response | `shared/event_consumer.py` | partial-match |
| `settings.py` (extend) | config | — | `settings.py` (same file) | exact |
| `main.py` (extend) | config | event-driven | `main.py` (same file) | exact |
| `migrations/provisioning/versions/YYYYMMDD_add_instance_tables.py` (new) | migration | CRUD | `migrations/provisioning/versions/20260602_0905_add_processed_event.py` | exact |
| `tests/conftest.py` (extend) | test | — | `tests/conftest.py` (same file) | exact |
| `tests/provisioning/test_models.py` (extend) | test | CRUD | `tests/provisioning/test_models.py` (same file) | exact |
| `tests/provisioning/test_handlers.py` (extend) | test | request-response | `tests/provisioning/test_handlers.py` (same file) | exact |
| `tests/provisioning/test_service.py` (new) | test | event-driven | `tests/provisioning/test_idempotency.py` | role-match |
| `tests/provisioning/test_tasks.py` (new) | test | event-driven | `tests/provisioning/test_idempotency.py` | role-match |
| `tests/provisioning/test_spec.py` (new) | test | transform | `tests/provisioning/test_models.py` | partial-match |

---

## Pattern Assignments

### `modules/provisioning/models.py` — add Instance, ProvisioningTask, EnforcementSnapshot

**Analog:** `modules/provisioning/models.py:ProcessedEvent` (lines 1–50)

**Module docstring pattern** (lines 1–10):
```python
"""SQLAlchemy mapped classes for the `provisioning` schema.

The first domain table is `processed_event` — the idempotency ledger...
Later phases add `instance`, `provisioning_task`, `enforcement_snapshot`...

`Base.metadata` is imported by `migrations/provisioning/env.py` so Alembic
autogenerate can diff future tables against the mapped models.
"""
```

**Imports pattern** (lines 12–18): Use `from datetime import datetime  # noqa: TC003` for any `Mapped[datetime]` column. Import `ClassVar`, `Final` from `typing`. Add `UUID` from `sqlalchemy.dialects.postgresql`, `Integer`, `JSONB`, `ForeignKey`, `Text`, `String` from `sqlalchemy`, and `ENUM as PG_ENUM` from `sqlalchemy.dialects.postgresql`.

**Schema constant** (line 22):
```python
_SCHEMA: Final[str] = "provisioning"
```

**Mapped class pattern** (lines 29–49):
```python
class ProcessedEvent(Base):
    """Idempotency ledger row — one row per `(event_id, consumer_group)` pair.

    [Google-style docstring explaining the table's role.]
    """

    __tablename__ = "processed_event"
    __table_args__: ClassVar[dict[str, str]] = {"schema": _SCHEMA}

    event_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    consumer_group: Mapped[str] = mapped_column(Text, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
```

**Key divergences for new tables:**
- Add Python `enum.Enum` subclasses (`InstanceStatus`, `ProvisioningTaskStatus`, `TaskType`) before the mapped classes — values are lowercase strings matching Postgres enum literal names.
- Use `PG_ENUM(InstanceStatus, name='instance_status', schema=_SCHEMA, create_type=False)` for all enum columns. The `create_type=False` flag is **mandatory** — it prevents SQLAlchemy from attempting to `CREATE TYPE` when `Base.metadata.create_all` is called in tests (the migration owns the DDL). Without it, `create_all` conflicts with the migration's `CREATE TYPE`.
- UUID primary keys: `mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)` — import `from uuid import uuid7` (Python 3.14 stdlib). Never use `uuid4` for PKs per RESEARCH.md Pitfall 7.
- `__table_args__` for tables with constraints: use a tuple ending with the schema dict — `(UniqueConstraint("subscription_id"), {"schema": _SCHEMA})`.
- JSONB columns: `mapped_column(JSONB, nullable=True)` — the `JSONB` type is from `sqlalchemy.dialects.postgresql`.
- `updated_at` with `onupdate`: `mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())`.
- `__all__` at top of file lists all exported names: `__all__ = ["Base", "ProcessedEvent", "Instance", "ProvisioningTask", "EnforcementSnapshot", "InstanceStatus", "ProvisioningTaskStatus", "TaskType"]`.

---

### `modules/provisioning/schemas.py` — Pydantic command/result models

**Analog:** `events/subscription.py:SubscriptionActivatedPayload` (lines 34–67)

**Pydantic frozen model pattern** (lines 34–67):
```python
class SubscriptionActivatedPayload(BaseModel):
    """Payload for ``subscription.activated`` (v1).

    [Google-style docstring with Attributes section for every field.]
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    line_count: int = Field(..., ge=1)
    total_amount: Decimal
    activated_at: datetime
```

**Key points for schemas.py:**
- All command/result models use `ConfigDict(frozen=True)` but do **not** require `extra="forbid"` for internal schemas (that's only for wire contracts). Internal domain schemas may use `frozen=True` only.
- `CredentialNotification` is a `frozen=True` Pydantic model or `@dataclass(frozen=True, slots=True)` (RESEARCH.md §NotificationTransport Port). Prefer dataclass for internal-only value objects (python-style.md §Data modeling).
- No `from __future__ import annotations` — Python 3.14 rule.
- Group imports: stdlib → third-party → local, as in subscription.py lines 17–22.
- `# noqa: TC003` comment on runtime-typed Pydantic fields (`datetime`, `UUID`, `Decimal`) that ruff's TCH rule would move to `TYPE_CHECKING` — keep them at runtime because Pydantic resolves them there.

---

### `modules/provisioning/repository.py` — async data access

**Analog:** `shared/event_consumer.py:_select_processed_event, _insert_processed_event` (lines 124–146)

**Async ORM query pattern** (lines 124–136):
```python
async def _select_processed_event(
    session: AsyncSession,
    event_id: str,
    consumer_group: str,
) -> ProcessedEvent | None:
    """Return the existing ledger row for the composite key, or ``None``."""
    result = await session.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.event_id == event_id,
            ProcessedEvent.consumer_group == consumer_group,
        )
    )
    return result.scalar_one_or_none()
```

**Insert pattern** (lines 139–146):
```python
async def _insert_processed_event(
    session: AsyncSession,
    event_id: str,
    consumer_group: str,
) -> None:
    """Stage the idempotency-ledger row for this event in ``session``."""
    session.add(ProcessedEvent(event_id=event_id, consumer_group=consumer_group))
```

**Key points for repository.py:**
- All methods are `async` and receive `AsyncSession` as first argument — the caller (handler or task) owns the session scope.
- ORM-only: no raw SQL strings, no Pydantic models. Returns ORM model instances or primitives.
- `TYPE_CHECKING` guard for `AsyncSession` import (see event_consumer.py lines 34–36).
- Repository class pattern (consistent with python-style.md §Class design): inject the session factory or accept session per method — given this codebase injects `session` per call (from `session_scope()`), keep the same calling convention.
- Google-style docstrings on every public method.

---

### `modules/provisioning/spec.py` — InstanceSpec builder

**Analog:** `events/subscription.py` (frozen Pydantic model pattern) and python-style.md (frozen dataclass for value objects).

**Frozen dataclass pattern** (from python-style.md and RESEARCH.md §InstanceSpec):
```python
@dataclass(frozen=True, slots=True)
class InstanceSpec:
    """Orchestrator-agnostic desired state for one Odoo instance.

    [Google-style docstring: all fields documented under Attributes.]
    """
    subscription_id: UUID
    customer_id: UUID
    slug: str
    admin_email: str
    odoo_image: str
    module_set: tuple[str, ...]
    seat_cap: int
    resource_caps: Mapping[str, int]
    env: Mapping[str, str]
    resources: ResourceRequests
```

**Key points for spec.py:**
- `@dataclass(frozen=True, slots=True)` for `InstanceSpec` and `ResourceRequests` (value objects — python-style.md §Data modeling).
- `Mapping[str, int]` not `dict[str, int]` — immutable view, consistent with frozen object.
- The builder function (not a class) takes `payload: SubscriptionActivatedPayload`, `settings: Settings`, `entitlement: EntitlementPicture` and returns `InstanceSpec` — pure transform, no I/O.
- Import pattern: `from dataclasses import dataclass` + `from collections.abc import Mapping`.
- `slug` derivation for M1: `f"{str(payload.subscription_id)[:8]}.{settings.instance_domain_suffix}"`.
- No `from __future__ import annotations`.

---

### `modules/provisioning/handlers.py` — real body for handle_subscription_activated

**Analog:** `modules/provisioning/handlers.py` (existing, lines 1–147)

**Handler signature pattern** (lines 59–76):
```python
async def handle_subscription_activated(
    raw_env,
    payload: SubscriptionActivatedPayload,
    session: AsyncSession,
) -> None:
    """Handle ``subscription.activated`` (Phase 2 no-op).

    Phase 3 will open a ``provisioning.instance`` row and enqueue a create
    task here, using the supplied ``session``.

    Args:
        raw_env: The validated inbound envelope.
        payload: The validated activation payload.
        session: The open session owned by the dedupe wrapper.
    """
    _bind_context(raw_env, str(payload.subscription_id))
    log.debug("subscription.activated received (no-op)")
```

**`_bind_context` pattern** (lines 40–56):
```python
def _bind_context(raw_env, subscription_id: str) -> None:
    """Bind per-event structured-logging context for the current handler.

    Binds only opaque identifiers — never secrets or tokens (CLAUDE.md §6.6).
    """
    structlog.contextvars.bind_contextvars(
        envelope_id=raw_env.id,
        subscription_id=subscription_id,
        correlation_id=raw_env.correlation_id,
    )
```

**Key points for the real handler body:**
- Phase 3 adds `instance_id` to `bind_contextvars` after `session.add(instance)` (CONTEXT.md Claude's Discretion, line 165).
- The handler uses `session.add(instance)` and `session.add(task)` — it **never** calls `session.commit()` (the dedupe wrapper in `shared/event_consumer.py:handle_with_dedupe` lines 85–92 owns the commit).
- Post-commit enqueue: the handler must NOT call `broker.task.kiq(...)` inline. Use a `ContextVar` slot to register `(instance_id, task_id)` — the dedupe wrapper drains it after `session.commit()`. This is the pattern RESEARCH.md §Pattern 1 (lines 232–282) documents as the resolved approach.
- `TYPE_CHECKING` guard for `AsyncSession` and payload types (existing pattern, lines 18–27).
- Keep other handlers as no-ops; only `handle_subscription_activated` gains a real body.

---

### `modules/provisioning/tasks.py` — Taskiq create task

**Analog:** `shared/event_consumer.py` (error handling, session use) + `adapters/valkey_streams.py` (structlog pattern)

**Taskiq task decorator pattern** (from RESEARCH.md §Task Registration, lines 692–702):
```python
@broker.task
async def create_instance_task(
    instance_id: str,
    task_id: str,
    settings: Annotated[Settings, TaskiqDepends()],
    adapter: Annotated[DeploymentAdapter, TaskiqDepends()],
    transport: Annotated[NotificationTransport, TaskiqDepends()],
    clock: Annotated[Clock, TaskiqDepends()],
) -> None:
    """Drive pending → deploying → configuring → ready for one instance.

    [Google-style docstring with Args, Raises sections.]
    """
```

**Error handling and logging pattern** (from `adapters/valkey_streams.py` lines 183–241):
```python
try:
    await handler(raw_env, payload)
except Exception:
    log.exception(
        "handler failed — leaving message unacked for reclaim",
        msg_id=msg_id,
        envelope_type=raw_env.type,
    )
    return
```

**Backoff formula pattern** (from RESEARCH.md §Backoff Formula, lines 706–713):
```python
def _compute_backoff_seconds(attempt_count: int, settings: Settings) -> float:
    """Exponential backoff: base * multiplier^attempt, capped at cap_s."""
    delay = settings.provisioning_base_delay_s * (settings.provisioning_multiplier ** attempt_count)
    return min(delay, settings.provisioning_cap_s)
```

**Key points for tasks.py:**
- `log = structlog.get_logger(__name__)` at module level — identical to valkey_streams.py line 44.
- Never log `*_password`, `*_token`, `*_secret` fields — bind only opaque IDs to structlog (CLAUDE.md §6.6).
- `asyncio.sleep` only inside the task function, never in the consumer loop (RESEARCH.md Pitfall 5, lines 818–822).
- `session_scope()` from `infrastructure/db.py` for all DB operations inside the task.
- The task uses `Annotated[DeploymentAdapter, TaskiqDepends()]` — types are `Protocol` classes, wired via `broker.add_dependency_context({DeploymentAdapter: fake_adapter, ...})` in `main.py`.
- Domain errors (`DeploymentFailed`, `AdapterTimeout`) are caught here; they must not propagate to the broker (which would treat them as unhandled exceptions).
- Credential delivery: call `transport.send_credentials(notification)` then immediately discard the secrets — never assign them to a variable that persists beyond the call or gets logged.

---

### `ports/deployment_adapter.py`, `ports/notification_transport.py`, `ports/entitlement_resolver.py`, `ports/clock.py` — new ports

**Analog:** `ports/event_consumer.py` (entire file, lines 1–67)

**Port module docstring pattern** (lines 1–11):
```python
"""Consume-side port for the Valkey Streams event consumer.

This Protocol is the stable seam between the domain/wiring layer and the
concrete Streams adapter. Only adapters import ``redis.asyncio`` (CLAUDE.md
§4 dependency rule); ``main.py`` and the idempotency wrapper in
``shared/event_consumer.py`` type against this Protocol so the bus stays
swappable and the consume loop stays testable with a fake.
"""
```

**Protocol class pattern** (lines 30–67):
```python
@runtime_checkable
class EventConsumer(Protocol):
    """Port for the Valkey Streams consume side.

    Lifecycle is ``start()`` → ``run(...)`` → ``close()``. Implementations
    must be idempotent on ``start()``...
    """

    async def start(self) -> None:
        """Create the consumer group idempotently before polling.

        Must tolerate a pre-existing group (BUSYGROUP)...

        Args: [if applicable]
        """
        ...

    async def run(
        self,
        handlers: dict[str, HandlerFn],
        shutdown: asyncio.Event,
    ) -> None:
        """Run the poll loop...

        Args:
            handlers: Map of dotted envelope type to its async handler.
            shutdown: Event the supervisor sets to request a graceful stop.
        """
        ...
```

**Key points for new ports:**
- `@runtime_checkable` on every Protocol — enables `isinstance(adapter, DeploymentAdapter)` checks in tests.
- `from typing import Protocol, runtime_checkable` — same import as event_consumer.py line 16.
- Supporting data structures (e.g., `InstanceHandle`, `CreateResult`, `DeploymentStatus`, `CredentialNotification`, `EntitlementPicture`) live in the same port file or a `shared/` module — RESEARCH.md §Ports & Adapters (lines 499–644) documents the exact placement. Keep them in the port file for discoverability.
- `__all__` exports both the Protocol and all supporting types.
- `# noqa: TC003` comment on any `asyncio` import used only in runtime-evaluated method signatures (see event_consumer.py line 14).
- No `from __future__ import annotations`.
- Supporting frozen dataclasses in port files: `@dataclass(frozen=True, slots=True)` (python-style.md §Data modeling).
- `DeploymentStatus` is a `class DeploymentStatus(enum.Enum)` — lowercase values matching Postgres literals is only required for DB-mapped enums; adapter status enums can use `UPPER_CASE` values.

---

### `adapters/fake_deployment.py`, `adapters/console_notification.py`, `adapters/m1_entitlement_resolver.py`, `adapters/system_clock.py` — new adapters

**Analog:** `adapters/valkey_streams.py` (entire file, lines 1–275)

**Adapter module docstring pattern** (lines 1–25): Describe what the adapter wraps, which port it implements, and key design choices.

**Adapter class pattern** (lines 75–97):
```python
class ValkeyStreamsConsumer:
    """Consume-side adapter over a Valkey Stream (implements EventConsumer).

    Reads ``events.subscription`` with the configured consumer group and
    dispatches each message through the strict two-phase parse + idempotency
    dedupe pipeline.
    """

    def __init__(self, settings: Settings) -> None:
        """Construct the consumer from settings (no I/O here).

        Args:
            settings: Application settings — supplies the Valkey URL, consumer
                group, consumer name, and the XAUTOCLAIM idle window.
        """
        self._client = aioredis.from_url(str(settings.valkey_url), decode_responses=True)
        self._group = settings.provisioning_consumer_group
```

**`TYPE_CHECKING` guard pattern** (lines 38–40):
```python
if TYPE_CHECKING:
    from provisioning_worker.ports.event_consumer import HandlerFn
    from provisioning_worker.settings import Settings
```

**`log = structlog.get_logger(__name__)` module-level logger** (line 44).

**Key points for new adapters:**
- `FakeDeploymentAdapter`: use `@dataclass` (not a class with `__init__`) for simplicity — `fail_on: set[str] = field(default_factory=set)`, `fail_count: int = 1`, `_call_counts: dict[str, int] = field(default_factory=dict, init=False)`, `_instances` internal state. (RESEARCH.md §Pattern 4, lines 329–352).
- `ConsoleNotificationTransport`: writes to `sys.stdout` **directly** — `print(...)` or `sys.stdout.write(...)` — NOT via `structlog`. Mark with `# dev-only` comment. This is the one place `print` is legitimate per CONTEXT.md D-12.
- `SystemClock` and `FakeClock`: these can be simple classes (no `@dataclass`) since they carry no fields (SystemClock) or a single `_now` field (FakeClock). `FakeClock.sleep()` is a no-op `async def` — no `asyncio.sleep` call.
- `DefaultEntitlementResolver` (M1): returns `module_set=()`, `seat_cap=settings.provisioning_default_seat_cap`, `resource_caps={}`. Does NOT invent a `line_count → seat_cap` mapping (D-03).
- All adapter classes implement their port Protocol structurally — no `class Fake(DeploymentAdapter)` inheritance; the `@runtime_checkable` Protocol check is the contract verification mechanism.
- Adapter-level exceptions (third-party library errors) are translated to domain errors (`DeploymentFailed`, `AdapterTimeout`) at the adapter boundary — see CLAUDE.md §6.2 and RESEARCH.md §shared/errors.py (lines 627–643).

---

### `shared/errors.py` — domain error hierarchy

**Analog:** `shared/event_consumer.py` (module structure pattern, lines 1–23)

**Module docstring pattern**: Describe the error hierarchy, when each exception is raised, and the translation rule (adapter exceptions → domain types at adapter boundary).

**Error class pattern** (from RESEARCH.md §shared/errors.py, lines 627–643):
```python
class ProvisioningError(Exception):
    """Base for all domain-layer provisioning failures."""


class DeploymentFailed(ProvisioningError):
    """Adapter could not provision/update/delete the instance."""


class AdapterTimeout(ProvisioningError):
    """Adapter call exceeded the configured timeout."""


class InvalidTransition(ProvisioningError):
    """Attempted state transition that the state machine does not allow."""


class InstanceNotFound(ProvisioningError):
    """No instance row exists for the given identifier."""
```

**Key points:**
- Simple `Exception` subclasses — no fields, no `__init__` override needed unless the exception carries structured data (e.g., `DeploymentFailed` may carry `step: str` and `reason: str` for setting `instance.failed_step`).
- One-line Google docstrings are sufficient for simple error classes.
- `__all__` lists every exported exception.
- `shared/` location is correct — it is cross-cutting and `modules/` imports from `shared/` (dependency rule: `modules/` may import from `shared/`, never the reverse).

---

### `settings.py` — extend with D-03 spec defaults and D-08 backoff knobs

**Analog:** `settings.py` (existing, lines 1–103)

**Existing field pattern** (lines 55–62):
```python
consumer_reclaim_min_idle_ms: int = Field(
    default=60_000,
    ge=1_000,
    description=(
        "XAUTOCLAIM min-idle-time in milliseconds. Entries idle longer "
        "than this are reclaimed. Default ~60s."
    ),
)
```

**Fields to add** (from RESEARCH.md §Settings Fields, lines 719–727):
```python
# ----- Instance provisioning — backoff (D-08) -----
provisioning_max_attempts: int = Field(default=5, ge=1)
provisioning_base_delay_s: float = Field(default=2.0, gt=0.0)
provisioning_multiplier: float = Field(default=2.0, gt=1.0)
provisioning_cap_s: float = Field(default=60.0, gt=0.0)

# ----- Instance provisioning — spec defaults (D-03) -----
provisioning_default_seat_cap: int = Field(default=10, ge=1)
provisioning_default_resource_caps: str = Field(default="{}")  # JSON; parsed at use site
```

**Existing fields already present** (settings.py lines 86–87) — do NOT duplicate:
```python
instance_domain_suffix: str = "example.local"
odoo_base_image: str = "odoo:17"
```

**Key points:**
- Group new fields under `# ----- Instance provisioning -----` comments, consistent with existing comment style.
- Fields with constraints use `Field(default=..., ge=..., gt=..., description="...")` — same pattern as `consumer_reclaim_min_idle_ms`.
- `provisioning_default_resource_caps` is a `str` (JSON) — parsed to `dict` at the use site in `spec.py` or the M1 resolver. Consistent with how Pydantic settings handles nested JSON values.
- `@lru_cache(maxsize=1)` on `get_settings()` already exists (line 99) — do not add another.

---

### `main.py` — wire new adapters and extend `_run_convergence`

**Analog:** `main.py` (entire file, lines 1–177)

**Import block pattern** (lines 1–28):
```python
import asyncio
import signal
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from sqlalchemy import text
from taskiq_redis import RedisStreamBroker

from provisioning_worker.adapters.valkey_streams import ValkeyStreamsConsumer
from provisioning_worker.infrastructure.db import dispose_engine, get_engine
from provisioning_worker.infrastructure.health_server import run_health_server
from provisioning_worker.infrastructure.logging import configure_logging
from provisioning_worker.infrastructure.observability import configure_tracing
from provisioning_worker.infrastructure.outbox_relay import run_outbox_relay
from provisioning_worker.modules.provisioning import handlers
from provisioning_worker.shared.event_consumer import make_handler_registry

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings
```

**`_run_convergence` extension pattern** (existing lines 114–132):
```python
async def _run_convergence(settings: Settings, shutdown: asyncio.Event) -> None:
    """Run the taskiq broker (connect-only, Phase 1).

    Phase 3 registers real Taskiq tasks and starts the in-process listener.

    Args:
        settings: Application settings — supplies Valkey URL.
        shutdown: Event set by the composition root on SIGTERM.
    """
    broker = RedisStreamBroker(url=str(settings.valkey_url))
    await broker.startup()

    log.info("taskiq broker connected", url=str(settings.valkey_url))

    await shutdown.wait()
    await broker.shutdown()
```

**Key points for Phase 3 wiring:**
- Add imports for all new adapters and ports in the import block.
- Instantiate adapters before `broker.startup()`: `fake_adapter = FakeDeploymentAdapter()`, etc.
- `broker.add_dependency_context({Settings: settings, DeploymentAdapter: fake_adapter, ...})` before `broker.startup()` (RESEARCH.md §Task Registration, lines 679–689).
- Import `modules/provisioning/tasks` **after** `add_dependency_context` so `@broker.task` decorators run with a wired context (RESEARCH.md Pitfall 8, lines 836–839).
- Boot-time recovery: query for overdue tasks (`status IN ('pending', 'failed') AND next_attempt_at <= now()`) before starting the listener — RESEARCH.md §Pattern 3 (lines 304–319).
- The four-concern `asyncio.TaskGroup` structure is unchanged (lines 69–75).

---

### `migrations/provisioning/versions/YYYYMMDD_add_instance_tables.py` — new Alembic revision

**Analog:** `migrations/provisioning/versions/20260602_0905_add_processed_event.py` (entire file, lines 1–37)

**Migration file structure pattern** (lines 1–37):
```python
"""add processed_event

Revision ID: 0e3f3be0f9ad
Revises: 
Create Date: 2026-06-02 09:05:50.007480
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0e3f3be0f9ad'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        schema="provisioning",
    )


def downgrade() -> None:
    op.drop_table("processed_event", schema="provisioning")
```

**Key divergences for the new migration:**
- `down_revision` points to `"0e3f3be0f9ad"` (the existing Phase 2 revision).
- **No `from __future__ import annotations`** — the existing migration has none; the project-wide rule forbids it. If autogenerate inserts it, delete it immediately.
- ENUM types must be created via `op.execute()` BEFORE the tables that reference them (RESEARCH.md §Alembic Migration Approach, lines 478–493):
  ```python
  def upgrade() -> None:
      op.execute("CREATE TYPE provisioning.instance_status AS ENUM ('pending', 'deploying', 'configuring', 'ready', 'suspended', 'failed', 'deprovisioning', 'deprovisioned')")
      op.execute("CREATE TYPE provisioning.task_type AS ENUM ('create', 'update', 'suspend', 'reinstate', 'delete')")
      op.execute("CREATE TYPE provisioning.task_status AS ENUM ('pending', 'running', 'succeeded', 'failed')")
      op.create_table(
          "instance",
          sa.Column("id", sa.UUID(), nullable=False),
          sa.Column("subscription_id", sa.UUID(), nullable=False),
          ...
          sa.UniqueConstraint("subscription_id"),
          schema="provisioning",
      )
      ...
  ```
- `downgrade()` drops tables in FK-safe order (child tables first), then drops ENUM types in reverse.
- Schema-qualified names: every `op.create_table(...)` call includes `schema="provisioning"`.
- Do NOT use autogenerate's `sa.Enum(...)` column stubs for schema-qualified types — they fail silently or produce wrong DDL. Replace them with `sa.Text()` referencing the named type via the Postgres catalog, or use the `postgresql_using` approach with the existing type name. The safest pattern: let autogenerate scaffold the table structure, then manually replace the ENUM column definitions with `server_default`-free `sa.Text()` columns that Postgres coerces via the type cast (because the `CREATE TYPE` DDL already exists from `op.execute()`).

---

### `tests/conftest.py` — extend with new fixtures

**Analog:** `tests/conftest.py` (entire file, lines 1–77)

**Session-scoped container fixture pattern** (lines 29–33):
```python
@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Start a real Postgres 18 container for the integration suite."""
    with PostgresContainer("postgres:18") as container:
        yield container
```

**Session-scoped engine fixture with schema+table creation** (lines 36–54):
```python
@pytest.fixture(scope="session")
async def pg_engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    """Yield an async engine bound to the container, schema + tables created.

    The testcontainers connection URL names the ``psycopg2`` driver; this repo
    pins ``psycopg`` (v3), so the driver token is rewritten.
    """
    url = postgres_container.get_connection_url().replace("psycopg2", "psycopg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS provisioning"))
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()
```

**Function-scoped session with truncate-after pattern** (lines 57–77):
```python
@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a function-scoped async session; truncate tables after the test."""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            for table in reversed(Base.metadata.sorted_tables):
                await session.execute(
                    text(f'TRUNCATE TABLE "{table.schema}"."{table.name}" CASCADE')
                )
            await session.commit()
```

**New fixtures to add** (from RESEARCH.md §Testing, lines 738–751):
```python
@pytest.fixture
def fake_adapter() -> FakeDeploymentAdapter:
    """FakeDeploymentAdapter with no fault injection — succeeds on all calls."""
    return FakeDeploymentAdapter()

@pytest.fixture
def fake_clock() -> FakeClock:
    """FakeClock with fixed time; sleep() is a no-op."""
    return FakeClock()

@pytest.fixture
def in_memory_broker(fake_adapter, fake_clock, settings_override) -> InMemoryBroker:
    """InMemoryBroker wired with test dependencies; executes tasks synchronously."""
    from taskiq import InMemoryBroker
    broker = InMemoryBroker(await_inplace=True, propagate_exceptions=True)
    broker.add_dependency_context({...})
    return broker
```

**Key points:**
- `Base.metadata.create_all` in `pg_engine` automatically includes all new tables once `Instance`, `ProvisioningTask`, `EnforcementSnapshot` are added to `models.py` and imported in `Base.metadata` — no fixture change needed for table creation itself.
- ENUM types are NOT created by `create_all` when `create_type=False` is set on the ORM columns. For integration tests, the ENUM types must be created before `create_all`. Add `op.execute(...)` calls (or equivalent) to the `pg_engine` fixture before `conn.run_sync(Base.metadata.create_all)`.
- `FakeDeploymentAdapter(fail_on={"create"}, fail_count=1)` is constructed inline in specific test cases — not as a fixture — to keep the fault-injection parameterization local to the test.

---

### `tests/provisioning/test_models.py` — extend with new table assertions

**Analog:** `tests/provisioning/test_models.py` (existing, lines 1–38)

**Column set assertion pattern** (lines 20–23):
```python
def test_processed_event_columns() -> None:
    """The mapped column set is exactly the three ledger columns."""
    cols = {c.name for c in ProcessedEvent.__table__.columns}
    assert cols == {"event_id", "consumer_group", "processed_at"}
```

**PK assertion pattern** (lines 25–28):
```python
def test_processed_event_composite_pk() -> None:
    """Both event_id and consumer_group form the composite primary key (D-07)."""
    pk = {c.name for c in ProcessedEvent.__table__.primary_key}
    assert pk == {"event_id", "consumer_group"}
```

**Base metadata inclusion assertion pattern** (lines 32–38):
```python
def test_base_metadata_registers_processed_event() -> None:
    """Base.metadata includes the schema-qualified processed_event table."""
    assert "provisioning.processed_event" in Base.metadata.tables
```

**New assertions to add for Phase 3 tables:**
- `test_instance_tablename`, `test_instance_schema`, `test_instance_columns`, `test_instance_subscription_id_unique`
- `test_provisioning_task_fk` — asserts FK to `instance.id` exists
- `test_enforcement_snapshot_pk` — `instance_id` is the sole PK
- `test_uuid_pk_version` — integration test asserting `instance.id.version == 7` after an insert (RESEARCH.md Pitfall 7)
- All unit tests (no DB) — same pattern as existing `test_models.py` tests.

---

### `tests/provisioning/test_handlers.py` — extend with real-body assertions

**Analog:** `tests/provisioning/test_handlers.py` (existing, lines 1–68)

**Mock env/payload factory pattern** (lines 32–37):
```python
def _mock_env() -> MagicMock:
    return MagicMock(id="01JZQABCDE12345678901234AB", correlation_id=None)

def _mock_payload() -> MagicMock:
    return MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")
```

**No-op handler parametrize pattern** (lines 40–49):
```python
@pytest.mark.parametrize("handler", _ALL_HANDLERS)
async def test_handler_is_no_op(handler) -> None:
    """Each handler returns None and performs no DB writes."""
    session = AsyncMock()
    result = await handler(_mock_env(), _mock_payload(), session)

    assert result is None
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()
```

**Log context assertion with monkeypatch** (lines 52–68):
```python
async def test_activated_binds_log_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_subscription_activated binds envelope/subscription/correlation ids."""
    bound: dict[str, object] = {}

    def _capture(**kwargs: object) -> None:
        bound.update(kwargs)

    monkeypatch.setattr(handlers_mod.structlog.contextvars, "bind_contextvars", _capture)

    env = MagicMock(id="01JZQABCDE12345678901234AB", correlation_id="corr-123")
    payload = MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")

    await handle_subscription_activated(env, payload, AsyncMock())

    assert bound["envelope_id"] == "01JZQABCDE12345678901234AB"
```

**Phase 3 changes to this file:**
- Remove the `session.add.assert_not_called()` assertion for `handle_subscription_activated` (it now does DB writes).
- Remove `handle_subscription_activated` from the no-op parametrize list — keep it for the other 4 handlers.
- Add new tests: `test_activated_opens_instance_row`, `test_activated_opens_task_row`, `test_activated_binds_instance_id_after_insert`.

---

### `tests/provisioning/test_service.py` — new convergence state machine tests

**Analog:** `tests/provisioning/test_idempotency.py` (lines 1–284)

**Integration test with patched session_scope pattern** (lines 140–155):
```python
@pytest.mark.integration
async def test_first_delivery_writes_one_row(pg_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-1: first delivery → exactly one processed_event row; handler called once."""
    _patch_session_scope(monkeypatch, pg_session)
    handler = AsyncMock()
    raw_env = MagicMock(id=_EVENT_ID)

    await handle_with_dedupe(raw_env, MagicMock(), handler, _GROUP)

    handler.assert_awaited_once()
    count = await pg_session.scalar(select(func.count()).select_from(ProcessedEvent))
    assert count == 1
```

**session_scope patch helper pattern** (lines 173–184):
```python
def _patch_session_scope(monkeypatch: pytest.MonkeyPatch, session) -> None:
    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(ec, "session_scope", _scope)
```

**Unit test with mock session pattern** (lines 217–244):
```python
async def test_concurrent_duplicate_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock(side_effect=IntegrityError("", None, None))
    mock_session.rollback = AsyncMock()

    @asynccontextmanager
    async def _fake_scope():
        yield mock_session

    monkeypatch.setattr(ec, "session_scope", _fake_scope)
    ...
```

---

### `tests/provisioning/test_tasks.py` — new create task and fault injection tests

**Analog:** `tests/provisioning/test_idempotency.py` (overall test structure)

**InMemoryBroker with `await_inplace=True` pattern** (from RESEARCH.md §Testing, lines 738–751):
```python
@pytest.fixture
def in_memory_broker(fake_adapter, fake_clock) -> InMemoryBroker:
    from taskiq import InMemoryBroker
    broker = InMemoryBroker(await_inplace=True, propagate_exceptions=True)
    broker.add_dependency_context({
        DeploymentAdapter: fake_adapter,
        NotificationTransport: console_transport,
        Clock: fake_clock,
        Settings: test_settings,
    })
    return broker
```

**Fault injection test structure** (from RESEARCH.md §Testing, lines 756–763):
```python
@pytest.mark.integration
async def test_create_fails_then_retries(pg_session) -> None:
    """PROV-04 canonical proof: fault injection → failed → retry → ready."""
    adapter = FakeDeploymentAdapter(fail_on={"create"}, fail_count=1)
    clock = FakeClock()
    # 1. Inject adapter + clock
    # 2. Call create_instance_task (via InMemoryBroker await_inplace)
    # 3. Assert: after first call, instance.status == "failed"
    # 4. Assert: provisioning_task.attempt_count == 1, last_error set
    # 5. Assert: after automatic retry, instance.status == "ready"
    # 6. Assert: ConsoleNotificationTransport called exactly once
```

---

### `tests/provisioning/test_spec.py` — new InstanceSpec builder tests

**Analog:** `tests/provisioning/test_models.py` (unit test style, no DB)

**Pure unit test pattern** (no async, no DB, no monkeypatch needed for spec tests):
```python
from provisioning_worker.modules.provisioning.spec import build_instance_spec, InstanceSpec

def test_spec_uses_settings_defaults() -> None:
    """InstanceSpec uses settings.provisioning_default_seat_cap, not line_count."""
    payload = SubscriptionActivatedPayload(
        subscription_id=UUID("018efa2c-0000-7000-8000-000000000001"),
        ...
        line_count=5,  # must NOT become seat_cap
        ...
    )
    settings = Settings(...)
    entitlement = EntitlementPicture(module_set=(), seat_cap=settings.provisioning_default_seat_cap, resource_caps={})

    spec = build_instance_spec(payload, settings, entitlement)

    assert spec.seat_cap == settings.provisioning_default_seat_cap
    assert spec.seat_cap != 5  # D-03: no line_count→seat_cap mapping
```

---

## Shared Patterns

### Module logger
**Source:** `adapters/valkey_streams.py` line 44; `shared/event_consumer.py` line 41
**Apply to:** All new modules with logging
```python
log = structlog.get_logger(__name__)
```

### `TYPE_CHECKING` guard for expensive imports
**Source:** `adapters/valkey_streams.py` lines 38–40; `modules/provisioning/handlers.py` lines 18–27
**Apply to:** All modules where `AsyncSession`, `Settings`, port types are only needed for type hints
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from provisioning_worker.settings import Settings
```

### Pydantic frozen model (wire contracts)
**Source:** `events/subscription.py` lines 56–67 (`SubscriptionActivatedPayload`)
**Apply to:** `schemas.py` command/result models that cross service boundaries
```python
model_config = ConfigDict(frozen=True, extra="forbid")
```

### Frozen dataclass (value objects)
**Source:** `docs/python-style.md §Data modeling`; RESEARCH.md §InstanceSpec (lines 548–563)
**Apply to:** `InstanceSpec`, `InstanceHandle`, `CreateResult`, `BackupRef`, `ResourceRequests`, `CredentialNotification`, `EntitlementPicture`
```python
@dataclass(frozen=True, slots=True)
class MyValueObject:
    """[Google docstring]"""
    field: type
```

### `session_scope()` usage
**Source:** `infrastructure/db.py` lines 86–103; `shared/event_consumer.py` line 79
**Apply to:** `tasks.py` (all DB operations inside the Taskiq task)
```python
async with session_scope() as session:
    # ORM operations
    await session.commit()
```

### Structured logging with context binding
**Source:** `modules/provisioning/handlers.py` lines 40–56 (`_bind_context`)
**Apply to:** `handlers.py` (real body), `tasks.py`
```python
structlog.contextvars.bind_contextvars(
    envelope_id=raw_env.id,
    subscription_id=str(payload.subscription_id),
    instance_id=str(instance.id),  # Phase 3 addition
    correlation_id=raw_env.correlation_id,
)
```
**Never bind:** `*_password`, `*_token`, `*_secret` fields.

### `__all__` export list
**Source:** `ports/event_consumer.py` line 18; `modules/provisioning/handlers.py` line 29; `adapters/valkey_streams.py` line 42
**Apply to:** Every new module
```python
__all__ = ["ClassName", "function_name", ...]
```

### Schema-qualified `__table_args__`
**Source:** `modules/provisioning/models.py` line 41
**Apply to:** All new ORM mapped classes
```python
__table_args__: ClassVar[dict[str, str]] = {"schema": _SCHEMA}
# For tables with additional constraints:
__table_args__: ClassVar[tuple] = (UniqueConstraint("column"), {"schema": _SCHEMA})
```

### `noqa: TC003` for runtime Pydantic/SQLAlchemy annotations
**Source:** `modules/provisioning/models.py` line 13; `events/subscription.py` lines 17–21
**Apply to:** Any `datetime`, `UUID`, `Decimal` import used in Pydantic field type or SQLAlchemy `Mapped[...]`
```python
from datetime import datetime  # noqa: TC003 — runtime import: SQLAlchemy resolves Mapped[datetime] at mapping time
```

---

## No Analog Found

All files have strong analogs in the codebase. No files require falling back to RESEARCH.md patterns exclusively.

| File | Nearest Analog | Reason for Partial-Match |
|---|---|---|
| `modules/provisioning/tasks.py` | `shared/event_consumer.py` + `adapters/valkey_streams.py` | No existing Taskiq task file in the codebase; pattern assembled from error handling (event_consumer) + logging/retry (valkey_streams) analogs |
| `modules/provisioning/service.py` | `shared/event_consumer.py` | No existing state machine; the transaction + session patterns transfer directly |

---

## Metadata

**Analog search scope:** `src/provisioning_worker/` (all subdirectories), `tests/`, `migrations/provisioning/`
**Files scanned:** 28
**Pattern extraction date:** 2026-06-02
