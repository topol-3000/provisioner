"""Unit and integration tests for provisioning Taskiq tasks.

Covers the create-path convergence loop: backoff formula, fault injection,
credential-delivery once-only guard, enforcement snapshot, and the full
end-to-end path from pending → deploying → configuring → ready.

Test structure:
- Unit tests (Docker-free, ``-m "not integration"``): mock session_scope,
  use FakeDeploymentAdapter + FakeClock for deterministic behaviour.
- Integration tests (``@pytest.mark.integration``, testcontainers Postgres):
  real DB rows, patched session_scope pointing at pg_session.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import provisioning_worker.modules.provisioning.tasks as tasks_mod
from provisioning_worker.adapters.fake_deployment import FakeDeploymentAdapter
from provisioning_worker.adapters.m1_entitlement_resolver import DefaultEntitlementResolver
from provisioning_worker.modules.provisioning.models import (
    Instance,
    InstanceStatus,
    ProvisioningTask,
    ProvisioningTaskStatus,
    TaskType,
)
from provisioning_worker.modules.provisioning.service import ProvisioningService
from provisioning_worker.modules.provisioning.tasks import (
    _compute_backoff_seconds,
    create_instance_task,
)
from provisioning_worker.ports.clock import FakeClock
from provisioning_worker.ports.deployment_adapter import InstanceSpec, ResourceRequests
from provisioning_worker.settings import Settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _test_settings() -> Settings:
    """Return a minimal Settings instance for unit tests."""
    return Settings(
        database_url="postgresql+psycopg://user:pass@localhost/test",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://user:pass@localhost/test",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )


def _test_spec() -> InstanceSpec:
    """Return a minimal InstanceSpec for unit tests."""
    return InstanceSpec(
        subscription_id=UUID("018efa2c-0000-7000-8000-000000000001"),
        customer_id=UUID("018efa2c-0000-7000-8000-000000000002"),
        slug="018efa2c.example.local",
        admin_email="test@example.com",
        odoo_image="odoo:17",
        module_set=("base",),
        seat_cap=10,
        resource_caps={"cpu": 2},
        env={},
        resources=ResourceRequests(),
    )


_UNIT_INSTANCE_ID = UUID("018efa2c-0000-7000-8000-000000000001")
_UNIT_TASK_ID = UUID("018efa2c-0000-7000-8000-000000000099")


def _make_instance(
    status: InstanceStatus = InstanceStatus.pending,
    subscription_id: UUID | None = None,
    instance_id: UUID | None = None,
) -> Instance:
    """Return a minimal Instance ORM object (not persisted) for unit tests.

    Sets ``id`` explicitly because SQLAlchemy defaults only fire on flush.
    Unit tests bypass flush, so the ID must be pre-assigned.
    """
    inst = Instance(
        subscription_id=subscription_id or UUID("018efa2c-0000-7000-8000-000000000001"),
        customer_id=UUID("018efa2c-0000-7000-8000-000000000002"),
        status=status,
        admin_email="test@example.com",
        desired_seat_cap=10,
        desired_resource_caps={},
        version=1,
    )
    # Pre-assign ID — SQLAlchemy column default only fires on flush/commit.
    inst.id = instance_id or _UNIT_INSTANCE_ID
    return inst


def _make_task(instance_id: UUID, spec: InstanceSpec, task_id: UUID | None = None) -> ProvisioningTask:
    """Return a minimal ProvisioningTask ORM object (not persisted) for unit tests.

    Sets ``id`` explicitly because SQLAlchemy defaults only fire on flush.
    """
    task = ProvisioningTask(
        instance_id=instance_id,
        task_type=TaskType.create,
        status=ProvisioningTaskStatus.pending,
        source_event_id="01JZQABCDE12345678901234AB",
        attempt_count=0,
        max_attempts=5,
        payload=spec.to_dict(),
    )
    task.id = task_id or _UNIT_TASK_ID
    return task


# ---------------------------------------------------------------------------
# Backoff formula (already green from Wave 2 — keep intact)
# ---------------------------------------------------------------------------


def test_backoff_formula_attempt_0() -> None:
    """Backoff at attempt 0 equals base delay (2.0 seconds)."""
    settings = _test_settings()
    result = _compute_backoff_seconds(attempt_count=0, settings=settings)
    assert result == 2.0


def test_backoff_formula_attempt_1() -> None:
    """Backoff at attempt 1 is base * multiplier^1 = 4.0 seconds."""
    settings = _test_settings()
    result = _compute_backoff_seconds(attempt_count=1, settings=settings)
    assert result == 4.0


def test_backoff_formula_capped() -> None:
    """Backoff at attempt 10 is capped at provisioning_cap_s (60.0 seconds)."""
    settings = _test_settings()
    result = _compute_backoff_seconds(attempt_count=10, settings=settings)
    assert result == 60.0


def test_backoff_formula_respects_cap() -> None:
    """Backoff never exceeds cap regardless of attempt count."""
    settings = _test_settings()
    for attempt in range(20):
        result = _compute_backoff_seconds(attempt_count=attempt, settings=settings)
        assert result <= settings.provisioning_cap_s


def test_create_instance_task_is_registered() -> None:
    """create_instance_task is a registered Taskiq decorated task with kiq method."""
    assert hasattr(create_instance_task, "kiq"), "create_instance_task must have .kiq() method"
    assert hasattr(create_instance_task, "broker"), "create_instance_task must have .broker"


# ---------------------------------------------------------------------------
# WARNING 5 round-trip proof — no DB needed
# ---------------------------------------------------------------------------


def test_spec_rebuilt_from_payload() -> None:
    """Spec stored in task.payload round-trips through from_dict with tuple preserved.

    WARNING 5 proof: tasks.py calls InstanceSpec.from_dict(task.payload).
    The reconstructed spec must have module_set as a tuple (not list) and
    resource_caps as a dict — verifies the WARNING 5 round-trip contract.
    """
    spec = _test_spec()
    payload = spec.to_dict()

    # Simulate what the task does:
    rebuilt = InstanceSpec.from_dict(payload)

    assert type(rebuilt.module_set) is tuple, "module_set must be tuple after from_dict"
    assert isinstance(rebuilt.resource_caps, dict), "resource_caps must be dict after from_dict"
    assert rebuilt.module_set == spec.module_set
    assert rebuilt.subscription_id == spec.subscription_id


# ---------------------------------------------------------------------------
# Unit tests — mocked session_scope, deterministic
# ---------------------------------------------------------------------------


def _patch_session_scope_for_tasks(monkeypatch: pytest.MonkeyPatch, session):
    """Patch session_scope in tasks module to yield the provided session."""

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks_mod, "session_scope", _scope)


def _make_unit_mock_session(instance: Instance, task: ProvisioningTask):
    """Build a mock AsyncSession that returns the given instance and task from queries."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()

    # execute returns a result whose scalar_one_or_none alternates between
    # instance and task depending on call order.
    call_results = [instance, task]
    call_index = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_index
        mock_result = MagicMock()
        # Determine which object to return based on call position.
        # The task loads instance first, then task in the first session_scope.
        # Subsequent scopes load instance again for status updates.
        result_value = call_results[call_index % len(call_results)]
        call_index += 1
        mock_result.scalar_one_or_none.return_value = result_value
        return mock_result

    session.execute = AsyncMock(side_effect=_execute)
    return session


