"""Unit tests for the provisioning convergence service.

Covers the state machine guard, enforcement-snapshot delegation, and
the open_instance domain method.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from provisioning_worker.modules.provisioning.models import InstanceStatus
from provisioning_worker.modules.provisioning.service import ProvisioningService
from provisioning_worker.ports.deployment_adapter import InstanceSpec, ResourceRequests
from provisioning_worker.settings import Settings
from provisioning_worker.shared.errors import InvalidTransition


def _test_spec() -> InstanceSpec:
    """Return a minimal InstanceSpec for unit tests."""
    return InstanceSpec(
        subscription_id=UUID("018efa2c-0000-7000-8000-000000000001"),
        customer_id=UUID("018efa2c-0000-7000-8000-000000000002"),
        slug="018efa2c.example.local",
        admin_email="test@example.com",
        odoo_image="odoo:17",
        module_set=(),
        seat_cap=10,
        resource_caps={},
        env={},
        resources=ResourceRequests(),
    )


class TestValidateTransition:
    """Tests for ProvisioningService.validate_transition state machine guard."""

    def setup_method(self) -> None:
        from provisioning_worker.adapters.m1_entitlement_resolver import (
            DefaultEntitlementResolver,
        )

        self.service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    def test_pending_to_deploying_is_valid(self) -> None:
        """pending → deploying is a valid create-path transition."""
        # Must not raise
        self.service.validate_transition(InstanceStatus.pending, InstanceStatus.deploying)

    def test_deploying_to_configuring_is_valid(self) -> None:
        """deploying → configuring is a valid create-path transition."""
        self.service.validate_transition(InstanceStatus.deploying, InstanceStatus.configuring)

    def test_configuring_to_ready_is_valid(self) -> None:
        """configuring → ready is a valid create-path transition."""
        self.service.validate_transition(InstanceStatus.configuring, InstanceStatus.ready)

    def test_any_to_failed_is_valid(self) -> None:
        """Any status → failed is allowed (failure recording)."""
        for status in InstanceStatus:
            self.service.validate_transition(status, InstanceStatus.failed)

    def test_ready_to_pending_is_invalid(self) -> None:
        """ready → pending is not a valid transition."""
        with pytest.raises(InvalidTransition):
            self.service.validate_transition(InstanceStatus.ready, InstanceStatus.pending)

    def test_configuring_to_deploying_is_invalid(self) -> None:
        """configuring → deploying is not a valid transition."""
        with pytest.raises(InvalidTransition):
            self.service.validate_transition(InstanceStatus.configuring, InstanceStatus.deploying)

    def test_failed_to_deploying_is_valid(self) -> None:
        """failed → deploying is valid (retry re-entry path, D-09)."""
        self.service.validate_transition(InstanceStatus.failed, InstanceStatus.deploying)


class TestWriteEnforcementSnapshot:
    """Tests that write_enforcement_snapshot delegates to repository functions."""

    def setup_method(self) -> None:
        from provisioning_worker.adapters.m1_entitlement_resolver import (
            DefaultEntitlementResolver,
        )

        self.service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    async def test_delegates_to_insert_enforcement_snapshot(self) -> None:
        """write_enforcement_snapshot calls repository.insert_enforcement_snapshot."""
        instance_id = UUID("018efa2c-0000-7000-8000-000000000001")
        spec = _test_spec()
        mock_session = AsyncMock()

        with (
            patch(
                "provisioning_worker.modules.provisioning.service.insert_enforcement_snapshot"
            ) as mock_insert,
            patch(
                "provisioning_worker.modules.provisioning.service.update_snapshot_version"
            ) as mock_update,
        ):
            mock_insert.return_value = None
            mock_update.return_value = None
            await self.service.write_enforcement_snapshot(mock_session, instance_id, spec)

        mock_insert.assert_called_once_with(mock_session, instance_id, spec, 1)
        mock_update.assert_called_once_with(mock_session, instance_id, 1)

    async def test_delegates_with_custom_version(self) -> None:
        """write_enforcement_snapshot passes version to repository functions."""
        instance_id = UUID("018efa2c-0000-7000-8000-000000000001")
        spec = _test_spec()
        mock_session = AsyncMock()

        with (
            patch(
                "provisioning_worker.modules.provisioning.service.insert_enforcement_snapshot"
            ) as mock_insert,
            patch(
                "provisioning_worker.modules.provisioning.service.update_snapshot_version"
            ) as mock_update,
        ):
            mock_insert.return_value = None
            mock_update.return_value = None
            await self.service.write_enforcement_snapshot(mock_session, instance_id, spec, version=2)

        mock_insert.assert_called_once_with(mock_session, instance_id, spec, 2)
        mock_update.assert_called_once_with(mock_session, instance_id, 2)


def _make_mock_session():
    """Return a mock AsyncSession with sync add and async execute/commit/rollback.

    open_instance calls session.add() (sync) — using AsyncMock() directly
    causes ``PytestUnraisableExceptionWarning`` because add becomes an
    unawaited coroutine. Build a MagicMock with explicit async attributes
    to match the real session interface.
    """
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


def _make_activated_payload():
    """Return a minimal SubscriptionActivatedPayload for open_instance tests."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from provisioning_worker.events.subscription import SubscriptionActivatedPayload

    return SubscriptionActivatedPayload(
        subscription_id=UUID("018efa2c-0000-7000-8000-000000000010"),
        customer_id=UUID("018efa2c-0000-7000-8000-000000000020"),
        quote_id=UUID("018efa2c-0000-7000-8000-000000000030"),
        stripe_subscription_id="sub_test_999",
        billing_cycle="monthly",
        currency="USD",
        line_count=3,
        total_amount=Decimal("99.00"),
        activated_at=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )


