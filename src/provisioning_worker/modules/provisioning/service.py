"""Convergence service and 8-state instance state machine.

:class:`ProvisioningService` is the only place in the codebase where state
transitions are judged legal (CLAUDE.md §6.1.1). It encapsulates:

1. ``validate_transition`` — the state machine guard (raises
   :class:`~provisioning_worker.shared.errors.InvalidTransition` for illegal
   moves).
2. ``open_instance`` — create ``Instance`` + ``ProvisioningTask`` rows inside
   the caller's session (handler's dedupe transaction).
3. ``write_enforcement_snapshot`` — domain coordination method that owns the
   enforcement-snapshot write at the ``configuring`` step (WARNING 4 fix).
   ``tasks.py`` calls this method; it delegates to the repository functions
   and never calls them directly.

All methods accept an open ``AsyncSession`` and do NOT commit — the caller
owns the transaction boundary.
"""

from typing import TYPE_CHECKING
from uuid import uuid7

import structlog

from provisioning_worker.modules.provisioning.models import (
    Instance,
    InstanceStatus,
    ProvisioningTask,
    ProvisioningTaskStatus,
    TaskType,
)
from provisioning_worker.modules.provisioning.repository import (
    insert_enforcement_snapshot,
    update_snapshot_version,
)
from provisioning_worker.modules.provisioning.spec import build_instance_spec
from provisioning_worker.shared.errors import InvalidTransition

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.ports.deployment_adapter import InstanceSpec
    from provisioning_worker.ports.entitlement_resolver import EntitlementResolver
    from provisioning_worker.settings import Settings

__all__ = ["ProvisioningService"]

log = structlog.get_logger(__name__)

# Allowed transitions in the state machine.
# Format: {current_status: set_of_valid_next_statuses}
# ``failed`` is reachable from ANY status (error recording path).
_ALLOWED_TRANSITIONS: dict[InstanceStatus, set[InstanceStatus]] = {
    InstanceStatus.pending: {InstanceStatus.deploying, InstanceStatus.failed},
    InstanceStatus.deploying: {InstanceStatus.configuring, InstanceStatus.failed},
    InstanceStatus.configuring: {InstanceStatus.ready, InstanceStatus.failed},
    InstanceStatus.ready: {
        InstanceStatus.suspended,
        InstanceStatus.deprovisioning,
        InstanceStatus.failed,
    },
    InstanceStatus.suspended: {
        InstanceStatus.ready,
        InstanceStatus.deprovisioning,
        InstanceStatus.failed,
    },
    InstanceStatus.failed: {InstanceStatus.deploying, InstanceStatus.failed},
    InstanceStatus.deprovisioning: {InstanceStatus.deprovisioned, InstanceStatus.failed},
    InstanceStatus.deprovisioned: {InstanceStatus.failed},
}


class ProvisioningService:
    """Convergence service: state machine guard and snapshot domain coordination.

    This is the single authoritative point for instance state transitions.
    ``tasks.py`` calls adapter methods and then calls this service to validate
    and record transitions. The repository is only called by this service for
    snapshot writes — never by tasks.py directly (WARNING 4 fix).

    Args:
        entitlement_resolver: The resolver that converts a subscription payload
            into an :class:`~provisioning_worker.ports.entitlement_resolver.EntitlementPicture`.
    """

    def __init__(self, entitlement_resolver: EntitlementResolver) -> None:
        self._entitlement_resolver = entitlement_resolver

    async def open_instance(
        self,
        payload: SubscriptionActivatedPayload,
        session: AsyncSession,
        settings: Settings,
        source_event_id: str = "",
    ) -> tuple[Instance, ProvisioningTask]:
        """Open instance + task rows inside the caller's session (no commit).

        Builds an ``InstanceSpec`` from the payload, constructs an
        ``Instance`` (``status=pending``) and a ``ProvisioningTask``
        (``status=pending``, ``task_type=create``), stages both via
        ``session.add()``, and returns them. The caller (dedupe wrapper) owns
        the commit.

        Args:
            payload: The validated ``subscription.activated`` payload.
            session: The open async session owned by the dedupe wrapper.
            settings: Application settings supplying provisioning defaults.
            source_event_id: The ULID envelope ``id`` of the triggering event.
                Used to populate ``provisioning_task.source_event_id`` for
                traceability. Passed from the handler which has access to
                ``raw_env.id``.

        Returns:
            A ``(instance, task)`` tuple with both rows staged in ``session``.
        """
        entitlement = self._entitlement_resolver.resolve(payload, settings)
        spec = build_instance_spec(payload, settings, entitlement)

        # Pre-generate the instance UUID so it is available immediately for
        # the FK in ProvisioningTask. SQLAlchemy's column default (uuid7) fires
        # at flush time, not at object construction — so instance.id would be
        # None if we relied on the default before the first flush.
        instance_id = uuid7()
        instance = Instance(
            id=instance_id,
            subscription_id=payload.subscription_id,
            customer_id=payload.customer_id,
            status=InstanceStatus.pending,
            admin_email=spec.admin_email,
            desired_seat_cap=spec.seat_cap,
            desired_resource_caps=dict(spec.resource_caps),
            version=1,
        )
        task = ProvisioningTask(
            instance_id=instance_id,
            task_type=TaskType.create,
            status=ProvisioningTaskStatus.pending,
            source_event_id=source_event_id,
            attempt_count=0,
            max_attempts=settings.provisioning_max_attempts,
            payload=spec.to_dict(),
        )
        session.add(instance)
        session.add(task)

        log.debug(
            "instance opened",
            instance_id=str(instance.id),
            task_id=str(task.id),
            subscription_id=str(payload.subscription_id),
        )
        return instance, task

    def validate_transition(
        self,
        current: InstanceStatus,
        target: InstanceStatus,
    ) -> None:
        """Assert that transitioning from ``current`` to ``target`` is legal.

        This is the ONLY place transition legality is judged (CLAUDE.md §6.1.1).
        All callers (``tasks.py``) must call this before calling
        ``repository.update_instance_status``.

        Args:
            current: The instance's current status.
            target: The requested next status.

        Raises:
            InvalidTransition: If the transition is not in the allowed set
                for the current status.
        """
        allowed = _ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTransition(f"Cannot transition from {current.value!r} to {target.value!r}")

    async def write_enforcement_snapshot(
        self,
        session: AsyncSession,
        instance_id: UUID,
        spec: InstanceSpec,
        version: int = 1,
    ) -> None:
        """Write the enforcement snapshot row for the given instance.

        WARNING 4 fix: this is the domain method that owns enforcement-snapshot
        coordination. ``tasks.py`` calls this; it in turn calls
        :func:`~provisioning_worker.modules.provisioning.repository.insert_enforcement_snapshot`
        and :func:`~provisioning_worker.modules.provisioning.repository.update_snapshot_version`.
        ``tasks.py`` must NEVER call those repository functions directly.

        Does NOT commit — the caller owns the transaction.

        Args:
            session: The open async session owned by the caller.
            instance_id: UUID of the instance the snapshot belongs to.
            spec: The ``InstanceSpec`` providing entitlement data.
            version: Snapshot version number (default 1 for initial write at
                ``configuring``; higher values for recompute in Phase 5).
        """
        await insert_enforcement_snapshot(session, instance_id, spec, version)
        await update_snapshot_version(session, instance_id, version)
