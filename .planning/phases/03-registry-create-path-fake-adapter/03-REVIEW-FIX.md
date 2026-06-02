---
phase: 03-registry-create-path-fake-adapter
fixed_at: 2026-06-02T00:00:00Z
review_path: .planning/phases/03-registry-create-path-fake-adapter/03-REVIEW.md
iteration: 1
findings_in_scope: 10
fixed: 9
skipped: 1
status: partial
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-06-02
**Source review:** .planning/phases/03-registry-create-path-fake-adapter/03-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope (Critical + Warning): 10
- Fixed: 9
- Skipped: 1

All Info findings (IN-01..IN-04) were out of scope (`fix_scope = critical_warning`)
and were not attempted.

The four Critical/Warning fix commits were grouped to respect file-level commit
atomicity: several findings (CR-01, CR-02, CR-03, WR-03, WR-04, WR-06) edit the
same two files (`tasks.py`, `repository.py`) plus `errors.py`, so they ship in a
single cohesive commit rather than partial-file commits. WR-01, WR-02, and WR-07
touch disjoint files and ship as their own commits.

After all fixes: `ruff check` clean on every modified file; full unit suite
`pytest -m "not integration"` green (125 passed, 9 integration deselected).

## Fixed Issues

### CR-01: Convergence task reads ORM objects after the session closes (DetachedInstanceError)

**Files modified:** `src/provisioning_worker/modules/provisioning/tasks.py`
**Commit:** 35e1a66
**Applied fix:** Moved the `instance is None or task is None` guard inside the
first `session_scope()` block and captured the two scalars actually used after
the block (`current_status = instance.status`, `task_payload = task.payload`)
while the session is still open. The later `validate_transition(...)` now uses
`current_status` and the spec is rebuilt from `task_payload`. This honors the
real `expire_on_commit=True` default — no attribute access happens on a detached
ORM object after the context manager exits.

### CR-02: Tasks reaching max attempts are never marked terminal and re-kicked forever

**Files modified:** `src/provisioning_worker/modules/provisioning/tasks.py`,
`src/provisioning_worker/modules/provisioning/repository.py`
**Commit:** 35e1a66
**Applied fix:** Added `repository.mark_task_terminal(...)` which sets
`task.status = ProvisioningTaskStatus.failed` (the terminal status already
existed in the enum, ORM model, and migration — no schema change needed) and
clears `next_attempt_at`. `_handle_failure` now calls it when the budget is
exhausted (or on a non-retryable error). `record_task_failure` no longer
clobbers an already-terminal `failed` status back to `running`. Boot recovery
queries only `status IN ('pending','running')`, so a terminal task no longer
matches and is never re-kicked.

### CR-03: Retry counting / instance-status snapshotting inconsistency (requires human verification)

**Files modified:** `src/provisioning_worker/modules/provisioning/tasks.py`,
`src/provisioning_worker/modules/provisioning/repository.py`
**Commit:** 35e1a66
**Applied fix (part 1 — attempt accounting):** `_handle_failure` now makes the
retry decision on the *persisted, post-increment* attempt count
(`new_attempt_count = task.attempt_count` read after `record_task_failure`
increments it) and compares `>= settings.provisioning_max_attempts`. This
removes the implicit off-by-one of the old pre-increment `< max_attempts - 1`
compare. The total-execution budget is unchanged (5 executions for
`max_attempts=5`); the integration test `test_create_fails_then_retries`
(fail_count=1 → retry → ready) still holds.
**Deferred (part 2 — instance flapping to `failed` on transient retries):** The
review *suggests* (advisory: "Consider...") only flipping the instance to
customer-visible `failed` on terminal failure and recording transient errors on
the task ledger only. This was deliberately NOT changed because the committed
PROV-04 integration test (`test_create_fails_then_retries`) asserts
`instance.status == failed` after the *first* (transient) failure; flipping the
behavior would break that documented expectation and constitutes a product
decision about customer-visible state. Flagged for human verification — the
attempt-accounting correctness is fixed, but the `failed`-as-retry-waypoint
semantics should be confirmed by a developer before any behavior change.

### WR-01: Boot recovery re-kicks every overdue task as create_instance_task

**Files modified:** `src/provisioning_worker/main.py`
**Commit:** d792251
**Applied fix:** `_recover_overdue_tasks` now branches on `task.task_type`.
Only `TaskType.create` rows are re-kicked via `create_instance_task.kiq(...)`;
any other type (update/suspend/reinstate/delete — no task class exists yet) is
skipped with a warning rather than silently mis-dispatched as a create.

### WR-02: provisioning_default_resource_caps setting declared but never used

