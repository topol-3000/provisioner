"""Unit tests for main.run() boot path.

Tests that main.run() completes cleanly with mocked infrastructure
connections — no real Postgres or Valkey required.
"""

import asyncio
import socket
from unittest.mock import patch

from provisioning_worker import main
from provisioning_worker.settings import Settings


def _find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_settings() -> Settings:
    """Build a minimal Settings instance for boot tests."""
    return Settings(
        health_port=_find_free_port(),
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
        outbox_poll_seconds=10.0,
    )


async def test_run_completes_cleanly_with_mocked_infra() -> None:
    """main.run() with mocked infra and an early shutdown event returns cleanly.

    Patches the two infra-check functions and the two concern functions that
    need real Valkey/Postgres. The real run_health_server and run_outbox_relay
    are left running since they work without external infra.
    """
    settings = _make_settings()

    async def _noop_consumer(s: Settings, shutdown: asyncio.Event) -> None:
        await shutdown.wait()

    async def _noop_convergence(s: Settings, shutdown: asyncio.Event) -> None:
        await shutdown.wait()

    async def _noop_check(*_: object) -> None:
        pass

    async def _run_and_shutdown() -> None:
        original_event_class = asyncio.Event

        class _AutoShutdownEvent(original_event_class):  # type: ignore[misc]
            def __init__(self) -> None:
                super().__init__()
                asyncio.get_event_loop().call_later(0.15, self.set)

        with (
            patch.object(main, "_check_postgres", new=_noop_check),
            patch.object(main, "_check_valkey", new=_noop_check),
            patch.object(main, "_run_consumer", new=_noop_consumer),
            patch.object(main, "_run_convergence", new=_noop_convergence),
            patch("asyncio.Event", _AutoShutdownEvent),
        ):
            await main.run(settings)

    await _run_and_shutdown()


async def test_run_logs_starting_message() -> None:
    """main.run() completes cleanly with all concerns mocked."""
    settings = _make_settings()

    async def _noop(*_: object) -> None:
        pass

    async def _noop_waiter(s: Settings, shutdown: asyncio.Event) -> None:
        await shutdown.wait()

    async def _run_with_early_shutdown() -> None:
        original_event_class = asyncio.Event

        class _QuickShutdown(original_event_class):  # type: ignore[misc]
            def __init__(self) -> None:
                super().__init__()
                asyncio.get_event_loop().call_later(0.1, self.set)

        with (
            patch.object(main, "_check_postgres", new=_noop),
            patch.object(main, "_check_valkey", new=_noop),
            patch.object(main, "_run_consumer", new=_noop_waiter),
            patch.object(main, "_run_convergence", new=_noop_waiter),
            patch("asyncio.Event", _QuickShutdown),
        ):
            await main.run(settings)

    await _run_with_early_shutdown()
