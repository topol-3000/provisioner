"""Composition root — wires the four concurrent concerns.

Boots them under a single asyncio.TaskGroup (crash-only: D-01).
SIGTERM sets the shared shutdown event; each concern exits its loop
and the TaskGroup completes cleanly (D-02).
"""

import asyncio
import signal
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from sqlalchemy import text
from taskiq_redis import RedisStreamBroker

from provisioning_worker.infrastructure.db import dispose_engine, get_engine
from provisioning_worker.infrastructure.health_server import run_health_server
from provisioning_worker.infrastructure.logging import configure_logging
from provisioning_worker.infrastructure.observability import configure_tracing
from provisioning_worker.infrastructure.outbox_relay import run_outbox_relay

if TYPE_CHECKING:
    from provisioning_worker.settings import Settings

log = structlog.get_logger(__name__)


async def run(settings: Settings) -> None:
    """Wire adapters, check infra, then run the four concerns.

    Calls configure_logging and configure_tracing, performs fail-fast
    connectivity checks on Postgres and Valkey, installs SIGTERM/SIGINT
    signal handlers, and starts all four concerns under a single
    asyncio.TaskGroup. On clean shutdown (shutdown event set) the
    TaskGroup exits normally and the engine is disposed. On any concern
    crash the TaskGroup raises ExceptionGroup and the process exits
    non-zero (D-01).

    Args:
        settings: Validated application settings.

    Raises:
        SystemExit: Non-zero if any concern crashes (D-01).
    """
    configure_logging(settings)
    configure_tracing(settings)

    log.info(
        "provisioning-worker starting",
        environment=settings.environment,
        deployment_adapter=settings.deployment_adapter,
    )

    # Fail-fast: verify Postgres + Valkey are reachable before starting concerns (D-05)
    await _check_postgres(settings)
    await _check_valkey(settings)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    get_engine(settings)
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_run_consumer(settings, shutdown), name="consumer")
            tg.create_task(_run_convergence(settings, shutdown), name="convergence")
            tg.create_task(run_outbox_relay(settings, shutdown), name="outbox_relay")
            tg.create_task(run_health_server(settings, shutdown), name="health_server")
    except* Exception as eg:
        raise SystemExit(1) from eg.exceptions[0]
    finally:
        await dispose_engine()


async def _run_consumer(settings: Settings, shutdown: asyncio.Event) -> None:
    """Run the Valkey Streams consumer loop (no-op Phase 1).

    Creates the consumer group idempotently (tolerating BUSYGROUP on
    restart), enters the XREADGROUP loop with a 1-second block timeout,
    dispatches received messages to a no-op handler, and exits when
    `shutdown` is set. Phase 2 replaces the no-op with real dispatch.

    Args:
        settings: Application settings — supplies consumer group name,
            consumer name, and Valkey URL.
        shutdown: Event set by the composition root on SIGTERM.
    """
    client = aioredis.from_url(str(settings.valkey_url), decode_responses=True)

    try:
        await client.xgroup_create(
            name="events.subscription",
            groupname=settings.provisioning_consumer_group,
            id="0",
            mkstream=True,
        )
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    log.info(
        "joined consumer group",
        group=settings.provisioning_consumer_group,
        stream="events.subscription",
        consumer=settings.consumer_name,
    )

    while not shutdown.is_set():
        results = await client.xreadgroup(
            groupname=settings.provisioning_consumer_group,
            consumername=settings.consumer_name,
            streams={"events.subscription": ">"},
            count=10,
            block=1000,
        )
        if results:
            for _stream, messages in results:
                for msg_id, _fields in messages:
                    log.debug("received event (no-op)", msg_id=msg_id)
                    await client.xack(
                        "events.subscription",
                        settings.provisioning_consumer_group,
                        msg_id,
                    )

    await client.aclose()


async def _run_convergence(settings: Settings, shutdown: asyncio.Event) -> None:
    """Run the taskiq broker (connect-only, Phase 1).

    Constructs a RedisStreamBroker from the Valkey URL, calls
    startup() to register the broker's internal consumer group, logs
    success, and waits until shutdown is set. Phase 3 registers real
    Taskiq tasks and starts the in-process listener.

    Args:
        settings: Application settings — supplies Valkey URL.
        shutdown: Event set by the composition root on SIGTERM.
    """
    broker = RedisStreamBroker(url=str(settings.valkey_url))
    await broker.startup()

    log.info("taskiq broker connected", url=str(settings.valkey_url))

    await shutdown.wait()
    await broker.shutdown()


async def _check_postgres(settings: Settings) -> None:
    """Verify Postgres is reachable. Fail-fast on error (D-05).

    Issues a ``SELECT 1`` via the async engine. Logs error and re-raises
    on failure so the process exits non-zero before the TaskGroup starts.

    Args:
        settings: Application settings — supplies database_url.

    Raises:
        Exception: Any SQLAlchemy or network error on connection failure.
    """
    engine = get_engine(settings)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.error("postgres unreachable at boot", error=str(exc))
        raise


async def _check_valkey(settings: Settings) -> None:
    """Verify Valkey is reachable. Fail-fast on error (D-05).

    Issues a PING via a transient redis.asyncio client. Logs error and
    re-raises on failure so the process exits non-zero before the
    TaskGroup starts.

    Args:
        settings: Application settings — supplies valkey_url.

    Raises:
        Exception: Any redis or network error on connection failure.
    """
    client = aioredis.from_url(str(settings.valkey_url))
    try:
        await client.ping()
    except Exception as exc:
        log.error("valkey unreachable at boot", error=str(exc))
        raise
    finally:
        await client.aclose()
