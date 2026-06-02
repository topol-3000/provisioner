---
phase: "03-registry-create-path-fake-adapter"
plan: "04"
subsystem: "provisioning"
tags: ["tests", "integration", "unit", "tdd", "fake-adapter", "convergence"]
dependency_graph:
  requires:
    - "03-01 (ports, models, spec builder, errors)"
    - "03-02 (fake adapters, repository layer)"
    - "03-03 (convergence engine, tasks, handlers, composition root)"
  provides:
    - "full-test-suite"
    - "prov-01-unique-violation-proof"
    - "prov-04-fault-injection-canonical-proof"
    - "prov-08-credential-once-proof"
    - "snap-01-enforcement-snapshot-proof"
    - "warning-4-repository-delegation-proof"
    - "warning-5-spec-round-trip-proof"
  affects: []
tech_stack:
  added: []
  patterns:
    - "DO $$ BEGIN ... EXCEPTION WHEN duplicate_object THEN NULL; END $$ for idempotent ENUM type creation"
    - "Pre-generate UUID via uuid7() before constructing FK-dependent ORM objects"
    - "MagicMock with explicit async attributes for sync-method session mocking"
    - "monkeypatch session_scope in tasks module for integration test isolation"
    - "spy_console_transport AsyncMock for credential-delivery call count assertions"
    - "in_memory_broker InMemoryBroker(await_inplace=True) for DI-wired task tests"
key_files:
  created: []
  modified:
    - tests/provisioning/test_models.py
    - tests/provisioning/test_spec.py
    - tests/provisioning/test_service.py
    - tests/provisioning/test_tasks.py
    - tests/conftest.py
    - src/provisioning_worker/modules/provisioning/service.py
key_decisions:
  - "Pre-generate UUID in service.open_instance: SQLAlchemy column default fires at INSERT (flush time), not object construction; passing instance.id to ProvisioningTask before flush yields None causing NotNullViolation"
  - "DO $$ BEGIN ... EXCEPTION WHEN duplicate_object $$ for ENUM types: Postgres does not support CREATE TYPE IF NOT EXISTS (unlike CREATE TABLE); anonymous block pattern is the correct idempotent approach"
  - "MagicMock with explicit async attrs for mock sessions: AsyncMock() makes add() an unawaited coroutine (PytestUnraisableExceptionWarning); must use MagicMock with .add=MagicMock() and .commit=AsyncMock()"
  - "session_scope patched in tasks_mod (not infrastructure.db): tasks.py imports session_scope directly via 'from ... import session_scope'; monkeypatch must target tasks_mod.session_scope, not the infrastructure module"
  - "test_concurrent_duplicate pre-existing failure excluded: CR-01 known issue from Phase 2, out of scope for this plan"
requirements-completed:
  - PROV-01
  - PROV-02
  - PROV-03
  - PROV-04
  - PROV-08
  - SNAP-01
duration: "~55m"
completed: "2026-06-02"
---

# Phase 03 Plan 04: Complete Test Suite (Wave 3) — Summary

**Full test coverage closing Phase 03: all VALIDATION.md rows green, Wave 0 stubs replaced, PROV-04 canonical fault-injection proof, PROV-08 credential-once guard, SNAP-01 enforcement snapshot, WARNING 4/5 delegation proofs.**

## Performance

- **Duration:** ~55 min
- **Started:** 2026-06-02T13:10:00Z
- **Completed:** 2026-06-02T14:05:00Z
- **Tasks:** 2 (TDD for both; plus 2 auto-fixed bugs)
- **Files modified:** 6

## Accomplishments

