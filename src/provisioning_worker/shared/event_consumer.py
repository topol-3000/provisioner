"""Idempotency dedupe guard for the consume side.

This module is the seam between the Valkey Streams adapter (which owns wire
parsing and ``XACK``) and the domain handlers (which own side-effects). It
wraps every raw handler so that:

1. A single transaction (:func:`session_scope`) covers the handler's
   side-effects **and** the ``processed_event`` insert — they commit together
   or not at all (D-06).
2. A re-delivered event short-circuits on the existing ``processed_event``
   row instead of running the handler twice.

Critical ordering (RESEARCH.md §Same-Transaction Dedupe, Pitfall 1):
``session.commit()`` is the **last** thing inside :func:`handle_with_dedupe`.
``XACK`` happens in the *caller* (the adapter) **after** this function returns
— never inside the session scope. That is what makes the crash-window
guarantee real:

- Crash before commit → message re-delivered → reprocesses cleanly.
- Crash after commit, before ``XACK`` → message re-delivered → the existing
  ``processed_event`` row causes the guard to short-circuit; no double-effect.

Post-commit enqueue pattern (Pitfall 1 mitigation):
Handlers must NOT call ``broker.task.kiq()`` before returning — the session
commit has not happened yet. Instead, handlers call :func:`register_post_commit`
to register a callback; :func:`handle_with_dedupe` drains all registered
callbacks **after** ``session.commit()`` succeeds. This guarantees a Taskiq
job is never enqueued against a row that doesn't exist in the DB yet.
"""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from provisioning_worker.infrastructure.db import session_scope
from provisioning_worker.modules.provisioning.models import ProcessedEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from provisioning_worker.ports.event_consumer import HandlerFn

__all__ = ["handle_with_dedupe", "make_handler_registry", "register_post_commit"]

log = structlog.get_logger(__name__)

# Post-commit enqueue queue (Pitfall 1 fix, T-3-11 mitigation).
# Handlers append callbacks here; handle_with_dedupe drains after commit.
# Default is None; handle_with_dedupe resets to [] at the start of each
# invocation to prevent cross-invocation bleed. None is used instead of []
# to satisfy B039 (mutable ContextVar default).
_POST_COMMIT_ENQUEUE: ContextVar[list[Callable[[], Awaitable[None]]] | None] = ContextVar(
    "_POST_COMMIT_ENQUEUE", default=None
)

# A raw handler takes the open session as a third argument; the dedupe wrapper
# supplies it. The adapter-facing wrapped handler (HandlerFn) is (raw_env,
# payload) -> Awaitable[None].
type RawHandlerFn = Callable[[object, object, AsyncSession], Awaitable[None]]


def register_post_commit(callback: Callable[[], Awaitable[None]]) -> None:
    """Register a callback to be drained after ``session.commit()`` succeeds.

    Handlers must call this instead of enqueuing Taskiq jobs directly (Pitfall 1
    fix, T-3-11 mitigation). The callback is appended to the current invocation's
    post-commit queue; :func:`handle_with_dedupe` drains the queue after the
    session commits.

    Calling this before ``handle_with_dedupe`` has been entered (outside a
    handler invocation) is safe but the callback will never be drained.

    Args:
        callback: An async callable with no arguments to invoke after commit.
    """
    current = _POST_COMMIT_ENQUEUE.get() or []
    _POST_COMMIT_ENQUEUE.set([*current, callback])


async def handle_with_dedupe(
    raw_env: object,
    payload: object,
    handler_fn: RawHandlerFn,
    consumer_group: str,
) -> None:
    """Run ``handler_fn`` exactly once per ``(envelope.id, consumer_group)``.

    Opens a single :func:`session_scope` transaction and short-circuits on
    duplicate delivery via two paths:

    1. **Sequential replay** — SELECT finds an existing ``processed_event`` row
       (fast path; the handler never runs).
    2. **Concurrent duplicate** — SELECT sees nothing (another transaction
       committed the row between the SELECT and this ``commit()``), the handler
       runs, then ``commit()`` raises ``IntegrityError`` from the composite PK
       conflict; this function rolls back and returns without raising, allowing
       the caller to proceed to ``XACK``.

    In both cases the function returns normally; the caller must ``XACK`` only
    **after** this coroutine returns (commit-then-ack). This function never acks.

    After ``session.commit()`` succeeds, any callbacks registered via
    :func:`register_post_commit` are drained in registration order. This
    satisfies T-3-11: post-commit enqueue callbacks run only after the DB
    transaction is durable.

    Args:
        raw_env: The validated inbound envelope (must expose ``id``).
        payload: The type-resolved payload model passed through to the handler.
        handler_fn: The raw handler, called as
            ``handler_fn(raw_env, payload, session)``.
        consumer_group: The consumer-group name forming the second half of the
            composite dedupe key.
    """
    # Reset post-commit queue for this invocation (prevent cross-invocation bleed).
    # Use a fresh list each time rather than clearing in-place (ContextVar isolation).
    _POST_COMMIT_ENQUEUE.set([])

    async with session_scope() as session:
        existing = await _select_processed_event(session, raw_env.id, consumer_group)
        if existing is not None:
            log.debug("dedupe short-circuit", envelope_id=raw_env.id)
            return
        await handler_fn(raw_env, payload, session)
        await _insert_processed_event(session, raw_env.id, consumer_group)
        try:
            await session.commit()
        except IntegrityError:
            # Concurrent duplicate won the race; its commit is authoritative.
            await session.rollback()
            log.debug("dedupe conflict — concurrent duplicate", envelope_id=raw_env.id)
            return

    # Drain post-commit callbacks (T-3-11: only after successful commit).
    for callback in _POST_COMMIT_ENQUEUE.get() or []:
        await callback()


def make_handler_registry(
    consumer_group: str,
    raw_handlers: dict[str, RawHandlerFn],
) -> dict[str, HandlerFn]:
    """Wrap each raw handler in the idempotency guard, bound to a group.

    The returned map is keyed by dotted envelope type and holds the
    ``(raw_env, payload) -> Awaitable[None]`` closures the adapter invokes.
    Each closure delegates to :func:`handle_with_dedupe` with ``consumer_group``
    already bound, so the adapter never threads the group through dispatch.

    Args:
        consumer_group: The consumer-group name to bind into every wrapper.
        raw_handlers: Map of dotted envelope type to its raw handler
            (the ``(raw_env, payload, session)`` form).

    Returns:
        Map of dotted envelope type to the wrapped ``HandlerFn``.
    """

    def _wrap(handler_fn: RawHandlerFn) -> HandlerFn:
        async def _wrapped(raw_env, payload) -> None:
            await handle_with_dedupe(raw_env, payload, handler_fn, consumer_group)

        return _wrapped

    return {event_type: _wrap(handler_fn) for event_type, handler_fn in raw_handlers.items()}


async def _select_processed_event(
    session: AsyncSession,
    event_id: str,
    consumer_group: str,
) -> ProcessedEvent | None:
    """Return the existing ledger row for the composite key, or ``None``."""
    result = await session.execute(
        select(ProcessedEvent).where(
            ProcessedEvent.event_id == event_id,
            ProcessedEvent.consumer_group == consumer_group,
        )
    )
    return result.scalar_one_or_none()


async def _insert_processed_event(
    session: AsyncSession,
    event_id: str,
    consumer_group: str,
) -> None:
    """Stage the idempotency-ledger row for this event in ``session``."""
    session.add(ProcessedEvent(event_id=event_id, consumer_group=consumer_group))
