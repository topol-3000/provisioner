# Phase 2: Event consumption & idempotency - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-02
**Phase:** 2-Event consumption & idempotency
**Areas discussed:** Consumer seam depth, Unknown-type vs poison, Stuck-entry reclaim, Round-trip fixtures, Dedupe transaction

---

## Consumer seam depth

| Option | Description | Selected |
|--------|-------------|----------|
| Full seam now | Extract inline loop into EventConsumer port + ValkeyStreamsConsumer adapter + shared/event_consumer.py wrapper, per architecture.md §Ports; main.py shrinks to wiring. | ✓ |
| Inline, extend in place | Keep loop in main.py; add parsing/dispatch/dedupe there; extract later. | |
| You decide | Let the planner choose. | |

**User's choice:** Full seam now (Recommended)
**Notes:** Honors the D-10 dependency rule (placeholders become real) and gives Phase 3 handlers a clean registration surface. → D-01/D-02.

---

## Unknown-type vs poison

| Option | Description | Selected |
|--------|-------------|----------|
| Skip+ack unknown; poison only malformed | Unknown-but-valid type → warning + XACK, no processed_event write. Malformed → error + XACK. Matches docs/events.md "unknown values as no-op". | ✓ |
| Treat unknown as poison too | Any un-actionable envelope → error + XACK identically. | |
| You decide | Let the planner pick. | |

**User's choice:** Skip+ack unknown; poison only malformed (Recommended)
**Notes:** Keeps the worker forward-compatible with new subscription.* types without error-log noise. → D-05.

---

## Stuck-entry reclaim (XAUTOCLAIM)

| Option | Description | Selected |
|--------|-------------|----------|
| Periodic, same dispatch path | XAUTOCLAIM every N poll cycles, ~60s min-idle (tunable); reclaimed entries re-flow through parse→dispatch→dedupe. | ✓ |
| Every poll cycle | XAUTOCLAIM before each XREADGROUP '>' read. | |
| Thin safety-net / minimal | Wire to satisfy CONS-01 minimally; revisit in Phase 3. | |

**User's choice:** Periodic, same dispatch path (Recommended)
**Notes:** Single code path; processed_event makes reclaim a safe no-op; crash recovery exercised even with no-op handlers. → D-08.

---

## Round-trip fixtures

| Option | Description | Selected |
|--------|-------------|----------|
| Author from docs/events.md, cross-check platform-api | Hand-author canonical JSON fixtures from docs/events.md; cross-reference platform-api's events/subscription.py field-by-field; no cross-repo import. | ✓ |
| Copy real serialized envelopes from platform-api tests | Pull actual emitted JSON from platform-api's test suite for byte-match. | |
| You decide | Let the planner choose. | |

**User's choice:** Author from docs/events.md, cross-check platform-api (Recommended)
**Notes:** docs/events.md is the per-repo contract source of truth (CLAUDE.md §6.2); contracts are per-repo, not shared. → D-09.

---

## Dedupe transaction (what the no-op stub commits)

| Option | Description | Selected |
|--------|-------------|----------|
| processed_event insert IS the committed work | Stub's only DB write is the processed_event row, committed via session_scope() before XACK; composite PK (event_id, consumer_group). | ✓ |
| Add a visible stub marker row | Stub also writes an observable marker row in the same txn. | |
| You decide | Let the planner pick. | |

**User's choice:** processed_event insert IS the committed work (Recommended)
**Notes:** Genuinely exercises the same-transaction guarantee (crash-after-commit-before-XACK → short-circuit; crash-before-commit → reprocess). Real state changes join this txn in Phase 3. → D-06/D-07.

---

## Claude's Discretion

- Exact Protocol granularity (`read`/`ack`/`autoclaim` vs `run(handlers)`); registry expression (dict vs decorator); handler function-vs-class.
- Poll `block`/`count`, XAUTOCLAIM cadence and exact min-idle default (~60s ballpark).
- `processed_event` niceties beyond the composite PK (indexes, `processed_at` default, optional `event_type` column).
- `bind_contextvars` keys per handler (envelope_id/subscription_id/correlation_id; instance_id is Phase 3).
- No-op handler log level (least-noisy honest level — no real transition yet).
- Test fixture layout and unit (fakeredis) vs integration (testcontainers) coverage of redelivery.

## Deferred Ideas

- `change_set_id` second-layer dedupe (needs `provisioning_task`) — Phase 3.
- Real handler bodies + instance state machine — Phase 3.
- `event_outbox` + relay + `instance.*` catalog — Phase 4.
- Dead-letter stream for poison forensics — not milestone 1.
- Metrics (consumer lag, poison/reclaim counts, convergence duration) — Phase 5.
- Out-of-order tolerance (lines_changed before activated) — Phase 3 convergence concern.