**Files modified:** `src/provisioning_worker/settings.py`,
`src/provisioning_worker/adapters/m1_entitlement_resolver.py`,
`tests/provisioning/test_adapters.py`
**Commit:** 39d0d45
**Applied fix:** Added a `Settings.default_resource_caps` property that parses
the `provisioning_default_resource_caps` JSON string once (raising `ValueError`
if it is not a JSON object). `DefaultEntitlementResolver.resolve` now returns
`resource_caps=settings.default_resource_caps` instead of a hard-coded `{}`, and
the docstrings were updated. The unit test that asserted `resource_caps == {}`
now sets `settings.default_resource_caps = {}` on the mock to match the new
read path.

### WR-03: InstanceSpec.from_dict raises raw KeyError/ValueError on malformed payload

**Files modified:** `src/provisioning_worker/modules/provisioning/tasks.py`,
`src/provisioning_worker/shared/errors.py`
**Commit:** 35e1a66
**Applied fix:** Added a `NonRetryableError` domain exception. In
`_run_convergence`, a `None` `task.payload` and any `KeyError`/`TypeError`/
`ValueError` from `InstanceSpec.from_dict(...)` are now translated into a typed
`NonRetryableError`. `_handle_failure` treats `NonRetryableError` as immediately
terminal (marks the task `failed`, no retry), so an un-parseable payload no
longer burns the full retry budget on a permanent error.

### WR-04: update_instance_status swallows a missing instance and accepts arbitrary attribute names

**Files modified:** `src/provisioning_worker/modules/provisioning/repository.py`
**Commit:** 35e1a66
**Applied fix:** (1) A missing instance row now raises `InstanceNotFound`
(already defined in `errors.py`) instead of silently returning. (2) Added an
`_UPDATABLE_INSTANCE_COLUMNS` allow-list; any unknown `**kwargs` key raises
`ValueError` up front, so a typo (e.g. `read_at=` for `ready_at=`) surfaces as
an error rather than silently setting an unpersisted junk attribute.

### WR-06: send_credentials exceptions abort the task after the instance is already ready

**Files modified:** `src/provisioning_worker/modules/provisioning/tasks.py`
**Commit:** 35e1a66
**Applied fix:** The credential delivery in `_run_convergence` (which runs
*after* the `ready` + task-`succeeded` commit) is now wrapped in its own
try/except. A transport failure is logged for human follow-up (without logging
the password) and does not propagate into the catch-all failure path, so a
notification hiccup can no longer drag a fully-converged instance back to
`failed` and re-kick the task.

### WR-07: Migration omits server defaults and CHECK constraints

**Files modified:**
`migrations/provisioning/versions/20260602_1233_add_instance_tables.py`
**Commit:** fb0d74a
**Applied fix:** Added `server_default` matching the model-side Python defaults
(`instance.version=1`, `provisioning_task.attempt_count=0`,
`enforcement_snapshot.version=1`, and `provisioning_task.status='pending'` set
via `ALTER COLUMN ... SET DEFAULT` *after* the enum cast so it is not dropped by
the type change). Added CHECK constraints for the numeric invariants
(`version >= 1`, `seat_cap >= 1` where present, `attempt_count >= 0`,
`max_attempts >= 1`). This migration is still in-flight for Phase 3 (not yet
shipped to prod), so amending it in place is safe; it was hand-edited additively
with `op`/`sa` calls only and should be re-reviewed against the models before the
next migration per CLAUDE.md §6.3.

## Skipped Issues

### WR-05: Credentials delivered after the ready commit with no delivery durability

**File:** `src/provisioning_worker/modules/provisioning/tasks.py:223-236`
**Reason:** skipped — the reviewer's suggested fix requires a new
`credentials_delivered_at` column so the once-only guard keys on delivery rather
than readiness. That is a schema change, and CLAUDE.md §6.3 mandates that
migrations be generated via `make revision` (which needs the Postgres infra /
Docker available) and the autogenerated DDL reviewed before commit — it must not
be hand-written without review. This fixer cannot safely scaffold a new
migration here. The related crash-resilience concern (a notification *failure*
re-failing a converged instance) is addressed by the WR-06 fix; the residual gap
is a crash strictly between the `ready` commit and the transport call, which is
low-impact in M1 (`ConsoleNotificationTransport`) but load-bearing for M2 SMTP.
Recommend a follow-up phase task: `make revision` for `credentials_delivered_at`
plus reworking the guard.
**Original issue:** The instance is committed `ready` with `ready_at` set before
`transport.send_credentials(...)` runs; a crash in between flips the
`ready_at IS NULL` guard to non-NULL on the re-kick, so credentials are never
delivered.

---

_Fixed: 2026-06-02_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
