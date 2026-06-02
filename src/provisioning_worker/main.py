"""Composition root — wires the four concurrent concerns.

Boots them under a single asyncio.TaskGroup (crash-only: D-01).
SIGTERM sets the shared shutdown event; each concern exits its loop
and the TaskGroup completes cleanly (D-02).
"""

import asyncio
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select, text
from taskiq import async_shared_broker
from taskiq.receiver import Receiver
from taskiq_redis import RedisStreamBroker

from provisioning_worker.adapters.console_notification import ConsoleNotificationTransport
from provisioning_worker.adapters.fake_deployment import FakeDeploymentAdapter
from provisioning_worker.adapters.m1_entitlement_resolver import DefaultEntitlementResolver
from provisioning_worker.adapters.valkey_streams import ValkeyStreamsConsumer
from provisioning_worker.infrastructure.db import dispose_engine, get_engine, session_scope
from provisioning_worker.infrastructure.health_server import run_health_server
from provisioning_worker.infrastructure.logging import configure_logging
from provisioning_worker.infrastructure.observability import configure_tracing
from provisioning_worker.infrastructure.outbox_relay import run_outbox_relay
from provisioning_worker.modules.provisioning import handlers
from provisioning_worker.modules.provisioning.models import (
    ProvisioningTask,
    ProvisioningTaskStatus,
    TaskType,
)
from provisioning_worker.modules.provisioning.service import ProvisioningService

# Import tasks module to register @async_shared_broker.task decorators.
# The actual DI context (TaskiqDepends) is resolved at task execution time,
# not at decoration time, so the import order relative to
# broker.add_dependency_context() does not affect correctness.
from provisioning_worker.modules.provisioning.tasks import (
    create_instance_task,
)
from provisioning_worker.ports.clock import Clock, SystemClock
from provisioning_worker.ports.deployment_adapter import DeploymentAdapter
from provisioning_worker.ports.notification_transport import NotificationTransport
from provisioning_worker.shared.event_consumer import make_handler_registry

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
    """Run the Valkey Streams consumer (Phase 2 real dispatch).

    Wires the :class:`ValkeyStreamsConsumer` adapter to the five
    ``subscription.*`` handlers, each wrapped by the idempotency dedupe guard
    via :func:`make_handler_registry`. Joins the consumer group, runs the
    XREADGROUP poll loop (with periodic XAUTOCLAIM reclaim) until `shutdown` is
    set, then releases the connection pool.

    Args:
        settings: Application settings — supplies the Valkey URL, consumer
            group, consumer name, and reclaim window.
        shutdown: Event set by the composition root on SIGTERM.
    """
    consumer = ValkeyStreamsConsumer(settings)
    await consumer.start()

    handler_map = make_handler_registry(
        settings.provisioning_consumer_group,
        {
            "subscription.activated": handlers.handle_subscription_activated,
            "subscription.lines_changed": handlers.handle_subscription_lines_changed,
            "subscription.suspended": handlers.handle_subscription_suspended,
            "subscription.reinstated": handlers.handle_subscription_reinstated,
            "subscription.cancelled": handlers.handle_subscription_cancelled,
        },
    )

    try:
        await consumer.run(handler_map, shutdown)
    finally:
        await consumer.close()


