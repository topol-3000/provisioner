---
phase: 03-registry-create-path-fake-adapter
reviewed: 2026-06-02T00:00:00Z
depth: standard
files_reviewed: 28
files_reviewed_list:
  - migrations/provisioning/versions/20260602_1233_add_instance_tables.py
  - src/provisioning_worker/adapters/console_notification.py
  - src/provisioning_worker/adapters/fake_deployment.py
  - src/provisioning_worker/adapters/m1_entitlement_resolver.py
  - src/provisioning_worker/adapters/system_clock.py
  - src/provisioning_worker/main.py
  - src/provisioning_worker/modules/provisioning/handlers.py
  - src/provisioning_worker/modules/provisioning/models.py
  - src/provisioning_worker/modules/provisioning/repository.py
  - src/provisioning_worker/modules/provisioning/schemas.py
  - src/provisioning_worker/modules/provisioning/service.py
  - src/provisioning_worker/modules/provisioning/spec.py
  - src/provisioning_worker/modules/provisioning/tasks.py
  - src/provisioning_worker/ports/clock.py
  - src/provisioning_worker/ports/deployment_adapter.py
  - src/provisioning_worker/ports/entitlement_resolver.py
  - src/provisioning_worker/ports/notification_transport.py
  - src/provisioning_worker/settings.py
  - src/provisioning_worker/shared/errors.py
  - src/provisioning_worker/shared/event_consumer.py
  - tests/conftest.py
  - tests/provisioning/test_adapters.py
  - tests/provisioning/test_handlers.py
  - tests/provisioning/test_models.py
  - tests/provisioning/test_repository.py
  - tests/provisioning/test_service.py
  - tests/provisioning/test_spec.py
  - tests/provisioning/test_tasks.py
findings:
  critical: 3
  warning: 7
  info: 4
  total: 14
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-06-02
**Depth:** standard
**Files Reviewed:** 28
**Status:** issues_found

## Summary

The create-path convergence pipeline (handler -> open rows -> Taskiq task ->
fake adapter -> ready + credentials) is largely well-structured and adheres to
the project's ports/adapters discipline, idempotency layering, and "secrets
never logged" rules. The state machine, the post-commit enqueue pattern, and
the stable-secret fake adapter are correct.

However, the convergence task contains a **detached-ORM-object bug** that will
crash on the real engine (masked by the unit tests, which use `MagicMock`
sessions). The retry/terminal-failure accounting in `_handle_failure` /
`record_task_failure` has two correctness defects: a task that reaches max
attempts is **never marked terminal** and is re-kicked forever by boot
recovery, and the instance is forced to `failed` even on transient retryable
failures, then the next attempt relies on an in-memory status snapshot that no
longer matches the DB. The boot recovery path also re-kicks the wrong task
class for non-create tasks (only `create_instance_task` exists, so any future
update/suspend task row would be mis-dispatched). These should be fixed before
shipping.

## Critical Issues

### CR-01: Convergence task reads ORM objects, then uses them after the session closes (DetachedInstanceError)

**File:** `src/provisioning_worker/modules/provisioning/tasks.py:164-184`
**Issue:**
`_run_convergence` loads `instance` and `task` inside one `session_scope()`
block, lets the context manager exit (closing/expiring the session), and then
accesses attributes on those now-detached objects in later code:

```python
async with session_scope() as session:
    instance = await repository.get_instance_by_id(session, instance_id)
    task = await repository.get_task_by_id(session, task_id)
# session is closed here

spec = InstanceSpec.from_dict(task.payload)          # line 177 — detached read
...
service.validate_transition(instance.status, InstanceStatus.deploying)  # line 184 — detached read
```

SQLAlchemy's async sessions default to `expire_on_commit=True`; once the
`session_scope()` context exits, `instance.status` and `task.payload` are
expired/detached and lazy-loading them outside an active session raises
`MissingGreenlet` / `DetachedInstanceError`. The unit tests do not catch this
because they monkeypatch `session_scope` to yield a `MagicMock` whose
attributes are plain in-memory values (`tasks.py:271`, `test_tasks.py:244-271`).
The integration tests pass only because `pg_session` is a single long-lived
session with `expire_on_commit=False` (`conftest.py:114`) reused via the patch
— production uses the real `session_scope`, not that fixture.

This means the happy path will crash in production on the first attribute read
after the initial load block.

**Fix:** Read the scalar values you need while the session is still open, or
keep the object attached for the duration of its use. For example, capture the
payload and current status inside the block:

