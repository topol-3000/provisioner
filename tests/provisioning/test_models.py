"""Unit tests for the provisioning ORM models.

Pins the `ProcessedEvent` idempotency-ledger mapping and the Phase 3
registry tables (`Instance`, `ProvisioningTask`, `EnforcementSnapshot`):
table names, schemas, column sets, PKs, FKs, and UNIQUE constraints.
Unit tests are pure metadata inspections — no database required.
Integration tests (marked ``@pytest.mark.integration``) require a real
Postgres container via the ``pg_session`` fixture.
"""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from provisioning_worker.modules.provisioning.models import (
    Base,
    EnforcementSnapshot,
    Instance,
    InstanceStatus,
    ProcessedEvent,
    ProvisioningTask,
    ProvisioningTaskStatus,
    TaskType,
)


# ---------------------------------------------------------------------------
# ProcessedEvent (Phase 2 — keep intact)
# ---------------------------------------------------------------------------


def test_processed_event_tablename() -> None:
    """ProcessedEvent maps to the `processed_event` table."""
    assert ProcessedEvent.__tablename__ == "processed_event"


def test_processed_event_schema() -> None:
    """ProcessedEvent lives in the `provisioning` schema."""
    assert ProcessedEvent.__table_args__ == {"schema": "provisioning"}


def test_processed_event_columns() -> None:
    """The mapped column set is exactly the three ledger columns."""
    cols = {c.name for c in ProcessedEvent.__table__.columns}
    assert cols == {"event_id", "consumer_group", "processed_at"}


def test_processed_event_composite_pk() -> None:
    """Both event_id and consumer_group form the composite primary key (D-07)."""
    pk = {c.name for c in ProcessedEvent.__table__.primary_key}
    assert pk == {"event_id", "consumer_group"}


def test_base_metadata_registers_processed_event() -> None:
    """Base.metadata includes the schema-qualified processed_event table.

    This is what `migrations/provisioning/env.py` imports for autogenerate.
    """
    assert "provisioning.processed_event" in Base.metadata.tables


# ---------------------------------------------------------------------------
# Python enum classes (Phase 3)
# ---------------------------------------------------------------------------


def test_instance_status_enum_values() -> None:
    """InstanceStatus has exactly the 8 documented lifecycle values."""
    assert set(InstanceStatus.__members__) == {
        "pending",
        "deploying",
        "configuring",
        "ready",
        "suspended",
        "failed",
        "deprovisioning",
        "deprovisioned",
    }


def test_provisioning_task_status_enum_values() -> None:
    """ProvisioningTaskStatus has the 4 documented task lifecycle values."""
    assert set(ProvisioningTaskStatus.__members__) == {
        "pending",
        "running",
        "succeeded",
        "failed",
    }


def test_task_type_enum_values() -> None:
    """TaskType has the 5 documented task type values."""
    assert set(TaskType.__members__) == {
        "create",
        "update",
        "suspend",
        "reinstate",
        "delete",
    }


# ---------------------------------------------------------------------------
# Instance table (Phase 3)
# ---------------------------------------------------------------------------


def test_instance_tablename() -> None:
    """Instance maps to the `instance` table."""
    assert Instance.__tablename__ == "instance"


def test_instance_schema() -> None:
    """Instance lives in the `provisioning` schema."""
    # __table_args__ is a tuple when there are constraints; schema is last element
    table_args = Instance.__table_args__
    if isinstance(table_args, tuple):
        schema_dict = table_args[-1]
    else:
        schema_dict = table_args
    assert schema_dict.get("schema") == "provisioning"


def test_instance_columns() -> None:
    """Instance has exactly the documented column set."""
    cols = {c.name for c in Instance.__table__.columns}
    assert cols == {
        "id",
        "subscription_id",
        "customer_id",
        "status",
        "hostname",
        "url",
        "admin_email",
        "desired_seat_cap",
        "desired_resource_caps",
        "deployment_handle",
        "failed_step",
        "failure_reason",
        "ready_at",
        "last_status_check_at",
        "snapshot_version",
        "version",
        "created_at",
        "updated_at",
    }


