"""Round-trip and extra-field-rejection tests for the five consumed payloads.

Canonical wire fixtures are hand-authored here from ``docs/events.md`` —
there is NO cross-repo import from platform-api (D-09). Each fixture is the
inner ``payload`` dict in exact wire format: UUIDs as strings, datetimes as
RFC-3339 strings, ``total_amount`` as the string ``"129.99"``.
"""

from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from provisioning_worker.events import UnknownEnvelopeType, payload_class_for
from provisioning_worker.events.envelope import EventEnvelope
from provisioning_worker.events.subscription import (
    SubscriptionActivatedPayload,
    SubscriptionCancelledPayload,
    SubscriptionLinesChangedPayload,
    SubscriptionReinstatedPayload,
    SubscriptionSuspendedPayload,
)

_SUB_ID = "018efa2c-0000-7000-8000-000000000001"
_CUST_ID = "018efa2c-0000-7000-8000-000000000002"

_ACTIVATED_PAYLOAD: dict[str, object] = {
    "subscription_id": _SUB_ID,
    "customer_id": _CUST_ID,
    "quote_id": "018efa2c-0000-7000-8000-000000000003",
    "stripe_subscription_id": "sub_test_abc123",
    "billing_cycle": "monthly",
    "currency": "USD",
    "line_count": 2,
    "total_amount": "129.99",
    "activated_at": "2026-06-01T00:00:00Z",
    "current_period_start": "2026-06-01T00:00:00Z",
    "current_period_end": "2026-07-01T00:00:00Z",
}

_LINES_CHANGED_PAYLOAD: dict[str, object] = {
    "subscription_id": _SUB_ID,
    "customer_id": _CUST_ID,
    "change_set_id": "018efa2c-0000-7000-8000-000000000004",
    "deltas": [
        {
            "line_id": "018efa2c-0000-7000-8000-000000000005",
            "sku_id": "018efa2c-0000-7000-8000-000000000006",
            "sku_key": "odoo.seats",
            "change": "qty_changed",
            "previous": {"quantity": "5"},
            "current": {"quantity": "8"},
        }
    ],
    "effective_at": "2026-06-01T00:00:00Z",
    "triggered_by": "operator",
    "actor_id": "kc-operator-1",
}

_SUSPENDED_PAYLOAD: dict[str, object] = {
    "subscription_id": _SUB_ID,
    "customer_id": _CUST_ID,
    "reason": "dunning_exhausted",
    "previous_status": "past_due",
    "suspended_at": "2026-06-01T00:00:00Z",
    "actor_id": None,
    "note": None,
}

_REINSTATED_PAYLOAD: dict[str, object] = {
    "subscription_id": _SUB_ID,
    "customer_id": _CUST_ID,
    "reason": "payment_recovered",
    "previous_status": "suspended",
    "reinstated_at": "2026-06-01T00:00:00Z",
    "actor_id": None,
}

_CANCELLED_PAYLOAD: dict[str, object] = {
    "subscription_id": _SUB_ID,
    "customer_id": _CUST_ID,
    "cancellation_kind": "at_period_end",
    "cancelled_at": "2026-07-01T00:00:00Z",
    "requested_at": "2026-06-15T00:00:00Z",
    "grace_until": "2026-07-01T00:00:00Z",
    "requested_by": "customer",
    "actor_id": "kc-customer-1",
    "reason_code": "too_expensive",
}


def _envelope_for(event_type: str, payload: dict[str, object]) -> dict[str, object]:
    """Wrap a payload dict in a minimal valid envelope dict for the given type."""
    return {
        "id": "01JZQABCDE12345678901234AB",
        "type": event_type,
        "version": 1,
        "occurred_at": "2026-06-01T00:00:00Z",
        "producer": "platform-api",
        "correlation_id": None,
        "causation_id": None,
        "payload": payload,
    }


_PAYLOAD_CASES: list[tuple[str, type[BaseModel], dict[str, object]]] = [
    ("subscription.activated", SubscriptionActivatedPayload, _ACTIVATED_PAYLOAD),
    ("subscription.lines_changed", SubscriptionLinesChangedPayload, _LINES_CHANGED_PAYLOAD),
    ("subscription.suspended", SubscriptionSuspendedPayload, _SUSPENDED_PAYLOAD),
    ("subscription.reinstated", SubscriptionReinstatedPayload, _REINSTATED_PAYLOAD),
    ("subscription.cancelled", SubscriptionCancelledPayload, _CANCELLED_PAYLOAD),
]


@pytest.mark.parametrize(("event_type", "model_cls", "payload"), _PAYLOAD_CASES)
def test_payload_round_trips_through_json(
    event_type: str,
    model_cls: type[BaseModel],
    payload: dict[str, object],
) -> None:
    """Each payload validates from wire JSON and round-trips to equality."""
    parsed = model_cls.model_validate(payload)
    dumped = parsed.model_dump(mode="json")
    re_parsed = model_cls.model_validate(dumped)
    assert re_parsed == parsed


def test_activated_envelope_round_trips_and_parses_decimal() -> None:
    """EventEnvelope[SubscriptionActivatedPayload] round-trips; amount is Decimal."""
    fixture = _envelope_for("subscription.activated", _ACTIVATED_PAYLOAD)
    env = EventEnvelope[SubscriptionActivatedPayload].model_validate(fixture)

    assert env.type == "subscription.activated"
    assert env.payload.total_amount == Decimal("129.99")

    dumped = env.model_dump(mode="json")
    assert set(dumped.keys()) == {
        "id",
        "type",
        "version",
        "occurred_at",
        "producer",
        "correlation_id",
        "causation_id",
        "payload",
    }
    assert isinstance(dumped["payload"]["total_amount"], str)
    assert dumped["payload"]["total_amount"] == "129.99"

    re_env = EventEnvelope[SubscriptionActivatedPayload].model_validate(dumped)
    assert re_env == env


@pytest.mark.parametrize(("event_type", "model_cls", "payload"), _PAYLOAD_CASES)
def test_payload_rejects_extra_field(
    event_type: str,
    model_cls: type[BaseModel],
    payload: dict[str, object],
) -> None:
    """An unexpected field on any payload raises ValidationError (extra=forbid)."""
    bad = {**payload, "unexpected_field": "x"}
    with pytest.raises(ValidationError):
        model_cls.model_validate(bad)


def test_payload_class_for_resolves_known_type() -> None:
    """payload_class_for returns the registered class for a known type."""
    assert payload_class_for("subscription.activated") is SubscriptionActivatedPayload


def test_payload_class_for_raises_on_unknown_type() -> None:
    """payload_class_for raises UnknownEnvelopeType for an unregistered type."""
    with pytest.raises(UnknownEnvelopeType):
        payload_class_for("subscription.unknown_future")
