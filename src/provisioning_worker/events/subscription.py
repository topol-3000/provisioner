"""Subscription-domain event payloads consumed by the provisioning worker.

These are the five ``subscription.*`` payload shapes this service consumes
from ``events.subscription``. Each model mirrors the corresponding
``docs/events.md`` section field-for-field; that document is the contract
(CLAUDE.md §6.2). The models are re-implemented here per repo — no
cross-repo import from platform-api (D-04, D-09).

The single divergence from platform-api's producer-side models is
``total_amount``: platform-api uses its ``MoneyDecimal`` annotated alias,
whereas the consume side uses a plain :class:`decimal.Decimal`. Pydantic
2.13 coerces the JSON string ``"129.99"`` to ``Decimal`` on validate and
serializes it back to a string in JSON mode, so the wire contract is
preserved without importing platform-api's serializer.
"""

from datetime import datetime  # noqa: TC003 — runtime-typed Pydantic field
from decimal import Decimal  # noqa: TC003 — runtime-typed Pydantic field
from typing import Literal
from uuid import UUID  # noqa: TC003 — runtime-typed Pydantic field

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "LineDelta",
    "SubscriptionActivatedPayload",
    "SubscriptionCancelledPayload",
    "SubscriptionLinesChangedPayload",
    "SubscriptionReinstatedPayload",
    "SubscriptionSuspendedPayload",
]


class SubscriptionActivatedPayload(BaseModel):
    """Payload for ``subscription.activated`` (v1).

    Emitted when a ``draft`` subscription transitions to ``active`` after
    Stripe confirms the first invoice payment. Field list is copied
    verbatim from ``docs/events.md §subscription.activated``.

    Attributes:
        subscription_id: Platform subscription UUID.
        customer_id: Platform customer UUID (the subscription owner).
        quote_id: The originating quote UUID (1:1 with the subscription).
        stripe_subscription_id: Stripe ``sub_…`` id, opaque to consumers.
        billing_cycle: Either ``"monthly"`` or ``"annual"``.
        currency: ISO-4217 uppercase code (3 chars).
        line_count: Number of subscription lines, ``>= 1``.
        total_amount: Aggregate subscription amount (major units);
            serialized to JSON as a string.
        activated_at: UTC timestamp of the state transition.
        current_period_start: UTC start of the current Stripe billing period.
        current_period_end: UTC end of the current Stripe billing period.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    quote_id: UUID
    stripe_subscription_id: str
    billing_cycle: Literal["monthly", "annual"]
    currency: str = Field(..., min_length=3, max_length=3)
    line_count: int = Field(..., ge=1)
    total_amount: Decimal
    activated_at: datetime
    current_period_start: datetime
    current_period_end: datetime


class LineDelta(BaseModel):
    """A single line-level change within a ``subscription.lines_changed`` event.

    Field list is copied verbatim from
    ``docs/events.md §subscription.lines_changed``.

    Attributes:
        line_id: UUID of the affected subscription line.
        sku_id: UUID of the SKU on this line.
        sku_key: Natural key of the SKU at the time of the change.
        change: The kind of change that occurred on this line.
        previous: Snapshot of the relevant fields *before* the change
            (stringified values); ``None`` for ``"added"`` changes.
        current: Snapshot of the relevant fields *after* the change
            (stringified values); ``None`` for ``"removed"`` changes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_id: UUID
    sku_id: UUID
    sku_key: str
    change: Literal["added", "removed", "qty_changed", "price_changed", "params_changed"]
    previous: dict[str, str] | None = None
    current: dict[str, str] | None = None