async def test_create_path_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: create_instance_task drives instance to ready status.

    Patches session_scope to use a mock session that returns a pre-built
    Instance and ProvisioningTask. FakeDeploymentAdapter and FakeClock
    ensure deterministic, instant execution.
    """
    spec = _test_spec()
    instance = _make_instance()
    task = _make_task(instance.id, spec)

    # Track status changes via session attribute mutations.
    statuses: list[InstanceStatus] = []
    original_add = MagicMock()

    adapter = FakeDeploymentAdapter()
    clock = FakeClock()
    settings = _test_settings()
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    # For this unit test we verify the adapter is called and no exception raised.
    # We mock session_scope to return an in-memory mock session.
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()

    call_count = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_count
        mock_result = MagicMock()
        call_count += 1
        # First two calls in the first session_scope: get_instance, get_task.
        if call_count <= 2:
            mock_result.scalar_one_or_none.return_value = (
                instance if call_count == 1 else task
            )
        else:
            # Subsequent calls: return instance for status update queries.
            mock_result.scalar_one_or_none.return_value = instance
        return mock_result

    session.execute = AsyncMock(side_effect=_execute)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks_mod, "session_scope", _scope)

    # Patch create_instance_task.kiq to prevent re-enqueue in failure path.
    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            AsyncMock(),  # transport
            clock,
            service,
        )

    # Adapter must have been called once.
    assert adapter._call_counts.get("create", 0) == 1


async def test_credentials_sent_once(
    spy_console_transport: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credentials are delivered exactly once on the first successful convergence."""
    spec = _test_spec()
    instance = _make_instance()
    task = _make_task(instance.id, spec)

    adapter = FakeDeploymentAdapter()
    clock = FakeClock()
    settings = _test_settings()
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    call_count = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_count
        mock_result = MagicMock()
        call_count += 1
        if call_count <= 2:
            mock_result.scalar_one_or_none.return_value = instance if call_count == 1 else task
        else:
            mock_result.scalar_one_or_none.return_value = instance
        return mock_result

    session.execute = AsyncMock(side_effect=_execute)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks_mod, "session_scope", _scope)

    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            spy_console_transport,
            clock,
            service,
        )

    spy_console_transport.send_credentials.assert_awaited_once()


