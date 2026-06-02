"""Unit tests for the provisioning repository layer (Task 2).

All repository tests use mock AsyncSession objects — no real database.
Integration tests (real Postgres via testcontainers) are marked with
``@pytest.mark.integration``.

Tests cover:
- get_instance_by_id: returns instance or None
- get_task_by_id: returns task or None
- get_instance_by_subscription_id: returns instance or None
- update_instance_status: sets status and extra kwargs on the row
- record_task_failure: increments attempt_count, sets last_error, sets instance failed
- record_task_success: sets task.status = succeeded
- insert_enforcement_snapshot: creates EnforcementSnapshot row
- update_snapshot_version: sets instance.snapshot_version
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from provisioning_worker.modules.provisioning.models import (
    EnforcementSnapshot,
    Instance,
    InstanceStatus,
    ProvisioningTask,
    ProvisioningTaskStatus,
)
from provisioning_worker.ports.deployment_adapter import InstanceSpec, ResourceRequests
from provisioning_worker.shared.errors import DeploymentFailed


def _make_spec() -> InstanceSpec:
    """Build a minimal InstanceSpec for test use."""
    return InstanceSpec(
        subscription_id=UUID("018efa2c-0000-7000-8000-000000000001"),
        customer_id=UUID("018efa2c-0000-7000-8000-000000000002"),
        slug="018efa2c.example.local",
        admin_email="admin@example.com",
        odoo_image="odoo:17",
        module_set=("base", "contacts"),
        seat_cap=10,
        resource_caps={"workers": 2},
        env={},
        resources=ResourceRequests(),
    )


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------


def test_settings_has_new_provisioning_fields() -> None:
    """Settings model has all 6 new provisioning fields."""
    from provisioning_worker.settings import Settings

    # Check the class has all expected field names
    fields = set(Settings.model_fields.keys())
    assert "provisioning_max_attempts" in fields
    assert "provisioning_base_delay_s" in fields
    assert "provisioning_multiplier" in fields
    assert "provisioning_cap_s" in fields
    assert "provisioning_default_seat_cap" in fields
    assert "provisioning_default_resource_caps" in fields


def test_settings_backoff_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings has correct default values for all backoff knobs."""
    # Provide required fields
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    monkeypatch.setenv("DATABASE_URL_SYNC", "postgresql+psycopg://user:pass@localhost/db")
    monkeypatch.setenv("VALKEY_URL", "redis://localhost:6379/0")

    from provisioning_worker.settings import Settings

    settings = Settings()
    assert settings.provisioning_max_attempts == 5
    assert settings.provisioning_base_delay_s == 2.0
    assert settings.provisioning_multiplier == 2.0
    assert settings.provisioning_cap_s == 60.0
    assert settings.provisioning_default_seat_cap == 10
    assert settings.provisioning_default_resource_caps == "{}"


# ---------------------------------------------------------------------------
# Repository unit tests (mock AsyncSession)
# ---------------------------------------------------------------------------


async def test_get_instance_by_id_found() -> None:
    """get_instance_by_id returns the instance when it exists."""
    from provisioning_worker.modules.provisioning.repository import get_instance_by_id

    instance = MagicMock(spec=Instance)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = instance
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    result = await get_instance_by_id(session, UUID("018efa2c-0000-7000-8000-000000000001"))
    assert result is instance


async def test_get_instance_by_id_not_found() -> None:
    """get_instance_by_id returns None when no row exists."""
    from provisioning_worker.modules.provisioning.repository import get_instance_by_id

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    result = await get_instance_by_id(session, UUID("018efa2c-0000-7000-8000-000000000001"))
    assert result is None


async def test_get_task_by_id_found() -> None:
    """get_task_by_id returns the task when it exists."""
    from provisioning_worker.modules.provisioning.repository import get_task_by_id

    task = MagicMock(spec=ProvisioningTask)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = task
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    result = await get_task_by_id(session, UUID("018efa2c-0000-7000-8000-000000000003"))
    assert result is task


async def test_get_task_by_id_not_found() -> None:
    """get_task_by_id returns None when no task row exists."""
    from provisioning_worker.modules.provisioning.repository import get_task_by_id

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    result = await get_task_by_id(session, UUID("018efa2c-0000-7000-8000-000000000003"))
    assert result is None


async def test_get_instance_by_subscription_id_found() -> None:
    """get_instance_by_subscription_id returns the instance when it exists."""
    from provisioning_worker.modules.provisioning.repository import (
        get_instance_by_subscription_id,
    )

    instance = MagicMock(spec=Instance)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = instance
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    result = await get_instance_by_subscription_id(
        session, UUID("018efa2c-0000-7000-8000-000000000001")
    )
    assert result is instance


