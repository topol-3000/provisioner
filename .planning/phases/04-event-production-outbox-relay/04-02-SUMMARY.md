---
phase: 04-event-production-outbox-relay
plan: "02"
subsystem: event-production
tags:
  - outbox
  - relay
  - emit-seam
  - hostname-derivation
  - integration-tests

dependency_graph:
  requires:
    - 04-01
  provides:
    - OutboxRepo class with enqueue() (modules/provisioning/repository.py)
    - ProvisioningService.emit_instance_provisioned() (modules/provisioning/service.py)
    - tasks.py step 4 hostname derivation (D-08) + emit call in is_first_ready guard (D-01)
    - Real outbox_relay._drain_once with FOR UPDATE SKIP LOCKED (infrastructure/outbox_relay.py)
    - run_outbox_relay(settings, session_factory, bus, shutdown) — 4-param signature
    - ValkeyStreamsBus wired in main.py + bus.close() in finally
    - Valkey testcontainers fixtures (conftest.py)
    - All Phase 4 test stubs green (EVT-01 + EVT-02)
  affects:
    - modules/provisioning/repository.py (OutboxRepo added)
    - modules/provisioning/service.py (emit_instance_provisioned added)
    - modules/provisioning/tasks.py (step 4 updated, _transition_to_ready extracted)
    - infrastructure/outbox_relay.py (no-op replaced with real drain)
    - main.py (bus construction + relay wiring)
    - tests/provisioning/test_outbox.py (stubs → real tests)
    - tests/provisioning/test_tasks.py (EVT-02 stubs → real tests)
    - tests/conftest.py (Valkey fixtures added)
    - tests/test_boot.py (log message updated)

tech_stack:
  added:
    - "sqlalchemy.dialects.postgresql.insert (pg_insert) — ON CONFLICT DO NOTHING in OutboxRepo"
    - "testcontainers.redis.RedisContainer — Valkey 8 container for relay round-trip test"
    - "redis.asyncio.from_url — async_redis_client fixture for XRANGE assertions"
  patterns:
    - "Transactional outbox emit — emit_instance_provisioned in same session_scope() as ready transition (D-01)"
    - "is_first_ready guard — prevents double-enqueue on task retry (D-02)"
    - "Injected session_factory — relay never calls session_scope() directly (Pitfall 4)"
    - "FOR UPDATE SKIP LOCKED — multi-replica-safe batch drain (D-05)"
    - "Relay-never-dies — try/except + log.exception per iteration (D-03, D-04)"
    - "bus.close() before dispose_engine() — prevents connection leak on shutdown (Pitfall 7)"
    - "_transition_to_ready helper — extracted from _run_convergence to fix PLR0915"

key_files:
  created: []
  modified:
    - src/provisioning_worker/modules/provisioning/repository.py
    - src/provisioning_worker/modules/provisioning/service.py
    - src/provisioning_worker/modules/provisioning/tasks.py
    - src/provisioning_worker/infrastructure/outbox_relay.py
    - src/provisioning_worker/main.py
    - tests/provisioning/test_outbox.py
    - tests/provisioning/test_tasks.py
    - tests/conftest.py
    - tests/test_boot.py

decisions:
  - "D-01: emit inside same session_scope() as ready transition — OutboxRepo.enqueue in _transition_to_ready before session.commit()"
  - "D-08: hostname = f'{spec.slug}.{settings.instance_domain_suffix}', url = f'https://{hostname}' — single derivation in _transition_to_ready"
  - "PLR0915 fix: extracted _transition_to_ready() helper from _run_convergence; helper owns the session_scope + emit + commit"
  - "test_outbox_row_written_atomically: InMemoryBroker removed — task called directly with explicit args to avoid Taskiq DI RuntimeWarning on Python 3.14 annotation resolution"

metrics:
  duration: "~19 minutes"
  completed_date: "2026-06-03"
  tasks_completed: 2
  tasks_total: 2
  files_created: 0
  files_modified: 9
---

# Phase 04 Plan 02: Relay Wiring and Test Stubs Green Summary

## One-liner

End-to-end ready → outbox → relay → events.instance path implemented: OutboxRepo.enqueue (ON CONFLICT DO NOTHING), ProvisioningService.emit_instance_provisioned inside the ready transaction, real _drain_once with FOR UPDATE SKIP LOCKED, and all 8 Phase 4 test stubs turned green.

## What Was Built

### Task 1: Emit Seam

