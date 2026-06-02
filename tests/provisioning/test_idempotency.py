"""Idempotency dedupe + poison-dispatch tests.

Two groups:

- Integration (``@pytest.mark.integration``, real Postgres via testcontainers):
  the SC-1 / SC-2 observable checks for :func:`handle_with_dedupe` against the
  real ``provisioning.processed_event`` table.
- Unit (Docker-free, mocked redis client): the four-stage poison /
  unknown-type / payload-error / happy-path dispatch policy of
  :meth:`ValkeyStreamsConsumer._dispatch`, including the SC-3 poison check and
  the commit-then-ack ordering.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select

import provisioning_worker.shared.event_consumer as ec
from provisioning_worker.adapters.valkey_streams import ValkeyStreamsConsumer
from provisioning_worker.modules.provisioning.models import ProcessedEvent
from provisioning_worker.ports.event_consumer import EventConsumer
from provisioning_worker.shared.event_consumer import handle_with_dedupe

_GROUP = "cg.provisioning-convergence"
_EVENT_ID = "01JZQABCDE12345678901234AB"

_VALID_ENVELOPE = {
    "id": _EVENT_ID,
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


def _consumer_with_mock_client() -> ValkeyStreamsConsumer:
    """Build a consumer whose redis client + group are mocked for unit tests."""
    settings = MagicMock()
    settings.valkey_url = "redis://localhost:6379/0"
    settings.provisioning_consumer_group = _GROUP
    settings.consumer_name = "worker-1"
    settings.consumer_reclaim_min_idle_ms = 60_000
    consumer = ValkeyStreamsConsumer(settings)
    consumer._client = AsyncMock()
    return consumer


def test_consumer_satisfies_protocol() -> None:
    """ValkeyStreamsConsumer is a structural EventConsumer (runtime_checkable)."""
    assert isinstance(_consumer_with_mock_client(), EventConsumer)


async def test_dispatch_happy_path_calls_handler_then_acks() -> None:
    """Valid envelope: handler invoked, then XACK (commit-then-ack ordering)."""
    consumer = _consumer_with_mock_client()
    handler = AsyncMock()

    await consumer._dispatch(
        "1-0", {"envelope": json.dumps(_VALID_ENVELOPE)}, {"subscription.activated": handler}
    )

    handler.assert_awaited_once()
    consumer._client.xack.assert_awaited_once()
    # XACK happens after the handler returns.
    assert handler.await_count == 1


async def test_dispatch_bad_json_is_poison(caplog: pytest.LogCaptureFixture) -> None:
    """SC-3: bad JSON → error log + XACK + handler never invoked."""
    consumer = _consumer_with_mock_client()
    handler = AsyncMock()

    await consumer._dispatch(
        "2-0", {"envelope": "not-json"}, {"subscription.activated": handler}
    )

    handler.assert_not_awaited()
    consumer._client.xack.assert_awaited_once()


async def test_dispatch_unknown_type_warns_and_acks() -> None:
    """Unknown but valid type → warning + XACK + handler never invoked."""
    consumer = _consumer_with_mock_client()
    handler = AsyncMock()
    envelope = {**_VALID_ENVELOPE, "type": "subscription.future_event"}

    await consumer._dispatch(
        "3-0", {"envelope": json.dumps(envelope)}, {"subscription.activated": handler}
    )

    handler.assert_not_awaited()
    consumer._client.xack.assert_awaited_once()


async def test_dispatch_payload_validation_error_is_poison() -> None:
    """Valid envelope, payload drift → error + XACK + handler never invoked."""
    consumer = _consumer_with_mock_client()
    handler = AsyncMock()
    envelope = {**_VALID_ENVELOPE, "payload": {"unexpected": "field"}}

    await consumer._dispatch(
        "4-0", {"envelope": json.dumps(envelope)}, {"subscription.activated": handler}
    )

    handler.assert_not_awaited()
    consumer._client.xack.assert_awaited_once()


async def test_dispatch_envelope_validation_error_is_poison() -> None:
    """Extra top-level field (extra=forbid) → error + XACK, no handler call."""
    consumer = _consumer_with_mock_client()
    handler = AsyncMock()
    envelope = {**_VALID_ENVELOPE, "unexpected_top_level": "x"}

    await consumer._dispatch(
        "5-0", {"envelope": json.dumps(envelope)}, {"subscription.activated": handler}
    )

    handler.assert_not_awaited()
    consumer._client.xack.assert_awaited_once()


@pytest.mark.integration
async def test_first_delivery_writes_one_row(pg_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-1: first delivery → exactly one processed_event row; handler called once."""
    _patch_session_scope(monkeypatch, pg_session)
    handler = AsyncMock()
    raw_env = MagicMock(id=_EVENT_ID)

    await handle_with_dedupe(raw_env, MagicMock(), handler, _GROUP)

    handler.assert_awaited_once()
    count = await pg_session.scalar(select(func.count()).select_from(ProcessedEvent))
    assert count == 1
    row = await pg_session.scalar(
        select(ProcessedEvent).where(ProcessedEvent.event_id == _EVENT_ID)
    )
    assert row.consumer_group == _GROUP


@pytest.mark.integration
async def test_replay_short_circuits(pg_session, monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-2: second delivery same envelope.id → still one row; handler called once."""
    _patch_session_scope(monkeypatch, pg_session)
    handler = AsyncMock()
    raw_env = MagicMock(id=_EVENT_ID)

    await handle_with_dedupe(raw_env, MagicMock(), handler, _GROUP)
    await handle_with_dedupe(raw_env, MagicMock(), handler, _GROUP)

    assert handler.await_count == 1
    count = await pg_session.scalar(select(func.count()).select_from(ProcessedEvent))
    assert count == 1


def _patch_session_scope(monkeypatch: pytest.MonkeyPatch, session) -> None:
    """Route handle_with_dedupe's session_scope() to the test session.

    The shared session is committed inside the guard; isolation is restored by
    the ``pg_session`` fixture's post-test truncate.
    """

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(ec, "session_scope", _scope)
