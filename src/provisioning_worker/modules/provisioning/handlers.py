"""One handler per consumed ``subscription.*`` event type.

Phase 3 implements the real body of ``handle_subscription_activated``:
it opens ``instance`` + ``provisioning_task`` rows inside the dedupe
session, registers a post-commit Taskiq enqueue callback, and binds
``instance_id`` to the structlog context. All other handlers remain
no-ops pending Phase 5 convergence work (CLAUDE.md §6.1.1).

The idempotency dedupe + ``processed_event`` insert is owned by the
wrapper in :mod:`provisioning_worker.shared.event_consumer`, so handlers
stay thin (CLAUDE.md §6.1.1). Handlers must NOT call
``broker.task.kiq()`` inline — use :func:`register_post_commit` instead
(Pitfall 1 fix, T-3-11 mitigation).
"""

from typing import TYPE_CHECKING

import structlog

from provisioning_worker.adapters.m1_entitlement_resolver import DefaultEntitlementResolver
from provisioning_worker.modules.provisioning.service import ProvisioningService
from provisioning_worker.modules.provisioning.tasks import create_instance_task
from provisioning_worker.settings import get_settings
from provisioning_worker.shared.event_consumer import register_post_commit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from provisioning_worker.events.subscription import (
        SubscriptionActivatedPayload,
        SubscriptionCancelledPayload,
        SubscriptionLinesChangedPayload,
        SubscriptionReinstatedPayload,
        SubscriptionSuspendedPayload,
    )

__all__ = [
    "handle_subscription_activated",
    "handle_subscription_cancelled",
    "handle_subscription_lines_changed",
    "handle_subscription_reinstated",
    "handle_subscription_suspended",
]

log = structlog.get_logger(__name__)

# Module-level service singleton: wired with the M1 placeholder resolver.
# main.py does not inject the service into handlers — the handler is thin
# and constructs the service with a fixed resolver (D-02 swap point is
# main.py's wiring, not the handler).
_service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())


def _bind_context(raw_env, subscription_id: str) -> None:
    """Bind per-event structured-logging context for the current handler.

    Binds only opaque identifiers — never secrets or tokens (CLAUDE.md §6.6).

    Args:
        raw_env: The validated inbound envelope (supplies ``id`` and
            ``correlation_id``).
        subscription_id: The subscription this event targets, as a string.
    """
    structlog.contextvars.bind_contextvars(
        envelope_id=raw_env.id,
        subscription_id=subscription_id,
        correlation_id=raw_env.correlation_id,
    )


async def handle_subscription_activated(
    raw_env,
    payload: SubscriptionActivatedPayload,
    session: AsyncSession,
) -> None:
    """Handle ``subscription.activated`` — open instance + enqueue create task.

    Opens a ``provisioning.instance`` row (``status=pending``) and a
    ``provisioning.provisioning_task`` row (``status=pending``,
    ``task_type=create``) inside the dedupe session. Registers a post-commit
    callback that enqueues ``create_instance_task`` after the session commits
    (Pitfall 1 fix: never enqueue before commit, T-3-11 mitigation).

    The handler does NOT commit — the dedupe wrapper in
    :mod:`provisioning_worker.shared.event_consumer` commits after staging
    the ``processed_event`` row.

    Args:
        raw_env: The validated inbound envelope (supplies ``id`` for
            ``source_event_id`` traceability).
        payload: The validated activation payload.
        session: The open session owned by the dedupe wrapper.
    """
    _bind_context(raw_env, str(payload.subscription_id))
    settings = get_settings()

    instance, task = await _service.open_instance(
        payload,
        session,
        settings,
        source_event_id=raw_env.id,
    )

    structlog.contextvars.bind_contextvars(instance_id=str(instance.id))

    # Register post-commit enqueue — NEVER call .kiq() inline here.
    # The dedupe wrapper drains this callback AFTER session.commit() (T-3-11).
    instance_id_str = str(instance.id)
    task_id_str = str(task.id)

    async def _enqueue() -> None:
        await create_instance_task.kiq(instance_id_str, task_id_str)

    register_post_commit(_enqueue)

    log.info(
        "subscription.activated — instance opened",
        instance_id=instance_id_str,
        task_id=task_id_str,
    )


async def handle_subscription_lines_changed(
    raw_env,
    payload: SubscriptionLinesChangedPayload,
    session: AsyncSession,
) -> None:
    """Handle ``subscription.lines_changed`` (Phase 2 no-op).

    Phase 5 will diff the entitlements and enqueue an update task here.

    Args:
        raw_env: The validated inbound envelope.
        payload: The validated lines-changed payload.
        session: The open session owned by the dedupe wrapper.
    """
    _bind_context(raw_env, str(payload.subscription_id))
    log.debug("subscription.lines_changed received (no-op)")


async def handle_subscription_suspended(
    raw_env,
    payload: SubscriptionSuspendedPayload,
    session: AsyncSession,
) -> None:
    """Handle ``subscription.suspended`` (Phase 2 no-op).

    Phase 5 will drive the instance to the suspended state here.

    Args:
        raw_env: The validated inbound envelope.
        payload: The validated suspension payload.
        session: The open session owned by the dedupe wrapper.
    """
    _bind_context(raw_env, str(payload.subscription_id))
    log.debug("subscription.suspended received (no-op)")


async def handle_subscription_reinstated(
    raw_env,
    payload: SubscriptionReinstatedPayload,
    session: AsyncSession,
) -> None:
    """Handle ``subscription.reinstated`` (Phase 2 no-op).

    Phase 5 will drive the instance back to the ready state here.

    Args:
        raw_env: The validated inbound envelope.
        payload: The validated reinstatement payload.
        session: The open session owned by the dedupe wrapper.
    """
    _bind_context(raw_env, str(payload.subscription_id))
    log.debug("subscription.reinstated received (no-op)")


async def handle_subscription_cancelled(
    raw_env,
    payload: SubscriptionCancelledPayload,
    session: AsyncSession,
) -> None:
    """Handle ``subscription.cancelled`` (Phase 2 no-op).

    Phase 5 will drive the instance through the deprovision path here.

    Args:
        raw_env: The validated inbound envelope.
        payload: The validated cancellation payload.
        session: The open session owned by the dedupe wrapper.
    """
    _bind_context(raw_env, str(payload.subscription_id))
    log.debug("subscription.cancelled received (no-op)")
