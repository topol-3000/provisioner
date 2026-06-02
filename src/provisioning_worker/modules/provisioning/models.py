"""SQLAlchemy mapped classes for the `provisioning` schema.

The first domain table is `processed_event` — the idempotency ledger that
backs at-least-once dedupe on `(event_id, consumer_group)`. Phase 3 adds
`instance`, `provisioning_task`, and `enforcement_snapshot` to this same
`Base.metadata`.

`Base.metadata` is imported by `migrations/provisioning/env.py` so Alembic
autogenerate can diff future tables against the mapped models.
"""

import enum
from datetime import (
    datetime,  # noqa: TC003 — runtime import: SQLAlchemy resolves Mapped[datetime] at mapping time
)
from typing import ClassVar, Final
from uuid import (
    UUID,
    uuid7,
)

from sqlalchemy import (
    TIMESTAMP,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "EnforcementSnapshot",
    "Instance",
    "InstanceStatus",
    "ProcessedEvent",
    "ProvisioningTask",
    "ProvisioningTaskStatus",
    "TaskType",
]

_SCHEMA: Final[str] = "provisioning"


# ---------------------------------------------------------------------------
# Python enums — values match Postgres ENUM literals exactly
# ---------------------------------------------------------------------------


class InstanceStatus(enum.Enum):
    """Lifecycle status for an Odoo instance managed by this worker."""

    pending = "pending"
    deploying = "deploying"
    configuring = "configuring"
    ready = "ready"
    suspended = "suspended"
    failed = "failed"
    deprovisioning = "deprovisioning"
    deprovisioned = "deprovisioned"


class ProvisioningTaskStatus(enum.Enum):
    """Status of a single provisioning task attempt."""

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskType(enum.Enum):
    """The type of operation a provisioning task performs."""

    create = "create"
    update = "update"
    suspend = "suspend"
    reinstate = "reinstate"
    delete = "delete"


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for every mapped class in the `provisioning` schema."""


# ---------------------------------------------------------------------------
# Mapped classes
# ---------------------------------------------------------------------------


class ProcessedEvent(Base):
    """Idempotency ledger row — one row per `(event_id, consumer_group)` pair.

    Inserted in the same transaction as any handler side-effects, and before
    the inbound message is `XACK`-ed. The composite primary key makes
    duplicate delivery a safe no-op: a re-delivered event conflicts on INSERT,
    the transaction rolls back, and the dedupe guard short-circuits on the
    re-query. A crash after commit but before `XACK` re-delivers the message,
    and the existing row causes the handler to skip the side-effects.
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


class Instance(Base):
    """The instance registry — one row per customer subscription (1:1:1).

    This table is the primary read model for platform-api's instance endpoints.
    The `subscription_id` UNIQUE constraint enforces the 1:1:1 invariant at
    the database level. UUID PKs are generated as uuid7 in Python (not
    Postgres gen_random_uuid()) so they carry a sortable timestamp component.

    `status` drives the 8-state instance lifecycle machine in service.py.
    `deployment_handle` is an opaque JSONB blob from the deployment adapter
    (Coolify project/app/db ids in M2; the domain never reads inside it).
    `snapshot_version` mirrors `enforcement_snapshot.version` for the
    platform-api plugin polling If-None-Match.
    """

    __tablename__ = "instance"
    __table_args__: ClassVar[tuple] = (
        UniqueConstraint("subscription_id"),
        {"schema": _SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    subscription_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    customer_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[InstanceStatus] = mapped_column(
        PG_ENUM(InstanceStatus, name="instance_status", schema=_SCHEMA, create_type=False),
        nullable=False,
    )
    hostname: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    desired_seat_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    desired_resource_caps: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    deployment_handle: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    failed_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_status_check_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    snapshot_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProvisioningTask(Base):
    """Unit-of-work and retry ledger for each provisioning operation.

    One row is opened per create/update/suspend/reinstate/delete intent.
    `attempt_count` / `max_attempts` / `next_attempt_at` drive exponential
    backoff; `last_error` captures the most recent failure detail.
    `payload` holds a serialized `InstanceSpec` (via `to_dict()`/`from_dict()`).

    The UNIQUE (instance_id, change_set_id) constraint makes re-delivery of
    `subscription.lines_changed` a no-op (Phase 5 use).
    """

    __tablename__ = "provisioning_task"
    __table_args__: ClassVar[tuple] = (
        UniqueConstraint("instance_id", "change_set_id"),
        {"schema": _SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
    )
    instance_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("provisioning.instance.id"),
        nullable=False,
    )
    task_type: Mapped[TaskType] = mapped_column(
        PG_ENUM(TaskType, name="task_type", schema=_SCHEMA, create_type=False),
        nullable=False,
    )
    status: Mapped[ProvisioningTaskStatus] = mapped_column(
        PG_ENUM(ProvisioningTaskStatus, name="task_status", schema=_SCHEMA, create_type=False),
        nullable=False,
        default=ProvisioningTaskStatus.pending,
    )
    source_event_id: Mapped[str] = mapped_column(String(26), nullable=False)
    change_set_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class EnforcementSnapshot(Base):
    """Current entitlement snapshot for one Odoo instance.

    Written at `configuring` (version=1) by the convergence service.
    Platform-api reads this table to serve entitlement data to the Odoo
    enforcement plugin (with If-None-Match on `version`). Recomputation
    and version-bump logic is a Phase 5 concern.

    `instance_id` is both the FK to `instance` and the sole PK — there is
    exactly one snapshot per instance at any time.
    """

    __tablename__ = "enforcement_snapshot"
    __table_args__: ClassVar[dict[str, str]] = {"schema": _SCHEMA}

    instance_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("provisioning.instance.id"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    module_set: Mapped[list] = mapped_column(JSONB, nullable=False)
    seat_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    resource_caps: Mapped[dict] = mapped_column(JSONB, nullable=False)
    feature_flags: Mapped[dict] = mapped_column(JSONB, nullable=False)
