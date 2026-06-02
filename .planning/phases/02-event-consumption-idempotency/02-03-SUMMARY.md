---
phase: 02-event-consumption-idempotency
plan: 03
subsystem: event-consumption
tags: [valkey-streams, consumer, idempotency, dedupe, poison-handling, ports-adapters]
requires:
  - "events/ package: EventEnvelope, payload_class_for, UnknownEnvelopeType (Plan 02-01)"
  - "ports/event_consumer.py: EventConsumer Protocol + HandlerFn (Plan 02-01)"
  - "ProcessedEvent ORM + processed_event table (Plan 02-02)"
  - "infrastructure/db.py session_scope (Phase 1)"
  - "settings.consumer_reclaim_min_idle_ms (Plan 02-02)"
provides:
  - "shared/event_consumer.py: handle_with_dedupe() guard + make_handler_registry() factory"
  - "adapters/valkey_streams.py: ValkeyStreamsConsumer (EventConsumer impl) with XREADGROUP/XAUTOCLAIM/poison dispatch"
  - "modules/provisioning/handlers.py: five no-op subscription.* handlers"
  - "main.py _run_consumer wired to the real consumer seam"
  - "testcontainers Postgres fixtures (pg_engine, pg_session) in conftest.py"
affects:
  - "Phase 3 (convergence): handlers gain real side-effects inside the dedupe session"
  - "Phase 4 (event production): outbox relay is the producer-side mirror"
tech-stack:
  added: []
  patterns:
    - "Two-phase parse: _RawEnvelope(extra=forbid, payload:dict) then payload_class_for(type).model_validate"
    - "Commit-then-ack: XACK only after handle_with_dedupe() commits (D-06)"
    - "Pre-wrapped handler registry (make_handler_registry) — adapter calls wrapped handlers, no double-wrap"
    - "XAUTOCLAIM 3-element unpack (cursor, messages, _deleted_ids), same dispatch path as fresh reads (D-08)"
    - "Poison vs unknown-type split: bad JSON/validation -> error+ack+no row; unknown type -> warning+ack+no row (D-05)"
key-files:
  created:
    - src/provisioning_worker/shared/event_consumer.py
    - src/provisioning_worker/adapters/valkey_streams.py
    - tests/provisioning/test_handlers.py
    - tests/provisioning/test_idempotency.py
  modified:
    - src/provisioning_worker/modules/provisioning/handlers.py
    - src/provisioning_worker/main.py
    - tests/conftest.py
decisions:
  - "D-01 realized: ValkeyStreamsConsumer is the only redis.asyncio consume site; main.py shrank to wiring"
  - "D-06 realized: processed_event insert + handler side-effects commit together inside session_scope(); XACK in the adapter AFTER the wrapped handler returns"
  - "D-05 realized: malformed -> error log + XACK + no row; unknown-but-valid type -> warning log + XACK + no row"
  - "D-08 realized: XAUTOCLAIM every 60 poll cycles, routed through the same _dispatch parse->dedupe path"
  - "Deviation: adapter dispatches PRE-WRAPPED handlers (make_handler_registry) rather than calling handle_with_dedupe directly — avoids double-wrapping; PATTERNS.md line 351 shape"
metrics:
  duration: ~40m
  completed: 2026-06-02
  tasks: 2
  files: 7
requirements: [CONS-01, CONS-03, CONS-04]
---

# Phase 2 Plan 3: Full Consumer Seam (Adapter + Dedupe + Handlers) Summary

Wired the three remaining consume-side layers so the pipeline is end-to-end real:
`shared/event_consumer.py` (the idempotency dedupe guard + a handler-registry
factory), `adapters/valkey_streams.py` (a full `ValkeyStreamsConsumer`
implementing the `EventConsumer` Protocol — XREADGROUP poll loop, periodic
XAUTOCLAIM reclaim, and a four-stage poison/unknown-type/payload-error/happy-path
dispatch), and five no-op `subscription.*` handlers. `main.py._run_consumer`
shrank from the Phase-1 inline no-op loop to pure wiring. XADD -> XREADGROUP ->
two-phase parse -> dedupe -> commit -> XACK is now real; replays short-circuit;
poison messages are acked and survived.

