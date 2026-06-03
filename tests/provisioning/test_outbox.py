"""Tests for the outbox → relay vertical slice (EVT-01 and EVT-02).

Test structure:
- Unit tests (Docker-free, ``-m "not integration"``): mock session_factory and
  bus to test drain logic in isolation.
- Integration tests (``@pytest.mark.integration``, testcontainers Postgres + Valkey):
  require real DB and Valkey containers for same-txn and round-trip guarantees.

Requirements covered:
- EVT-01: provisioning.event_outbox + relay publishes instance.* envelopes
- EVT-02: instance.provisioned payload shape, hostname derivation, no-duplicate-emit
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
import redis.asyncio as aioredis

from provisioning_worker.events.envelope import EventEnvelope
from provisioning_worker.events.instance import InstanceProvisionedPayload
from provisioning_worker.infrastructure.outbox_relay import _drain_once
from provisioning_worker.modules.provisioning.models import EventOutbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provisioned_envelope() -> EventEnvelope[InstanceProvisionedPayload]:
    """Build a realistic instance.provisioned envelope for tests."""
    payload = InstanceProvisionedPayload(
        instance_id=UUID("018efa2c-0000-7000-8000-000000000001"),
        subscription_id=UUID("018efa2c-0000-7000-8000-000000000002"),
        customer_id=UUID("018efa2c-0000-7000-8000-000000000003"),
        hostname="test-slug.example.local",
        url="https://test-slug.example.local",
        admin_email="admin@example.com",
        snapshot_version=1,
        provisioned_at=datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC),
    )
    return EventEnvelope.build(
        type="instance.provisioned",
        version=1,
        payload=payload,
        causation_id="01JWAAAAAAAAAAAAAAAAAAABBB",
    )


def _make_mock_settings(batch_size: int = 50) -> MagicMock:
    """Return a mock settings with outbox_batch_size."""
    settings = MagicMock()
    settings.outbox_batch_size = batch_size
    return settings


def _make_mock_outbox_row(envelope: EventEnvelope) -> MagicMock:
    """Return a mock EventOutbox row for unit tests."""
    row = MagicMock(spec=EventOutbox)
    row.envelope_id = envelope.id
    row.envelope_type = envelope.type
    row.payload = json.loads(envelope.model_dump_json())
    row.sent_at = None
    row.last_error = None
    row.attempt_count = 0
    return row


def _make_mock_session_factory(rows: list[MagicMock]) -> MagicMock:
    """Return a mock async_sessionmaker that yields a session containing ``rows``."""
    session = MagicMock()
    session.commit = AsyncMock()

    scalars_mock = MagicMock()
    scalars_mock.all.return_value = rows

    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_mock

    session.execute = AsyncMock(return_value=execute_result)

    @asynccontextmanager
    async def _factory():
        yield session

    factory = MagicMock()
    factory.return_value = _factory()
    return factory


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


async def test_drain_once_marks_sent() -> None:
    """EVT-01: _drain_once sets sent_at and calls bus.publish once.

    Setup: a mock session_factory yielding a session with one unsent EventOutbox
    row, and an AsyncMock bus.publish. After calling _drain_once, the row's
    sent_at must be non-None and bus.publish must have been called exactly once.
    """
    envelope = _make_provisioned_envelope()
    row = _make_mock_outbox_row(envelope)
    factory = _make_mock_session_factory([row])
    bus = MagicMock()
    bus.publish = AsyncMock()
    settings = _make_mock_settings()

    count = await _drain_once(settings, factory, bus)

    assert count == 1
    assert row.sent_at is not None, "sent_at must be set after successful publish"
    bus.publish.assert_awaited_once()


async def test_drain_once_records_failure() -> None:
    """EVT-01: _drain_once records last_error and bumps attempt_count on publish failure.

    Setup: mock bus.publish raises redis.RedisError. After _drain_once:
    - row.last_error must be a non-empty string truncated to <= 2000 chars
    - row.attempt_count must equal 1
    - row.sent_at must remain None
    """
    envelope = _make_provisioned_envelope()
    row = _make_mock_outbox_row(envelope)
    factory = _make_mock_session_factory([row])
    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=aioredis.RedisError("connection timeout"))
    settings = _make_mock_settings()

    count = await _drain_once(settings, factory, bus)

    assert count == 1
    assert row.sent_at is None, "sent_at must remain None after publish failure"
    assert row.last_error is not None and len(row.last_error) > 0, (
        "last_error must be non-empty after failure"
    )
    assert len(row.last_error) <= 2000, "last_error must be truncated to <= 2000 chars"
    assert row.attempt_count == 1, "attempt_count must be incremented from 0 to 1"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_enqueue_idempotent(pg_session) -> None:
    """EVT-01: OutboxRepo.enqueue called twice with same envelope ULID inserts exactly 1 row.

    Uses ON CONFLICT DO NOTHING on envelope_id to enforce idempotency (D-02).
    Requires real Postgres for the unique constraint round-trip.
    """
    from sqlalchemy import select, func

    from provisioning_worker.modules.provisioning.repository import OutboxRepo

    envelope = _make_provisioned_envelope()
    repo = OutboxRepo(pg_session)

    # First enqueue
    await repo.enqueue(envelope)
    # Second enqueue with same envelope.id — must be no-op
    await repo.enqueue(envelope)
    await pg_session.commit()

    # Count rows with this envelope_id
    result = await pg_session.execute(
        select(func.count()).where(EventOutbox.envelope_id == envelope.id)
    )
    count = result.scalar_one()
    assert count == 1, f"ON CONFLICT DO NOTHING must insert exactly 1 row, got {count}"


@pytest.mark.integration
async def test_outbox_row_written_atomically(
    pg_session,
    pg_engine,
    monkeypatch,
) -> None:
    """EVT-01: outbox row is written in the same transaction as the ready transition.

    Runs create_instance_task on an InMemoryBroker backed by a real pg_session.
    After task completion:
    - instance.status == ready
    - event_outbox has exactly 1 row with envelope_type='instance.provisioned'
    - row.sent_at IS NULL (row written; relay has not yet published it)

    This test proves the D-01 atomicity guarantee: "instance reached ready" and
    "instance.provisioned enqueued" commit or roll back together.
    """
    from contextlib import asynccontextmanager
    from decimal import Decimal
    from unittest.mock import patch

    from sqlalchemy import select

    import provisioning_worker.modules.provisioning.tasks as tasks_mod
    from provisioning_worker.adapters.fake_deployment import FakeDeploymentAdapter
    from provisioning_worker.adapters.m1_entitlement_resolver import DefaultEntitlementResolver
    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.modules.provisioning.models import Instance, InstanceStatus
    from provisioning_worker.modules.provisioning.service import ProvisioningService
    from provisioning_worker.modules.provisioning.tasks import create_instance_task
    from provisioning_worker.ports.clock import FakeClock
    from provisioning_worker.settings import Settings

    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost:5432/test",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://test:test@localhost:5432/test",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )
    service = ProvisioningService(entitlement_resolver=DefaultEntitlementResolver())

    payload = SubscriptionActivatedPayload(
        subscription_id=UUID("018efa2c-0000-7000-8000-100000000020"),
        customer_id=UUID("018efa2c-0000-7000-8000-200000000020"),
        quote_id=UUID("018efa2c-0000-7000-8000-300000000020"),
        stripe_subscription_id="sub_outbox_atomicity_01",
        billing_cycle="monthly",
        currency="USD",
        line_count=1,
        total_amount=Decimal("99.00"),
        activated_at=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_start=datetime(2026, 6, 1, tzinfo=UTC),
        current_period_end=datetime(2026, 7, 1, tzinfo=UTC),
    )
    instance, task = await service.open_instance(
        payload, pg_session, settings, source_event_id="01OUTBOXATOMICTEST00000001"
    )
    await pg_session.commit()

    # Patch session_scope in tasks module to use the test pg_session so the
    # task's real DB operations commit to the testcontainers Postgres instance.
    @asynccontextmanager
    async def _scope():
        yield pg_session

    monkeypatch.setattr(tasks_mod, "session_scope", _scope)

    # Call create_instance_task directly with all explicit arguments — no Taskiq
    # DI needed since we're not calling via kiq.
    with patch.object(create_instance_task, "kiq", new=AsyncMock()):
        await create_instance_task(
            str(instance.id),
            str(task.id),
            settings,
            FakeDeploymentAdapter(),
            AsyncMock(),
            FakeClock(),
            service,
        )

    # Verify instance reached ready.
    inst_result = await pg_session.execute(
        select(Instance).where(Instance.id == instance.id)
    )
    persisted = inst_result.scalar_one_or_none()
    assert persisted is not None
    assert persisted.status == InstanceStatus.ready, (
        f"Expected status=ready, got {persisted.status}"
    )

    # Verify exactly 1 outbox row with correct type and sent_at IS NULL.
    outbox_result = await pg_session.execute(
        select(EventOutbox).where(
            EventOutbox.envelope_type == "instance.provisioned"
        )
    )
    outbox_rows = outbox_result.scalars().all()
    assert len(outbox_rows) == 1, (
        f"Expected exactly 1 outbox row, got {len(outbox_rows)}"
    )
    outbox_row = outbox_rows[0]
    assert outbox_row.sent_at is None, (
        "sent_at must be NULL — relay has not yet published the row"
    )
    assert outbox_row.envelope_type == "instance.provisioned"


@pytest.mark.integration
async def test_relay_xadd_roundtrip(pg_session, pg_engine, async_redis_client) -> None:
    """EVT-01: after _drain_once with a real ValkeyStreamsBus, event appears on events.instance.

    Requires both a Postgres container (pg_session) and a Valkey container
    (async_redis_client). After _drain_once:
    - The outbox row's sent_at is set.
    - events.instance stream has one entry with producer='provisioning-worker'.
    - The envelope causation_id matches the triggering subscription.activated id.
    """
    from decimal import Decimal
    from contextlib import asynccontextmanager

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from provisioning_worker.adapters.valkey_streams_bus import ValkeyStreamsBus
    from provisioning_worker.modules.provisioning.repository import OutboxRepo
    from provisioning_worker.settings import Settings

    # Build settings pointing at the real Valkey container.
    # Extract host/port from the live async_redis_client connection pool.
    conn_kwargs = async_redis_client.connection_pool.connection_kwargs
    host = conn_kwargs.get("host", "localhost")
    port = conn_kwargs.get("port", 6379)
    db = conn_kwargs.get("db", 0)
    valkey_url_str = f"redis://{host}:{port}/{db}"

    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost:5432/test",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://test:test@localhost:5432/test",  # type: ignore[arg-type]
        valkey_url=valkey_url_str,  # type: ignore[arg-type]
        outbox_poll_seconds=1.0,
        outbox_batch_size=50,
    )

    # Insert one outbox row directly via OutboxRepo.
    causation_id = "01JWXADD0000000000000RELAY"
    envelope = EventEnvelope.build(
        type="instance.provisioned",
        version=1,
        payload=InstanceProvisionedPayload(
            instance_id=UUID("018efa2c-0000-7000-8000-000000000099"),
            subscription_id=UUID("018efa2c-0000-7000-8000-000000000098"),
            customer_id=UUID("018efa2c-0000-7000-8000-000000000097"),
            hostname="relay-test.example.local",
            url="https://relay-test.example.local",
            admin_email="relay@example.com",
            snapshot_version=1,
            provisioned_at=datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC),
        ),
        causation_id=causation_id,
    )
    repo = OutboxRepo(pg_session)
    await repo.enqueue(envelope)
    await pg_session.commit()

    # Verify row is unsent before relay.
    pre_result = await pg_session.execute(
        select(EventOutbox).where(EventOutbox.envelope_id == envelope.id)
    )
    pre_row = pre_result.scalar_one_or_none()
    assert pre_row is not None
    assert pre_row.sent_at is None

    # Run _drain_once with the real ValkeyStreamsBus.
    bus = ValkeyStreamsBus(settings)
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    try:
        count = await _drain_once(settings, session_factory, bus)
    finally:
        await bus.close()

    assert count == 1, "drain_once must have processed 1 row"

    # Verify the outbox row is now marked sent.
    await pg_session.refresh(pre_row)
    assert pre_row.sent_at is not None, "sent_at must be set after successful relay"

    # Verify the event landed on events.instance.
    messages = await async_redis_client.xrange("events.instance", "-", "+")
    assert len(messages) >= 1, "events.instance must have at least one entry after drain"

    # The most recent message must carry the correct producer and causation_id.
    _, fields = messages[-1]
    raw_envelope = fields.get(b"envelope")
    assert raw_envelope is not None, "event must have an 'envelope' field"
    published = json.loads(raw_envelope.decode("utf-8"))
    assert published["producer"] == "provisioning-worker", (
        f"producer must be 'provisioning-worker', got {published['producer']!r}"
    )
    assert published["causation_id"] == causation_id, (
        f"causation_id mismatch: expected {causation_id!r}, got {published['causation_id']!r}"
    )
    assert published["type"] == "instance.provisioned"
