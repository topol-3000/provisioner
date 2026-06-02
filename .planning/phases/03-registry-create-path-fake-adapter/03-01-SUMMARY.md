---
phase: "03-registry-create-path-fake-adapter"
plan: "01"
subsystem: "provisioning"
tags: ["orm", "alembic", "ports", "domain-errors", "spec-builder", "tdd"]
dependency_graph:
  requires: ["02-event-consumption-idempotency"]
  provides: ["instance-registry-tables", "deployment-adapter-port", "notification-transport-port", "entitlement-resolver-port", "clock-port", "domain-error-hierarchy", "instance-spec-builder"]
  affects: ["03-02", "03-03", "03-04"]
tech_stack:
  added: []
  patterns: ["frozen-dataclass-value-objects", "runtime-checkable-protocols", "create-type-false-enum-pattern", "op-execute-enum-ddl"]
key_files:
  created:
    - src/provisioning_worker/modules/provisioning/models.py
    - src/provisioning_worker/shared/errors.py
    - src/provisioning_worker/ports/deployment_adapter.py
    - src/provisioning_worker/ports/notification_transport.py
    - src/provisioning_worker/ports/entitlement_resolver.py
    - src/provisioning_worker/ports/clock.py
    - src/provisioning_worker/modules/provisioning/spec.py
    - migrations/provisioning/versions/20260602_1233_add_instance_tables.py
    - tests/provisioning/test_models.py
    - tests/provisioning/test_spec.py
    - tests/provisioning/test_tasks.py
    - tests/provisioning/test_service.py
  modified:
    - src/provisioning_worker/modules/provisioning/schemas.py
    - src/provisioning_worker/settings.py
    - tests/conftest.py
decisions:
  - "InstanceSpec and ResourceRequests defined in ports/deployment_adapter.py — not in modules/ — so the dependency arrow always points inward (BLOCKER 1 fix)"
  - "Alembic migration uses op.execute() for ENUM DDL before tables; ORM columns use create_type=False (T-3-01 mitigation)"
  - "test_tasks.py and test_service.py created as Wave 0 collectable stubs — Wave 2 verify commands won't fail on missing files (BLOCKER 2 fix)"
  - "entitlement.seat_cap used in spec builder, never payload.line_count (D-03)"
metrics:
  duration: "~45m"
  completed_date: "2026-06-02"
  tasks_completed: 2
  files_changed: 15
---

# Phase 03 Plan 01: DB Contracts, Ports, Spec Builder — Summary

Wave 0 foundation: typed boundaries (ports, models, spec builder, errors) established before any convergence implementation touches them. Two TDD tasks completed.

## What Was Built

**Task 1: DB schema — models.py + Alembic migration**

Extended `models.py` with three Python enum classes (`InstanceStatus` 8-value, `ProvisioningTaskStatus` 4-value, `TaskType` 5-value) and three ORM mapped classes (`Instance`, `ProvisioningTask`, `EnforcementSnapshot`). All enum columns use `create_type=False` (T-3-01 mitigation). UUID PKs use Python `uuid7()`.

Created `migrations/provisioning/versions/20260602_1233_add_instance_tables.py` with:
- Three `op.execute("CREATE TYPE provisioning.instance_status ...")` calls before any `op.create_table()` (T-3-03 mitigation)
- No `from __future__ import annotations` (project rule)
- `down_revision = "0e3f3be0f9ad"` (chains from Phase 2 processed_event migration)
- Tables with explicit `schema="provisioning"` on all `op.create_table()` calls

15 `test_models.py` assertions cover column sets, PKs, FKs, UNIQUE constraints, enum member sets, and Base.metadata inclusion for all three new tables.

**Task 2: Ports + errors + schemas + spec builder + Wave 0 test stubs**

