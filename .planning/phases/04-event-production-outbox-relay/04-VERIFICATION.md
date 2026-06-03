---
phase: 04-event-production-outbox-relay
verified: 2026-06-03T00:00:00Z
status: human_needed
score: 7/7 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Run make test-integration and confirm test_relay_xadd_roundtrip passes"
    expected: "11 passed (1 pre-existing failure: test_concurrent_duplicate confirmed pre-Phase 4 in SUMMARY.md)"
    why_human: "Integration tests require Docker + live Postgres + Valkey containers; cannot execute in this verification environment"
---

# Phase 04: Event Production (Outbox → Relay) Verification Report

**Phase Goal:** Reaching `ready` reliably emits an `instance.provisioned` envelope on `events.instance`, atomically with the state change.
**Verified:** 2026-06-03
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | The transition to `ready` writes an `instance.provisioned` row to `provisioning.event_outbox` in the same transaction as the instance update | VERIFIED | `_transition_to_ready()` in `tasks.py:305-339` opens a single `session_scope()`, calls `update_instance_status`, `record_task_success`, then `service.emit_instance_provisioned(session, refreshed, ...)` inside `if is_first_ready:`, and then `session.commit()`. All three writes share one transaction boundary. `test_outbox_row_written_atomically` proves it with real Postgres. |
| 2 | The relay publishes unsent outbox rows to `events.instance` via XADD (single `envelope` field, MAXLEN ~ 100000) and marks them sent; `producer="provisioning-worker"`, fresh ULID `id`, `causation_id` = triggering `subscription.activated` id | VERIFIED | `_drain_once` in `outbox_relay.py:85-138` rebuilds typed envelopes via `envelope_class_for`, calls `bus.publish(envelope)`, sets `row.sent_at`. `ValkeyStreamsBus.publish()` uses `xadd(maxlen=100_000, approximate=True)`. `EventEnvelope.build()` mints ULID and sets `producer="provisioning-worker"`. `causation_id=source_event_id` passes through from `task.source_event_id` (captured at `tasks.py:183`). `test_relay_xadd_roundtrip` verifies the full round-trip. |
| 3 | A relay/publish failure leaves the row unsent (records `last_error`, bumps `attempt_count`) and is retried on the next poll — no event is lost or duplicated | VERIFIED | `outbox_relay.py:127-135` catches any exception, sets `row.last_error = _truncate(repr(exc), max_len=2000)`, `row.attempt_count += 1`. `run_outbox_relay` wraps each iteration in `try/except` logging `"outbox relay iteration crashed"` — relay never dies. `test_drain_once_records_failure` confirms last_error set, attempt_count=1, sent_at=None. |
| 4 | `InstanceProvisionedPayload` is frozen + `extra="forbid"` and carries no credentials | VERIFIED | `events/instance.py:46`: `model_config = ConfigDict(frozen=True, extra="forbid")`. 8 fields: `instance_id`, `subscription_id`, `customer_id`, `hostname`, `url`, `admin_email`, `snapshot_version`, `provisioned_at`. No `admin_password` field. Python spot-check confirmed. |
| 5 | `EventEnvelope.build()` mints a fresh 26-char ULID, sets `producer="provisioning-worker"`, `occurred_at=datetime.now(UTC)`, accepts `causation_id` | VERIFIED | `envelope.py:63-103`: `id=str(ULID())` (26 chars), `producer="provisioning-worker"` (hardcoded Literal), `occurred_at=datetime.now(tz=UTC)`, both `causation_id` and `correlation_id` params accepted. Python spot-check confirmed. |
| 6 | `envelope_class_for("instance.provisioned")` resolves; relay rebuilds typed envelope from JSONB via this registry | VERIFIED | `events/__init__.py:102-103`: `_ENVELOPE_REGISTRY = {"instance.provisioned": EventEnvelope[InstanceProvisionedPayload]}`. `envelope_class_for` raises `UnknownEnvelopeType` on miss. `outbox_relay._drain_once:124` calls `envelope_class_for(row.envelope_type).model_validate(row.payload)`. Spot-check confirmed. |
| 7 | `main.py` boots the relay as the fourth concern with injected `ValkeyStreamsBus` and `get_session_factory()`; `bus.close()` in finally | VERIFIED | `main.py:97`: `bus = ValkeyStreamsBus(settings)`. `main.py:103`: `run_outbox_relay(settings, get_session_factory(), bus, shutdown)` in TaskGroup. `main.py:110`: `await bus.close()` in finally before `dispose_engine()`. |

