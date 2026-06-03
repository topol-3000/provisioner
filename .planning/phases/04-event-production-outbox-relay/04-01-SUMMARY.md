---
phase: 04-event-production-outbox-relay
plan: "01"
subsystem: event-production
tags:
  - outbox
  - relay
  - event-contracts
  - alembic
  - tdd-scaffold

dependency_graph:
  requires:
    - 03-registry-create-path-fake-adapter
  provides:
    - InstanceProvisionedPayload model (events/instance.py)
    - EventEnvelope.build() classmethod (events/envelope.py)
    - envelope_class_for() produced-side registry (events/__init__.py)
    - MessageBus Protocol (ports/message_bus.py)
    - ValkeyStreamsBus adapter (adapters/valkey_streams_bus.py)
    - _truncate helper (shared/strings.py)
    - EventOutbox ORM model (modules/provisioning/models.py)
    - Alembic migration 20260603_1136_add_event_outbox.py
    - Test scaffolds for EVT-01 and EVT-02 (test_outbox.py + test_tasks.py stubs)
  affects:
    - events/__init__.py (extended with produced-side registry)
    - events/envelope.py (extended with build() classmethod)
    - modules/provisioning/models.py (extended with EventOutbox class)

tech_stack:
  added:
    - "python-ulid 3.1.* — str(ULID()) in EventEnvelope.build() (already pinned)"
    - "redis.asyncio — XADD publish in ValkeyStreamsBus (already pinned, was consume-only)"
    - "sqlalchemy JSONB + UniqueConstraint — EventOutbox mapped class"
    - "sqlalchemy text() — server_default=text('0') on attempt_count"
  patterns:
    - "Transactional outbox — EventOutbox table; emit in same txn as state change (D-01)"
    - "Produced-side envelope registry — envelope_class_for() parallel to payload_class_for()"
    - "Protocol-based DI — MessageBus publish-only port + ValkeyStreamsBus adapter"
    - "TDD scaffold — pytest.skip stubs as RED phase before implementation (Plan 02)"

key_files:
  created:
    - src/provisioning_worker/events/instance.py
    - src/provisioning_worker/ports/message_bus.py
    - src/provisioning_worker/adapters/valkey_streams_bus.py
    - src/provisioning_worker/shared/strings.py
    - migrations/provisioning/versions/20260603_1136_add_event_outbox.py
    - tests/provisioning/test_outbox.py
  modified:
    - src/provisioning_worker/events/envelope.py
    - src/provisioning_worker/events/__init__.py
    - src/provisioning_worker/modules/provisioning/models.py
    - tests/provisioning/test_tasks.py

decisions:
  - "D-02: EventEnvelope.build() mints fresh ULID; UNIQUE(envelope_id) is backstop not primary guard"
  - "D-06: produced-side envelope_class_for registry; relay rebuilds typed envelope from JSONB before publish"
  - "D-09: InstanceProvisionedPayload has no credentials; frozen+extra=forbid rejects accidental additions"
  - "Alembic autogenerate reviewed: server_defaults on attempt_count + created_at hand-verified"

metrics:
  duration: "~6 minutes"
  completed_date: "2026-06-03"
  tasks_completed: 2
  tasks_total: 2
  files_created: 6
  files_modified: 4
---

# Phase 04 Plan 01: Event Contracts and Test Scaffolds Summary

## One-liner

Contracts-first scaffold: InstanceProvisionedPayload (frozen, no credentials), EventEnvelope.build() with ULID minting, produced-side envelope_class_for registry, MessageBus publish-only Protocol, ValkeyStreamsBus XADD adapter, _truncate helper, EventOutbox ORM model with UNIQUE(envelope_id), and Alembic migration — all in place before Plan 02 wires them.

## What Was Built

### Task 1: Test Scaffolds (RED phase)

Created `tests/provisioning/test_outbox.py` with 5 pytest.skip stubs covering EVT-01 and EVT-02 behaviors:
- `test_drain_once_marks_sent` — relay marks sent_at and calls bus.publish once (unit)
- `test_drain_once_records_failure` — relay records last_error, bumps attempt_count on publish failure (unit)
- `test_enqueue_idempotent` — ON CONFLICT DO NOTHING on duplicate envelope ULID (integration)
- `test_outbox_row_written_atomically` — outbox row in same txn as ready transition (integration)
- `test_relay_xadd_roundtrip` — event appears on events.instance after drain (integration)

Added 3 stubs to `tests/provisioning/test_tasks.py`:
- `test_emit_instance_provisioned_fields` — causation_id, hostname, url, snapshot_version correctness
- `test_hostname_derivation` — instance.url and instance.hostname use FQDN pattern
- `test_no_duplicate_emit_on_retry` — is_first_ready guard prevents double-enqueue

All stubs skip gracefully; `make test` exits 0 with 125 passing + 5 skipped.

### Task 2: Contracts

**`events/instance.py`** — `InstanceProvisionedPayload` model:
- `model_config = ConfigDict(frozen=True, extra="forbid")`
- 8 fields: `instance_id`, `subscription_id`, `customer_id`, `hostname`, `url`, `admin_email`, `snapshot_version`, `provisioned_at`
- No `admin_password` or credentials (D-09, T-04-01)

