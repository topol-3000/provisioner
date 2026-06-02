---
phase: "03-registry-create-path-fake-adapter"
plan: "03"
subsystem: "provisioning"
tags: ["taskiq", "convergence", "state-machine", "post-commit-enqueue", "tdd", "fake-adapter"]
dependency_graph:
  requires:
    - "03-01 (ports, models, spec builder, errors)"
    - "03-02 (fake adapters, repository layer)"
  provides:
    - "provisioning-convergence-service"
    - "create-instance-taskiq-task"
    - "post-commit-enqueue-pattern"
    - "handlers-real-body"
    - "main-composition-root-wired"
  affects: ["03-04"]
tech_stack:
  added: []
  patterns:
    - "async_shared_broker for deferred broker wiring without circular imports"
    - "ContextVar post-commit queue — register_post_commit drains after session.commit()"
    - "Receiver.listen(shutdown) for clean TaskGroup integration"
    - "ready_at IS NULL guard for idempotent credential delivery"
key_files:
  created: []
  modified:
    - src/provisioning_worker/shared/event_consumer.py
    - src/provisioning_worker/modules/provisioning/service.py
    - src/provisioning_worker/modules/provisioning/tasks.py
    - src/provisioning_worker/modules/provisioning/handlers.py
    - src/provisioning_worker/main.py
    - tests/provisioning/test_tasks.py
    - tests/provisioning/test_service.py
    - tests/provisioning/test_handlers.py
key_decisions:
  - "async_shared_broker used in tasks.py; main.py sets _default_broker = redis_broker before startup — avoids circular import, DI resolved at task execution time"
  - "_POST_COMMIT_ENQUEUE ContextVar uses None default (B039 compliance); reset to [] at each handle_with_dedupe invocation for cross-invocation isolation"
  - "ProvisioningService injected as TaskiqDepends in create_instance_task — not constructed inline in task body"
  - "settings.__class__ as DI key (not Settings class directly) to allow mock override in tests"
  - "Receiver.listen(shutdown) used as all-in-one task listener — not manual broker.listen() loop"
  - "source_event_id passed from handler raw_env.id to open_instance for provisioning_task traceability"
  - "Boot recovery queries status IN (pending, running) AND next_attempt_at <= now() OR NULL — covers freshly-opened tasks"
requirements-completed:
  - PROV-01
  - PROV-02
  - PROV-03
  - PROV-04
  - PROV-08
  - SNAP-01
duration: "~35m"
completed: "2026-06-02"
---

# Phase 03 Plan 03: Convergence Engine, Task Loop, and Composition Root — Summary

**End-to-end create path: `subscription.activated` drives `provisioning.instance` from `pending` to `ready` through `FakeDeploymentAdapter` with retry/backoff, enforcement snapshot, and once-only credential delivery.**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-06-02T12:56:00Z
- **Completed:** 2026-06-02T13:31:00Z
- **Tasks:** 2 (TDD: RED → GREEN → lint for Task 1; Task 2 direct)
- **Files modified:** 8

## Accomplishments

- `ProvisioningService` with sole state-machine guard (`validate_transition`), `open_instance` (handler domain logic), and `write_enforcement_snapshot` (WARNING 4 fix — only domain method that may call repository snapshot functions)
- `create_instance_task` Taskiq task via `async_shared_broker`: drives pending→deploying→configuring→ready, polls `get_instance_status`, writes enforcement snapshot at configuring, delivers credentials once on `ready_at IS NULL`, records failures with exponential backoff, re-kicks below max_attempts
- `_POST_COMMIT_ENQUEUE` ContextVar + `register_post_commit` in `event_consumer.py` — Pitfall 1 fix (T-3-11): Taskiq enqueue only fires after `session.commit()` succeeds
- `handle_subscription_activated` real body: calls `service.open_instance`, binds `instance_id`, registers post-commit enqueue callback
- `_run_convergence` in `main.py` fully wired: `FakeDeploymentAdapter` + `ConsoleNotificationTransport` + `SystemClock` + `ProvisioningService` DI; `Receiver.listen(shutdown)` task loop; boot-time recovery for overdue tasks (D-10)

## Task Commits

Each task was committed atomically:

1. **RED phase: failing tests** — `0fce2ee` (test)
2. **Task 1: service.py + tasks.py** — `ad2b0e5` (feat)
3. **Task 2: handlers.py + main.py** — `7b62d9a` (feat)

## Files Created/Modified

