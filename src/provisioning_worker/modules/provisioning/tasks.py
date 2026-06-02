"""Taskiq retryable tasks — adapter calls with backoff.

This module defines :func:`create_instance_task`, the Taskiq task that drives
the create-path convergence loop:
``pending -> deploying -> configuring -> ready`` against the
:class:`~provisioning_worker.ports.deployment_adapter.DeploymentAdapter` port.

Architecture notes (CLAUDE.md §6.7, RESEARCH.md §Architectural Responsibility Map):

- Adapter calls (slow, fail-prone) live HERE — not in ``service.py``.
- ``ProvisioningService.validate_transition`` is called before every status
  update — it is the sole state-machine guard.
- ``ProvisioningService.write_enforcement_snapshot`` is called at the
  ``configuring`` step (WARNING 4 fix). Tasks NEVER call repository snapshot
  functions directly.
- Secrets from ``CreateResult`` are passed in-memory to
  ``NotificationTransport`` and then discarded — never bound to structlog,
  never persisted (D-12, T-3-07).
- The ``ready_at IS NULL`` guard prevents double credential delivery on retry
  (D-13, T-3-09).
- All exceptions are caught; the broker's ``listen()`` loop never sees an
  unhandled exception (T-3-10).

The ``broker`` used for ``@broker.task`` is :data:`taskiq.async_shared_broker`.
``main.py`` sets ``async_shared_broker._default_broker = redis_broker`` and
imports this module AFTER ``broker.add_dependency_context(...)`` to wire
dependencies. This pattern avoids a circular import while keeping the task
registered on the real broker.
"""

import dataclasses
from datetime import timedelta
from typing import Annotated
from uuid import UUID

import structlog
from taskiq import TaskiqDepends, async_shared_broker

from provisioning_worker.infrastructure.db import session_scope
from provisioning_worker.modules.provisioning import repository
from provisioning_worker.modules.provisioning.models import InstanceStatus
from provisioning_worker.modules.provisioning.service import (
    ProvisioningService,  # noqa: TC001 — runtime DI
)
from provisioning_worker.ports.clock import Clock  # noqa: TC001 — runtime DI
from provisioning_worker.ports.deployment_adapter import (
    DeploymentAdapter,
    DeploymentStatus,
    InstanceSpec,
)
from provisioning_worker.ports.notification_transport import (
    CredentialNotification,
    NotificationTransport,
)
from provisioning_worker.settings import Settings  # noqa: TC001 — runtime DI
from provisioning_worker.shared.errors import AdapterTimeout, NonRetryableError

__all__ = ["create_instance_task"]

log = structlog.get_logger(__name__)

# Poll interval for get_instance_status; FakeClock.sleep() is a no-op so
# this constant does not delay unit tests.
_POLL_INTERVAL_S: float = 5.0
# Maximum status-poll iterations before declaring AdapterTimeout.
_MAX_POLL_ITERATIONS: int = 60


def _compute_backoff_seconds(attempt_count: int, settings: Settings) -> float:
    """Compute exponential backoff delay, capped at ``settings.provisioning_cap_s``.

    Formula: ``base * multiplier^attempt_count``, capped at cap.
    Default sequence (5 attempts, 2s base, x2, 60s cap): 2, 4, 8, 16, 32.

    Args:
        attempt_count: Zero-based attempt index (0 = first failure).
        settings: Application settings providing backoff knobs.

    Returns:
        Delay in seconds as a float, in range
        ``[settings.provisioning_base_delay_s, settings.provisioning_cap_s]``.
    """
    delay = settings.provisioning_base_delay_s * (settings.provisioning_multiplier**attempt_count)
    return min(delay, settings.provisioning_cap_s)


@async_shared_broker.task
async def create_instance_task(
    instance_id: str,
    task_id: str,
    settings: Annotated[Settings, TaskiqDepends()],
    adapter: Annotated[DeploymentAdapter, TaskiqDepends()],
    transport: Annotated[NotificationTransport, TaskiqDepends()],
    clock: Annotated[Clock, TaskiqDepends()],
    service: Annotated[ProvisioningService, TaskiqDepends()],
) -> None:
    """Drive ``pending -> deploying -> configuring -> ready`` for one instance.

    This task is enqueued by ``handle_subscription_activated`` (post-commit)
    and consumed by the ``_run_convergence`` concern in ``main.py``.

    Convergence steps:

    1. Load instance + task from DB.
    2. Call ``adapter.create_instance(spec)`` -> handle + secrets.
    3. Transition instance to ``deploying``; persist handle.
    4. Poll ``adapter.get_instance_status(handle)`` until HEALTHY.
    5. Transition to ``configuring``; write enforcement snapshot via service.
    6. First-ready guard: if ``ready_at`` is NULL, deliver credentials and
       set ``ready_at``; otherwise skip credential delivery.
    7. Transition to ``ready``; mark task succeeded.

    On any failure: record to DB, schedule delayed re-kick if below
    ``max_attempts``; otherwise mark terminal. The exception is caught here
    and the broker loop sees a clean return (T-3-10).

    Args:
        instance_id: String UUID of the provisioning instance.
        task_id: String UUID of the provisioning task (retry ledger).
        settings: Application settings (backoff knobs, etc.).
        adapter: The deployment adapter (real or fake).
        transport: The notification transport (console in dev).
        clock: The clock (system or fake for deterministic tests).
        service: The provisioning service (state machine guard + snapshot).
    """
    structlog.contextvars.bind_contextvars(instance_id=instance_id, task_id=task_id)
    log.info("create_instance_task started")

    inst_uuid = UUID(instance_id)
    task_uuid = UUID(task_id)

    try:
        await _run_convergence(inst_uuid, task_uuid, settings, adapter, transport, clock, service)
    except Exception as exc:
        # Catch-all: record failure, schedule retry or mark terminal.
        # Must not propagate — the broker loop must never see an unhandled
        # exception from a task (T-3-10, D-10).
        await _handle_failure(inst_uuid, task_uuid, exc, settings, clock)


