"""Valkey Streams consume-side adapter.

The single ``redis.asyncio`` consume site in this service (CLAUDE.md §4
dependency rule). It implements the :class:`EventConsumer` Protocol: join the
consumer group idempotently (:meth:`start`), poll ``events.subscription`` with
``XREADGROUP`` and periodically reclaim stuck entries with ``XAUTOCLAIM``
(:meth:`run`), and release the pool (:meth:`close`).

Every inbound message flows through the same :meth:`_dispatch` pipeline — both
fresh reads and reclaimed PEL entries (D-08) — which performs a two-phase parse
behind a strict ``extra="forbid"`` boundary:

1. ``json.loads`` the ``envelope`` field — bad JSON / missing field → poison →
   error log + ``XACK`` + no ledger row.
2. validate the outer envelope (:class:`_RawEnvelope`) — drift → poison →
   error log + ``XACK`` + no ledger row.
3. resolve the payload class for ``type`` — unknown but valid type →
   forward-compat → warning log + ``XACK`` + no ledger row (D-05).
4. validate the inner payload — drift → poison → error log + ``XACK``.

On the happy path the type-resolved payload is handed to the handler, which is
already wrapped by ``make_handler_registry`` so its body runs through
:func:`provisioning_worker.shared.event_consumer.handle_with_dedupe`; ``XACK``
is issued **only after** that wrapped handler returns (commit-then-ack, D-06).
"""

import asyncio  # noqa: TC003 — runtime-typed run() signature annotation
import json
from datetime import datetime  # noqa: TC003 — runtime-typed Pydantic field on _RawEnvelope
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from provisioning_worker.events import UnknownEnvelopeType, payload_class_for

if TYPE_CHECKING:
    from provisioning_worker.ports.event_consumer import HandlerFn
    from provisioning_worker.settings import Settings

__all__ = ["ValkeyStreamsConsumer"]

log = structlog.get_logger(__name__)

_STREAM = "events.subscription"
_RECLAIM_EVERY_N_CYCLES = 60
_POLL_COUNT_BLOCK_MS = 1000
_POLL_COUNT = 10
_FULL_SCAN_CURSOR = "0-0"