async def test_consumer_does_not_crash_on_adapter_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task exception is caught internally — broker loop sees a clean return (T-3-10)."""
    adapter = FakeDeploymentAdapter(fail_on={"create"}, fail_count=999)  # always fails
    clock = FakeClock()
    settings = _test_settings()
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    spec = _test_spec()
    instance = _make_instance()
    task = _make_task(instance.id, spec)

    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    call_count = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_count
        mock_result = MagicMock()
        call_count += 1
        if call_count <= 2:
            mock_result.scalar_one_or_none.return_value = instance if call_count == 1 else task
        else:
            mock_result.scalar_one_or_none.return_value = task
        return mock_result

    session.execute = AsyncMock(side_effect=_execute)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks_mod, "session_scope", _scope)

    # Must not raise — create_instance_task catches all exceptions (T-3-10).
    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        result = await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            AsyncMock(),
            clock,
            service,
        )

    assert result is None


# ---------------------------------------------------------------------------
# Integration tests — real Postgres via pg_session fixture
# ---------------------------------------------------------------------------


def _patch_task_session_scope(monkeypatch: pytest.MonkeyPatch, session):
    """Route tasks module session_scope() calls to the test session."""

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(tasks_mod, "session_scope", _scope)


@pytest.mark.integration
async def test_create_path_succeeds_integration(
    pg_session,
    spy_console_transport: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PROV-02: full create path → instance.status == ready, instance.url set.

    Uses real Postgres rows + FakeDeploymentAdapter. session_scope in the
    task module is patched to use the test pg_session for isolation.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from provisioning_worker.events.subscription import SubscriptionActivatedPayload

    settings = _test_settings()
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    # Open instance + task rows directly via the service (same as the handler does).
    payload = SubscriptionActivatedPayload(
        subscription_id=UUID("018efa2c-0000-7000-8000-100000000010"),
        customer_id=UUID("018efa2c-0000-7000-8000-200000000010"),
        quote_id=UUID("018efa2c-0000-7000-8000-300000000010"),
        stripe_subscription_id="sub_integ_001",
        billing_cycle="monthly",
        currency="USD",
        line_count=2,
        total_amount=Decimal("99.00"),
        activated_at=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )
    instance, task = await service.open_instance(
        payload, pg_session, settings, source_event_id="01INTEG00001234567890123AB"
    )
    await pg_session.commit()

    _patch_task_session_scope(monkeypatch, pg_session)

    adapter = FakeDeploymentAdapter()
    clock = FakeClock()

    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            spy_console_transport,
            clock,
            service,
        )

    # Re-query to get the committed state.
    from sqlalchemy import select

    from provisioning_worker.modules.provisioning.models import Instance as InstanceModel

    result = await pg_session.execute(
        select(InstanceModel).where(InstanceModel.id == instance.id)
    )
    persisted = result.scalar_one_or_none()
    assert persisted is not None
    assert persisted.status == InstanceStatus.ready
    assert persisted.url is not None
    assert persisted.url.startswith("https://")

    spy_console_transport.send_credentials.assert_awaited_once()


@pytest.mark.integration
async def test_enforcement_snapshot_written(
    pg_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SNAP-01: enforcement_snapshot row written at configuring with version=1.

    After create_instance_task completes, there must be exactly one
    EnforcementSnapshot row for the instance with version=1, and
    instance.snapshot_version must equal 1.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import select

    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.modules.provisioning.models import (
        EnforcementSnapshot,
        Instance as InstanceModel,
    )

    settings = _test_settings()
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    payload = SubscriptionActivatedPayload(
        subscription_id=UUID("018efa2c-0000-7000-8000-100000000011"),
        customer_id=UUID("018efa2c-0000-7000-8000-200000000011"),
        quote_id=UUID("018efa2c-0000-7000-8000-300000000011"),
        stripe_subscription_id="sub_integ_002",
        billing_cycle="monthly",
        currency="USD",
        line_count=2,
        total_amount=Decimal("99.00"),
        activated_at=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )
    instance, task = await service.open_instance(
        payload, pg_session, settings, source_event_id="01INTEG00001234567890124AB"
    )
    await pg_session.commit()

    _patch_task_session_scope(monkeypatch, pg_session)

    adapter = FakeDeploymentAdapter()
    clock = FakeClock()

    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            AsyncMock(),  # transport — not checking credentials here
            clock,
            service,
        )

    # Check enforcement_snapshot row.
    snap_result = await pg_session.execute(
        select(EnforcementSnapshot).where(EnforcementSnapshot.instance_id == instance.id)
    )
    snapshot = snap_result.scalar_one_or_none()
    assert snapshot is not None, "EnforcementSnapshot row must exist after create task"
    assert snapshot.version == 1
    assert snapshot.seat_cap == settings.provisioning_default_seat_cap

    # Check instance.snapshot_version.
    inst_result = await pg_session.execute(
        select(InstanceModel).where(InstanceModel.id == instance.id)
    )
    persisted_instance = inst_result.scalar_one_or_none()
    assert persisted_instance is not None
    assert persisted_instance.snapshot_version == 1


@pytest.mark.integration
async def test_create_fails_then_retries(
    pg_session,
    monkeypatch: pytest.MonkeyPatch,
    spy_console_transport: AsyncMock,
) -> None:
    """PROV-04 canonical proof: fault injection → failed → retry → ready.

    FakeDeploymentAdapter(fail_on={"create"}, fail_count=1) ensures the first
    create_instance_task call fails with DeploymentFailed. After the failure:
    - instance.status == failed
    - provisioning_task.attempt_count == 1
    - task.last_error is not None

    The task re-kicks itself; the second invocation succeeds:
    - instance.status == ready (PROV-04)
    - send_credentials called exactly once (PROV-08)
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import select

    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.modules.provisioning.models import (
        Instance as InstanceModel,
        ProvisioningTask as TaskModel,
    )

    settings = _test_settings()
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    payload = SubscriptionActivatedPayload(
        subscription_id=UUID("018efa2c-0000-7000-8000-100000000012"),
        customer_id=UUID("018efa2c-0000-7000-8000-200000000012"),
        quote_id=UUID("018efa2c-0000-7000-8000-300000000012"),
        stripe_subscription_id="sub_integ_003",
        billing_cycle="monthly",
        currency="USD",
        line_count=2,
        total_amount=Decimal("99.00"),
        activated_at=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )
    instance, task = await service.open_instance(
        payload, pg_session, settings, source_event_id="01INTEG00001234567890125AB"
    )
    await pg_session.commit()

    _patch_task_session_scope(monkeypatch, pg_session)

    adapter = FakeDeploymentAdapter(fail_on={"create"}, fail_count=1)
    clock = FakeClock()

    # Capture re-kicks to call the task a second time manually.
    rekicked_args: list[tuple[str, str]] = []

    async def _capture_kiq(instance_id_arg: str, task_id_arg: str) -> None:
        rekicked_args.append((instance_id_arg, task_id_arg))

    with patch.object(create_instance_task, "kiq", new=_capture_kiq):
        # First invocation — will fail, record failure, call kiq for retry.
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            spy_console_transport,
            clock,
            service,
        )

    # After first failure: instance must be 'failed'.
    inst_result = await pg_session.execute(
        select(InstanceModel).where(InstanceModel.id == instance.id)
    )
    failed_instance = inst_result.scalar_one_or_none()
    assert failed_instance is not None
    assert failed_instance.status == InstanceStatus.failed, (
        f"Expected 'failed' after first attempt, got '{failed_instance.status}'"
    )

    task_result = await pg_session.execute(
        select(TaskModel).where(TaskModel.id == task.id)
    )
    failed_task = task_result.scalar_one_or_none()
    assert failed_task is not None
    assert failed_task.attempt_count == 1
    assert failed_task.last_error is not None

    # Confirm re-kick was scheduled.
    assert len(rekicked_args) == 1, "Task must re-kick itself after failure"

    # Second invocation — adapter succeeds now (fail_count exhausted).
    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            spy_console_transport,
            clock,
            service,
        )

    inst_result2 = await pg_session.execute(
        select(InstanceModel).where(InstanceModel.id == instance.id)
    )
    ready_instance = inst_result2.scalar_one_or_none()
    assert ready_instance is not None
    assert ready_instance.status == InstanceStatus.ready, (
        f"Expected 'ready' after retry, got '{ready_instance.status}'"
    )

    # PROV-08: credentials delivered exactly once total (not twice).
    spy_console_transport.send_credentials.assert_awaited_once()


