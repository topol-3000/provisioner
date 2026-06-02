---
phase: "03-registry-create-path-fake-adapter"
plan: "02"
subsystem: "provisioning"
tags: ["adapters", "fake-adapter", "repository", "tdd", "ports-adapters", "fault-injection"]
dependency_graph:
  requires: ["03-01"]
  provides:
    - "fake-deployment-adapter"
    - "console-notification-transport"
    - "m1-entitlement-resolver"
    - "system-clock-adapter"
    - "provisioning-repository"
    - "settings-backoff-knobs"
  affects: ["03-03", "03-04"]
tech_stack:
  added: []
  patterns:
    - "dataclass-based-adapter"
    - "fault-injection-via-field"
    - "stable-secrets-per-spec"
    - "stdout-only-credential-channel"
    - "session-per-caller-repository"
key_files:
  created:
    - src/provisioning_worker/adapters/fake_deployment.py
    - src/provisioning_worker/adapters/console_notification.py
    - src/provisioning_worker/adapters/m1_entitlement_resolver.py
    - src/provisioning_worker/adapters/system_clock.py
    - tests/provisioning/test_adapters.py
    - tests/provisioning/test_repository.py
  modified:
    - src/provisioning_worker/modules/provisioning/repository.py
decisions:
  - "FakeDeploymentAdapter is a @dataclass (not a plain class) for zero-boilerplate field defaults and init=False internal state (_call_counts, _instances)"
  - "system_clock.py re-exports SystemClock/FakeClock from ports/clock.py — both were already defined there in Plan 01 (ports-colocation decision)"
  - "ConsoleNotificationTransport uses TYPE_CHECKING guard for CredentialNotification to satisfy ruff TC001 (never needed at runtime under PEP 649)"
  - "repository.py moves datetime/UUID to TYPE_CHECKING block (ruff TC003 unsafe-fix applied — PEP 649 deferred annotation evaluation)"
metrics:
  duration: "~7m"
  completed_date: "2026-06-02"
  tasks_completed: 2
  files_changed: 7
---

# Phase 03 Plan 02: Adapters and Repository Layer — Summary

Concrete adapter implementations and the data-access layer that the convergence task will call. Both tasks followed TDD (RED → GREEN → lint).

## What Was Built

**Task 1: Four adapters (TDD)**

- `adapters/fake_deployment.py`: `FakeDeploymentAdapter` — `@dataclass` with `fail_on: set[str]`, `fail_count: int`, `_call_counts` and `_instances` internal state. Implements all six `DeploymentAdapter` Protocol methods. Fault injection raises `DeploymentFailed(step="create", reason="fault injection")` on the first N calls then succeeds. Stable secrets (`admin_password="test-password-stable"`) for idempotent re-run (D-11). Passes `isinstance(fake, DeploymentAdapter)`.

- `adapters/console_notification.py`: `ConsoleNotificationTransport` — writes credentials to `sys.stdout` using `sys.stdout.write()` directly. No `structlog` import, no log calls (D-12, T-3-04 mitigation). Marked `# dev-only`.

- `adapters/m1_entitlement_resolver.py`: `DefaultEntitlementResolver` — M1 placeholder that returns `EntitlementPicture(module_set=(), seat_cap=settings.provisioning_default_seat_cap, resource_caps={})`. Never uses `payload.line_count` in the computation (D-03). Docstring explains the M1→M2 swap path.

- `adapters/system_clock.py`: Re-exports `SystemClock` and `FakeClock` from `ports/clock.py` (where they were defined in Plan 01 alongside the `Clock` Protocol).

17 adapter tests created and passing.

**Task 2: Repository layer + Settings verification (TDD)**

- `modules/provisioning/repository.py`: 7 async public functions, all receiving `AsyncSession` from the caller (no session lifecycle ownership). ORM-only — no raw SQL, no Pydantic return types.
  - `get_instance_by_id`, `get_task_by_id`, `get_instance_by_subscription_id` — SELECT with `scalar_one_or_none`
  - `update_instance_status(session, id, status, **kwargs)` — loads row, sets status + any extra kwargs (url, ready_at, deployment_handle, etc.); caller owns commit
  - `record_task_failure` — increments `task.attempt_count`, sets `task.last_error`, `task.next_attempt_at`, `task.status=running`; sets `instance.status=failed`, extracts `step`/`reason` from `DeploymentFailed` for `failed_step`/`failure_reason`
  - `record_task_success` — sets `task.status=succeeded`
  - `insert_enforcement_snapshot` — creates `EnforcementSnapshot` row from `InstanceSpec` fields
  - `update_snapshot_version` — sets `instance.snapshot_version`

  All imports of `InstanceSpec` come from `ports/deployment_adapter`, not `modules/` (BLOCKER 1 continuity). Docstring on `update_instance_status` notes caller responsibility for transition validity (T-3-06).

- Settings verification: confirmed all 6 new provisioning fields present with correct defaults (`provisioning_max_attempts=5`, `provisioning_base_delay_s=2.0`, `provisioning_multiplier=2.0`, `provisioning_cap_s=60.0`, `provisioning_default_seat_cap=10`, `provisioning_default_resource_caps="{}"`). These were added in Plan 01.

14 repository + settings tests created and passing.

## Deviations from Plan

None — plan executed exactly as written.

## Threat Mitigations Applied

| ID | Component | Mitigation |
|----|-----------|-----------|
| T-3-04 | ConsoleNotificationTransport | `# dev-only` marker on class; `sys.stdout.write()` only, no structlog import; test `test_console_transport_does_not_use_structlog` verifies no log pipeline involvement |
| T-3-05 | DefaultEntitlementResolver | `resolve()` returns `seat_cap=settings.provisioning_default_seat_cap` with no reference to `payload.line_count`; `test_default_resolver_no_line_count_mapping` explicitly asserts `seat_cap != payload.line_count` |
| T-3-06 | repository.update_instance_status | Docstring states "Caller is responsible for valid transition — service.py is the only authorised caller" |
| T-3-SC | Package installs | No new packages — all Phase 3 dependencies already in uv.lock |

## Known Stubs

None — all files are fully implemented for their Plan 02 scope.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes beyond the documented Phase 3 scope.

## Self-Check: PASSED

Files confirmed present:
- src/provisioning_worker/adapters/fake_deployment.py ✓
- src/provisioning_worker/adapters/console_notification.py ✓
- src/provisioning_worker/adapters/m1_entitlement_resolver.py ✓
- src/provisioning_worker/adapters/system_clock.py ✓
- src/provisioning_worker/modules/provisioning/repository.py ✓
- tests/provisioning/test_adapters.py ✓
- tests/provisioning/test_repository.py ✓

Commits confirmed:
- 922025a: test(03-02): add failing tests for adapters (RED phase)
- 427a5fd: feat(03-02): implement four adapters
- 855edb5: test(03-02): add failing tests for repository + settings (RED phase)
- 8e14156: feat(03-02): implement repository layer