- `test_models.py`: Added 2 integration tests — `test_uuid_pk_version` (uuid7 PK assertion) and `test_subscription_id_unique_violation` (PROV-01 UNIQUE constraint proof)
- `test_spec.py`: Added `test_spec_resource_caps_is_mapping` (Mapping contract verification)
- `test_handlers.py`: Already complete from Plan 03-03; no changes needed
- `tests/conftest.py`: Added `in_memory_broker` fixture (InMemoryBroker wired with FakeDeploymentAdapter, FakeClock, spy_console_transport, Settings, ProvisioningService) and `spy_console_transport` AsyncMock fixture
- `test_service.py`: Replaced `pytest.skip` placeholder with `TestOpenInstance` class — 4 unit tests for `open_instance` (return types, staging both rows, pending status, spec.to_dict() payload)
- `test_tasks.py`: Replaced `pytest.skip` placeholder with full unit + integration suite:
  - Unit: `test_spec_rebuilt_from_payload` (WARNING 5 round-trip), `test_create_path_succeeds`, `test_credentials_sent_once`, `test_consumer_does_not_crash_on_adapter_failure`
  - Integration: `test_create_path_succeeds_integration` (PROV-02), `test_enforcement_snapshot_written` (SNAP-01), `test_create_fails_then_retries` (PROV-04 canonical proof), `test_no_credential_resend_on_retry` (PROV-08 guard)
- `service.py`: Fixed pre-flush UUID bug — pre-generate via `uuid7()` before constructing FK-dependent `ProvisioningTask`
- `conftest.py`: Fixed `CREATE TYPE IF NOT EXISTS` → idempotent `DO $$ ... EXCEPTION` pattern

## Task Commits

1. **Task 1: test_models, test_spec, conftest broker fixture** — `fe90be8` (test)
2. **Task 2: full test suites for test_tasks.py and test_service.py** — `80f0a7f` (feat)
3. **Bug fixes (2 integration blockers)** — `2e3a5ea` (fix)

## Files Created/Modified

- `tests/provisioning/test_models.py` — Added 2 integration tests: uuid7 PK version and UNIQUE violation
- `tests/provisioning/test_spec.py` — Added `test_spec_resource_caps_is_mapping`
- `tests/provisioning/test_service.py` — Replaced placeholder with TestOpenInstance (4 unit tests); added `_make_mock_session` and `_make_activated_payload` helpers
- `tests/provisioning/test_tasks.py` — Replaced placeholder with full suite: 8 unit tests + 4 integration tests; added `_make_instance` and `_make_task` with pre-assigned UUIDs
- `tests/conftest.py` — Added `spy_console_transport` and `in_memory_broker` fixtures; fixed `DO $$` ENUM creation; fixed `CREATE TYPE IF NOT EXISTS` bug
- `src/provisioning_worker/modules/provisioning/service.py` — Pre-generate UUID in `open_instance` before constructing `ProvisioningTask`

## Decisions Made

- **Pre-generate UUID before FK construction:** `instance.id` was None at object construction time (SQLAlchemy Python-side defaults fire at flush, not init). Fix: call `uuid7()` explicitly and pass `id=instance_id` to `Instance` and `instance_id=instance_id` to `ProvisioningTask`.
- **`DO $$ BEGIN ... EXCEPTION WHEN duplicate_object` pattern:** Postgres does not support `CREATE TYPE IF NOT EXISTS` in any version. The anonymous PL/pgSQL block is the canonical way to create types idempotently.
- **`MagicMock` with explicit async attrs for mock sessions:** `AsyncMock()` makes all attributes awaitable, but `session.add()` is sync in SQLAlchemy. Use `MagicMock()` with `session.add = MagicMock()`, `session.commit = AsyncMock()` to avoid `PytestUnraisableExceptionWarning`.
- **Patch `tasks_mod.session_scope` not `infrastructure.db.session_scope`:** `tasks.py` uses `from provisioning_worker.infrastructure.db import session_scope` — a direct name binding. Patching the infrastructure module has no effect; must patch the name in the tasks module namespace.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `CREATE TYPE IF NOT EXISTS` invalid Postgres syntax in conftest.py**
- **Found during:** Integration test execution (pg_engine fixture setup failure)
- **Issue:** Postgres does NOT support `IF NOT EXISTS` for `CREATE TYPE` in any version. All integration tests failed immediately at the `pg_engine` fixture with `SyntaxError`.
- **Fix:** Replaced with `DO $$ BEGIN ... EXCEPTION WHEN duplicate_object THEN NULL; END $$` anonymous PL/pgSQL block (correct idempotent pattern)
- **Files modified:** `tests/conftest.py`
- **Committed in:** `2e3a5ea`

