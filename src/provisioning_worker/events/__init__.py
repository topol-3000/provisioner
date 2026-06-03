"""Event package: payload models, envelope, and registries for consumed + produced events.

This package holds both sides of the event data layer for the provisioning worker:

Consume side:
- The generic :class:`EventEnvelope`, the five ``subscription.*`` payload models,
  and a type→model registry (``payload_class_for``) used by the two-phase parse
  in the Valkey Streams adapter.

Produce side:
- ``InstanceProvisionedPayload`` for the single M1 produced event.
- ``envelope_class_for`` registry used by the outbox relay to reconstruct typed
  envelopes from JSONB rows before publishing (D-06).

All models are re-implemented per repo against ``docs/events.md`` — there is no
shared contracts package (CLAUDE.md §6.2).
"""

from typing import Any

from pydantic import BaseModel  # noqa: TC002 — runtime-evaluated registry annotation

from provisioning_worker.events.envelope import EventEnvelope, stream_for_envelope_type
from provisioning_worker.events.instance import InstanceProvisionedPayload
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
    "InstanceProvisionedPayload",
    "LineDelta",
    "SubscriptionActivatedPayload",
    "SubscriptionCancelledPayload",
    "SubscriptionLinesChangedPayload",
    "SubscriptionReinstatedPayload",
    "SubscriptionSuspendedPayload",
    "UnknownEnvelopeType",
    "envelope_class_for",
    "payload_class_for",
    "stream_for_envelope_type",
]


class UnknownEnvelopeType(Exception):
    """Raised when ``envelope.type`` is not in the consume-side or produced-side registry.

    The Valkey Streams adapter (Plan 03) catches this to route unknown
    future event types to a warning-and-XACK path rather than crashing the
    poll loop — a forward-compatibility requirement (D-05). The outbox relay
    raises this for an unrecognised produced event type stored in the outbox.
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


# Produced-side registry: dotted envelope type → parameterised EventEnvelope class.
#
# Used by the outbox relay to reconstruct typed envelopes from JSONB rows
# before publishing. Values are ``EventEnvelope[PayloadClass]`` (not bare payload
# classes) because the relay calls .model_validate(row.payload) on the full envelope
# shape — this gives a concrete SchemaSerializer and validates the stored payload.
# Grows with the Phase-5 catalog (instance.updated, .suspended, etc.).
_ENVELOPE_REGISTRY: dict[str, type[EventEnvelope[Any]]] = {
    "instance.provisioned": EventEnvelope[InstanceProvisionedPayload],
}


def envelope_class_for(envelope_type: str) -> type[EventEnvelope[Any]]:
    """Return the parameterised EventEnvelope class for ``envelope_type``.

    Called by the outbox relay before ``model_validate(row.payload)`` so the
    rebuilt envelope carries the concrete payload class and a real
    ``SchemaSerializer`` (D-06). Symmetric to ``payload_class_for`` on the
    consume side.

    Args:
        envelope_type: Dotted event-type string, e.g. ``"instance.provisioned"``.

    Returns:
        Parameterised generic class, e.g. ``EventEnvelope[InstanceProvisionedPayload]``.

    Raises:
        UnknownEnvelopeType: If the type is not in the produced-side registry.
    """
    try:
        return _ENVELOPE_REGISTRY[envelope_type]
    except KeyError as exc:
        raise UnknownEnvelopeType(
            f"no envelope class registered for envelope_type={envelope_type!r}"
        ) from exc
