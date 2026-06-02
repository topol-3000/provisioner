"""Unit tests for the five no-op subscription.* handlers.

Docker-free: handlers are pure no-ops over a mocked session, so these run in
the default ``make test`` suite. The contract under test is (1) each handler is
an awaitable returning ``None``, (2) no DB writes occur (no ``execute`` / ``add``
on the session), and (3) ``subscription.activated`` binds the expected
structured-logging context.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import provisioning_worker.modules.provisioning.handlers as handlers_mod
from provisioning_worker.modules.provisioning.handlers import (
    handle_subscription_activated,
    handle_subscription_cancelled,
    handle_subscription_lines_changed,
    handle_subscription_reinstated,
    handle_subscription_suspended,
)

_ALL_HANDLERS = [
    handle_subscription_activated,
    handle_subscription_lines_changed,
    handle_subscription_suspended,
    handle_subscription_reinstated,
    handle_subscription_cancelled,
]


def _mock_env() -> MagicMock:
    return MagicMock(id="01JZQABCDE12345678901234AB", correlation_id=None)


def _mock_payload() -> MagicMock:
    return MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")


@pytest.mark.parametrize("handler", _ALL_HANDLERS)
async def test_handler_is_no_op(handler) -> None:
    """Each handler returns None and performs no DB writes."""
    session = AsyncMock()
    result = await handler(_mock_env(), _mock_payload(), session)

    assert result is None
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.commit.assert_not_called()


async def test_activated_binds_log_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_subscription_activated binds envelope/subscription/correlation ids."""
    bound: dict[str, object] = {}

    def _capture(**kwargs: object) -> None:
        bound.update(kwargs)

    monkeypatch.setattr(handlers_mod.structlog.contextvars, "bind_contextvars", _capture)

    env = MagicMock(id="01JZQABCDE12345678901234AB", correlation_id="corr-123")
    payload = MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")

    await handle_subscription_activated(env, payload, AsyncMock())

    assert bound["envelope_id"] == "01JZQABCDE12345678901234AB"
    assert bound["subscription_id"] == "018efa2c-0000-7000-8000-000000000001"
    assert bound["correlation_id"] == "corr-123"