class _RawEnvelope(BaseModel):
    """First-phase envelope: outer fields validated, payload left as a dict.

    Internal to the adapter (not exported). Mirrors
    :class:`provisioning_worker.events.envelope.EventEnvelope` but keeps
    ``payload`` as a raw ``dict`` because the concrete payload class is not
    known until ``type`` is read. ``extra="forbid"`` makes any envelope-level
    drift a poison message.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    type: str
    version: int
    occurred_at: datetime
    producer: str
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict


class ValkeyStreamsConsumer:
    """Consume-side adapter over a Valkey Stream (implements EventConsumer).

    Reads ``events.subscription`` with the configured consumer group and
    dispatches each message through the strict two-phase parse + idempotency
    dedupe pipeline.
    """

    def __init__(self, settings: Settings) -> None:
        """Construct the consumer from settings (no I/O here).

        Uses ``decode_responses=True`` so stream fields arrive as ``str`` (the
        consume side differs from platform-api's publish bus, RESEARCH.md
        Pitfall 2).

        Args:
            settings: Application settings — supplies the Valkey URL, consumer
                group, consumer name, and the XAUTOCLAIM idle window.
        """
        self._client = aioredis.from_url(str(settings.valkey_url), decode_responses=True)
        self._group = settings.provisioning_consumer_group
        self._consumer = settings.consumer_name
        self._reclaim_min_idle_ms = settings.consumer_reclaim_min_idle_ms
        self._reclaim_every_n = _RECLAIM_EVERY_N_CYCLES

    async def start(self) -> None:
        """Create the consumer group idempotently, tolerating BUSYGROUP.

        ``mkstream=True`` creates ``events.subscription`` if it does not yet
        exist. A restart re-joins the existing group rather than failing.
        """
        try:
            await self._client.xgroup_create(
                name=_STREAM,
                groupname=self._group,
                id="0",
                mkstream=True,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        log.info(
            "joined consumer group",
            group=self._group,
            stream=_STREAM,
            consumer=self._consumer,
        )

    async def run(self, handlers: dict[str, HandlerFn], shutdown: asyncio.Event) -> None:
        """Poll the stream and dispatch until ``shutdown`` is set.

        Every :data:`_RECLAIM_EVERY_N_CYCLES` poll cycles, reclaims stuck PEL
        entries via :meth:`_reclaim` so a crashed consumer's in-flight messages
        are reprocessed (D-08).

        Args:
            handlers: Map of dotted envelope type to its dedupe-wrapped handler.
            shutdown: Event the supervisor sets to request a graceful stop.
        """
        poll_count = 0
        while not shutdown.is_set():
            results = await self._client.xreadgroup(
                groupname=self._group,
                consumername=self._consumer,
                streams={_STREAM: ">"},
                count=_POLL_COUNT,
                block=_POLL_COUNT_BLOCK_MS,
            )
            if results:
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        await self._dispatch(msg_id, fields, handlers)
            poll_count += 1
            if poll_count % self._reclaim_every_n == 0:
                await self._reclaim(handlers)

    async def close(self) -> None:
        """Release the underlying connection pool."""
        await self._client.aclose()

    async def _dispatch(
        self,
        msg_id: str,
        fields: dict[str, str],
        handlers: dict[str, HandlerFn],
    ) -> None:
        """Parse, validate, and dispatch one stream message.

        Implements the four-stage poison / unknown-type / payload-error /
        happy-path policy. Every terminal branch ``XACK``s the message: poison
        and unknown-type entries are acked without a ledger row (retrying
        cannot fix them); the happy path acks only after
        :func:`handle_with_dedupe` commits (D-06).

        Args:
            msg_id: The stream entry id.
            fields: The entry's field map (``envelope`` holds the JSON blob).
            handlers: Map of dotted envelope type to its dedupe-wrapped handler.
        """
        try:
            raw_data = json.loads(fields["envelope"])
        except json.JSONDecodeError, KeyError:
            log.error("poison message — bad JSON or missing envelope field", msg_id=msg_id)
            await self._ack(msg_id)
            return

        try:
            raw_env = _RawEnvelope.model_validate(raw_data)
        except ValidationError:
            log.error(
                "poison message — envelope validation failed",
                msg_id=msg_id,
                envelope_type=raw_data.get("type", "<unknown>"),
            )
            await self._ack(msg_id)
            return

        try:
            payload_cls = payload_class_for(raw_env.type)
        except UnknownEnvelopeType:
            log.warning(
                "unknown envelope type — skipping",
                msg_id=msg_id,
                envelope_type=raw_env.type,
            )
            await self._ack(msg_id)
            return

        try:
            payload = payload_cls.model_validate(raw_env.payload)
        except ValidationError:
            log.error(
                "poison message — payload validation failed",
                msg_id=msg_id,
                envelope_type=raw_env.type,
            )
            await self._ack(msg_id)
            return

        handler = handlers.get(raw_env.type)
        if handler is not None:
            await handler(raw_env, payload)
        # Commit-then-ack: XACK only after the dedupe wrapper has committed.
        await self._ack(msg_id)

    async def _reclaim(self, handlers: dict[str, HandlerFn]) -> None:
        """Reclaim stuck PEL entries and route them through :meth:`_dispatch`.

        Walks the pending list with ``XAUTOCLAIM`` from cursor ``"0-0"`` until
        the scan completes, reclaiming entries idle longer than the configured
        window. The result is a 3-element list — ``cursor, messages,
        deleted_ids`` — and must be unpacked as such (RESEARCH.md Pitfall 6).

        Args:
            handlers: Map of dotted envelope type to its dedupe-wrapped handler.
        """
        cursor = _FULL_SCAN_CURSOR
        while True:
            result = await self._client.xautoclaim(
                name=_STREAM,
                groupname=self._group,
                consumername=self._consumer,
                min_idle_time=self._reclaim_min_idle_ms,
                start_id=cursor,
                count=_POLL_COUNT,
            )
            cursor, messages, _deleted_ids = result
            for msg_id, fields in messages or []:
                await self._dispatch(msg_id, fields, handlers)
            if cursor == _FULL_SCAN_CURSOR:
                break

    async def _ack(self, msg_id: str) -> None:
        """XACK a single message on the consumer group."""
        await self._client.xack(_STREAM, self._group, msg_id)
