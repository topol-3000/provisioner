"""aiohttp health server — serves GET /healthz on HEALTH_PORT.

Runs as one of the four TaskGroup concerns. Uses AppRunner + TCPSite
so it does not block the event loop (unlike the blocking helper).
"""

from typing import TYPE_CHECKING

import structlog
from aiohttp import web

if TYPE_CHECKING:
    from asyncio import Event

    from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)


async def run_health_server(settings: Settings, shutdown: Event) -> None:
    """Start the /healthz aiohttp server and block until shutdown.

    Binds to 0.0.0.0 on `settings.health_port`, serves GET /healthz,
    then waits for the shutdown event before cleaning up.

    Args:
        settings: Application settings — supplies health_port.
        shutdown: Event set by the composition root on SIGTERM.
    """
    app = web.Application()
    app.router.add_get("/healthz", _healthz)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.health_port)  # noqa: S104
    await site.start()

    log.info("health server listening", port=settings.health_port)
    await shutdown.wait()
    await runner.cleanup()


async def _healthz(request: web.Request) -> web.Response:
    """Handle GET /healthz liveness probe.

    Args:
        request: The incoming aiohttp request (unused).

    Returns:
        200 JSON response with body {"status":"ok"}.
    """
    return web.Response(content_type="application/json", text='{"status":"ok"}')
