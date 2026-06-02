"""Event package: consumed subscription.* payload models, envelope, and registry.

This package holds the consume-side data layer for the provisioning worker:
the generic :class:`EventEnvelope`, the five ``subscription.*`` payload
models, and a type→model registry used by the two-phase parse in the Valkey
Streams adapter (Plan 03). All models are re-implemented per repo against
``docs/events.md`` — there is no shared contracts package (CLAUDE.md §6.2).
"""

from pydantic import BaseModel  # noqa: TC002 — runtime-evaluated registry annotation

from provisioning_worker.events.envelope import EventEnvelope, stream_for_envelope_type
from provisioning_worker.events.subscription import (
    LineDelta,
    SubscriptionActivatedPayload,
    SubscriptionCancelledPayload,
    SubscriptionLinesChangedPayload,
    SubscriptionReinstatedPayload,
    SubscriptionSuspendedPayload,
)

__all__ = [
    "EventEnvelope",
    "LineDelta",
    "SubscriptionActivatedPayload",
    "SubscriptionCancelledPayload",
    "SubscriptionLinesChangedPayload",
    "SubscriptionReinstatedPayload",
    "SubscriptionSuspendedPayload",
    "UnknownEnvelopeType",
    "payload_class_for",
    "stream_for_envelope_type",
]


class UnknownEnvelopeType(Exception):
    """Raised when ``envelope.type`` is not in the consume-side registry.

    The Valkey Streams adapter (Plan 03) catches this to route unknown
    future event types to a warning-and-XACK path rather than crashing the
    poll loop — a forward-compatibility requirement (D-05).
    """


# Consume-side registry: dotted event type → payload model class.
#
# Values are the bare payload classes (NOT ``EventEnvelope[Payload]``)
# because this registry feeds the two-phase parse: the outer envelope is
# read first to learn ``type``, then the matching payload class validates
# the inner ``payload`` dict.
_PAYLOAD_REGISTRY: dict[str, type[BaseModel]] = {
    "subscription.activated": SubscriptionActivatedPayload,
    "subscription.lines_changed": SubscriptionLinesChangedPayload,
    "subscription.suspended": SubscriptionSuspendedPayload,
    "subscription.reinstated": SubscriptionReinstatedPayload,
    "subscription.cancelled": SubscriptionCancelledPayload,
}


def payload_class_for(envelope_type: str) -> type[BaseModel]:
    """Return the payload model class registered for ``envelope_type``.

    Args:
        envelope_type: Dotted event type, e.g. ``"subscription.activated"``.

    Returns:
        The payload model class for the given type.

    Raises:
        UnknownEnvelopeType: If no payload class is registered for the type.
    """
    try:
        return _PAYLOAD_REGISTRY[envelope_type]
    except KeyError as exc:
        raise UnknownEnvelopeType(
            f"no payload class registered for envelope_type={envelope_type!r}"
        ) from exc
