"""ValkeyStreamsBus — the only place redis.asyncio XADD publish lives.

Implements :class:`~provisioning_worker.ports.message_bus.MessageBus` against
Valkey/Redis Streams. Domain code and the outbox relay talk to the
``MessageBus`` port; only this adapter imports ``redis.asyncio``.

Transport errors propagate to callers — the outbox relay catches them and
records ``last_error`` / bumps ``attempt_count``, then retries on the next
poll (D-03, D-04). This adapter never silently swallows publish failures.

Mirrors ``../platform-api/adapters/valkey_streams_bus.py`` verbatim:
schema-name and producer-literal differences are in other modules.
"""

from typing import TYPE_CHECKING, Final

import redis.asyncio as aioredis
import structlog

from provisioning_worker.events import stream_for_envelope_type
from provisioning_worker.ports.message_bus import MessageBus

if TYPE_CHECKING:
    from provisioning_worker.events.envelope import EventEnvelope
    from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)

_MAXLEN: Final[int] = 100_000
_APPROXIMATE: Final[bool] = True


class ValkeyStreamsBus(MessageBus):
    """Valkey Streams adapter for the ``MessageBus`` publish port.

    Publishes envelopes to ``events.<prefix>`` streams via ``XADD`` with an
    approximate ``MAXLEN`` trim to bound stream size (``docs/events.md §Retention``).
    One Redis connection pool per instance; call :meth:`close` at shutdown
    to release pool resources (Pitfall 7).

    Args:
        settings: Application settings providing ``valkey_url``.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = aioredis.from_url(
            str(settings.valkey_url),
            encoding="utf-8",
            decode_responses=False,
        )

    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish ``envelope`` to the Valkey stream for its type.

        Serialises the full envelope (including payload) via
        ``model_dump_json()`` and appends it to ``events.<prefix>`` as a
        single ``envelope`` field. Transport errors propagate so the relay
        can record ``last_error`` and retry (D-03, D-04).

        Args:
            envelope: A :class:`EventEnvelope` built via
                :meth:`EventEnvelope.build`. The ``type`` field drives
                stream selection via :func:`stream_for_envelope_type`.

        Raises:
            redis.RedisError: On any Valkey/Redis transport failure.
        """
        stream = stream_for_envelope_type(envelope.type)
        serialized = envelope.model_dump_json().encode("utf-8")
        await self._client.xadd(
            name=stream,
            fields={b"envelope": serialized},
            maxlen=_MAXLEN,
            approximate=_APPROXIMATE,
        )
        log.debug(
            "event published",
            stream=stream,
            envelope_id=envelope.id,
            envelope_type=envelope.type,
        )

    async def close(self) -> None:
        """Close the underlying Redis connection pool.

        Must be called in the application ``finally`` block at shutdown to
        avoid ``ResourceWarning`` from unreleased pool connections (Pitfall 7).
        """
        await self._client.aclose()
