"""Async SQLAlchemy data-access layer for the provisioning schema.

ORM-only: all functions use SQLAlchemy mapped classes — no raw SQL strings,
no Pydantic models in return types. Every function receives an open
``AsyncSession`` from the caller; session lifecycle (commit, rollback) is
owned by the handler or task that opens the ``session_scope()``. Functions
here do NOT commit.

Caller responsibility note (T-3-06):
    ``update_instance_status`` has no transition guard — the state machine
    in ``service.py`` is the only authorised caller and is responsible for
    ensuring the requested transition is valid before calling this function.

``InstanceSpec`` is imported from ``ports/deployment_adapter.py`` (the
canonical home) rather than ``modules/provisioning/spec.py`` — the dependency
arrow always points inward.
"""

from typing import TYPE_CHECKING

from sqlalchemy import select

from provisioning_worker.modules.provisioning.models import (
    EnforcementSnapshot,
    Instance,
    InstanceStatus,
    ProvisioningTask,
    ProvisioningTaskStatus,
)
from provisioning_worker.shared.errors import DeploymentFailed

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from provisioning_worker.ports.deployment_adapter import InstanceSpec

__all__ = [
    "get_instance_by_id",
    "get_instance_by_subscription_id",
    "get_task_by_id",
    "insert_enforcement_snapshot",
    "record_task_failure",
    "record_task_success",
    "update_instance_status",
    "update_snapshot_version",
]


async def get_instance_by_id(
    session: AsyncSession,
    instance_id: UUID,
) -> Instance | None:
    """Return the Instance row for the given id, or ``None`` if absent.

    Args:
        session: The open async session owned by the caller.
        instance_id: UUID primary key of the instance.

    Returns:
        The matching ``Instance`` ORM object, or ``None``.
    """
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    return result.scalar_one_or_none()


async def get_task_by_id(
    session: AsyncSession,
    task_id: UUID,
) -> ProvisioningTask | None:
    """Return the ProvisioningTask row for the given id, or ``None`` if absent.

    Args:
        session: The open async session owned by the caller.
        task_id: UUID primary key of the provisioning task.

    Returns:
        The matching ``ProvisioningTask`` ORM object, or ``None``.
    """
    result = await session.execute(select(ProvisioningTask).where(ProvisioningTask.id == task_id))
    return result.scalar_one_or_none()


async def get_instance_by_subscription_id(
    session: AsyncSession,
    subscription_id: UUID,
) -> Instance | None:
    """Return the Instance row for the given subscription_id, or ``None``.

    Args:
        session: The open async session owned by the caller.
        subscription_id: The platform subscription UUID (UNIQUE on the table).

    Returns:
        The matching ``Instance`` ORM object, or ``None``.
    """
    result = await session.execute(
        select(Instance).where(Instance.subscription_id == subscription_id)
    )
    return result.scalar_one_or_none()


async def update_instance_status(
    session: AsyncSession,
    instance_id: UUID,
    status: InstanceStatus,
    **kwargs: object,
) -> None:
    """Set the instance's status and any extra keyword fields.

    Loads the instance row, sets ``status``, then applies any additional
    keyword arguments as attribute assignments (e.g. ``url=``, ``ready_at=``,
    ``deployment_handle=``). Does NOT commit — the caller owns the transaction.

    Caller is responsible for valid transition — service.py is the only
    authorised caller (T-3-06).

    Args:
        session: The open async session owned by the caller.
        instance_id: UUID primary key of the instance to update.
        status: The new ``InstanceStatus`` value.
        **kwargs: Extra attributes to set on the instance row
            (e.g. ``url``, ``hostname``, ``ready_at``, ``snapshot_version``,
            ``deployment_handle``, ``failed_step``, ``failure_reason``).
    """
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if instance is None:
        return
    instance.status = status
    for key, value in kwargs.items():
        setattr(instance, key, value)