@pytest.mark.integration
async def test_no_credential_resend_on_retry(
    pg_session,
    monkeypatch: pytest.MonkeyPatch,
    spy_console_transport: AsyncMock,
) -> None:
    """PROV-08: re-converging to ready when ready_at is already set does NOT resend creds.

    This test verifies the ready_at IS NULL guard (D-13, T-3-09): if the
    instance already reached ready and somehow the task runs again, credentials
    must NOT be sent a second time.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from sqlalchemy import select

    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.modules.provisioning.models import Instance as InstanceModel

    settings = _test_settings()
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    payload = SubscriptionActivatedPayload(
        subscription_id=UUID("018efa2c-0000-7000-8000-100000000013"),
        customer_id=UUID("018efa2c-0000-7000-8000-200000000013"),
        quote_id=UUID("018efa2c-0000-7000-8000-300000000013"),
        stripe_subscription_id="sub_integ_004",
        billing_cycle="monthly",
        currency="USD",
        line_count=2,
        total_amount=Decimal("99.00"),
        activated_at=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )
    instance, task = await service.open_instance(
        payload, pg_session, settings, source_event_id="01INTEG00001234567890126AB"
    )
    await pg_session.commit()

    _patch_task_session_scope(monkeypatch, pg_session)

    adapter = FakeDeploymentAdapter()
    clock = FakeClock()

    # First successful run: credentials sent once.
    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            spy_console_transport,
            clock,
            service,
        )

    spy_console_transport.send_credentials.assert_awaited_once()

    # Set instance back to 'configuring' status to simulate a re-run scenario
    # while ready_at is already set (the guard must prevent re-delivery).
    inst_result = await pg_session.execute(
        select(InstanceModel).where(InstanceModel.id == instance.id)
    )
    ready_instance = inst_result.scalar_one_or_none()
    assert ready_instance is not None
    assert ready_instance.ready_at is not None, "ready_at must be set after first run"

    # Force instance back to configuring so the task can run again.
    # The ready_at field stays set — this simulates the retry guard scenario.
    ready_instance.status = InstanceStatus.configuring
    await pg_session.commit()

    # Second run with ready_at already set — should NOT call send_credentials again.
    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            adapter,
            spy_console_transport,
            clock,
            service,
        )

    # Still exactly one credential delivery total (not two).
    assert spy_console_transport.send_credentials.await_count == 1, (
        "send_credentials must be called exactly once even on re-run when ready_at is set"
    )