```python
async with session_scope() as session:
    instance = await repository.get_instance_by_id(session, instance_id)
    task = await repository.get_task_by_id(session, task_id)
    if instance is None or task is None:
        log.warning(...)
        return
    current_status = instance.status
    task_payload = task.payload

spec = InstanceSpec.from_dict(task_payload)
...
service.validate_transition(current_status, InstanceStatus.deploying)
```

(The subsequent transitions already hard-code their source status, so only the
first `instance.status` read and the `task.payload` read need this treatment.)

### CR-02: Tasks reaching max attempts are never marked terminal and are re-kicked forever on every boot

**File:** `src/provisioning_worker/modules/provisioning/repository.py:167-171`,
`src/provisioning_worker/modules/provisioning/tasks.py:311-325`,
`src/provisioning_worker/main.py:194-233`
**Issue:**
`record_task_failure` always sets `task.status = ProvisioningTaskStatus.running`
(repository.py:171) — even on the final, terminal failure. In `_handle_failure`,
when `attempt_count >= max_attempts - 1` the task is declared "terminal" only in
a log line (tasks.py:320-325); the DB row is left `status=running` with
`next_attempt_at` set to a past time.

Boot recovery (`_recover_overdue_tasks`, main.py:212-218) queries for
`status IN ('pending','running')` with `next_attempt_at <= now()` and re-kicks
every match. A task that exhausted all attempts therefore matches this query on
**every** subsequent worker boot and gets re-kicked indefinitely, repeatedly
re-running a doomed convergence (and re-failing). There is no `failed` terminal
status for tasks, so the task ledger never closes.

**Fix:** Mark the task terminal when attempts are exhausted, and exclude
terminal rows from boot recovery. Add the terminal transition in
`_handle_failure`:

```python
if attempt_count < settings.provisioning_max_attempts - 1:
    ...
    await create_instance_task.kiq(str(instance_id), str(task_id))
else:
    async with session_scope() as session:
        task = await repository.get_task_by_id(session, task_id)
        if task is not None:
            task.status = ProvisioningTaskStatus.failed
            task.next_attempt_at = None
        await session.commit()
    log.error("max attempts reached — task is terminal", ...)
```

and ensure `record_task_failure` does not unconditionally force `running`
(it currently overwrites a `pending` task to `running` on the first failure,
which is acceptable, but it must not clobber a terminal `failed`).

### CR-03: Retry counting and instance-status snapshotting are inconsistent — instance forced to `failed` on every transient failure, then re-driven from a stale in-memory status

**File:** `src/provisioning_worker/modules/provisioning/tasks.py:298-319`,
`src/provisioning_worker/modules/provisioning/repository.py:174-183`
**Issue:**
Two related correctness problems in the retry path:

1. `_handle_failure` reads `attempt_count = task.attempt_count` (tasks.py:304)
   *before* calling `record_task_failure`, which increments it (repository.py:168).
   The backoff and the `attempt_count < max_attempts - 1` decision use the
   pre-increment value, while the persisted `attempt_count` is post-increment.
   With `max_attempts=5` this yields 5 total executions (attempts 0..4) — but the
   off-by-one reasoning is implicit and fragile: the comparison is against
   `max_attempts - 1` using a value that is one behind the stored counter,
   so the persisted `attempt_count` after the last retry is 5 while the guard
   compared 4. Any future reader will mis-derive the retry budget. This is
   error-prone and should be made explicit (compare the same value that is
   persisted).

2. `record_task_failure` unconditionally sets `instance.status =
   InstanceStatus.failed` (repository.py:177) on *every* failure, including
   transient ones that will be retried. On the next attempt, `_run_convergence`
   validates `instance.status -> deploying` using the in-memory `instance`
   object loaded at the top of the new task invocation. Because of CR-01 the
   object is reloaded fresh each invocation, so it will read `failed` from the
   DB — and `failed -> deploying` is permitted by `_ALLOWED_TRANSITIONS`
   (service.py:70), so it happens to work. But this couples correctness to the
   `failed` state being a retry waypoint, which is undocumented in the create
   path and means the instance visibly flaps `failed -> deploying -> ...` on
   each transient retry, surfacing a misleading `failed` state (with
   `failure_reason`) to platform-api readers even while a retry is pending.

**Fix:** Make the attempt accounting explicit and avoid forcing the instance to
a customer-visible `failed` state for retryable failures:

```python
# read the authoritative (post-increment) attempt count for the decision
await repository.record_task_failure(session, task_id, instance_id, exc, next_attempt_at)
await session.commit()
new_attempt_count = attempt_count + 1
...
if new_attempt_count < settings.provisioning_max_attempts:
    # schedule retry; keep instance in a 'retrying' / 'deploying' state, not 'failed'
    ...
else:
    # terminal: now set instance.status = failed (see CR-02)
```

