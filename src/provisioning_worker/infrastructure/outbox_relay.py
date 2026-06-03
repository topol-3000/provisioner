"""Outbox relay — drain ``provisioning.event_outbox`` to ``events.instance``.

Polls ``provisioning.event_outbox`` for unsent rows (``sent_at IS NULL``)
in batches, rebuilds each row's envelope via the produced-side
:func:`~provisioning_worker.events.envelope_class_for` registry, and
publishes it to the appropriate Valkey Stream via the injected
:class:`~provisioning_worker.ports.message_bus.MessageBus`.

On publish success: marks ``sent_at = datetime.now(UTC)`` and commits.
On any failure: records ``last_error`` (truncated to ``_MAX_LAST_ERROR_LEN``),
bumps ``attempt_count``, and retries on the next poll (D-03, D-04).

The relay NEVER imports or calls the module-level ``session_scope`` helper —
it uses the injected ``session_factory`` so integration tests can supply a test
engine (Pitfall 4).

This is a direct mirror of ``platform-api/infrastructure/outbox_relay.py``
with ``billing.event_outbox`` replaced by ``provisioning.event_outbox``
and the produced-side registry adapted for provisioning-worker's event catalog.
"""

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


async def run_outbox_relay(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bus: MessageBus,
    shutdown: asyncio.Event,
) -> None:
    """Drive the outbox relay loop until shutdown is set.

    On each iteration, calls :func:`_drain_once` to publish unsent rows.
    Catches any iteration-level exception and logs it so the relay never dies
    (D-03, D-04 — "relay-never-dies" invariant). Sleeps between iterations
    using ``asyncio.wait_for + contextlib.suppress(TimeoutError)`` so SIGTERM
    wakes the loop promptly.

    Args:
        settings: Application settings — supplies ``outbox_poll_seconds`` and
            ``outbox_batch_size``.
        session_factory: Injected async session factory — opened directly, not via
            the module-level helper.
            The relay opens its own sessions so integration tests can supply
            a test engine without affecting the global singleton (Pitfall 4).
        bus: The message bus adapter (``ValkeyStreamsBus`` in production,
            ``AsyncMock`` in unit tests).
        shutdown: Event set by the composition root on SIGTERM.
    """
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


async def _drain_once(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    bus: MessageBus,
) -> int:
    """Select unsent outbox rows, publish each to Valkey, and commit.

    Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so concurrent relay replicas
    claim disjoint row sets (multi-replica-safe at zero cost for M1 single
    replica — D-05). One transaction per drain: row locks release at the
    commit boundary regardless of publish success or failure.

    For each row:
    - Rebuilds the typed envelope via
      :func:`~provisioning_worker.events.envelope_class_for` (D-06).
    - Publishes via ``bus.publish(envelope)``.
    - On success: sets ``row.sent_at``.
    - On any exception (transport or validation): records ``last_error``
      (truncated), bumps ``attempt_count``, logs a warning (D-03, D-04).

    Args:
        settings: Application settings for batch size.
        session_factory: The injected session factory.
        bus: The message bus adapter.

    Returns:
        Number of rows processed (sent + failed) in this drain.
    """
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
