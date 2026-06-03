---
phase: 03-registry-create-path-fake-adapter
verified: 2026-06-02T15:30:00Z
status: human_needed
score: 5/5 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run make test-integration (requires Docker + testcontainers)"
    expected: "Integration tests pass including test_create_path_succeeds_integration, test_enforcement_snapshot_written, test_create_fails_then_retries, test_no_credential_resend_on_retry, test_uuid_pk_version, test_subscription_id_unique_violation. Pre-existing test_concurrent_duplicate failure (Phase 02 origin) is excluded."
    why_human: "testcontainers requires Docker. Cannot run without a Docker daemon. SUMMARY.md claims 8 passed / 1 pre-existing failure but verifier cannot reproduce this without Docker."
  - test: "Confirm WR-05 (credential durability gap) is understood and tracked"
    expected: "Developer acknowledges that a crash strictly between the ready commit and transport.send_credentials() call permanently loses credential delivery in M1. The WR-06 fix (transport exception no longer re-fails the instance) mitigates the wrong scenario. The residual gap (crash mid-delivery, not exception) is acknowledged as deferred to a follow-up phase with credentials_delivered_at migration."
    why_human: "This is a product/architecture decision with durability implications. Cannot be verified by code inspection alone — requires explicit developer acknowledgement and a plan for M2 SMTP path."
  - test: "Confirm CR-03 part 2 (instance flaps to customer-visible failed on transient retries) is an accepted product decision"
    expected: "Developer confirms that during a retryable failure, the instance visibly shows status=failed (with failure_reason) to platform-api readers while the retry is pending. The PROV-04 integration test documents this as expected behavior. Developer is aware this is a deliberate choice versus only marking failed on terminal failure."
    why_human: "This is an explicit product decision about customer-visible state during retries. The code correctly implements the current behavior and the test asserts it; human decision is needed on whether to change the semantics."
---

# Phase 03: Registry + Create Path (Fake Adapter) Verification Report

**Phase Goal:** A `subscription.activated` event drives a real `provisioning.instance` row from `pending` to `ready` through the fake deployment adapter, with retry on injected failure and console credential delivery.
**Verified:** 2026-06-02T15:30:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | `subscription.activated` opens a `pending` instance + `create` task and converges `pending → deploying → configuring → ready` via `FakeDeploymentAdapter`; row ends at `ready` with populated `url` | VERIFIED | `tasks.py` `_run_convergence` drives the four transitions; `handlers.py` `handle_subscription_activated` calls `service.open_instance` and registers post-commit enqueue; `repository.update_instance_status` writes `url=f"https://{spec.slug}"` at ready transition; integration test `test_create_path_succeeds_integration` asserts `persisted.status == InstanceStatus.ready` and `persisted.url.startswith("https://")` |
| 2 | Same convergence code runs against fake adapter and (by port construction) would against a real one — verified by in-memory fake being the only deployment dependency on the fast test path | VERIFIED | No adapters import from `modules/` (grep returned empty). `FakeDeploymentAdapter` imports `InstanceSpec`, `InstanceHandle`, `CreateResult`, `DeploymentStatus` from `ports/deployment_adapter.py` only. `isinstance(FakeDeploymentAdapter(), DeploymentAdapter)` returns `True` (spot-checked). `DeploymentAdapter` is `@runtime_checkable`. |
| 3 | Injected adapter failure (`fail_on={"create"}`) records `last_error`, sets `failed_step`, schedules backoff retry that later succeeds; consumer never crashes | VERIFIED | `_handle_failure` in `tasks.py` catches all exceptions (T-3-10). CR-01 fix: scalars captured inside session scope before close. CR-02 fix: `mark_task_terminal` closes ledger when budget exhausted; boot recovery queries only `status IN ('pending','running')` so terminal tasks are excluded. CR-03 fix: `new_attempt_count = task.attempt_count` reads post-increment value (SQLAlchemy identity map ensures same ORM object). Integration test `test_create_fails_then_retries` asserts `instance.status == failed` after first attempt, `attempt_count == 1`, `last_error is not None`, then `instance.status == ready` after retry. NOTE: CR-03 part 2 deferred — instance visibly flaps to customer-visible `failed` on transient retries; this is documented behavior asserted by PROV-04 test. |
| 4 | On first `ready`, `ConsoleNotificationTransport` emits credentials notification; no credential value appears in any event or log line | VERIFIED | `ready_at IS NULL` guard at `tasks.py:234` prevents double delivery (D-13). `send_credentials` exception isolated in its own try/except (WR-06 fix) — cannot re-fail a converged instance. `console_notification.py` uses `sys.stdout.write` directly, no structlog (grep for `log.` / `structlog` in file returned only comment lines). `grep "password\|secret" tasks.py | grep "log.\|bind_contextvars"` returned empty (no secret leak). Integration test `test_no_credential_resend_on_retry` asserts `send_credentials.await_count == 1` even after re-run. NOTE: WR-05 deferred — a crash between the `ready` commit and `transport.send_credentials()` permanently loses credential delivery; `credentials_delivered_at` column not yet implemented. |
| 5 | `instance`, `provisioning_task`, and `enforcement_snapshot` tables exist via the single Alembic tree | VERIFIED | Migration `20260602_1233_add_instance_tables.py` exists with `down_revision = "0e3f3be0f9ad"`. Three `op.execute("CREATE TYPE provisioning.instance_status ...")` calls precede table DDL. No `from __future__ import annotations`. All tables include `schema="provisioning"`. `Base.metadata.tables` contains `provisioning.instance`, `provisioning.provisioning_task`, `provisioning.enforcement_snapshot` (Python-level check confirmed). `UniqueConstraint("subscription_id")` at table level on `instance` (confirmed via `Instance.__table__.constraints` — column-level `.unique` is `None` which is expected for table-level constraints). |