class SubscriptionLinesChangedPayload(BaseModel):
    """Payload for ``subscription.lines_changed`` (v1).

    Emitted when an ``active`` subscription's lines are added, removed, or
    modified. Field list is copied verbatim from
    ``docs/events.md §subscription.lines_changed``.

    Attributes:
        subscription_id: Platform subscription UUID.
        customer_id: Platform customer UUID (the subscription owner).
        change_set_id: Idempotency key for downstream provisioning; scopes
            one logical set of line mutations.
        deltas: Non-empty list of per-line change descriptors.
        effective_at: UTC timestamp when the line change took effect.
        triggered_by: Actor category that initiated the change.
        actor_id: JWT subject of the operator or customer that triggered
            the change; ``None`` for system-initiated changes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    change_set_id: UUID
    deltas: list[LineDelta] = Field(..., min_length=1)
    effective_at: datetime
    triggered_by: Literal["operator", "customer", "system"]
    actor_id: str | None = None


class SubscriptionSuspendedPayload(BaseModel):
    """Payload for ``subscription.suspended`` (v1).

    Emitted on transition to ``suspended`` (dunning exhausted, or operator
    action). Field list is copied verbatim from
    ``docs/events.md §subscription.suspended``.

    Attributes:
        subscription_id: Platform subscription UUID.
        customer_id: Platform customer UUID (the subscription owner).
        reason: Why the subscription was suspended.
        previous_status: The subscription's status immediately before
            suspension (``"active"`` or ``"past_due"``).
        suspended_at: UTC timestamp when the suspension took effect.
        actor_id: JWT subject of the operator that initiated a manual
            suspension; ``None`` for dunning-exhausted or policy paths.
        note: Optional human-readable note from the operator.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    reason: Literal["dunning_exhausted", "manual_operator", "policy_violation"]
    previous_status: Literal["active", "past_due"]
    suspended_at: datetime
    actor_id: str | None = None
    note: str | None = None


class SubscriptionReinstatedPayload(BaseModel):
    """Payload for ``subscription.reinstated`` (v1).

    Emitted on transition out of ``suspended`` or ``past_due`` back to
    ``active``. Field list is copied verbatim from
    ``docs/events.md §subscription.reinstated``.

    Attributes:
        subscription_id: Platform subscription UUID.
        customer_id: Platform customer UUID (the subscription owner).
        reason: Why the subscription was reinstated.
        previous_status: The subscription's status immediately before
            reinstatement (``"past_due"`` or ``"suspended"``).
        reinstated_at: UTC timestamp when reinstatement took effect.
        actor_id: JWT subject of the operator for manual reinstatement;
            ``None`` for the payment-recovered (webhook-triggered) path.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    reason: Literal["payment_recovered", "manual_operator"]
    previous_status: Literal["past_due", "suspended"]
    reinstated_at: datetime
    actor_id: str | None = None


class SubscriptionCancelledPayload(BaseModel):
    """Payload for ``subscription.cancelled`` (v1).

    Emitted on subscription termination. Two flavors: ``immediate``
    (operator hard-cancel or customer immediate cancel) and
    ``at_period_end`` (period rolled over). Field list is copied verbatim
    from ``docs/events.md §subscription.cancelled``.

    Attributes:
        subscription_id: Platform subscription UUID.
        customer_id: Platform customer UUID (the subscription owner).
        cancellation_kind: ``"immediate"`` or ``"at_period_end"``.
        cancelled_at: UTC timestamp when the cancellation took effect.
        requested_at: UTC timestamp when cancellation was first requested.
        grace_until: UTC timestamp of the end of any grace period (may
            equal ``cancelled_at`` for immediate cancellations).
        requested_by: Actor category that initiated the cancellation.
        actor_id: JWT subject of the customer or operator that requested
            cancellation; ``None`` for ``stripe_dunning``-initiated
            cancellations.
        reason_code: Optional machine-readable reason from the requester.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    cancellation_kind: Literal["immediate", "at_period_end"]
    cancelled_at: datetime
    requested_at: datetime
    grace_until: datetime
    requested_by: Literal["customer", "operator", "stripe_dunning"]
    actor_id: str | None = None
    reason_code: str | None = None