- `shared/errors.py`: 5-class hierarchy — `ProvisioningError` (base), `DeploymentFailed` (carries `step`/`reason`), `AdapterTimeout`, `InvalidTransition`, `InstanceNotFound`
- `ports/deployment_adapter.py`: `DeploymentAdapter` `@runtime_checkable` Protocol + `InstanceSpec`, `ResourceRequests`, `InstanceHandle`, `CreateResult`, `BackupRef`, `DeploymentStatus`. `InstanceSpec.to_dict()` / `from_dict()` round-trip (WARNING 5 fix). CRITICAL: `InstanceSpec` defined here, not in `modules/`
- `ports/notification_transport.py`: `NotificationTransport` Protocol + `CredentialNotification` dataclass (sensitive field comments per T-3-02)
- `ports/entitlement_resolver.py`: `EntitlementResolver` Protocol + `EntitlementPicture` dataclass
- `ports/clock.py`: `Clock` Protocol + `SystemClock` + `FakeClock` (no-op `sleep()` for deterministic tests, D-06)
- `modules/provisioning/schemas.py`: `CreateInstanceCommand` internal Pydantic model
- `modules/provisioning/spec.py`: `build_instance_spec()` builder — uses `entitlement.seat_cap`, NEVER `payload.line_count` (D-03); slug derived as `{subscription_id[:8]}.{instance_domain_suffix}`
- `settings.py`: Added provisioning defaults (`provisioning_default_seat_cap`, `provisioning_default_resource_caps`) and backoff knobs (`provisioning_max_attempts`, `provisioning_base_delay_s`, `provisioning_multiplier`, `provisioning_cap_s`)
- `tests/conftest.py`: Added ENUM type creation before `Base.metadata.create_all` in `pg_engine` fixture + `fake_clock()` fixture
- `test_spec.py`: 7 passing assertions (seat_cap defaults, slug derivation, tuple module_set, frozen, runtime_checkable, error hierarchy, round-trip)
- `test_tasks.py`, `test_service.py`: Wave 0 collectable stubs (BLOCKER 2 fix)

## Deviations from Plan

None - plan executed exactly as written.

## Threat Mitigations Applied

| ID | Component | Mitigation |
|----|-----------|-----------|
| T-3-01 | models.py ENUM columns | All PG_ENUM columns use `create_type=False`; ENUM types created exclusively via migration DDL |
| T-3-02 | CredentialNotification | `admin_password` and `db_password` fields carry `# sensitive: never log, never serialize to DB` comments |
| T-3-03 | Alembic ENUM DDL | Every `op.execute("CREATE TYPE ...")` uses schema-qualified name `provisioning.instance_status` etc.; all `op.create_table()` calls include `schema="provisioning"` |
| T-3-SC | Package installs | No new packages — all Phase 3 dependencies already in uv.lock |

## Known Stubs

None — all module files are fully implemented for their Wave 0 scope. The `test_tasks.py` and `test_service.py` stubs are intentional Wave 0 Nyquist placeholders; Plan 03-04 (Wave 3) fills them in.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes beyond the documented Phase 3 scope.

## Self-Check: PASSED

Files confirmed present:
- src/provisioning_worker/modules/provisioning/models.py ✓
- src/provisioning_worker/shared/errors.py ✓
- src/provisioning_worker/ports/deployment_adapter.py ✓
- src/provisioning_worker/ports/notification_transport.py ✓
- src/provisioning_worker/ports/entitlement_resolver.py ✓
- src/provisioning_worker/ports/clock.py ✓
- src/provisioning_worker/modules/provisioning/spec.py ✓
- migrations/provisioning/versions/20260602_1233_add_instance_tables.py ✓
- tests/provisioning/test_spec.py ✓
- tests/provisioning/test_tasks.py ✓
- tests/provisioning/test_service.py ✓

Commits confirmed:
- 494130c: feat(03-01): add Instance, ProvisioningTask, EnforcementSnapshot ORM models + Alembic migration
- c83d7b8: feat(03-01): add ports, errors, spec builder, schemas + Wave 0 test stubs