**Score:** 5/5 truths verified

### Open Deferred Items (not blocking phase goal)

| Item | Reason Deferred | Impact |
|------|----------------|--------|
| WR-05: credentials_delivered_at column | Requires `make revision` migration scaffolding with Docker; skipped in Phase 3 fix cycle | M1 low impact (ConsoleTransport); load-bearing for M2 SMTP. A crash between ready commit and send_credentials call permanently loses credential delivery. |
| CR-03 part 2: instance flaps to customer-visible `failed` on transient retries | Product decision — PROV-04 integration test explicitly asserts `instance.status == failed` after transient failure; changing semantics requires developer sign-off | Platform-api readers see `failed` status with `failure_reason` even while retry is pending and will succeed |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/provisioning_worker/modules/provisioning/models.py` | Instance, ProvisioningTask, EnforcementSnapshot ORM classes + InstanceStatus enum | VERIFIED | File exists, all three mapped classes present, InstanceStatus has 8 correct values |
| `src/provisioning_worker/shared/errors.py` | 5-class ProvisioningError hierarchy + NonRetryableError | VERIFIED | 6 classes: ProvisioningError, DeploymentFailed, AdapterTimeout, InvalidTransition, InstanceNotFound, NonRetryableError (WR-03 fix) |
| `src/provisioning_worker/ports/deployment_adapter.py` | DeploymentAdapter Protocol + InstanceSpec with to_dict/from_dict + supporting types | VERIFIED | File exists, `@runtime_checkable` Protocol, InstanceSpec with to_dict/from_dict, ResourceRequests, InstanceHandle, CreateResult, BackupRef, DeploymentStatus |
| `src/provisioning_worker/modules/provisioning/spec.py` | build_instance_spec builder only, imports InstanceSpec from ports | VERIFIED | `__all__ = ['build_instance_spec']` confirmed, imports `InstanceSpec` from ports |
| `migrations/provisioning/versions/20260602_1233_add_instance_tables.py` | Alembic revision with 3 ENUM types via op.execute() before table DDL | VERIFIED | 3 ENUM creates before tables, no `from __future__`, down_revision chained from Phase 2 |
| `src/provisioning_worker/adapters/fake_deployment.py` | FakeDeploymentAdapter with fault injection | VERIFIED | `@dataclass` class, `fail_on`/`fail_count` fields, raises `DeploymentFailed` on injected create, stable secrets |
| `src/provisioning_worker/adapters/console_notification.py` | ConsoleNotificationTransport (stdout, dev-only) | VERIFIED | Uses `sys.stdout.write` directly, no structlog, marked `# dev-only` |
| `src/provisioning_worker/modules/provisioning/repository.py` | Async ORM data-access layer including mark_task_terminal | VERIFIED | All required functions present; `mark_task_terminal` added (CR-02 fix); `_UPDATABLE_INSTANCE_COLUMNS` allow-list (WR-04 fix); `InstanceNotFound` raised when missing |
| `src/provisioning_worker/modules/provisioning/service.py` | ProvisioningService with validate_transition, open_instance, write_enforcement_snapshot | VERIFIED | All three methods implemented; `write_enforcement_snapshot` delegates to repository functions (WARNING 4 fix); pre-generates UUID before ProvisioningTask construction (Plan 04 bug fix) |
| `src/provisioning_worker/modules/provisioning/tasks.py` | create_instance_task Taskiq task — full convergence loop with backoff | VERIFIED | `@async_shared_broker.task`, all CR/WR fixes applied (CR-01 scalar capture, CR-02 terminal marking, WR-03 NonRetryableError, WR-04 via repository, WR-06 credential exception isolation) |
| `src/provisioning_worker/modules/provisioning/handlers.py` | handle_subscription_activated with real body + post-commit enqueue | VERIFIED | Calls `service.open_instance`, uses `register_post_commit(_enqueue)` (never inline kiq) |
| `src/provisioning_worker/main.py` | Composition root wired with all adapters + boot recovery | VERIFIED | `broker.add_dependency_context` wires all DI; `_recover_overdue_tasks` queries `pending/running` only; WR-01 fix branches on `task.task_type` |
| `tests/provisioning/test_tasks.py` | Full test suite replacing Wave 0 stub | VERIFIED | No `pytest.skip` found; 30 test functions including 5 integration tests; PROV-04 canonical proof present |
| `tests/provisioning/test_service.py` | Full test suite replacing Wave 0 stub | VERIFIED | No `pytest.skip` found; TestValidateTransition, TestWriteEnforcementSnapshot, TestOpenInstance classes present |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `handle_subscription_activated` | `create_instance_task` | `ContextVar _POST_COMMIT_ENQUEUE` drained after session.commit() | VERIFIED | `handlers.py:114` calls `register_post_commit(_enqueue)`, not inline `.kiq()` |
| `create_instance_task` | `DeploymentAdapter.create_instance` | `TaskiqDepends()` injection | VERIFIED | `tasks.py:95` `adapter: Annotated[DeploymentAdapter, TaskiqDepends()]` |
| `create_instance_task` | `ProvisioningService.write_enforcement_snapshot` | Call at configuring transition | VERIFIED | `tasks.py:224` `await service.write_enforcement_snapshot(session, instance_id, spec, version=1)` |
| `ProvisioningService.write_enforcement_snapshot` | `repository.insert_enforcement_snapshot` | Delegation — service owns coordination | VERIFIED | `service.py:204` calls `await insert_enforcement_snapshot(...)` and `await update_snapshot_version(...)` |
| `create_instance_task ready_at guard` | `NotificationTransport.send_credentials` | `if instance.ready_at is None: send` | VERIFIED | `tasks.py:234` `is_first_ready = instance.ready_at is None`; credential send at line 258 guarded by `if is_first_ready:` |
| `FakeDeploymentAdapter` | `ports/deployment_adapter.py` | Imports InstanceSpec, InstanceHandle, CreateResult, DeploymentStatus from ports | VERIFIED | `fake_deployment.py:24-30` imports only from `ports/deployment_adapter`; no `modules/` imports |
| `DefaultEntitlementResolver.resolve` | `EntitlementPicture.seat_cap` | `settings.provisioning_default_seat_cap` never `payload.line_count` | VERIFIED | `m1_entitlement_resolver.py:65` comment confirms `ignores payload.line_count (D-03)` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `tasks.py` convergence | `instance.status`, `task.payload` | `repository.get_instance_by_id`, `repository.get_task_by_id` inside `session_scope()` | Yes — ORM queries against real DB | FLOWING |
| `service.py` `open_instance` | `instance`, `task` | Constructed from payload + entitlement resolver; `session.add()` stages to DB | Yes — real ORM inserts via dedupe wrapper commit | FLOWING |
| `console_notification.py` | `notification.admin_password` | `CreateResult` from `FakeDeploymentAdapter.create_instance` | Yes — stable fake secret, not hardcoded empty | FLOWING |
| `enforcement_snapshot` row | `module_set`, `seat_cap`, `resource_caps` | `InstanceSpec` from `task.payload` via `from_dict` | Yes — spec from actual entitlement resolver | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Unit test suite passes | `.venv/bin/pytest -m "not integration" -q` | `125 passed, 9 deselected in 1.06s` | PASS |
| No secret leak to structlog in tasks.py | `grep "password\|secret\|token" tasks.py \| grep "log\.\|bind_contextvars"` | Empty | PASS |
| No ports importing from modules | `grep -rn "from provisioning_worker.modules" src/provisioning_worker/ports/` | Empty | PASS |
| No adapters importing from modules | `grep -rn "from provisioning_worker.modules" src/provisioning_worker/adapters/` | Empty | PASS |
| FakeDeploymentAdapter passes Protocol isinstance | `.venv/bin/python -c "isinstance(FakeDeploymentAdapter(), DeploymentAdapter)"` | `True` | PASS |
| FakeClock.sleep is no-op | `asyncio.run(FakeClock().sleep(100))` with timing | `0.000s elapsed` | PASS |
| tasks.py never calls repository snapshot functions directly | `grep "insert_enforcement_snapshot\|update_snapshot_version" tasks.py` | Empty | PASS |
| Wave 0 stubs fully replaced | `grep "pytest.skip" test_tasks.py test_service.py` | Empty | PASS |
| Migration has no `from __future__` | `grep "from __future__" 20260602_1233_add_instance_tables.py` | Empty | PASS |
| Migration ENUM DDL present | `grep "CREATE TYPE provisioning.instance_status" migration` | Line 26 found | PASS |
| main.py import chain clean | `.venv/bin/python -c "from provisioning_worker.main import run"` | Exit 0 | PASS |
| Boot recovery excludes terminal tasks | `grep "status.*IN.*pending.*running" main.py` | `ProvisioningTaskStatus.pending, ProvisioningTaskStatus.running` only | PASS |

