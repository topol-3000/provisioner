"""Unit tests for the provisioning convergence service.

Wave 2 — unit-only assertions (state machine, snapshot delegation).
Full integration assertions arrive in Plan 03-04 (Wave 3).
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from provisioning_worker.modules.provisioning.models import InstanceStatus
from provisioning_worker.modules.provisioning.service import ProvisioningService
from provisioning_worker.ports.deployment_adapter import InstanceSpec, ResourceRequests
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


def test_placeholder() -> None:
    """Placeholder test — full integration assertions added in Wave 3 (Plan 03-04)."""
    pytest.skip("full integration assertions in Wave 3 — Plan 03-04")