def test_instance_subscription_id_unique() -> None:
    """subscription_id has a UNIQUE constraint (1:1:1 invariant)."""
    for constraint in Instance.__table__.constraints:
        cols = {c.name for c in constraint.columns}
        if cols == {"subscription_id"}:
            return  # found UniqueConstraint covering subscription_id
    # Also check the column-level unique flag
    for col in Instance.__table__.columns:
        if col.name == "subscription_id" and col.unique:
            return
    raise AssertionError("No UNIQUE constraint found on Instance.subscription_id")


# ---------------------------------------------------------------------------
# ProvisioningTask table (Phase 3)
# ---------------------------------------------------------------------------


def test_provisioning_task_fk() -> None:
    """ProvisioningTask.instance_id has a FK pointing to provisioning.instance.id."""
    fks = ProvisioningTask.__table__.foreign_keys
    assert len(fks) >= 1
    targets = {fk.target_fullname for fk in fks}
    assert "provisioning.instance.id" in targets


# ---------------------------------------------------------------------------
# EnforcementSnapshot table (Phase 3)
# ---------------------------------------------------------------------------


def test_enforcement_snapshot_pk() -> None:
    """EnforcementSnapshot.instance_id is the sole primary key column."""
    pk_cols = {c.name for c in EnforcementSnapshot.__table__.primary_key}
    assert pk_cols == {"instance_id"}


# ---------------------------------------------------------------------------
# Base.metadata completeness (Phase 3)
# ---------------------------------------------------------------------------


def test_base_metadata_includes_new_tables() -> None:
    """Base.metadata includes all three Phase-3 registry tables."""
    tables = Base.metadata.tables
    assert "provisioning.instance" in tables
    assert "provisioning.provisioning_task" in tables
    assert "provisioning.enforcement_snapshot" in tables


# ---------------------------------------------------------------------------
# Integration tests (require real Postgres via pg_session fixture)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_uuid_pk_version(pg_session) -> None:
    """Instance.id is a UUID v7 (PROV-01, RESEARCH.md Pitfall 7).

    Python 3.14 stdlib ``uuid.uuid7()`` default is used; version must be 7.
    """
    instance = Instance(
        subscription_id=UUID("018efa2c-0000-7000-8000-100000000001"),
        customer_id=UUID("018efa2c-0000-7000-8000-200000000001"),
        status=InstanceStatus.pending,
        admin_email="test@example.com",
        desired_seat_cap=10,
        desired_resource_caps={},
        version=1,
    )
    pg_session.add(instance)
    await pg_session.commit()

    result = await pg_session.execute(select(Instance).where(Instance.id == instance.id))
    persisted = result.scalar_one_or_none()
    assert persisted is not None
    assert isinstance(persisted.id, UUID)
    assert persisted.id.version == 7


@pytest.mark.integration
async def test_subscription_id_unique_violation(pg_session) -> None:
    """Two Instance rows with the same subscription_id raise IntegrityError (PROV-01).

    Verifies the UNIQUE constraint on ``provisioning.instance.subscription_id``
    that enforces the 1:1:1 subscription-to-instance invariant.
    """
    shared_sub_id = UUID("018efa2c-0000-7000-8000-100000000002")

    first = Instance(
        subscription_id=shared_sub_id,
        customer_id=UUID("018efa2c-0000-7000-8000-200000000001"),
        status=InstanceStatus.pending,
        admin_email="first@example.com",
        desired_seat_cap=10,
        desired_resource_caps={},
        version=1,
    )
    pg_session.add(first)
    await pg_session.commit()

    second = Instance(
        subscription_id=shared_sub_id,  # duplicate — must violate UNIQUE
        customer_id=UUID("018efa2c-0000-7000-8000-200000000002"),
        status=InstanceStatus.pending,
        admin_email="second@example.com",
        desired_seat_cap=10,
        desired_resource_caps={},
        version=1,
    )
    pg_session.add(second)
    with pytest.raises(IntegrityError):
        await pg_session.commit()