**Score:** 7/7 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/provisioning_worker/events/instance.py` | `InstanceProvisionedPayload` frozen, no credentials | VERIFIED | 8-field model, `frozen=True, extra="forbid"`, matches `docs/events.md` exactly |
| `src/provisioning_worker/events/envelope.py` | `EventEnvelope.build()` classmethod | VERIFIED | Mints ULID, sets `producer="provisioning-worker"`, `occurred_at=now(UTC)` |
| `src/provisioning_worker/events/__init__.py` | `envelope_class_for` registry, `InstanceProvisionedPayload` in `__all__` | VERIFIED | `_ENVELOPE_REGISTRY` maps `"instance.provisioned"`, both symbols in `__all__` |
| `src/provisioning_worker/ports/message_bus.py` | `MessageBus` Protocol, `@runtime_checkable` | VERIFIED | Protocol with single `async def publish(self, envelope: EventEnvelope) -> None` |
| `src/provisioning_worker/adapters/valkey_streams_bus.py` | `ValkeyStreamsBus.publish` via XADD `maxlen=100_000` | VERIFIED | `_MAXLEN=100_000, _APPROXIMATE=True`; `xadd(maxlen=_MAXLEN, approximate=_APPROXIMATE)` |
| `src/provisioning_worker/shared/strings.py` | `_truncate(s, *, max_len)` | VERIFIED | Truncates to `max_len-1` chars + `"…"`; used in relay for `last_error` bounding |
| `src/provisioning_worker/modules/provisioning/models.py` | `EventOutbox` with `UNIQUE(envelope_id)`, server defaults | VERIFIED | `UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id")`, `server_default=func.now()` on `created_at`, `server_default=text("0")` on `attempt_count` |
| `migrations/provisioning/versions/20260603_1136_add_event_outbox.py` | Alembic migration, no `from __future__`, correct server_defaults, UniqueConstraint | VERIFIED | `server_default=sa.text("now()")` on `created_at`; `server_default=sa.text("0")` on `attempt_count`; `UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id")`; `schema="provisioning"`; no `from __future__ import annotations` |
| `src/provisioning_worker/modules/provisioning/repository.py` | `OutboxRepo` with `enqueue()`, ON CONFLICT DO NOTHING, `model_dump(mode="json")` | VERIFIED | `pg_insert(EventOutbox).values(...payload=envelope.model_dump(mode="json")...).on_conflict_do_nothing(index_elements=["envelope_id"])` |
| `src/provisioning_worker/modules/provisioning/service.py` | `emit_instance_provisioned()` method, only place an `instance.*` event is emitted | VERIFIED | Method exists, constructs payload from instance row, calls `EventEnvelope.build()`, delegates to `OutboxRepo.enqueue()`. No other `enqueue` call in domain code. |
| `src/provisioning_worker/modules/provisioning/tasks.py` | `source_event_id` captured before session exits; hostname D-08; emit inside `is_first_ready` guard; `_transition_to_ready` extracted | VERIFIED | `tasks.py:183`: `source_event_id = task.source_event_id`; `tasks.py:230`: `hostname = f"{spec.slug}.{settings.instance_domain_suffix}"`. `_transition_to_ready()` owns the session, emit is at line 333 inside `if is_first_ready:`, `session.commit()` follows at line 339. |
| `src/provisioning_worker/infrastructure/outbox_relay.py` | `_drain_once`, `run_outbox_relay(settings, session_factory, bus, shutdown)`, zero `session_scope` imports | VERIFIED | Both functions implemented. `grep -c "session_scope"` returns 1 (docstring only, no code import). `with_for_update(skip_locked=True)` confirmed. |
| `src/provisioning_worker/main.py` | 4-param `run_outbox_relay`, `bus = ValkeyStreamsBus(settings)`, `bus.close()` in finally | VERIFIED | Lines 97, 103, 110 confirmed. |
| `tests/provisioning/test_outbox.py` | 5 real tests (not stubs) covering EVT-01 | VERIFIED | All 5 tests have real assertions; no `pytest.skip`. Unit tests pass in `make test`. |
| `tests/provisioning/test_tasks.py` | 3 EVT-02 tests green | VERIFIED | `test_emit_instance_provisioned_fields`, `test_hostname_derivation`, `test_no_duplicate_emit_on_retry` all pass in `make test`. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `tasks.py _transition_to_ready` | `service.emit_instance_provisioned` | `await service.emit_instance_provisioned(session, refreshed, causation_id=source_event_id)` at `tasks.py:333` | WIRED | Inside `if is_first_ready:` guard; same `session_scope()` as `update_instance_status` + `record_task_success` |
| `service.emit_instance_provisioned` | `events/envelope.py EventEnvelope.build()` | `EventEnvelope.build(type="instance.provisioned", version=1, payload=payload, causation_id=causation_id)` at `service.py:250` | WIRED | Runtime import (not TYPE_CHECKING) |
| `service.emit_instance_provisioned` | `repository.OutboxRepo.enqueue` | `outbox = OutboxRepo(session); await outbox.enqueue(envelope)` at `service.py:257-258` | WIRED | |
| `outbox_relay._drain_once` | `events/__init__.py envelope_class_for` | `envelope_class_for(row.envelope_type).model_validate(row.payload)` at `outbox_relay.py:124` | WIRED | |
| `outbox_relay._drain_once` | `ports/message_bus.py MessageBus.publish` | `await bus.publish(envelope)` at `outbox_relay.py:125` | WIRED | |
| `main.py run()` | `adapters/valkey_streams_bus.py ValkeyStreamsBus` | `bus = ValkeyStreamsBus(settings)` at `main.py:97` | WIRED | `get_session_factory()` and `bus` injected into `run_outbox_relay` |
| `events/__init__.py _ENVELOPE_REGISTRY` | `events/instance.py InstanceProvisionedPayload` | `"instance.provisioned": EventEnvelope[InstanceProvisionedPayload]` at `events/__init__.py:103` | WIRED | |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `outbox_relay._drain_once` | `rows` (unsent outbox rows) | `SELECT … WHERE sent_at IS NULL FOR UPDATE SKIP LOCKED` against real `provisioning.event_outbox` | Yes — real DB query with `envelope_class_for` rebuild + `bus.publish` | FLOWING |
| `service.emit_instance_provisioned` | `payload` | Instance ORM row fields (`hostname`, `url`, `admin_email`, `snapshot_version`, `ready_at`) — real values written in same txn by `update_instance_status` | Yes — real row fields, not hardcoded | FLOWING |
| `ValkeyStreamsBus.publish` | `serialized` | `envelope.model_dump_json().encode("utf-8")` — full typed envelope | Yes — typed Pydantic model with real field values | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `InstanceProvisionedPayload` fields match contract + no credentials | `.venv/bin/python -c "..."` | `payload fields OK: ['admin_email', 'customer_id', 'hostname', 'instance_id', 'provisioned_at', 'snapshot_version', 'subscription_id', 'url']` | PASS |
| `EventEnvelope.build()` mints correct producer + 26-char ULID | `.venv/bin/python -c "..."` | `producer: provisioning-worker id len: 26` | PASS |
| `envelope_class_for("instance.provisioned")` resolves, unknown raises `UnknownEnvelopeType` | `.venv/bin/python -c "..."` | Both confirmed | PASS |
| `make test` — 130 unit tests pass | `make test` | `130 passed, 12 deselected in 1.06s` | PASS |
| `make check` — ruff lint + format | `make check` | `All checks passed! 41 files already formatted` | PASS |
| Zero `session_scope` code-calls in `outbox_relay.py` | `grep -c "session_scope" outbox_relay.py` | 1 (docstring only, no code import or call) | PASS |
| `is_first_ready` guard prevents double-emit | `test_no_duplicate_emit_on_retry` in `make test` | PASSED | PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` found for this phase. `make test` and `make check` serve as the verification probes and both passed.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| EVT-01 | 04-01, 04-02 | `provisioning.event_outbox` + relay publishing to `events.instance` | SATISFIED | `EventOutbox` ORM + migration; `_drain_once` with FOR UPDATE SKIP LOCKED; `ValkeyStreamsBus.publish`; `run_outbox_relay` in `main.py`; unit tests `test_drain_once_marks_sent`, `test_drain_once_records_failure`, `test_enqueue_idempotent`, `test_outbox_row_written_atomically` |
| EVT-02 | 04-01, 04-02 | `instance.provisioned` emitted atomically on first `ready` | SATISFIED | `emit_instance_provisioned` in `service.py`; `_transition_to_ready` in `tasks.py`; `source_event_id` captured as `causation_id`; hostname D-08 derivation; `test_emit_instance_provisioned_fields`, `test_hostname_derivation`, `test_no_duplicate_emit_on_retry` |

### Anti-Patterns Found

No debt markers (`TBD`, `FIXME`, `XXX`) found in any files modified by this phase.

| File | Pattern | Finding | Severity |
|------|---------|---------|---------|
| `outbox_relay.py` | `session_scope` | Appears once in module docstring ("The relay NEVER imports or calls the module-level ``session_scope`` helper"), zero code references | Info — intentional anti-pattern documentation |
| `test_outbox.py` | `pytest.skip` | Zero occurrences — all stubs are real assertions | None |
| `test_tasks.py` | `pytest.skip` | Zero occurrences — all stubs are real assertions | None |

### Human Verification Required

#### 1. Integration Test Suite (make test-integration)

**Test:** Run `make test-integration` from the repo root with `platform-infra` running (Postgres 18 + Valkey 8 containers available via Docker/testcontainers).

**Expected:** 11 tests pass. One pre-existing failure (`test_concurrent_duplicate` in `test_idempotency.py`) should remain — it was confirmed pre-Phase 4 in the SUMMARY.md and is unrelated to Phase 4 changes. The 5 Phase 4 integration tests should all pass:
- `test_enqueue_idempotent` — ON CONFLICT DO NOTHING inserts exactly 1 row on duplicate ULID
- `test_outbox_row_written_atomically` — outbox row written in same txn as ready transition, `sent_at IS NULL`
- `test_relay_xadd_roundtrip` — `XRANGE events.instance` returns entry with `producer="provisioning-worker"` and correct `causation_id`

**Why human:** Testcontainers integration tests require Docker engine and pull `postgres:18-alpine` + `valkey/valkey:8` images. Cannot run in this verification environment.

---

## Gaps Summary

No gaps. All 7 ROADMAP success criteria for Phase 4 are met by the codebase:

1. **Same-transaction outbox write** — `_transition_to_ready` commits instance status, task success, and outbox row atomically (D-01 contract).
2. **Relay publishes to events.instance** — `_drain_once` with FOR UPDATE SKIP LOCKED + `ValkeyStreamsBus.publish`; fields verified: `producer="provisioning-worker"`, fresh ULID `id`, `causation_id` = triggering subscription envelope id.
3. **Relay failure handling** — `last_error` truncated via `_truncate`, `attempt_count` bumped, `sent_at` stays NULL; relay never dies.
4. **No credentials in payload** — `InstanceProvisionedPayload` has no `admin_password` field; frozen + `extra="forbid"` prevents accidental addition.

The only open item is human confirmation of the integration test suite results, which requires Docker.

---

_Verified: 2026-06-03_
_Verifier: Claude (gsd-verifier)_
