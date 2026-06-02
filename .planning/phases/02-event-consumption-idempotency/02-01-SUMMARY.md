---
phase: 02-event-consumption-idempotency
plan: 01
subsystem: events
tags: [events, contracts, pydantic, ports, protocol, idempotency]
requires: []
provides:
  - "EventEnvelope[P: BaseModel] generic wire envelope (frozen, extra=forbid)"
  - "Five consumed subscription.* payload models + LineDelta"
  - "type->payload registry (payload_class_for / UnknownEnvelopeType)"
  - "stream_for_envelope_type routing helper"
  - "EventConsumer Protocol port (start/run/close) + HandlerFn alias"
affects:
  - "Plan 02-02 (idempotency: ProcessedEvent, dedupe wrapper)"
  - "Plan 02-03 (ValkeyStreamsConsumer adapter + handlers + main.py wiring)"
tech_stack:
  added: []
  patterns:
    - "Consume-only envelope re-implementation drops build() (producer concern, D-03)"
    - "type: str (not Literal) for forward-compat unknown-type routing (D-05)"
    - "Plain Decimal for total_amount (Pydantic 2.13 string<->Decimal round-trip, D-04)"
    - "Two-phase parse registry: outer envelope -> type -> payload class"
key_files:
  created:
    - src/provisioning_worker/events/envelope.py
    - src/provisioning_worker/events/subscription.py
    - src/provisioning_worker/ports/event_consumer.py
    - tests/events/__init__.py
    - tests/events/test_envelope.py
    - tests/events/test_subscription_payloads.py
  modified:
    - src/provisioning_worker/events/__init__.py
decisions:
  - "D-03: EventEnvelope re-implemented frozen/extra=forbid, no build() (producer-only, Phase 4+)"
  - "D-04: Five payloads copied field-for-field from docs/events.md; MoneyDecimal -> plain Decimal"
  - "D-05/Pitfall 4: envelope type is str, not Literal (forward-compat)"
  - "D-09: canonical JSON fixtures hand-authored from docs/events.md, no cross-repo import"
  - "D-01: EventConsumer port is the consume-side seam Plan 03's adapter implements"
metrics:
  duration: ~25m
  completed: 2026-06-02
  tasks: 2
  files: 7
---

# Phase 2 Plan 01: Event Contracts & EventConsumer Port Summary

Established the consume-side typed data layer — a generic `EventEnvelope`, the
five `subscription.*` payload models with a two-phase-parse registry, and the
`EventConsumer` Protocol port — so Plans 02 and 03 have exact symbols to import
with no codebase exploration and the round-trip unit tests are green.

## What Was Built

**Task 1 — `events/` package (commit `edda7f8`)**
- `events/envelope.py`: `EventEnvelope[P: BaseModel]` generic, `frozen` +
  `extra="forbid"`, `type: str` (not `Literal`) for forward-compat, no
  `build()` classmethod (consume-only). Plus `stream_for_envelope_type`.
- `events/subscription.py`: `SubscriptionActivatedPayload`, `LineDelta`,
  `SubscriptionLinesChangedPayload`, `SubscriptionSuspendedPayload`,
  `SubscriptionReinstatedPayload`, `SubscriptionCancelledPayload` — copied
  field-for-field from `docs/events.md`, with `total_amount: Decimal` (plain
  `Decimal`, not platform-api's `MoneyDecimal`).
- `events/__init__.py`: `UnknownEnvelopeType` exception, `_PAYLOAD_REGISTRY`
  mapping all five dotted types, `payload_class_for()` (raises on miss).
- `tests/events/`: envelope field-set pin + extra-field rejection +
  `stream_for_envelope_type`; all-five payload round-trips (parametrized) +
  Decimal-as-string assertion + extra-field rejection + registry lookup.

**Task 2 — EventConsumer port (commit `bbdfa89`)**
- `ports/event_consumer.py`: `@runtime_checkable EventConsumer(Protocol)` with
  async `start` / `run(handlers, shutdown)` / `close`, plus the `HandlerFn`
  type alias for the `(raw_env, payload) -> Awaitable[None]` signature.

## Verification

- `make test` (`-m "not integration"`): **30 passed** (10 pre-existing + 16 new
  events tests + 4 others).
- Repo-wide `ruff check .` + `ruff format --check .`: clean (26 files).
- Plan verification block: all imports + assertions pass
  (`payload_class_for('subscription.activated') is SubscriptionActivatedPayload`,
  unknown type raises `UnknownEnvelopeType`,
  `stream_for_envelope_type('subscription.activated') == 'events.subscription'`).
- No `from __future__ import annotations` in any new file (grep: 0 matches).
- All 7 Task-1 behavior assertions pass as pytest assertions.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking: CI-gate conflict] `HandlerFn` uses `type` keyword instead of `TypeAlias`**
- **Found during:** Task 2
- **Issue:** The plan's `<action>` specified
  `HandlerFn: TypeAlias = Callable[[Any, Any], Awaitable[None]]`, but ruff's
  `UP040` (an enabled lint group, `UP`) flags `TypeAlias` annotations in favor
  of the Python 3.12+ `type` statement. CLAUDE.md §6.1 makes `make check`
  (ruff) the CI gate, and project convention takes precedence over the plan.
- **Fix:** Used `type HandlerFn = Callable[[Any, Any], Awaitable[None]]`.
  Functionally identical (still importable, still usable in the `run` signature
  and `dict[str, HandlerFn]`); satisfies ruff. `__all__` still exports
  `HandlerFn`.
- **Files modified:** `src/provisioning_worker/ports/event_consumer.py`
- **Commit:** `bbdfa89`

**2. [Rule 3 - Blocking] `# noqa` on runtime-typed imports (TC002/TC003)**
- **Found during:** Tasks 1 & 2
- **Issue:** ruff `TC002`/`TC003` (flake8-type-checking) want
  `datetime`/`Decimal`/`UUID`/`BaseModel`/`asyncio` moved into a
  `TYPE_CHECKING` block. These are runtime-evaluated Pydantic field types and a
  runtime-evaluated registry annotation, so they must stay at import time.
- **Fix:** Added targeted `# noqa: TC00x — runtime-typed ...` comments,
  mirroring platform-api's `events/subscription.py` analog which uses the same
  pattern.
- **Files modified:** `events/envelope.py`, `events/subscription.py`,
  `events/__init__.py`, `ports/event_consumer.py`
- **Commit:** `edda7f8`, `bbdfa89`

## Known Stubs

None. All models are fully implemented; the `EventConsumer` Protocol is an
interface by design (Plan 03 supplies the `ValkeyStreamsConsumer`
implementation) — not a stub.

## Notes for Downstream Plans

- Plan 02 (idempotency): import `EventEnvelope` from
  `provisioning_worker.events.envelope`; the dedupe wrapper keys on
  `envelope.id` (the 26-char ULID `id` field).
- Plan 03 (adapter + handlers + wiring): import `payload_class_for`,
  `UnknownEnvelopeType`, `EventEnvelope`, `stream_for_envelope_type` from
  `provisioning_worker.events`; implement the `EventConsumer` Protocol from
  `provisioning_worker.ports.event_consumer`; handlers match the `HandlerFn`
  `(raw_env, payload)` shape.

## Self-Check: PASSED
