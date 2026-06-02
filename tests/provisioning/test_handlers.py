"""Unit tests for the ``subscription.*`` handlers.

Docker-free: handlers are tested with mocked sessions and services.
After Phase 3, ``handle_subscription_activated`` has a real body that
opens instance + task rows and registers a post-commit enqueue.
The other four handlers remain no-ops.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

import provisioning_worker.modules.provisioning.handlers as handlers_mod
from provisioning_worker.modules.provisioning.handlers import (
    handle_subscription_activated,
    handle_subscription_cancelled,
    handle_subscription_lines_changed,
    handle_subscription_reinstated,
    handle_subscription_suspended,
)
from provisioning_worker.modules.provisioning.models import (
    Instance,
    ProvisioningTask,
)

_NO_OP_HANDLERS = [
    handle_subscription_lines_changed,
    handle_subscription_suspended,
    handle_subscription_reinstated,
    handle_subscription_cancelled,
]


def _mock_env() -> MagicMock:
    return MagicMock(id="01JZQABCDE12345678901234AB", correlation_id=None)


def _mock_payload() -> MagicMock:
    return MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")


@pytest.mark.parametrize("handler", _NO_OP_HANDLERS)
async def test_no_op_handler_returns_none_no_db_writes(handler) -> None:
    """Each no-op handler returns None and performs no DB writes."""
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

    # Patch service.open_instance to avoid real DB work in unit test.
    mock_instance = MagicMock(spec=Instance)
    mock_instance.id = UUID("018efa2c-0000-7000-8000-000000000003")
    mock_task = MagicMock(spec=ProvisioningTask)
    mock_task.id = UUID("018efa2c-0000-7000-8000-000000000004")

    with patch.object(
        handlers_mod._service,
        "open_instance",
        new=AsyncMock(return_value=(mock_instance, mock_task)),
    ):
        await handle_subscription_activated(env, payload, AsyncMock())

    assert bound["envelope_id"] == "01JZQABCDE12345678901234AB"
    assert bound["subscription_id"] == "018efa2c-0000-7000-8000-000000000001"
    assert bound["correlation_id"] == "corr-123"


async def test_activated_binds_instance_id_after_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handle_subscription_activated binds instance_id after opening the row."""
    bound: dict[str, object] = {}

    def _capture(**kwargs: object) -> None:
        bound.update(kwargs)

    monkeypatch.setattr(handlers_mod.structlog.contextvars, "bind_contextvars", _capture)

    env = MagicMock(id="01JZQABCDE12345678901234AB", correlation_id=None)
    payload = MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")

    mock_instance = MagicMock(spec=Instance)
    mock_instance.id = UUID("018efa2c-0000-7000-8000-000000000005")
    mock_task = MagicMock(spec=ProvisioningTask)
    mock_task.id = UUID("018efa2c-0000-7000-8000-000000000006")

    with patch.object(
        handlers_mod._service,
        "open_instance",
        new=AsyncMock(return_value=(mock_instance, mock_task)),
    ):
        await handle_subscription_activated(env, payload, AsyncMock())

    assert bound["instance_id"] == str(mock_instance.id)


async def test_activated_calls_open_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_subscription_activated calls service.open_instance with the payload."""
    monkeypatch.setattr(
        handlers_mod.structlog.contextvars, "bind_contextvars", lambda **kw: None
    )

    env = MagicMock(id="01JZQABCDE12345678901234AB", correlation_id=None)
    payload = MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")

    mock_instance = MagicMock(spec=Instance)
    mock_instance.id = UUID("018efa2c-0000-7000-8000-000000000005")
    mock_task = MagicMock(spec=ProvisioningTask)
    mock_task.id = UUID("018efa2c-0000-7000-8000-000000000006")

    with patch.object(
        handlers_mod._service,
        "open_instance",
        new=AsyncMock(return_value=(mock_instance, mock_task)),
    ) as mock_open:
        session = AsyncMock()
        await handle_subscription_activated(env, payload, session)

    mock_open.assert_awaited_once()
    call_args = mock_open.call_args
    assert call_args.args[0] is payload
    assert call_args.args[1] is session
    assert call_args.kwargs["source_event_id"] == "01JZQABCDE12345678901234AB"


async def test_activated_registers_post_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_subscription_activated registers a post-commit callback (not inline kiq)."""
    monkeypatch.setattr(
        handlers_mod.structlog.contextvars, "bind_contextvars", lambda **kw: None
    )

    env = MagicMock(id="01JZQABCDE12345678901234AB", correlation_id=None)
    payload = MagicMock(subscription_id="018efa2c-0000-7000-8000-000000000001")

    mock_instance = MagicMock(spec=Instance)
    mock_instance.id = UUID("018efa2c-0000-7000-8000-000000000005")
    mock_task = MagicMock(spec=ProvisioningTask)
    mock_task.id = UUID("018efa2c-0000-7000-8000-000000000006")

    registered_callbacks: list = []

    with (
        patch.object(
            handlers_mod._service,
            "open_instance",
            new=AsyncMock(return_value=(mock_instance, mock_task)),
        ),
        patch.object(
            handlers_mod,
            "register_post_commit",
            side_effect=lambda cb: registered_callbacks.append(cb),
        ),
        patch.object(
            handlers_mod.create_instance_task,
            "kiq",
            new=AsyncMock(),
        ) as mock_kiq,
    ):
        session = AsyncMock()
        await handle_subscription_activated(env, payload, session)

        # post-commit callback was registered, NOT called inline
        assert len(registered_callbacks) == 1
        mock_kiq.assert_not_awaited()

        # when the callback fires (post-commit), it should call kiq
        await registered_callbacks[0]()
        mock_kiq.assert_awaited_once_with(
            str(mock_instance.id), str(mock_task.id)
        )
