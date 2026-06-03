"""MessageBus port — domain-event publisher abstraction.

Publish-only in Phase 4; a consume surface is deferred until the port is needed.
Only the adapter (``adapters/valkey_streams_bus.py``) imports the Valkey client —
domain code talks to this port exclusively (CLAUDE.md §4 dependency rule).
Symmetric to the consume-side :class:`~provisioning_worker.ports.event_consumer.EventConsumer`
Protocol.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from provisioning_worker.events.envelope import EventEnvelope


@runtime_checkable
class MessageBus(Protocol):
    """Domain-event publisher abstraction (publish-only in Phase 4).

    Adapters guarantee at-least-once delivery to the appropriate Valkey
    Stream (one stream per envelope-type prefix). Callers must construct
    envelopes via :meth:`EventEnvelope.build` — never direct construction.

    A ``consume(...)`` method will be added if/when an ``events.instance``
    consumer is needed. Keeping the surface minimal matches the Phase-4 scope.
    """

    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish a domain event to its stream.

        Args:
            envelope: A fully-built :class:`EventEnvelope`. Use
                :meth:`EventEnvelope.build` upstream — the adapter does
                not mint ids or timestamps.

        Raises:
            Exception: The adapter re-raises the underlying transport error
                (e.g. ``redis.RedisError``) so the outbox relay can record
                ``last_error`` on the originating row and retry (D-03, D-04).
        """
        ...
