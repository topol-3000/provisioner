"""Test stubs for the outbox → relay vertical slice (EVT-01 and EVT-02).

These tests are the RED phase of TDD for Phase 4. Each test is marked with
``pytest.skip`` so the unit suite exits 0 now and the stubs are fulfilled by
Plan 02 when the implementation lands.

Test structure:
- Unit tests (Docker-free, ``-m "not integration"``): mock session_factory and
  bus to test drain logic in isolation.
- Integration tests (``@pytest.mark.integration``, testcontainers Postgres + Redis):
  require real DB and Valkey containers for same-txn and round-trip guarantees.

Requirements covered:
- EVT-01: provisioning.event_outbox + relay publishes instance.* envelopes
- EVT-02: instance.provisioned payload shape, hostname derivation, no-duplicate-emit
"""

import pytest

# --- Unit tests ---


async def test_drain_once_marks_sent() -> None:
    """EVT-01: _drain_once sets sent_at and calls bus.publish once.

    Setup: a mock session_factory yielding a session with one unsent EventOutbox
    row, and an AsyncMock bus.publish. After calling _drain_once, the row's
    sent_at must be non-None and bus.publish must have been called exactly once.
    """
    pytest.skip("EVT-01 — _drain_once not implemented yet (Plan 02)")


async def test_drain_once_records_failure() -> None:
    """EVT-01: _drain_once records last_error and bumps attempt_count on publish failure.

    Setup: mock bus.publish raises redis.RedisError. After _drain_once:
    - row.last_error must be a non-empty string truncated to <= 2000 chars
    - row.attempt_count must equal 1
    - row.sent_at must remain None
    """
    pytest.skip("EVT-01 — _drain_once failure path not implemented yet (Plan 02)")


# --- Integration tests ---


@pytest.mark.integration
async def test_enqueue_idempotent(pg_session) -> None:
    """EVT-01: OutboxRepo.enqueue called twice with same envelope ULID inserts exactly 1 row.

    Uses ON CONFLICT DO NOTHING on envelope_id to enforce idempotency (D-02).
    Requires real Postgres for the unique constraint round-trip.
    """
    pytest.skip("EVT-01 — OutboxRepo.enqueue not implemented yet (Plan 02)")


@pytest.mark.integration
async def test_outbox_row_written_atomically(
    pg_session,
    monkeypatch,
) -> None:
    """EVT-01: outbox row is written in the same transaction as the ready transition.

    Runs create_instance_task on an in_memory_broker backed by a real pg_session.
    After task completion:
    - instance.status == ready
    - event_outbox has exactly 1 row with envelope_type='instance.provisioned'
    - row.sent_at IS NULL (row written; relay has not yet published it)

    This test proves the D-01 atomicity guarantee: "instance reached ready" and
    "instance.provisioned enqueued" commit or roll back together.
    """
    pytest.skip("EVT-01 — emit_instance_provisioned not implemented yet (Plan 02)")


@pytest.mark.integration
async def test_relay_xadd_roundtrip(pg_session) -> None:
    """EVT-01: after _drain_once with a real ValkeyStreamsBus, event appears on events.instance.

    Requires both a Postgres container (pg_session fixture) and a Valkey/Redis
    container (constructed inline). After _drain_once:
    - The outbox row's sent_at is set.
    - events.instance stream has one entry with producer='provisioning-worker'.
    """
    pytest.skip(
        "EVT-01 — relay + ValkeyStreamsBus integration not implemented yet (Plan 02); "
        "requires @pytest.mark.integration + testcontainers[redis]"
    )