Consider only transitioning the *instance* to `failed` on terminal failure, and
recording transient errors on the task ledger (`last_error`, `attempt_count`)
without flipping the instance's customer-visible status.

## Warnings

### WR-01: Boot recovery re-kicks every overdue task as a `create_instance_task`, ignoring `task_type`

**File:** `src/provisioning_worker/main.py:224-231`
**Issue:** `_recover_overdue_tasks` calls `create_instance_task.kiq(...)` for
*every* overdue row regardless of `task.task_type`. Today only `create` tasks
exist, so it is latent, but the `ProvisioningTask.task_type` enum already
includes `update/suspend/reinstate/delete`. The moment Phase 5 opens any
non-create task, a crash-recovery boot will mis-dispatch it as a create,
silently corrupting convergence.
**Fix:** Branch on `task.task_type` and dispatch the matching task, or filter
the recovery query to `task_type == TaskType.create` until the other task
classes exist (and assert/log on unexpected types).

### WR-02: `provisioning_default_resource_caps` setting is declared but never parsed or used

**File:** `src/provisioning_worker/settings.py:93-96`,
`src/provisioning_worker/adapters/m1_entitlement_resolver.py:64-68`
**Issue:** `Settings.provisioning_default_resource_caps` is a JSON string
"parsed at use site", but `DefaultEntitlementResolver.resolve` hard-codes
`resource_caps={}` and never reads it. The setting is dead configuration —
operators who set it will see no effect, and the resolver docstring claims
resource caps come from settings.
**Fix:** Either parse and apply the setting in the resolver, or remove the
setting and update the docstrings to state resource caps are always empty in M1.

### WR-03: `InstanceSpec.from_dict` performs no validation and will raise raw `KeyError`/`ValueError` on a malformed payload

**File:** `src/provisioning_worker/ports/deployment_adapter.py:110-138`,
`src/provisioning_worker/modules/provisioning/tasks.py:177`
**Issue:** `from_dict` indexes required keys directly (`data["subscription_id"]`,
`data["resources"]["cpu_request"]`, etc.). The `task.payload` JSONB column is
nullable (`models.py:228`); if a task row is opened without a payload (or a
schema-evolved older row lacks a key), `from_dict(task.payload)` raises an
untyped `KeyError`/`TypeError` inside `_run_convergence`. That is caught by the
catch-all in `create_instance_task` and routed to `_handle_failure`, which will
retry the same un-parseable payload `max_attempts` times — burning the retry
budget on a permanent, non-retryable error.
**Fix:** Guard `task.payload is None` explicitly (treat as a permanent failure,
mark terminal, do not retry) and/or translate parse failures into a typed
non-retryable `ProvisioningError` so `_handle_failure` can skip the backoff loop.

### WR-04: `update_instance_status(**kwargs)` silently swallows a missing instance and accepts arbitrary attribute names

**File:** `src/provisioning_worker/modules/provisioning/repository.py:128-134`
**Issue:** Two robustness gaps:
(1) When the instance row is absent, the function returns silently (line 131).
In `_run_convergence` the deploying/configuring/ready transitions call this
without re-checking existence, so a concurrently-deprovisioned or missing
instance produces a no-op update and the task still proceeds to mark the task
`succeeded` and (on first ready) deliver credentials — for an instance that no
longer exists.
(2) `setattr(instance, key, value)` for arbitrary `**kwargs` keys means a typo
(e.g. `read_at=` instead of `ready_at=`) sets a junk attribute on the ORM object
with no error; the bug surfaces only as a silently-unpersisted field.
**Fix:** Raise `InstanceNotFound` (already defined in `errors.py`) when the row
is missing so the caller can react, and constrain the writable keyword set to a
known allow-list (or accept an explicit typed update object per python-style.md's
"avoid primitive bags of data" guidance).

### WR-05: Credentials are delivered after the ready-transition commit but with no delivery durability — a crash loses the only credential channel

**File:** `src/provisioning_worker/modules/provisioning/tasks.py:223-236`
**Issue:** The instance is committed to `ready` with `ready_at` set (line 223)
*before* `transport.send_credentials(...)` runs (line 228). If the process
crashes between the commit and the transport call, the `ready_at IS NULL` guard
(line 211) now reads non-NULL on the re-kicked attempt, so credentials are
**never** delivered. The "exactly once" guard guarantees at-most-once, but the
create path's whole point is to deliver credentials, and there is no compensating
path. (In M1 with `ConsoleNotificationTransport` this is low impact, but the
contract is load-bearing for M2 SMTP.)
**Fix:** Either deliver credentials before flipping `ready_at`, or track a
separate `credentials_delivered_at` column committed after a successful send,
so the guard keys on delivery rather than readiness.