**`modules/provisioning/repository.py`** — `OutboxRepo` class added:
- Constructor takes `AsyncSession`; caller owns transaction
- `enqueue(envelope)`: `pg_insert(EventOutbox).values(...).on_conflict_do_nothing(index_elements=["envelope_id"])`
- `payload=envelope.model_dump(mode="json")` for JSON-native types in JSONB column (Pitfall 5)
- Calls `flush()` after execute; no commit (no-commit convention)

**`modules/provisioning/service.py`** — `emit_instance_provisioned` method added:
- Constructs `InstanceProvisionedPayload` from instance row fields (no credentials — D-09)
- Calls `EventEnvelope.build(type="instance.provisioned", causation_id=causation_id)`
- Instantiates `OutboxRepo(session)` and calls `await outbox.enqueue(envelope)`
- ONLY place an `instance.*` event is emitted (CLAUDE.md §6.1.1)
- Does NOT commit

**`modules/provisioning/tasks.py`** — step 4 updated:
- Added `source_event_id = task.source_event_id` in first session_scope block (CR-01 fix, D-09)
- Extracted `_transition_to_ready(instance_id, task_id, hostname, url, source_event_id, clock, service)` helper
- `hostname = f"{spec.slug}.{settings.instance_domain_suffix}"`, `url = f"https://{hostname}"` (D-08)
- `_transition_to_ready` does: validate_transition, update_instance_status, record_task_success, emit inside `if is_first_ready:` guard, commit
- Credential delivery block uses computed `url` variable (not `f"https://{spec.slug}"` placeholder)

**Rule 1 auto-fix:** Added `session.flush = AsyncMock()` to all unit test mock sessions in `test_tasks.py` — the new `OutboxRepo.enqueue` calls `await session.flush()` which was not mocked.

### Task 2: Relay + Wiring

**`infrastructure/outbox_relay.py`** — full real implementation replacing Phase 1 no-op:
- `run_outbox_relay(settings, session_factory, bus, shutdown)` — 4-param signature
- `_drain_once`: `SELECT … FOR UPDATE SKIP LOCKED`, batched, one txn per drain
- On publish success: `row.sent_at = datetime.now(UTC)`
- On any exception: `row.last_error = _truncate(repr(exc), 2000)`, `row.attempt_count += 1`
- `except Exception: log.exception("outbox relay iteration crashed")` — relay never dies
- `asyncio.wait_for + contextlib.suppress(TimeoutError)` sleep between iterations
- NEVER imports or calls `session_scope` — uses injected `session_factory`

**`main.py`** — updated:
- `from provisioning_worker.adapters.valkey_streams_bus import ValkeyStreamsBus`
- `from provisioning_worker.infrastructure.db import get_session_factory`
- `bus = ValkeyStreamsBus(settings)` before TaskGroup
- `run_outbox_relay(settings, get_session_factory(), bus, shutdown)`
- `await bus.close()` in finally before `await dispose_engine()`

**`tests/conftest.py`** — Valkey fixtures:
- `valkey_container`: session-scoped `RedisContainer("valkey/valkey:8")`
- `async_redis_client`: function-scoped redis.asyncio client, flushes after test

**`tests/provisioning/test_outbox.py`** — 5 stubs turned green:
- `test_drain_once_marks_sent`: mock session_factory + AsyncMock bus → asserts `sent_at` set
- `test_drain_once_records_failure`: mock bus raises RedisError → asserts `last_error`, `attempt_count=1`, `sent_at=None`
- `test_enqueue_idempotent`: real Postgres, two enqueues with same ULID → count=1
- `test_outbox_row_written_atomically`: real Postgres, create_instance_task → outbox row exists, sent_at=NULL
- `test_relay_xadd_roundtrip`: real Postgres + real Valkey → XRANGE events.instance shows producer=provisioning-worker

**`tests/provisioning/test_tasks.py`** — 3 EVT-02 stubs turned green:
- `test_emit_instance_provisioned_fields`: causation_id, hostname, url, snapshot_version, no admin_password
- `test_hostname_derivation`: update_instance_status called with correct FQDN hostname + url (spy + noop enqueue)
- `test_no_duplicate_emit_on_retry`: enqueue called exactly once even when task runs twice (ready_at set on second run)