class TestOpenInstance:
    """Tests for ProvisioningService.open_instance."""

    def setup_method(self) -> None:
        from provisioning_worker.adapters.m1_entitlement_resolver import (
            DefaultEntitlementResolver,
        )

        self.service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    async def test_open_instance_returns_instance_and_task(self) -> None:
        """open_instance returns (Instance, ProvisioningTask) tuple."""
        from provisioning_worker.modules.provisioning.models import Instance, ProvisioningTask

        mock_session = _make_mock_session()
        payload = _make_activated_payload()

        result = await self.service.open_instance(payload, mock_session, _test_settings())

        assert isinstance(result, tuple)
        assert len(result) == 2
        instance, task = result
        assert isinstance(instance, Instance)
        assert isinstance(task, ProvisioningTask)

    async def test_open_instance_stages_both_rows(self) -> None:
        """open_instance calls session.add() twice — once for Instance, once for ProvisioningTask."""
        from provisioning_worker.modules.provisioning.models import Instance, ProvisioningTask

        mock_session = _make_mock_session()
        payload = _make_activated_payload()

        await self.service.open_instance(payload, mock_session, _test_settings())

        assert mock_session.add.call_count == 2
        added_types = [type(call.args[0]) for call in mock_session.add.call_args_list]
        assert Instance in added_types
        assert ProvisioningTask in added_types

    async def test_open_instance_sets_pending_status(self) -> None:
        """open_instance creates instance with status=pending."""
        mock_session = _make_mock_session()
        payload = _make_activated_payload()

        instance, _ = await self.service.open_instance(payload, mock_session, _test_settings())

        assert instance.status == InstanceStatus.pending

    async def test_open_instance_payload_contains_spec_dict(self) -> None:
        """open_instance stores spec.to_dict() in task.payload (WARNING 5 contract)."""
        mock_session = _make_mock_session()
        payload = _make_activated_payload()

        _, task = await self.service.open_instance(payload, mock_session, _test_settings())

        assert isinstance(task.payload, dict)
        assert "subscription_id" in task.payload


def _test_settings() -> Settings:
    """Return a minimal Settings instance for unit tests."""
    return Settings(
        database_url="postgresql+psycopg://user:pass@localhost/test",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://user:pass@localhost/test",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
