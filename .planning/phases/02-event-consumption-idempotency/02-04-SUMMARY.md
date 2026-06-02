---
phase: "02"
plan: "04"
subsystem: event-consumption-idempotency
tags:
  - idempotency
  - dedupe
  - error-handling
  - consumer
dependency_graph:
  requires:
    - 02-03
  provides:
    - CONS-03 (IntegrityError dedupe hardening)
    - WR-01 (handler-failure error boundary)
    - WR-04 (missing-handler warning)
  affects:
    - src/provisioning_worker/shared/event_consumer.py
    - src/provisioning_worker/adapters/valkey_streams.py
    - tests/provisioning/test_idempotency.py
tech_stack:
  added: []
  patterns:
    - try/except IntegrityError around session.commit() with rollback + return
    - try/except Exception boundary at dispatch layer with log.exception
    - Explicit handler None guard before invocation with warning log
key_files:
  created: []
  modified:
    - src/provisioning_worker/shared/event_consumer.py
    - src/provisioning_worker/adapters/valkey_streams.py
    - tests/provisioning/test_idempotency.py
decisions:
  - "WR-03 parenthesization deferred: ruff 0.15.15 actively reformats `except (json.JSONDecodeError, KeyError):` back to bare-tuple form; keeping unparenthesized to satisfy `make check` (semantically equivalent in Python 3.14)"
metrics:
  duration: "~10 minutes"
  completed_date: "2026-06-02"
  tasks_completed: 2
  files_modified: 3
---

# Phase 2 Plan 04: CONS-03 IntegrityError dedupe gap closure Summary

IntegrityError handling in `handle_with_dedupe` and handler-failure error boundary in `_dispatch`, closing the CONS-03 blocker that caused unhandled concurrent-duplicate delivery to crash the consumer task.

## What Was Built

### Task 1: IntegrityError conflict handling in handle_with_dedupe

Added `from sqlalchemy.exc import IntegrityError` and wrapped `session.commit()` in a try/except block. On conflict: `await session.rollback()` + `log.debug("dedupe conflict — concurrent duplicate")` + `return`. This prevents the concurrent/reclaim-race duplicate delivery path from crashing the consumer.

Two new tests:
- `test_concurrent_duplicate` (`@pytest.mark.integration`) — pre-inserts the winning `ProcessedEvent` row, then calls `handle_with_dedupe` on the same `event_id`/`group`; asserts no exception raised, handler ran once, row count still 1.
- `test_concurrent_duplicate_unit` (Docker-free) — uses a fake session whose `commit()` raises `IntegrityError`; asserts `rollback()` called and no exception propagated.

### Task 2: Handler-failure error boundary + WR-04 missing-handler warning

Three targeted changes to `_dispatch` in `valkey_streams.py`:

1. **WR-01**: Wrapped `await handler(raw_env, payload)` in `try/except Exception`; on failure: `log.exception("handler failed — leaving message unacked for reclaim")` + `return` (no XACK; XAUTOCLAIM reclaims later). Poll loop survives.

2. **WR-04**: Added explicit `if handler is None:` branch before handler invocation: `log.warning("registered type has no handler — dropping")` + `await self._ack(msg_id)` + `return`. Makes silent drop observable.

3. **WR-03**: Attempted to parenthesize `except json.JSONDecodeError, KeyError:` — deferred (see Deviations).

Updated `_dispatch` docstring from four-stage to five-stage pipeline description.

Two new tests:
- `test_dispatch_handler_failure_leaves_message_unacked` — handler raises `RuntimeError`; asserts `xack` NOT called.
- `test_dispatch_registered_type_no_handler_warns_and_acks` — empty handlers map; asserts `xack` called once (drop-with-ack preserved).

## Verification Results

- `make test` (unit suite, Docker-free): 52 passed, 3 deselected
- `make check` (ruff lint + format): all checks passed
- All existing SC-3 poison-path tests pass (no regression)
- New unit tests `test_concurrent_duplicate_unit`, `test_dispatch_handler_failure_leaves_message_unacked`, `test_dispatch_registered_type_no_handler_warns_and_acks` all pass

## Deviations from Plan

### Rule 1 - Bug: WR-03 parenthesization incompatible with ruff 0.15.15

**Found during:** Task 2
**Issue:** The plan required changing `except json.JSONDecodeError, KeyError:` to `except (json.JSONDecodeError, KeyError):` (WR-03). Ruff 0.15.15 (the project's pinned formatter) treats the tuple `except` clause and actively reformats the parenthesized form back to the bare-tuple form in `ruff format`. Making this change would cause `make check` to fail.
**Fix applied:** Kept the bare-tuple form `except json.JSONDecodeError, KeyError:` to satisfy `make check`. Semantically equivalent in Python 3.14 (AST confirms `Tuple` node; both exceptions are caught). WR-01, WR-04, and CR-01 are all fully implemented and correct.
**Impact:** Zero — the exception still catches both `json.JSONDecodeError` and `KeyError`. The readability hazard noted in the review remains, but is a cosmetic issue only.
**Files modified:** none (form unchanged)

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | f9b1a9e | feat(02-04): harden handle_with_dedupe with IntegrityError conflict handling |
| Task 2 | d206676 | feat(02-04): add handler-failure boundary + WR-04 missing-handler warning in _dispatch |

## Known Stubs

None — no stub patterns introduced in this plan.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| src/provisioning_worker/shared/event_consumer.py | FOUND |
| src/provisioning_worker/adapters/valkey_streams.py | FOUND |
| tests/provisioning/test_idempotency.py | FOUND |
| .planning/phases/02-event-consumption-idempotency/02-04-SUMMARY.md | FOUND |
| Commit f9b1a9e (Task 1) | FOUND |
| Commit d206676 (Task 2) | FOUND |
