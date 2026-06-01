"""Smoke tests for GET /healthz.

Uses an in-process AppRunner + TCPSite to start the health server on a
free port and verifies the 200 response — no real infra required.
"""

import asyncio
import socket

import aiohttp

from provisioning_worker.infrastructure.health_server import run_health_server
from provisioning_worker.settings import Settings

_HTTP_OK = 200


def _find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_healthz_returns_200() -> None:
    """GET /healthz returns 200 with body {"status":"ok"}."""
    port = _find_free_port()
    shutdown = asyncio.Event()

    settings = Settings(
        health_port=port,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        database_url_sync="postgresql+psycopg://u:p@localhost:5432/db",  # type: ignore[arg-type]
        valkey_url="redis://localhost:6379/0",  # type: ignore[arg-type]
    )

    server_task = asyncio.create_task(run_health_server(settings, shutdown))

    # Give the server a moment to start
    await asyncio.sleep(0.1)

    try:
        async with (
            aiohttp.ClientSession() as client,
            client.get(f"http://127.0.0.1:{port}/healthz") as resp,
        ):
            assert resp.status == _HTTP_OK
            body = await resp.json()
            assert body == {"status": "ok"}
    finally:
        shutdown.set()
        await server_task
