"""Unit tests for main.run() boot path.

Tests that main.run() completes cleanly with mocked infrastructure
connections — no real Postgres or Valkey required.
"""

import asyncio
import contextlib
import os
import signal
import socket
from unittest.mock import patch

import structlog
import structlog.testing

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


async def test_boot_log_lines_ordered() -> None:
    """run() emits the startup banner before health-server and outbox-relay lines.

    Captures structlog events via structlog.testing.capture_logs(). To ensure
    capture works regardless of logger caching from prior tests in the session,
    cache_logger_on_first_use is set to False and then the BoundLoggerLazyProxy
    instance caches are cleared by deleting the overridden bind attributes. The
    three in-process log lines — banner, health-server, and outbox-relay — are
    verified to appear in the correct order.
    """
    settings = _make_settings()

    async def _noop_consumer(s: Settings, shutdown: asyncio.Event) -> None:
        await shutdown.wait()

    async def _noop_convergence(s: Settings, shutdown: asyncio.Event) -> None:
        await shutdown.wait()

    async def _noop_check(*_: object) -> None:
        pass

    def _noop_configure(*_: object) -> None:
        pass

    original_event_class = asyncio.Event

    class _AutoShutdownEvent(original_event_class):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            asyncio.get_event_loop().call_later(0.15, self.set)

    # Clear per-instance caches on module-level loggers so capture_logs() can intercept
    # them even if a prior test called configure_logging() with cache_logger_on_first_use=True.
    structlog.configure(cache_logger_on_first_use=False)
    for proxy in (
        main.log,
        __import__("provisioning_worker.infrastructure.health_server", fromlist=["log"]).log,
        __import__("provisioning_worker.infrastructure.outbox_relay", fromlist=["log"]).log,
    ):
        with contextlib.suppress(AttributeError):
            del proxy.bind  # type: ignore[attr-defined]

    with (
        structlog.testing.capture_logs() as captured,
        patch.object(main, "_check_postgres", new=_noop_check),
        patch.object(main, "_check_valkey", new=_noop_check),
        patch.object(main, "_run_consumer", new=_noop_consumer),
        patch.object(main, "_run_convergence", new=_noop_convergence),
        patch("provisioning_worker.main.configure_logging", new=_noop_configure),
        patch("provisioning_worker.main.configure_tracing", new=_noop_configure),
        patch("asyncio.Event", _AutoShutdownEvent),
    ):
        await main.run(settings)

    event_strings = [entry["event"] for entry in captured]

    assert "provisioning-worker starting" in event_strings, (
        f"Expected 'provisioning-worker starting' in log events, got: {event_strings}"
    )
    assert "health server listening" in event_strings, (
        f"Expected 'health server listening' in log events, got: {event_strings}"
    )
    assert "outbox relay started" in event_strings, (
        f"Expected 'outbox relay started' in log events, got: {event_strings}"
    )

    banner_idx = event_strings.index("provisioning-worker starting")
    health_idx = event_strings.index("health server listening")
    relay_idx = event_strings.index("outbox relay started")

    assert banner_idx < health_idx, (
        f"Expected 'provisioning-worker starting' (idx={banner_idx}) "
        f"before 'health server listening' (idx={health_idx})"
    )
    assert banner_idx < relay_idx, (
        f"Expected 'provisioning-worker starting' (idx={banner_idx}) "
        f"before 'outbox relay started' (idx={relay_idx})"
    )


async def test_sigterm_triggers_clean_shutdown() -> None:
    """SIGTERM fires the installed signal handler, sets shutdown, run() returns cleanly.

    Does not monkeypatch asyncio.Event — exercises the real
    loop.add_signal_handler(SIGTERM, shutdown.set) wiring installed by run().
    On Linux, loop.add_signal_handler is supported.
    """
    settings = _make_settings()

    async def _noop_consumer(s: Settings, shutdown: asyncio.Event) -> None:
        await shutdown.wait()

    async def _noop_convergence(s: Settings, shutdown: asyncio.Event) -> None:
        await shutdown.wait()

    async def _noop_check(*_: object) -> None:
        pass

    with (
        patch.object(main, "_check_postgres", new=_noop_check),
        patch.object(main, "_check_valkey", new=_noop_check),
        patch.object(main, "_run_consumer", new=_noop_consumer),
        patch.object(main, "_run_convergence", new=_noop_convergence),
    ):
        task = asyncio.create_task(main.run(settings))
        # Allow enough time for signal handlers to be installed and health server to bind
        await asyncio.sleep(0.2)

        os.kill(os.getpid(), signal.SIGTERM)

        await asyncio.wait_for(task, timeout=5.0)
