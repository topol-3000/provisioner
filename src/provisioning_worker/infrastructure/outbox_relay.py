"""Outbox relay — Phase 1 no-op poll loop.

In Phase 1 the `event_outbox` table does not yet exist. This concern
runs the poll loop and logs startup, but performs no DB query per
iteration. Phase 4 replaces the loop body with the real drain.

The poll-sleep pattern (asyncio.wait_for + contextlib.suppress) is
copied from platform-api/infrastructure/outbox_relay.py.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)


async def run_outbox_relay(settings: Settings, shutdown: asyncio.Event) -> None:
    """Drive the no-op outbox relay loop until shutdown is set.

    Logs startup and then sleeps between poll intervals using the
    asyncio.wait_for + contextlib.suppress pattern so SIGTERM wakes the
    loop promptly. Phase 4 will replace the loop body with a real DB drain.

    Args:
        settings: Application settings — supplies outbox_poll_seconds.
        shutdown: Event set by the composition root on SIGTERM.
    """
    log.info("outbox relay started", poll_seconds=settings.outbox_poll_seconds)
    while not shutdown.is_set():
        # Phase 4 will replace this with: await _drain_once(settings, session_factory, bus)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(), timeout=settings.outbox_poll_seconds)
    log.info("outbox relay stopped")