**`events/envelope.py`** — extended with `EventEnvelope.build()`:
- Mints `id=str(ULID())`, sets `producer="provisioning-worker"`, `occurred_at=datetime.now(tz=UTC)`
- Accepts `causation_id` and `correlation_id` pass-through
- Phase-2 D-03 deliberately dropped this; Phase-4 D-02 restores it

**`events/__init__.py`** — extended with produced-side registry:
- `_ENVELOPE_REGISTRY = {"instance.provisioned": EventEnvelope[InstanceProvisionedPayload]}`
- `envelope_class_for()` raises `UnknownEnvelopeType` on KeyError (same pattern as `payload_class_for`)
- `InstanceProvisionedPayload` and `envelope_class_for` added to `__all__`

**`ports/message_bus.py`** — `MessageBus` Protocol:
- `@runtime_checkable` for `isinstance` checks
- Single `async def publish(self, envelope: EventEnvelope) -> None` method
- Transport errors propagate (relay catches, records last_error)

**`adapters/valkey_streams_bus.py`** — `ValkeyStreamsBus`:
- `_MAXLEN = 100_000`, `_APPROXIMATE = True`
- `publish()`: XADD with `{b"envelope": envelope.model_dump_json().encode("utf-8")}`
- `close()`: `await self._client.aclose()` for pool cleanup

**`shared/strings.py`** — `_truncate(s, *, max_len)`:
- Returns `s` if `len(s) <= max_len`, else `s[:max_len-1] + "…"`
- Used by relay for `last_error` bounding

**`modules/provisioning/models.py`** — extended with `EventOutbox`:
- `UNIQUE(envelope_id, name="uq_event_outbox_envelope_id")` backstop
- `created_at` with `server_default=func.now()`
- `attempt_count` with `default=0, server_default=text("0")`
- Uses `TIMESTAMP(timezone=True)` consistent with all existing mapped classes

**`migrations/provisioning/versions/20260603_1136_add_event_outbox.py`**:
- Autogenerated then hand-verified (Pitfall 3 check)
- Confirmed: `server_default=sa.text("now()")` on `created_at`
- Confirmed: `server_default=sa.text("0")` on `attempt_count`
- Confirmed: `UniqueConstraint("envelope_id", name="uq_event_outbox_envelope_id")` present
- Confirmed: `schema='provisioning'` present
- Confirmed: NO `from __future__ import annotations`

## Deviations from Plan

None — plan executed exactly as written.

Ruff UP037 (remove quotes from string annotations) was auto-fixed by `make lint-fix` for `envelope.py` and `message_bus.py` — these are trivial style fixups within ruff's scope, not deviations.

## Known Stubs

8 test stubs exist as intentional RED phase for Plan 02:

| Stub | File | Reason |
|------|------|--------|
| `test_drain_once_marks_sent` | tests/provisioning/test_outbox.py | `_drain_once` not built until Plan 02 |
| `test_drain_once_records_failure` | tests/provisioning/test_outbox.py | `_drain_once` not built until Plan 02 |
| `test_enqueue_idempotent` | tests/provisioning/test_outbox.py | `OutboxRepo` not built until Plan 02 |
| `test_outbox_row_written_atomically` | tests/provisioning/test_outbox.py | `emit_instance_provisioned` not built until Plan 02 |
| `test_relay_xadd_roundtrip` | tests/provisioning/test_outbox.py | relay body not built until Plan 02 |
| `test_emit_instance_provisioned_fields` | tests/provisioning/test_tasks.py | emit not wired until Plan 02 |
| `test_hostname_derivation` | tests/provisioning/test_tasks.py | hostname fix (D-08) not wired until Plan 02 |
| `test_no_duplicate_emit_on_retry` | tests/provisioning/test_tasks.py | emit guard not wired until Plan 02 |

These stubs are the intended RED phase. Plan 02 (emit seam) fulfills them.

## Threat Flags

No new threat surface introduced beyond what was declared in the plan's threat model. All T-04-01 through T-04-05 mitigations are either implemented in this plan (T-04-01: no credentials in payload; `frozen+extra=forbid`) or deferred to Plan 02 (T-04-02: relay last_error logging; T-04-04: relay-never-dies).

## Self-Check: PASSED

Created files exist:
- `src/provisioning_worker/events/instance.py` ✓
- `src/provisioning_worker/ports/message_bus.py` ✓
- `src/provisioning_worker/adapters/valkey_streams_bus.py` ✓
- `src/provisioning_worker/shared/strings.py` ✓
- `migrations/provisioning/versions/20260603_1136_add_event_outbox.py` ✓
- `tests/provisioning/test_outbox.py` ✓

Commits exist:
- `392f3d8` — test(04-01): add failing test stubs for EVT-01 and EVT-02 behaviors ✓
- `1d95df2` — feat(04-01): add event contracts — models, ports, adapters, ORM model, migration ✓