**2. [Rule 1 - Bug] `open_instance` passed `None` as `instance_id` to `ProvisioningTask`**
- **Found during:** Integration test execution (NotNullViolation on `provisioning_task.instance_id`)
- **Issue:** SQLAlchemy Python-side column defaults (`default=uuid7`) are applied during INSERT/flush, not at object construction. `instance.id` was `None` when `ProvisioningTask(instance_id=instance.id)` was called, causing `NotNullViolation` on every integration test.
- **Fix:** Pre-generate `instance_id = uuid7()` before constructing either ORM object; pass explicitly as `Instance(id=instance_id, ...)` and `ProvisioningTask(instance_id=instance_id, ...)`
- **Files modified:** `src/provisioning_worker/modules/provisioning/service.py`
- **Committed in:** `2e3a5ea`

---

**Total deviations:** 2 auto-fixed (2 Rule 1 bugs)
**Impact on plan:** Both were blocking integration tests. The service.py fix is a production correctness fix — the bug would have manifested in real deployment when `handle_subscription_activated` ran, preventing any instance from being created.

## Verification Results

| Check | Result |
|-------|--------|
| `make test` (no Docker, `-m "not integration"`) | 125 passed, 0 failed |
| `make test-integration` (testcontainers Postgres) | 8 passed, 1 pre-existing failure (`test_concurrent_duplicate` CR-01) |
| `make check` (ruff lint + format) | All checks passed |
| `grep pytest.skip test_tasks.py test_service.py` | Empty (stubs fully replaced) |

## VALIDATION.md Coverage (per-requirement test map)

| Requirement | Test | Status |
|-------------|------|--------|
| PROV-01 UNIQUE constraint | `test_subscription_id_unique_violation` (integration) | Green |
| PROV-01 schema/constraints | `test_instance_columns`, `test_instance_subscription_id_unique` (unit) | Green |
| PROV-02 create path | `test_create_path_succeeds_integration` (integration) | Green |
| PROV-03 no line_count→seat_cap | `test_spec_uses_settings_defaults` (unit) | Green |
| PROV-04 fault injection | `test_create_fails_then_retries` (integration, PROV-04 canonical proof) | Green |
| PROV-08 credential once | `test_credentials_sent_once`, `test_no_credential_resend_on_retry` | Green |
| SNAP-01 enforcement snapshot | `test_enforcement_snapshot_written` (integration) | Green |
| WARNING 4 repository delegation | `test_delegates_to_insert_enforcement_snapshot` (unit) | Green |
| WARNING 5 spec round-trip | `test_instance_spec_round_trip`, `test_spec_rebuilt_from_payload` (unit) | Green |

## Threat Mitigations Applied

| ID | Component | Mitigation |
|----|-----------|-----------|
| T-3-12 | Credential tests | spy_console_transport asserts `call_count` only — no admin_password in test output |
| T-3-13 | Integration test isolation | `pg_session` fixture truncates all tables after each test (CASCADE confirmed) |

## Known Stubs

None — all test placeholders replaced with real assertions.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced.

## Self-Check: PASSED

Files confirmed present:
- tests/provisioning/test_models.py ✓
- tests/provisioning/test_spec.py ✓
- tests/provisioning/test_handlers.py ✓
- tests/provisioning/test_service.py ✓
- tests/provisioning/test_tasks.py ✓
- tests/conftest.py ✓
- src/provisioning_worker/modules/provisioning/service.py ✓

Commits confirmed:
- fe90be8: test(03-04): complete test_models, test_spec, test_handlers + conftest broker fixture
- 80f0a7f: feat(03-04): replace Wave 0 stubs with full test suites in test_tasks.py and test_service.py
- 2e3a5ea: fix(03-04): fix two integration-blocking bugs found during test execution