async def _run_convergence(settings: Settings, shutdown: asyncio.Event) -> None:
    """Run the Taskiq convergence concern (Phase 3 real task listener).

    Wires the dependency context, sets the real broker on
    :data:`taskiq.async_shared_broker`, performs boot-time recovery
    (re-kicks overdue tasks), and starts ``Receiver.listen(shutdown)``
    to process incoming task messages.

    Dependency injection (via TaskiqDepends):

    - :class:`~provisioning_worker.settings.Settings` — application settings
    - :class:`~provisioning_worker.ports.deployment_adapter.DeploymentAdapter` —
      ``FakeDeploymentAdapter`` (milestone 1)
    - :class:`~provisioning_worker.ports.notification_transport.NotificationTransport` —
      ``ConsoleNotificationTransport`` (dev-only)
    - :class:`~provisioning_worker.ports.clock.Clock` — ``SystemClock``
    - :class:`~provisioning_worker.modules.provisioning.service.ProvisioningService` —
      wired with ``DefaultEntitlementResolver``

    Args:
        settings: Application settings — supplies Valkey URL and backoff knobs.
        shutdown: Event set by the composition root on SIGTERM.
    """
    broker = RedisStreamBroker(url=str(settings.valkey_url))

    # Wire dependency context — DI is resolved at task execution time,
    # so this can be called before or after tasks are imported/registered.
    broker.add_dependency_context(
        {
            settings.__class__: settings,  # Settings class as key
            DeploymentAdapter: FakeDeploymentAdapter(),
            NotificationTransport: ConsoleNotificationTransport(),
            Clock: SystemClock(),
            ProvisioningService: ProvisioningService(
                entitlement_resolver=DefaultEntitlementResolver()
            ),
        }
    )

    # Point the shared broker at the real Redis broker so create_instance_task.kiq()
    # sends to the correct stream. The tasks module is already imported at the top
    # of this file, which registered the @async_shared_broker.task decorators.
    async_shared_broker._default_broker = broker

    await broker.startup()
    log.info("taskiq broker connected", url=str(settings.valkey_url))

    # Boot-time recovery: re-kick overdue tasks (D-10: restart durability).
    await _recover_overdue_tasks()

    # Start the Taskiq task listener. Receiver.listen(shutdown) blocks until
    # the shutdown event is set, then exits cleanly.
    receiver = Receiver(broker, run_startup=False)
    await receiver.listen(shutdown)

    await broker.shutdown()


async def _recover_overdue_tasks() -> None:
    """Re-kick any provisioning tasks that are overdue for execution.

    Queries for ``provisioning_task`` rows with ``status IN ('pending',
    'running')`` and ``next_attempt_at <= now()`` (or ``next_attempt_at``
    IS NULL, which covers freshly-opened tasks). Re-kicks each via
    ``create_instance_task.kiq()``.

    Called at boot to close the durability gap: if the worker crashed mid-sleep
    during the backoff wait, the ``next_attempt_at`` column has the authoritative
    scheduled time; this function ensures those tasks are not silently dropped.

    D-10: provisioning_task is the durable backoff ledger.
    """
    now = datetime.now(tz=UTC)

    async with session_scope() as session:
        result = await session.execute(
            select(ProvisioningTask).where(
                ProvisioningTask.status.in_(
                    [ProvisioningTaskStatus.pending, ProvisioningTaskStatus.running]
                ),
                (ProvisioningTask.next_attempt_at.is_(None))
                | (ProvisioningTask.next_attempt_at <= now),
            )
        )
        overdue_tasks = result.scalars().all()

    if overdue_tasks:
        log.info("boot recovery: re-kicking overdue tasks", count=len(overdue_tasks))
        for task in overdue_tasks:
            # WR-01: dispatch by task_type. Today only `create` exists; any
            # other type (update/suspend/reinstate/delete) has no task class
            # yet, so re-kicking it as a create would silently corrupt
            # convergence. Skip + warn until those task classes land (Phase 5).
            if task.task_type is not TaskType.create:
                log.warning(
                    "boot recovery: skipping overdue task of unsupported type — "
                    "no task class exists for it yet",
                    task_id=str(task.id),
                    instance_id=str(task.instance_id),
                    task_type=task.task_type.value,
                    status=task.status.value,
                )
                continue
            log.info(
                "re-kicking overdue task",
                task_id=str(task.id),
                instance_id=str(task.instance_id),
                task_type=task.task_type.value,
                status=task.status.value,
            )
            await create_instance_task.kiq(str(task.instance_id), str(task.id))
    else:
        log.info("boot recovery: no overdue tasks found")


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