**`tests/test_boot.py`** — Rule 1 fix:
- Updated expected log message from `"outbox relay started"` to `"outbox relay starting"` (new relay's startup log)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Mock sessions missing session.flush = AsyncMock()**
- **Found during:** Task 1
- **Issue:** `OutboxRepo.enqueue` calls `await session.flush()` but unit test mock sessions only mocked `commit`, `rollback`, `add`. The new `flush` call caused `TypeError: 'MagicMock' object can't be awaited` on all existing unit tests.
- **Fix:** Added `session.flush = AsyncMock()` to all 4 mock session setups in `test_tasks.py`
- **Files modified:** `tests/provisioning/test_tasks.py`
- **Commit:** `621f72c`

**2. [Rule 1 - Bug] test_boot.py expected wrong log message**
- **Found during:** Task 2
- **Issue:** `test_boot_log_lines_ordered` expected `"outbox relay started"` but new relay logs `"outbox relay starting"` (mirror of platform-api's log message)
- **Fix:** Updated test to expect `"outbox relay starting"` (correct message matching the relay body)
- **Files modified:** `tests/test_boot.py`
- **Commit:** `9b4d251`

**3. [Rule 1 - Bug] PLR0915: _run_convergence too many statements**
- **Found during:** Task 1
- **Issue:** Adding `source_event_id` capture + emit call pushed `_run_convergence` to 55 statements (limit 50)
- **Fix:** Extracted `_transition_to_ready()` private helper that owns step 4's session_scope + emit + commit
- **Files modified:** `tasks.py`
- **Commit:** `621f72c`

**4. [Rule 1 - Bug] Taskiq DI RuntimeWarning on Python 3.14 annotation resolution**
- **Found during:** Task 2 integration test run
- **Issue:** `test_outbox_row_written_atomically` created an `InMemoryBroker` with `ProvisioningService` as dependency. Taskiq 0.12 tries to resolve type hints via `get_type_hints(ProvisioningService.__init__)` but TYPE_CHECKING imports cause `NameError → RuntimeWarning`. pytest's `filterwarnings = ["error"]` turns this into a test failure.
- **Fix:** Removed the InMemoryBroker from the test entirely — `create_instance_task` was being called with explicit arguments anyway, making the broker unnecessary.
- **Files modified:** `tests/provisioning/test_outbox.py`
- **Commit:** `9b4d251`

### Pre-existing Issues (Out of Scope)

**`test_concurrent_duplicate` (pre-existing, confirmed pre-Phase 4):**
- `tests/provisioning/test_idempotency.py::test_concurrent_duplicate` was already failing before our changes (confirmed by stashing and running the test on the previous commit)
- Cause: dedupe guard IntegrityError handling on concurrent reclaim-race path (noted in STATE.md)
- NOT introduced by Phase 4; out of scope for this plan
- Logged to deferred-items as a pre-existing issue

## Known Stubs

None — all 8 Plan 02 stubs are now green.

## Threat Flags

No new threat surface introduced beyond what was declared in the plan's threat_model. All T-04-06 through T-04-11 mitigations are implemented:
- T-04-06: InstanceProvisionedPayload has no credentials (D-09)
- T-04-07: relay last_error uses str(exc) not repr in log; repr goes to DB column bounded by _truncate
- T-04-08: emit inside `if is_first_ready:` guard (Pitfall 2); ON CONFLICT DO NOTHING backstop
- T-04-09: with_for_update(skip_locked=True) prevents stuck row blocking batch
- T-04-10: bus.close() in finally block before dispose_engine()
- T-04-11: source_event_id captured in first session_scope (CR-01 fix)

## Self-Check: PASSED

Modified files exist:
- `src/provisioning_worker/modules/provisioning/repository.py` ✓ (OutboxRepo class)
- `src/provisioning_worker/modules/provisioning/service.py` ✓ (emit_instance_provisioned)
- `src/provisioning_worker/modules/provisioning/tasks.py` ✓ (_transition_to_ready + hostname fix)
- `src/provisioning_worker/infrastructure/outbox_relay.py` ✓ (_drain_once)
- `src/provisioning_worker/main.py` ✓ (ValkeyStreamsBus + get_session_factory)
- `tests/provisioning/test_outbox.py` ✓ (real tests)
- `tests/provisioning/test_tasks.py` ✓ (EVT-02 green)
- `tests/conftest.py` ✓ (Valkey fixtures)
- `tests/test_boot.py` ✓ (log message fix)

Commits exist:
- `621f72c` — feat(04-02): emit seam ✓
- `9b4d251` — feat(04-02): relay wiring ✓

Test results:
- `make test`: 130 passed, 12 deselected ✓
- `make test-integration`: 11 passed (1 pre-existing failure: test_concurrent_duplicate, confirmed pre-Phase 4) ✓
- `make check`: All checks passed ✓
- `grep -c "session_scope" outbox_relay.py`: 0 ✓
