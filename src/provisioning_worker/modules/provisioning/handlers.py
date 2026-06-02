"""One handler per consumed ``subscription.*`` event type.

Phase 2 ships these as no-ops: each handler binds structured-logging context
(envelope, subscription, correlation ids) and logs a debug line, but performs
no DB writes. The idempotency dedupe + ``processed_event`` insert is owned by
the wrapper in :mod:`provisioning_worker.shared.event_consumer`, so handlers
stay thin (CLAUDE.md §6.1.1).

Phase 3 replaces the no-op bodies with real convergence side-effects (open a
``provisioning.instance`` row, enqueue a create/update task, etc.) using the
``session`` already opened by the dedupe wrapper.
"""

from typing import TYPE_CHECKING

import structlog

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


def _bind_context(raw_env, subscription_id: str) -> None:
    """Bind per-event structured-logging context for the current handler.

    Binds only opaque identifiers — never secrets or tokens (CLAUDE.md §6.6).
    ``instance_id`` is intentionally omitted; it is not known until Phase 3
    opens the instance row.

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


async def handle_subscription_lines_changed(
    raw_env,
    payload: SubscriptionLinesChangedPayload,
    session: AsyncSession,
) -> None:
    """Handle ``subscription.lines_changed`` (Phase 2 no-op).

    Phase 3 will diff the entitlements and enqueue an update task here.

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

    Phase 3 will drive the instance to the suspended state here.

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

    Phase 3 will drive the instance back to the ready state here.

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

    Phase 3 will drive the instance through the deprovision path here.

    Args:
        raw_env: The validated inbound envelope.
        payload: The validated cancellation payload.
        session: The open session owned by the dedupe wrapper.
    """
    _bind_context(raw_env, str(payload.subscription_id))
    log.debug("subscription.cancelled received (no-op)")