- `src/provisioning_worker/shared/event_consumer.py` — Added `_POST_COMMIT_ENQUEUE` ContextVar, `register_post_commit()`, post-commit drain in `handle_with_dedupe` (T-3-11)
- `src/provisioning_worker/modules/provisioning/service.py` — `ProvisioningService`: `validate_transition` state machine guard, `open_instance`, `write_enforcement_snapshot` (WARNING 4 fix)
- `src/provisioning_worker/modules/provisioning/tasks.py` — `create_instance_task` via `async_shared_broker`; `_compute_backoff_seconds`; full convergence loop with retry/backoff; `ready_at IS NULL` guard (D-13); secrets never logged (T-3-07)
- `src/provisioning_worker/modules/provisioning/handlers.py` — `handle_subscription_activated` real body; other four handlers remain no-ops
- `src/provisioning_worker/main.py` — `_run_convergence` fully wired with adapter DI, broker startup, `Receiver.listen(shutdown)`, boot recovery; `_recover_overdue_tasks` helper
- `tests/provisioning/test_tasks.py` — Backoff formula unit tests, task registration check
- `tests/provisioning/test_service.py` — State machine validation tests, `write_enforcement_snapshot` delegation tests
- `tests/provisioning/test_handlers.py` — Updated for real body: open_instance call test, instance_id binding, post-commit registration test

## Decisions Made

- **`async_shared_broker` pattern:** `tasks.py` uses `@async_shared_broker.task`; `main.py` sets `async_shared_broker._default_broker = redis_broker`. Avoids circular import (tasks can't import main.py) while ensuring `kiq()` sends to the real Redis stream. DI is resolved at task execution time, not decoration time.
- **`_POST_COMMIT_ENQUEUE` uses `None` default:** B039 (mutable ContextVar default) compliance. `handle_with_dedupe` resets to `[]` at each invocation.
- **`source_event_id` added to `open_instance`:** Required for `provisioning_task.source_event_id` traceability. Handler passes `raw_env.id`; the service interface exposes it as an optional kwarg with `""` default.
- **Boot recovery queries `pending` + `running`:** `running` status covers tasks that were mid-execution when the worker crashed — they need re-kick since the Taskiq job slot was lost.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] source_event_id parameter added to open_instance**
- **Found during:** Task 1 (implementing service.py)
- **Issue:** `ProvisioningTask.source_event_id` is `nullable=False` with no default. The plan's `open_instance` interface omitted this parameter. Without it, the DB insert would fail at runtime.
- **Fix:** Added `source_event_id: str = ""` parameter to `open_instance`. Handler passes `raw_env.id`.
- **Files modified:** `service.py`, `handlers.py`
- **Verification:** All tests pass including test_activated_calls_open_instance asserting the kwarg is passed.
- **Committed in:** ad2b0e5 (Task 1 feat commit)

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Required for DB correctness. No scope creep.

## Threat Mitigations Applied

| ID | Component | Mitigation |
|----|-----------|-----------|
| T-3-07 | structlog binding in tasks.py | `grep -n "password" tasks.py | grep "log\.\|bind_contextvars"` returns empty — no secret leak |
| T-3-09 | Double credential delivery on retry | `ready_at IS NULL` guard checked and set atomically in same DB transaction — D-13 |
| T-3-10 | Uncaught exception in task crashes consumer | `create_instance_task` top-level `try/except Exception` — broker loop sees clean return |
| T-3-11 | Post-commit enqueue fires before commit | `_POST_COMMIT_ENQUEUE` ContextVar drained only after `session.commit()` in `handle_with_dedupe` |

## Known Stubs

- `test_tasks.py` and `test_service.py`: Wave 2 unit tests pass; integration assertions (full convergence path, fault injection, credential delivery end-to-end) deferred to Plan 03-04 (Wave 3) as documented.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes beyond the documented Phase 3 scope.

## Self-Check: PASSED

Files confirmed present:
- src/provisioning_worker/shared/event_consumer.py ✓
- src/provisioning_worker/modules/provisioning/service.py ✓
- src/provisioning_worker/modules/provisioning/tasks.py ✓
- src/provisioning_worker/modules/provisioning/handlers.py ✓
- src/provisioning_worker/main.py ✓
- tests/provisioning/test_handlers.py ✓
- tests/provisioning/test_tasks.py ✓
- tests/provisioning/test_service.py ✓

Commits confirmed:
- 0fce2ee: test(03-03): add failing tests for service.py and tasks.py (RED phase)
- ad2b0e5: feat(03-03): implement ProvisioningService, create_instance_task, and post-commit enqueue
- 7b62d9a: feat(03-03): implement handlers.py real body + main.py composition root wiring
