"""Consume-side port for the Valkey Streams event consumer.

This Protocol is the stable seam between the domain/wiring layer and the
concrete Streams adapter. Only adapters import ``redis.asyncio`` (CLAUDE.md
§4 dependency rule); ``main.py`` and the idempotency wrapper in
``shared/event_consumer.py`` type against this Protocol so the bus stays
swappable and the consume loop stays testable with a fake.

Plan 03's :class:`ValkeyStreamsConsumer` adapter implements this Protocol;
the producer side (outbox relay) is a separate concern and is not modelled
here.
"""

import asyncio  # noqa: TC003 — runtime-evaluated method-signature annotation
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

__all__ = ["EventConsumer", "HandlerFn"]

type HandlerFn = Callable[[Any, Any], Awaitable[None]]
"""Async handler signature: ``(raw_env, payload) -> Awaitable[None]``.

Both arguments are typed ``Any`` because the generic payload type is not
fixed at the port level: the adapter performs the two-phase parse and passes
the raw envelope plus the type-resolved payload model; Plan 03's handlers
narrow the types at their own call sites.
"""


@runtime_checkable
class EventConsumer(Protocol):
    """Port for the Valkey Streams consume side.

    Lifecycle is ``start()`` → ``run(...)`` → ``close()``. Implementations
    must be idempotent on ``start()`` (a restart re-joins an existing
    consumer group rather than failing) and must release their connection
    pool on ``close()``.
    """

    async def start(self) -> None:
        """Create the consumer group idempotently before polling.

        Must tolerate a pre-existing group (BUSYGROUP) so a worker restart
        re-joins rather than crashing. Call before :meth:`run`.
        """
        ...

    async def run(
        self,
        handlers: dict[str, HandlerFn],
        shutdown: asyncio.Event,
    ) -> None:
        """Run the poll loop, dispatching messages until shutdown is set.

        Args:
            handlers: Map of dotted envelope type to its async handler.
                Messages whose type is absent from the map are skipped per
                the adapter's unknown-type policy.
            shutdown: Event the supervisor sets to request a graceful stop;
                the loop exits after the in-flight poll cycle completes.
        """
        ...

    async def close(self) -> None:
        """Release the underlying connection pool; call after :meth:`run`."""
        ...