async def test_update_instance_status_changes_status() -> None:
    """update_instance_status sets the given status and extra kwargs on the instance row.

    Uses a mock session where execute returns a scalable result.
    """
    from provisioning_worker.modules.provisioning.repository import update_instance_status

    instance = MagicMock(spec=Instance)
    instance.status = InstanceStatus.pending

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = instance
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    await update_instance_status(
        session,
        UUID("018efa2c-0000-7000-8000-000000000001"),
        InstanceStatus.deploying,
    )

    assert instance.status == InstanceStatus.deploying


async def test_update_instance_status_with_kwargs() -> None:
    """update_instance_status sets extra kwargs like url and ready_at on the instance."""
    from provisioning_worker.modules.provisioning.repository import update_instance_status

    instance = MagicMock(spec=Instance)
    instance.status = InstanceStatus.pending

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = instance
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    now = datetime.now(tz=UTC)
    await update_instance_status(
        session,
        UUID("018efa2c-0000-7000-8000-000000000001"),
        InstanceStatus.ready,
        url="https://test.example.local",
        ready_at=now,
    )

    assert instance.status == InstanceStatus.ready
    assert instance.url == "https://test.example.local"
    assert instance.ready_at == now


async def test_record_task_failure_sets_fields() -> None:
    """record_task_failure increments attempt_count, sets last_error, next_attempt_at, and instance.status=failed."""
    from provisioning_worker.modules.provisioning.repository import record_task_failure

    task = MagicMock(spec=ProvisioningTask)
    task.attempt_count = 1

    instance = MagicMock(spec=Instance)

    mock_result_task = MagicMock()
    mock_result_task.scalar_one_or_none.return_value = task

    mock_result_instance = MagicMock()
    mock_result_instance.scalar_one_or_none.return_value = instance

    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_result_task
        return mock_result_instance

    session = MagicMock()
    session.execute = _execute

    exc = DeploymentFailed("something broke", step="create", reason="timeout")
    next_attempt_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    task_id = UUID("018efa2c-0000-7000-8000-000000000003")
    instance_id = UUID("018efa2c-0000-7000-8000-000000000001")

    await record_task_failure(session, task_id, instance_id, exc, next_attempt_at)

    assert task.attempt_count == 2
    assert task.last_error == str(exc)
    assert task.next_attempt_at == next_attempt_at
    assert instance.status == InstanceStatus.failed
    assert instance.failed_step == "create"
    assert instance.failure_reason == "timeout"


async def test_record_task_success_sets_status() -> None:
    """record_task_success sets task.status = succeeded."""
    from provisioning_worker.modules.provisioning.repository import record_task_success

    task = MagicMock(spec=ProvisioningTask)
    task.status = ProvisioningTaskStatus.running

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = task
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    await record_task_success(session, UUID("018efa2c-0000-7000-8000-000000000003"))

    assert task.status == ProvisioningTaskStatus.succeeded


async def test_insert_enforcement_snapshot_creates_row() -> None:
    """insert_enforcement_snapshot adds an EnforcementSnapshot row with correct fields."""
    from provisioning_worker.modules.provisioning.repository import insert_enforcement_snapshot

    session = MagicMock()
    session.add = MagicMock()

    spec = _make_spec()
    instance_id = UUID("018efa2c-0000-7000-8000-000000000001")

    await insert_enforcement_snapshot(session, instance_id, spec)

    session.add.assert_called_once()
    added_obj = session.add.call_args[0][0]
    assert isinstance(added_obj, EnforcementSnapshot)
    assert added_obj.instance_id == instance_id
    assert added_obj.version == 1
    assert added_obj.seat_cap == spec.seat_cap
    assert added_obj.module_set == list(spec.module_set)


async def test_insert_enforcement_snapshot_custom_version() -> None:
    """insert_enforcement_snapshot respects the version parameter."""
    from provisioning_worker.modules.provisioning.repository import insert_enforcement_snapshot

    session = MagicMock()
    session.add = MagicMock()

    spec = _make_spec()
    instance_id = UUID("018efa2c-0000-7000-8000-000000000001")

    await insert_enforcement_snapshot(session, instance_id, spec, version=3)

    added_obj = session.add.call_args[0][0]
    assert added_obj.version == 3


async def test_update_snapshot_version() -> None:
    """update_snapshot_version sets instance.snapshot_version."""
    from provisioning_worker.modules.provisioning.repository import update_snapshot_version

    instance = MagicMock(spec=Instance)
    instance.snapshot_version = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = instance
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)

    await update_snapshot_version(
        session, UUID("018efa2c-0000-7000-8000-000000000001"), version=2
    )

    assert instance.snapshot_version == 2