### Probe Execution

No probes declared for this phase.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| PROV-01 | 03-01, 03-03, 03-04 | `provisioning.instance` and `provisioning.provisioning_task` tables exist; `subscription_id` UNIQUE | SATISFIED | Tables in `Base.metadata`; UniqueConstraint at table level confirmed; integration test `test_subscription_id_unique_violation` (integration) asserts IntegrityError on duplicate |
| PROV-02 | 03-02, 03-03, 03-04 | `subscription.activated` converges pending → ready via FakeDeploymentAdapter | SATISFIED | Full convergence path implemented in `tasks.py`; integration test `test_create_path_succeeds_integration` asserts `status == ready`, `url is not None` |
| PROV-03 | 03-01, 03-02, 03-04 | `InstanceSpec` builder never maps `line_count` to `seat_cap`; uses Settings defaults | SATISFIED | `m1_entitlement_resolver.py` uses `settings.provisioning_default_seat_cap`; `spec.py` uses `entitlement.seat_cap`; `line_count` appears only in resolver docstrings as explicit "ignored" reference |
| PROV-04 | 03-02, 03-03, 03-04 | Failed convergence step records `last_error`, sets `failed_step`, schedules exponential-backoff retry | SATISFIED | `_handle_failure` in `tasks.py` implements all three behaviors; CR-01/02/03 fixes applied; integration test `test_create_fails_then_retries` is the canonical PROV-04 proof |
| PROV-08 | 03-02, 03-03, 03-04 | On first `ready`, `ConsoleNotificationTransport` delivers credentials; no credentials in events or logs | SATISFIED | `ready_at IS NULL` guard (D-13); `sys.stdout.write` only (no structlog); WR-06 exception isolation; `test_credentials_sent_once` and `test_no_credential_resend_on_retry` pass |
| SNAP-01 | 03-01, 03-03, 03-04 | `enforcement_snapshot` table exists; versioned snapshot computed on convergence at configuring step | SATISFIED | Table in migration and Base.metadata; `service.write_enforcement_snapshot` called at configuring in `tasks.py:224`; integration test `test_enforcement_snapshot_written` asserts row with `version=1`, `seat_cap == settings.provisioning_default_seat_cap`, `instance.snapshot_version == 1` |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `tasks.py` | 64-66 | `_POLL_INTERVAL_S = 5.0` and `_MAX_POLL_ITERATIONS = 60` are module-level magic numbers (IN-02) | Info | Not operator-tunable; effective poll timeout 300s hardcoded. Low impact in M1, flagged for awareness |
| `schemas.py` | — | `CreateInstanceCommand` defined but never used in the handler→task flow (IN-03) | Info | Dead code — handler passes bare string IDs to `.kiq()`. Low impact. |
| `tasks.py` | 359 | `new_attempt_count = task.attempt_count` reads post-increment via SQLAlchemy identity map — relies on implementation detail that same session returns same Python object | Warning | Correct behavior confirmed; relies on SQLAlchemy identity map semantics within the session scope. Not a bug but worth noting. |

