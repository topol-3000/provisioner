"""Domain-event envelope — generic wire shape for every consumed event.

Re-implemented per repo (no shared ``platform-contracts`` package —
CLAUDE.md §6.2). This module is the consume-side source of truth for the
envelope contract (see ``docs/events.md §Envelope``).

The envelope is a Pydantic v2 generic model so each consume site fixes the
payload type at the call site
(``EventEnvelope[SubscriptionActivatedPayload].model_validate(...)``) and
gets field-level validation for free. Unlike platform-api's producer-side
envelope, this re-implementation drops the ``build()`` classmethod — minting
envelopes is a producer concern that lands in Phase 4+ (D-03).
"""

from datetime import datetime  # noqa: TC003 — runtime-typed Pydantic field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["EventEnvelope", "stream_for_envelope_type"]


class EventEnvelope[P: BaseModel](BaseModel):
    """Wire envelope for every domain event consumed by this service.

    Frozen, ``extra="forbid"`` — drift from the documented contract is a
    validation failure rather than a silent runtime degradation. The
    ``type`` field is a plain ``str`` (NOT a ``Literal``) so an unknown
    future event type does not raise a strict-enum ``ValidationError`` at
    the envelope boundary; unknown types are routed through
    :class:`provisioning_worker.events.UnknownEnvelopeType` instead
    (forward-compat, D-05).

    Attributes:
        id: 26-char Crockford-base32 ULID. Acts as the consumer-side
            idempotency key (see ``docs/events.md §Handler idempotency``).
        type: Dotted event type, e.g. ``"subscription.activated"``. The
            prefix before the first dot drives stream selection (see
            :func:`stream_for_envelope_type`).
        version: Payload schema version, ``>= 1``.
        occurred_at: UTC producer wall-clock when the event was minted.
        producer: Service that emitted the event, constrained to the three
            first-party services.
        correlation_id: Upstream request/trace id; ``None`` when the event
            has no upstream request.
        causation_id: ``envelope.id`` of the immediately-preceding event in
            a chain; ``None`` for root events.
        payload: The typed event-specific body.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=26, max_length=26)
    type: str
    version: int = Field(..., ge=1)
    occurred_at: datetime
    producer: Literal["platform-api", "provisioning-worker", "telemetry-worker"]
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: P


def stream_for_envelope_type(envelope_type: str) -> str:
    """Return the Valkey Stream name for a given envelope type.

    Single source of truth for the consume-side routing rule: the segment
    before the first dot selects the stream.

    Args:
        envelope_type: Dotted event type, e.g. ``"subscription.activated"``.

    Returns:
        ``"events.<prefix>"`` where ``<prefix>`` is the segment before the
        first dot, e.g. ``"events.subscription"`` for
        ``"subscription.activated"``.
    """
    return f"events.{envelope_type.split('.', 1)[0]}"
