---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone-1-fake-adapter-pipeline
status: verifying
last_updated: "2026-06-03T12:11:51.600Z"
last_activity: 2026-06-03
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 13
  completed_plans: 13
  percent: 80
---

# Project State

## Project Reference

See: .planning/PROJECT.md (created 2026-06-01)

**Core value:** A paid subscription becomes a running, correctly-entitled, dedicated
Odoo instance — automatically, idempotently, and observably — by consuming
`subscription.*` lifecycle events and converging the instance through a pluggable
deployment adapter.
**Current focus:** Phase 04 — event-production-outbox-relay
`FakeDeploymentAdapter` (no Coolify, no real Odoo). Phase 1 — repo scaffold & worker skeleton.

## Current Position

Phase: 04 (event-production-outbox-relay) — EXECUTING
Plan: 2 of 2
Status: Phase complete — ready for verification
dedupe guard (shared/event_consumer.py) lacks IntegrityError handling on the
concurrent/reclaim-race path; uncaught commit crashes the consumer with no XACK.
Code review CR-01 + verifier both confirmed (probe vs real Postgres). Phase NOT
complete — next: `/gsd-plan-phase 2 --gaps` to close. See 02-VERIFICATION.md.
Last activity: 2026-06-03
Resume file: None
Stopped at: Phase 4 context gathered

Progress: [██████████] 100%

## Notes

- The repo's `docs/` are authoritative and predate `.planning/`: `overview.md`,
  `architecture.md`, `events.md`, `deployment-adapter.md`, `conventions.md`,
  `local-development.md`, `python-style.md`, plus `CLAUDE.md`. Roadmap phases
  reference them rather than restating.

- `.planning/codebase/` maps are not yet generated — run `/gsd-map-codebase`
  after Phase 1 lands real source.

- Milestone 2 (Coolify spike + `CoolifyAdapter`, Odoo stack template, per-instance
  token, served `enforcement_snapshot`, SMTP, operator retry) is a separate
  milestone, captured in `.planning/seeds/` and ROADMAP "Beyond milestone 1".

## Performance Metrics

| Phase | Plan | Duration | Notes |
|-------|------|----------|-------|
| Phase 01 P02 | 811 | 2 tasks | 31 files |
| Phase 02 P01 | 25m | 2 tasks | 7 files |
| Phase Phase 02 P02 P15m | 2 tasks | 5 files tasks | - files |
| Phase 02 P03 | ~40m | 2 tasks | 7 files |
| Phase 03 P02 | 7m | 2 tasks | 7 files |
| Phase 03 P03 | 35m | - tasks | - files |
| Phase 03 P04 | 55m | 2 tasks | 6 files |
| Phase 04 P01 | 6m | 2 tasks | 10 files |
| Phase 04 P02 | ~19m | 2 tasks | 9 files |

## Decisions

- [Phase ?]: Phase 2 Plan 1: EventEnvelope re-implemented consume-only (no build, D-03); five subscription.* payloads with plain Decimal (D-04); envelope type:str for forward-compat (D-05); EventConsumer Protocol is the consume-side seam (D-01)
- [Phase ?]: Phase 2 Plan 2: ProcessedEvent ORM on provisioning.processed_event with composite PK (event_id VARCHAR(26), consumer_group TEXT) + processed_at TIMESTAMPTZ (D-07, first table in single Alembic tree); consumer_reclaim_min_idle_ms Settings tunable default 60_000 ge 1_000 backing XAUTOCLAIM (D-08); env.py target_metadata=Base.metadata enables autogenerate
- [Phase ?]: Phase 2 Plan 3: ValkeyStreamsConsumer is the sole redis.asyncio consume site (D-01); commit-then-ack — processed_event insert + handler in one session_scope, XACK after the dedupe-wrapped handler returns (D-06); poison -> error+ack+no-row vs unknown-type -> warning+ack+no-row (D-05); XAUTOCLAIM every 60 cycles, 3-element unpack, same dispatch path (D-08); handlers dispatched PRE-WRAPPED via make_handler_registry (no double-wrap)
- [Phase ?]: async_shared_broker pattern: tasks.py uses @async_shared_broker.task; main.py sets _default_broker = redis_broker
- [Phase ?]: _POST_COMMIT_ENQUEUE ContextVar with None default; reset per invocation; post-commit callbacks drained only after session.commit() (T-3-11)
- [Phase ?]: ProvisioningService.write_enforcement_snapshot owns snapshot coordination; tasks.py never calls repository snapshot functions directly (WARNING 4 fix)
- [Phase ?]: Receiver.listen(shutdown) used as all-in-one task listener in _run_convergence; boot recovery re-kicks overdue tasks (D-10)
- [Phase ?]: Phase 04-01: D-02 EventEnvelope.build() mints fresh ULID; UNIQUE(envelope_id) is backstop
- [Phase ?]: Phase 04-01: D-06 produced-side envelope_class_for registry — relay rebuilds typed envelope from JSONB before XADD
- [Phase ?]: Phase 04-01: D-09 InstanceProvisionedPayload has no credentials; frozen+extra=forbid blocks accidental field additions