## What Was Built

**Task 1 — dedupe guard, handlers, fixtures (commit `1698682`)**
- `shared/event_consumer.py`: `handle_with_dedupe(raw_env, payload, handler_fn,
  consumer_group)` opens one `session_scope()`, SELECTs the `ProcessedEvent`
  row, short-circuits on a hit, else runs the handler, INSERTs the ledger row,
  and `commit()`s — commit is the last statement; **XACK is the caller's job,
  after this returns** (D-06, RESEARCH.md Pitfall 1). `make_handler_registry(
  consumer_group, raw_handlers)` returns the `(raw_env, payload)` closures the
  adapter dispatches, each binding the group into the guard. Private helpers
  `_select_processed_event` / `_insert_processed_event`.
- `modules/provisioning/handlers.py`: five no-op handlers
  (`handle_subscription_{activated,lines_changed,suspended,reinstated,cancelled}`),
  each binding `envelope_id` / `subscription_id` / `correlation_id` via
  `bind_contextvars` (never secrets, CLAUDE.md §6.6) and logging a debug line.
  No DB writes — Phase 3 adds those inside the supplied `session`.
- `tests/conftest.py`: session-scoped `postgres_container` (Postgres 18) +
  `pg_engine` (creates the `provisioning` schema then `Base.metadata`) +
  function-scoped `pg_session` (truncates mapped tables post-test for commit
  isolation).
- `tests/provisioning/test_handlers.py`: parametrized no-op assertions (no
  `execute`/`add`/`commit`) + a `bind_contextvars` capture test.

**Task 2 — adapter, wiring, idempotency tests (commit `a7c6d39`)**
- `adapters/valkey_streams.py`: `ValkeyStreamsConsumer` (satisfies
  `EventConsumer`, verified via `isinstance`). `start()` XGROUP-CREATE tolerating
  BUSYGROUP; `run()` XREADGROUP loop calling `_reclaim()` every 60 cycles;
  `close()` `aclose()`. `_dispatch()` is the four-stage policy — `json.loads`
  (bad JSON/missing field -> error+ack), `_RawEnvelope.model_validate`
  (`extra="forbid"`, drift -> error+ack), `payload_class_for` (unknown type ->
  warning+ack), `payload_cls.model_validate` (drift -> error+ack), then the
  happy path: `await handler(raw_env, payload)` then `_ack`. `_reclaim()`
  unpacks the **3-element** `cursor, messages, _deleted_ids` XAUTOCLAIM result
  and routes through the same `_dispatch`.
- `main.py`: `_run_consumer` now constructs `ValkeyStreamsConsumer`, builds the
  handler map with `make_handler_registry`, and runs in try/finally with
  `close()`. Imports hoisted to module top (PLC0415).
- `tests/provisioning/test_idempotency.py`: unit dispatch-policy tests (mocked
  client) covering happy-path, bad JSON, unknown type, payload error, envelope
  error, plus the protocol `isinstance` check; integration SC-1 (first delivery
  -> 1 row, handler called once) and SC-2 (replay -> still 1 row, handler called
  once) against real Postgres.

## Verification

- `.venv/bin/pytest -m "not integration"`: **49 passed** (stable over 5 runs).
- `make test-integration` equivalent (`pytest -m integration` for this file):
  **2 passed** (SC-1, SC-2) against a real Postgres 18 testcontainer.
- `make check` (ruff lint + format): **all checks passed** (28 files).
- Protocol conformance: `isinstance(ValkeyStreamsConsumer(...), EventConsumer)`
  is `True`; `import provisioning_worker.main` is clean.
- XACK ordering (code review): `await handler(raw_env, payload)` precedes
  `await self._ack(msg_id)` in the `_dispatch` happy path (line 216 before 218).
- XAUTOCLAIM unpack (code review): `cursor, messages, _deleted_ids = result`.
- No `from __future__ import annotations` in any new/modified file (grep: 0).

## Deviations from Plan

### Design choices (within Rules 1-3)

