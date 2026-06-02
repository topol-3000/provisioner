"""Field-set pinning and contract tests for :class:`EventEnvelope`.

These tests pin the envelope's field set and assert its ``extra="forbid"``
contract so that any drift from ``docs/events.md §Envelope`` is caught as a
test failure.
"""

import pytest
from pydantic import ValidationError

from provisioning_worker.events.envelope import EventEnvelope, stream_for_envelope_type
from provisioning_worker.events.subscription import SubscriptionActivatedPayload

_ENVELOPE_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "type",
        "version",
        "occurred_at",
        "producer",
        "correlation_id",
        "causation_id",
        "payload",
    }
)

_VALID_ENVELOPE: dict[str, object] = {
    "id": "01JZQABCDE12345678901234AB",
    "type": "subscription.activated",
    "version": 1,
    "occurred_at": "2026-06-01T00:00:00Z",
    "producer": "platform-api",
    "correlation_id": None,
    "causation_id": None,
    "payload": {
        "subscription_id": "018efa2c-0000-7000-8000-000000000001",
        "customer_id": "018efa2c-0000-7000-8000-000000000002",
        "quote_id": "018efa2c-0000-7000-8000-000000000003",
        "stripe_subscription_id": "sub_test_abc123",
        "billing_cycle": "monthly",
        "currency": "USD",
        "line_count": 2,
        "total_amount": "129.99",
        "activated_at": "2026-06-01T00:00:00Z",
        "current_period_start": "2026-06-01T00:00:00Z",
        "current_period_end": "2026-07-01T00:00:00Z",
    },
}


def test_envelope_field_set_is_pinned() -> None:
    """EventEnvelope exposes exactly the eight documented fields."""
    assert set(EventEnvelope.model_fields.keys()) == _ENVELOPE_FIELDS


def test_envelope_rejects_extra_top_level_field() -> None:
    """An unexpected top-level field raises ValidationError (extra=forbid)."""
    bad = {**_VALID_ENVELOPE, "unexpected_field": "x"}
    with pytest.raises(ValidationError):
        EventEnvelope[SubscriptionActivatedPayload].model_validate(bad)


def test_stream_for_envelope_type_uses_dotted_prefix() -> None:
    """stream_for_envelope_type maps the type prefix to its Valkey stream."""
    assert stream_for_envelope_type("subscription.activated") == "events.subscription"
    assert stream_for_envelope_type("instance.provisioned") == "events.instance"