async def _run_convergence(
    instance_id: UUID,
    task_id: UUID,
    settings: Settings,
    adapter: DeploymentAdapter,
    transport: NotificationTransport,
    clock: Clock,
    service: ProvisioningService,
) -> None:
    """Execute the convergence steps for one instance (raises on failure).

    Separated from the catch-all in :func:`create_instance_task` so that
    the exception path is clearly delimited.

    Args:
        instance_id: UUID of the instance.
        task_id: UUID of the provisioning task.
        settings: Application settings.
        adapter: Deployment adapter.
        transport: Notification transport.
        clock: Clock for time/sleep.
        service: Provisioning service for state machine + snapshot.
    """
    async with session_scope() as session:
        instance = await repository.get_instance_by_id(session, instance_id)
        task = await repository.get_task_by_id(session, task_id)

        if instance is None or task is None:
            log.warning(
                "create_instance_task: instance or task not found — skipping",
                instance_found=instance is not None,
                task_found=task is not None,
            )
            return

        # CR-01 fix: read every scalar we need WHILE the session is still open.
        # SQLAlchemy async sessions default to expire_on_commit=True, so once
        # this `session_scope()` block exits the ORM objects are detached and
        # any further attribute access lazy-loads — raising MissingGreenlet /
        # DetachedInstanceError on the real engine. Capture the values now.
        current_status = instance.status
        task_payload = task.payload

    # WR-03 fix: guard a missing/malformed payload as a permanent (non-retryable)
    # failure rather than burning the retry budget on an un-parseable payload.
    if task_payload is None:
        raise NonRetryableError(
            "task payload is missing — cannot build InstanceSpec",
            step="parse_payload",
            reason="task.payload is None",
        )
    try:
        # Rebuild spec from task.payload (WARNING 5 fix: canonical round-trip).
        spec = InstanceSpec.from_dict(task_payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise NonRetryableError(
            "task payload is malformed — cannot build InstanceSpec",
            step="parse_payload",
            reason=str(exc),
        ) from exc

    # --- Step 1: create_instance -> deploying ---
    create_result = await adapter.create_instance(spec)
    handle_dict = dataclasses.asdict(create_result.handle)

    async with session_scope() as session:
        service.validate_transition(current_status, InstanceStatus.deploying)
        await repository.update_instance_status(
            session, instance_id, InstanceStatus.deploying, deployment_handle=handle_dict
        )
        await session.commit()

    # --- Step 2: poll until HEALTHY -> configuring ---
    handle = create_result.handle
    await _poll_until_healthy(handle, adapter, clock)

    async with session_scope() as session:
        service.validate_transition(InstanceStatus.deploying, InstanceStatus.configuring)
        await repository.update_instance_status(session, instance_id, InstanceStatus.configuring)
        await session.commit()

    # --- Step 3: write enforcement snapshot at configuring ---
    async with session_scope() as session:
        await service.write_enforcement_snapshot(session, instance_id, spec, version=1)
        await session.commit()

    # --- Step 4: first-ready guard + credential delivery ---
    async with session_scope() as session:
        instance = await repository.get_instance_by_id(session, instance_id)
        if instance is None:
            log.error("instance disappeared before ready transition")
            return

        is_first_ready = instance.ready_at is None
        ready_at = clock.now()

        service.validate_transition(InstanceStatus.configuring, InstanceStatus.ready)
        await repository.update_instance_status(
            session,
            instance_id,
            InstanceStatus.ready,
            ready_at=ready_at,
            url=f"https://{spec.slug}",
        )
        await repository.record_task_success(session, task_id)
        await session.commit()

    # Send credentials exactly once: only when ready_at was NULL (D-13, T-3-09).
    # Secrets are discarded after this call — never assigned to any persistent variable.
    #
    # WR-06 fix: convergence has already committed `ready` + task `succeeded`
    # above. A notification-transport hiccup must NOT drag a fully-converged
    # instance back to `failed` and re-kick the task. Isolate the delivery in
    # its own try/except so a send failure is logged for human follow-up but
    # never propagates into the convergence failure path.
    if is_first_ready:
        try:
            await transport.send_credentials(
                CredentialNotification(
                    recipient_email=spec.admin_email,
                    instance_id=instance_id,
                    instance_url=f"https://{spec.slug}",
                    admin_login=spec.admin_email,
                    admin_password=create_result.admin_password,  # never logged (T-3-07)
                )
            )
            log.info("credentials delivered", instance_id=str(instance_id))
        except Exception as exc:  # convergence already succeeded; do not re-fail
            # Never log the password or notification contents (T-3-07).
            log.error(
                "credential delivery failed after instance reached ready — "
                "convergence is complete; manual re-delivery required",
                instance_id=str(instance_id),
                error=str(exc),
                error_type=type(exc).__name__,
            )
    else:
        log.info("skipping credential re-delivery — ready_at already set (D-13)")

    log.info("create_instance_task completed", status="ready")


async def _poll_until_healthy(
    handle,
    adapter: DeploymentAdapter,
    clock: Clock,
) -> None:
    """Poll ``adapter.get_instance_status`` until HEALTHY.

    Uses ``clock.sleep`` between polls — ``FakeClock.sleep`` is a no-op so
    unit tests are deterministic (D-06). Raises :class:`AdapterTimeout` if
    the instance does not become healthy within ``_MAX_POLL_ITERATIONS``.

    Args:
        handle: The adapter handle returned by ``create_instance``.
        adapter: The deployment adapter.
        clock: The clock (provides sleep).

    Raises:
        AdapterTimeout: If the instance is not HEALTHY after max iterations.
    """
    for _ in range(_MAX_POLL_ITERATIONS):
        status = await adapter.get_instance_status(handle)
        if status == DeploymentStatus.HEALTHY:
            return
        await clock.sleep(_POLL_INTERVAL_S)
    raise AdapterTimeout("instance did not become healthy within the poll timeout")


async def _handle_failure(
    instance_id: UUID,
    task_id: UUID,
    exc: Exception,
    settings: Settings,
    clock: Clock,
) -> None:
    """Record a task failure and either schedule a retry or mark it terminal.

    Persists ``last_error``, ``failed_step``, ``failure_reason`` to DB and
    sets ``instance.status = failed`` (D-09). The retry decision is made on the
    *persisted* (post-increment) ``attempt_count`` returned by
    ``record_task_failure`` — comparing the same value that is stored avoids
    the implicit off-by-one of the previous pre-increment compare (CR-03).

    When the persisted attempt count reaches ``provisioning_max_attempts`` —
    or when the failure is a :class:`NonRetryableError` (e.g. an un-parseable
    payload, WR-03) — the task is marked terminal via
    ``repository.mark_task_terminal`` (``status=failed``, ``next_attempt_at``
    cleared) so boot recovery never re-kicks a doomed task forever (CR-02).

    Args:
        instance_id: UUID of the instance.
        task_id: UUID of the provisioning task.
        exc: The exception that triggered the failure.
        settings: Application settings (backoff knobs, max_attempts).
        clock: The clock for time/sleep (FakeClock.sleep is a no-op).
    """
    non_retryable = isinstance(exc, NonRetryableError)
    log.error(
        "create_instance_task failed",
        error=str(exc),
        error_type=type(exc).__name__,
        non_retryable=non_retryable,
    )

    async with session_scope() as session:
        task = await repository.get_task_by_id(session, task_id)
        if task is None:
            log.warning("task not found during failure recording — skipping retry")
            return

        # Backoff is computed on the pre-increment attempt index (0-based), so
        # the first failure waits `base_delay` (attempt index 0).
        backoff = _compute_backoff_seconds(task.attempt_count, settings)
        next_attempt_at = clock.now() + timedelta(seconds=backoff)

        await repository.record_task_failure(session, task_id, instance_id, exc, next_attempt_at)
        # The authoritative, persisted attempt count after this failure.
        new_attempt_count = task.attempt_count

        is_terminal = non_retryable or new_attempt_count >= settings.provisioning_max_attempts
        if is_terminal:
            # CR-02: close the ledger so boot recovery does not re-kick forever.
            await repository.mark_task_terminal(session, task_id)

        await session.commit()

    if is_terminal:
        log.error(
            "task is terminal — no further retries",
            attempt_count=new_attempt_count,
            max_attempts=settings.provisioning_max_attempts,
            non_retryable=non_retryable,
        )
        return

    log.info(
        "scheduling retry",
        attempt_count=new_attempt_count,
        backoff_s=backoff,
        next_attempt_at=str(next_attempt_at),
    )
    await clock.sleep(backoff)
    await create_instance_task.kiq(str(instance_id), str(task_id))