async def record_task_failure(
    session: AsyncSession,
    task_id: UUID,
    instance_id: UUID,
    error: Exception,
    next_attempt_at: datetime,
) -> None:
    """Record a convergence step failure on both the task and the instance.

    Increments ``task.attempt_count``, sets ``task.last_error`` and
    ``task.next_attempt_at`` (for the retry scheduler), and transitions
    the instance to ``failed`` with ``failed_step`` and ``failure_reason``
    populated from the error if it is a ``DeploymentFailed``.

    Does NOT commit — the caller owns the transaction.

    Args:
        session: The open async session owned by the caller.
        task_id: UUID of the provisioning task that failed.
        instance_id: UUID of the associated instance to mark failed.
        error: The exception that caused the failure. If it is a
            ``DeploymentFailed``, its ``step`` and ``reason`` attributes
            are extracted for ``failed_step`` / ``failure_reason``.
        next_attempt_at: When the next retry should be attempted.
    """
    # Update task
    task_result = await session.execute(
        select(ProvisioningTask).where(ProvisioningTask.id == task_id)
    )
    task = task_result.scalar_one_or_none()
    if task is not None:
        task.attempt_count += 1
        task.last_error = str(error)
        task.next_attempt_at = next_attempt_at
        task.status = ProvisioningTaskStatus.running  # stays running until max attempts

    # Update instance
    instance_result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = instance_result.scalar_one_or_none()
    if instance is not None:
        instance.status = InstanceStatus.failed
        if isinstance(error, DeploymentFailed):
            instance.failed_step = error.step or None
            instance.failure_reason = error.reason or str(error)
        else:
            instance.failed_step = None
            instance.failure_reason = str(error)


async def record_task_success(
    session: AsyncSession,
    task_id: UUID,
) -> None:
    """Mark a provisioning task as successfully completed.

    Sets ``task.status = succeeded``. Does NOT commit — the caller owns the
    transaction.

    Args:
        session: The open async session owned by the caller.
        task_id: UUID of the provisioning task to mark succeeded.
    """
    result = await session.execute(select(ProvisioningTask).where(ProvisioningTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is not None:
        task.status = ProvisioningTaskStatus.succeeded


async def insert_enforcement_snapshot(
    session: AsyncSession,
    instance_id: UUID,
    spec: InstanceSpec,
    version: int = 1,
) -> None:
    """Write an enforcement snapshot row for the given instance.

    Creates a new ``EnforcementSnapshot`` with the entitlement data from the
    spec. Called by ``ProvisioningService.write_enforcement_snapshot()`` in
    Plan 03 — the service owns coordination; the repository owns the ORM write.

    Does NOT commit — the caller owns the transaction.

    Args:
        session: The open async session owned by the caller.
        instance_id: UUID of the instance the snapshot belongs to.
        spec: The ``InstanceSpec`` providing ``module_set``, ``seat_cap``,
            and ``resource_caps`` for the snapshot row.
        version: Snapshot version number. Defaults to 1 (initial write at
            ``configuring``); subsequent recomputes (Phase 5) use higher values.
    """
    session.add(
        EnforcementSnapshot(
            instance_id=instance_id,
            version=version,
            module_set=list(spec.module_set),
            seat_cap=spec.seat_cap,
            resource_caps=dict(spec.resource_caps),
            feature_flags={},
        )
    )


async def update_snapshot_version(
    session: AsyncSession,
    instance_id: UUID,
    version: int,
) -> None:
    """Set the instance's snapshot_version to reflect the latest snapshot.

    Called after ``insert_enforcement_snapshot`` to keep
    ``instance.snapshot_version`` in sync with ``enforcement_snapshot.version``
    for the platform-api If-None-Match polling seam.

    Does NOT commit — the caller owns the transaction.

    Args:
        session: The open async session owned by the caller.
        instance_id: UUID of the instance to update.
        version: The new snapshot version to record on the instance row.
    """
    result = await session.execute(select(Instance).where(Instance.id == instance_id))
    instance = result.scalar_one_or_none()
    if instance is not None:
        instance.snapshot_version = version