### WR-06: `send_credentials` exceptions abort the task after the instance is already `ready`, triggering a spurious failure + retry

**File:** `src/provisioning_worker/modules/provisioning/tasks.py:227-236`,
`src/provisioning_worker/ports/notification_transport.py:62-72`
**Issue:** `send_credentials` is called inside `_run_convergence`, which is
wrapped by the catch-all that routes any exception to `_handle_failure`. The
port docstring explicitly allows implementations to raise on delivery failure.
If the (M2) transport raises, the instance — already committed `ready` and task
already `succeeded` — gets dragged to `failed` by `record_task_failure` and the
task is re-kicked, even though convergence fully succeeded. This is a
state-corruption path triggered purely by a notification hiccup.
**Fix:** Wrap the credential delivery in its own try/except that logs and does
not propagate into the convergence failure path (the convergence already
committed success), or move delivery into a separate retryable task keyed on
`credentials_delivered_at`.

### WR-07: Migration drops the model-level NOT NULL/default coverage and omits CHECK constraints; `attempt_count`/`version`/`status` have no server defaults

**File:** `migrations/provisioning/versions/20260602_1233_add_instance_tables.py:38-138`
**Issue:** The mapped models carry Python-side defaults (`version=1`,
`attempt_count=0`, `status=pending`) but the migration adds no matching
`server_default`. Any INSERT that does not go through the ORM (e.g. a manual
backfill, a future raw-SQL path, or platform-api tooling) into `instance.version`
/ `provisioning_task.attempt_count` will fail the NOT NULL with no default.
Additionally, per CLAUDE.md §6.3 the team flags that autogenerate "loses CHECK
constraints and mishandles enums" — there are no CHECK constraints here at all
(e.g. `attempt_count >= 0`, `max_attempts >= 1`, `seat_cap >= 1`), so invalid
values are accepted at the DB layer.
**Fix:** Add `server_default` for the columns that have Python defaults
(`version`, `attempt_count`, `status`) and add CHECK constraints for the numeric
invariants, matching the model intent. Review against the model before the next
migration.

## Info

### IN-01: `create_instance_task` declares but never uses `db_password` from the adapter

**File:** `src/provisioning_worker/modules/provisioning/tasks.py:180-236`,
`src/provisioning_worker/ports/deployment_adapter.py:172-173`
**Issue:** `CreateResult.db_password` is returned by the fake adapter and is part
of the port contract, but the convergence task only forwards `admin_password` to
the notification. `db_password` is silently discarded. That is correct for the
secrets-discipline (never persist), but the dead field is worth a comment or an
explicit note that M1 does not surface it.
**Fix:** Add a brief comment that `db_password` is intentionally unused in M1, or
omit it from `CreateResult` until a consumer exists.

### IN-02: `_POLL_INTERVAL_S` / `_MAX_POLL_ITERATIONS` are hard-coded magic numbers, not settings

**File:** `src/provisioning_worker/modules/provisioning/tasks.py:64-66`
**Issue:** The poll interval (5s) and max iterations (60) are module constants,
unlike every other backoff/timeout knob which lives in `Settings`. The effective
status-poll timeout (300s) is therefore not operator-tunable and is inconsistent
with the project's "typed configuration objects, no hardcoded env-specific
values" convention (python-style.md §Configuration).
**Fix:** Promote these to `Settings` fields with documented defaults.

### IN-03: `CreateInstanceCommand` schema is defined and exported but never used

**File:** `src/provisioning_worker/modules/provisioning/schemas.py:21-40`
**Issue:** `CreateInstanceCommand` (with `enqueued_at`) is the documented
handler->task command object, but the actual enqueue passes bare string args
(`create_instance_task.kiq(instance_id_str, task_id_str)`, handlers.py:112). The
schema is dead code — `enqueued_at` is never populated and the typed command
boundary the docstring describes does not exist.
**Fix:** Either route the enqueue through `CreateInstanceCommand` (preferred per
python-style.md's command-object guidance) or remove the unused schema.

### IN-04: `register_post_commit` outside a handler invocation silently no-ops

**File:** `src/provisioning_worker/shared/event_consumer.py:66-81`
**Issue:** The docstring notes that calling `register_post_commit` outside
`handle_with_dedupe` is "safe but the callback will never be drained." This is a
silent-loss footgun: a future caller that registers a post-commit enqueue from
the wrong context loses the enqueue with no warning. Low risk today (only the
activated handler uses it), but worth a guard.
**Fix:** Log a warning (or raise) when `register_post_commit` is called with no
active post-commit context established by `handle_with_dedupe`.

---

_Reviewed: 2026-06-02_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
