"""Unit tests for the InstanceSpec builder in modules/provisioning/spec.py.

All tests are pure unit tests — no DB, no async, no monkeypatching needed.
Verifies spec builder behaviour per decisions D-01, D-02, D-03.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from provisioning_worker.events.subscription import SubscriptionActivatedPayload
from provisioning_worker.modules.provisioning.spec import build_instance_spec
from provisioning_worker.ports.deployment_adapter import DeploymentAdapter, InstanceSpec
from provisioning_worker.ports.entitlement_resolver import EntitlementPicture
from provisioning_worker.settings import Settings
from provisioning_worker.shared.errors import DeploymentFailed, ProvisioningError

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

_SUBSCRIPTION_ID = UUID("018efa2c-0000-7000-8000-000000000001")
_CUSTOMER_ID = UUID("018efa2c-0000-7000-8000-000000000002")
_DEFAULT_SEAT_CAP = 10
_LINE_COUNT = 5

_ACTIVATED_AT = datetime(2026, 6, 1, tzinfo=UTC)


def _make_payload(line_count: int = _LINE_COUNT) -> SubscriptionActivatedPayload:
    """Return a minimal valid SubscriptionActivatedPayload."""
    return SubscriptionActivatedPayload(
        subscription_id=_SUBSCRIPTION_ID,
        customer_id=_CUSTOMER_ID,
        quote_id=UUID("018efa2c-0000-7000-8000-000000000003"),
        stripe_subscription_id="sub_test_123",
        billing_cycle="monthly",
        currency="USD",
        line_count=line_count,
        total_amount=Decimal("129.99"),
        activated_at=_ACTIVATED_AT,
        current_period_start=_ACTIVATED_AT,
        current_period_end=_ACTIVATED_AT,
    )


def _make_settings(seat_cap: int = _DEFAULT_SEAT_CAP) -> Settings:
    """Return Settings with test defaults (avoids real env parsing)."""
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost:5432/test",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://test:test@localhost:5432/test",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        instance_domain_suffix="test.local",
        odoo_base_image="odoo:17",
        provisioning_default_seat_cap=seat_cap,
        provisioning_default_resource_caps="{}",
    )


def _make_entitlement(seat_cap: int = _DEFAULT_SEAT_CAP) -> EntitlementPicture:
    """Return a minimal EntitlementPicture matching settings defaults."""
    return EntitlementPicture(module_set=(), seat_cap=seat_cap, resource_caps={})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_spec_uses_settings_defaults() -> None:
    """build_instance_spec uses entitlement.seat_cap, not line_count (D-03).

    The entitlement picture carries the resolved seat_cap from settings
    defaults; line_count is explicitly NOT mapped to seat_cap.
    """
    settings = _make_settings(seat_cap=_DEFAULT_SEAT_CAP)
    payload = _make_payload(line_count=_LINE_COUNT)
    entitlement = _make_entitlement(seat_cap=settings.provisioning_default_seat_cap)

    spec = build_instance_spec(payload, settings, entitlement)

    assert spec.seat_cap == _DEFAULT_SEAT_CAP
    assert spec.seat_cap != _LINE_COUNT  # D-03: no line_count → seat_cap mapping


def test_spec_slug_derivation() -> None:
    """slug is derived from subscription_id prefix + instance_domain_suffix."""
    settings = _make_settings()
    payload = _make_payload()
    entitlement = _make_entitlement()

    spec = build_instance_spec(payload, settings, entitlement)

    assert spec.slug.startswith("018efa2c")
    assert spec.slug.endswith(settings.instance_domain_suffix)


def test_spec_module_set_is_tuple() -> None:
    """module_set must be a tuple (not list) — frozen dataclass contract."""
    settings = _make_settings()
    payload = _make_payload()
    entitlement = _make_entitlement()

    spec = build_instance_spec(payload, settings, entitlement)

    assert type(spec.module_set) is tuple


def test_spec_is_frozen() -> None:
    """InstanceSpec is frozen — attribute assignment must raise."""
    settings = _make_settings()
    payload = _make_payload()
    entitlement = _make_entitlement()

    spec = build_instance_spec(payload, settings, entitlement)

    with pytest.raises((AttributeError,)):
        spec.seat_cap = 999  # type: ignore[misc]


def test_deployment_adapter_is_runtime_checkable() -> None:
    """DeploymentAdapter is @runtime_checkable — isinstance checks work."""
    # An arbitrary object without the protocol methods must return False.
    assert isinstance(object(), DeploymentAdapter) is False


def test_deployment_failed_is_provisioning_error() -> None:
    """DeploymentFailed is a subclass of ProvisioningError (hierarchy check)."""
    exc = DeploymentFailed("adapter failure")
    assert isinstance(exc, ProvisioningError) is True


def test_instance_spec_round_trip() -> None:
    """InstanceSpec.to_dict() / from_dict() is a lossless round-trip.

    - module_set must be a tuple after round-trip (not a list)
    - resource_caps must be a dict after round-trip
    - all UUID fields are preserved
    """
    settings = _make_settings()
    payload = _make_payload()
    entitlement = EntitlementPicture(
        module_set=("base", "mail"),
        seat_cap=_DEFAULT_SEAT_CAP,
        resource_caps={"cpu": 2, "memory": 4},
    )

    spec = build_instance_spec(payload, settings, entitlement)
    as_dict = spec.to_dict()
    restored = InstanceSpec.from_dict(as_dict)

    assert restored == spec
    assert type(restored.module_set) is tuple, "module_set should be tuple after round-trip"
    assert isinstance(restored.resource_caps, dict), "resource_caps should be dict after round-trip"
    assert isinstance(restored.subscription_id, UUID), "subscription_id should be UUID"