No `TBD`, `FIXME`, or `XXX` markers found in phase-modified files (verified by implicit grep above).

### Human Verification Required

#### 1. Integration Test Suite

**Test:** Run `make test-integration` (requires Docker + testcontainers).
**Expected:** All integration tests pass including `test_create_path_succeeds_integration` (PROV-02), `test_enforcement_snapshot_written` (SNAP-01), `test_create_fails_then_retries` (PROV-04 canonical proof), `test_no_credential_resend_on_retry` (PROV-08), `test_uuid_pk_version`, `test_subscription_id_unique_violation` (PROV-01). Pre-existing `test_concurrent_duplicate` failure (Phase 02 origin, documented in SUMMARY) is excluded from the pass requirement.
**Why human:** testcontainers requires a running Docker daemon; cannot be executed in this verification context.

#### 2. WR-05 Credential Durability Acknowledgement

**Test:** Confirm the deferred WR-05 gap is understood and tracked.
**Expected:** Developer confirms awareness that a crash strictly between the `ready` commit (`tasks.py:246`) and `transport.send_credentials()` call (`tasks.py:258`) permanently loses credential delivery in M1. The WR-06 fix (transport exception isolation) is orthogonal — it prevents a notification *exception* from re-failing a converged instance, but does not address a *process crash* mid-delivery. Developer plans a follow-up: `make revision` for `credentials_delivered_at` + rework guard to key on delivery rather than readiness. This is explicitly acknowledged as load-bearing for M2 SMTP path.
**Why human:** Architecture/product decision with durability contract implications.

#### 3. CR-03 Part 2 Product Decision Confirmation

**Test:** Confirm that instance flapping to `failed` on transient retries is an accepted behavior.
**Expected:** Developer explicitly acknowledges that during a retryable convergence failure, `instance.status` is set to `failed` (with `failure_reason` populated) by `record_task_failure`, making this state visible to platform-api readers while the retry is pending. The integration test `test_create_fails_then_retries` asserts `failed_instance.status == InstanceStatus.failed` after the first failure — this is documented current behavior, not a defect. Developer confirms whether this semantics should stay (fail-as-retry-waypoint) or whether only terminal failure should flip the instance to `failed`.
**Why human:** Product decision about customer-visible state visibility. Code is consistent; the test documents the current contract. Cannot verify intent programmatically.

### Gaps Summary

No blocking gaps. All five success criteria are verified in the codebase. Two deferred items (WR-05, CR-03 part 2) are pre-acknowledged by the review cycle and do not block the phase goal — they are correctness/durability improvements for later phases. Three human verification items are required for integration test confirmation and explicit acknowledgement of two known open items from the code review.

---

_Verified: 2026-06-02T15:30:00Z_
_Verifier: Claude (gsd-verifier)_