**1. [Rule 3 - Blocking: CI-gate] Adapter dispatches PRE-WRAPPED handlers instead of calling `handle_with_dedupe` directly**
- **Found during:** Task 2, reconciling the plan's two stated designs.
- **Issue:** The plan's `<action>` step 5 said the adapter should
  `await handle_with_dedupe(raw_env, payload, handler, self._group)`, while it
  also said `main.py` passes handlers wrapped by `make_handler_registry`.
  Doing both double-wraps the dedupe guard. PATTERNS.md (lines 349-351) shows
  the correct shape: the adapter calls `await handler(raw_env, payload)` on a
  handler that is *already* dedupe-wrapped by the registry.
- **Fix:** `make_handler_registry` produces the dedupe-wrapped closures;
  `main.py` passes them to `consumer.run`; the adapter invokes
  `handler(raw_env, payload)` and then XACKs. Single wrap, commit-then-ack
  preserved. The structural grep "`handle_with_dedupe` appears in the adapter"
  no longer holds literally; it now appears in `shared/event_consumer.py`
  (where `make_handler_registry` wraps), which the adapter's module docstring
  documents. `handle_with_dedupe` import was removed from the adapter (ruff
  F401). Behavior is identical and the SC-1/SC-2 integration tests prove the
  guard runs.

**2. [Rule 3 - Blocking: CI-gate] ruff TC001/TC003/PLC0415 fixes across new files**
- **Found during:** Tasks 1 & 2 (running `make check`).
- **Issue:** ruff (the CI gate, CLAUDE.md §6.1) flagged annotation-only imports
  (`AsyncSession`, payload models, `HandlerFn`, `Iterator`/`AsyncIterator`) for
  TYPE_CHECKING (TC001/TC003), in-function imports (PLC0415) in `main.py`'s
  `_run_consumer` and the test helper, and a long log line.
- **Fix:** moved annotation-only imports into `TYPE_CHECKING` blocks (safe under
  PEP 649 deferred evaluation, Python 3.14); hoisted `main.py` consumer imports
  to module top (no circular-import risk — adapter/shared are leaf modules);
  hoisted the test helper's imports; wrapped the long line. `datetime` on the
  adapter's `_RawEnvelope` stays a **runtime** import (Pydantic resolves the
  field at model-build time) with `# noqa: TC003`, mirroring Plan 02-01/02-02.

### Auto-fixed Issues

**3. [Rule 1 - Bug] Integration-test isolation: committed rows leak across tests**
- **Found during:** Task 2, designing the integration fixtures.
- **Issue:** `handle_with_dedupe` calls `session.commit()`, so a plain
  rollback-after-yield in `pg_session` cannot undo the durable `processed_event`
  row — the second integration test would see the first test's row and miscount.
- **Fix:** `pg_session` now rolls back any open transaction and then `TRUNCATE
  ... CASCADE`s every mapped table (reverse dependency order) after each test.
- **Files modified:** `tests/conftest.py`
- **Commit:** `a7c6d39`

## Deferred Issues

**Pre-existing `make test` failure (out of scope):**
`tests/test_settings.py::test_env_file_loading` fails under the `make test`
target (`uv run pytest`) because a real repo-root `.env` is loaded over the
test's tmp `_env_file`. **Confirmed pre-existing** — reproduced on commit
`061c000` (before any 02-03 work). The unit suite passes via
`.venv/bin/pytest -m "not integration"` (49 passed) and via `uv run pytest`
invoked directly. Not caused by this plan (touches no settings machinery).
Logged in `deferred-items.md` with a suggested hermetic-test fix for the
settings-test owner.

## Known Stubs

The five handlers are deliberate **Phase-2 no-ops** — documented in each
docstring ("Phase 3 will ..."). They are not data-flow stubs: the consume
pipeline (parse -> dedupe -> processed_event insert -> XACK) is fully real and
proven by the SC-1/SC-2 integration tests. The plan's goal for this phase
(reliable exactly-once consumption + poison survival) is achieved; convergence
side-effects are explicitly Phase 3 scope.

## Self-Check: PASSED
