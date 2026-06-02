---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone-1-fake-adapter-pipeline
status: executing
last_updated: "2026-06-02T09:08:27.542Z"
last_activity: 2026-06-02
progress:
  total_phases: 5
  completed_phases: 1
  total_plans: 6
  completed_plans: 5
  percent: 20
---

# Project State

## Project Reference

See: .planning/PROJECT.md (created 2026-06-01)

**Core value:** A paid subscription becomes a running, correctly-entitled, dedicated
Odoo instance — automatically, idempotently, and observably — by consuming
`subscription.*` lifecycle events and converging the instance through a pluggable
deployment adapter.
**Current focus:** Phase 02 — event-consumption-idempotency
`FakeDeploymentAdapter` (no Coolify, no real Odoo). Phase 1 — repo scaffold & worker skeleton.

## Current Position

Phase: 02 (event-consumption-idempotency) — EXECUTING
Plan: 3 of 3
Status: Ready to execute
Last activity: 2026-06-02
Resume file: None
Stopped at: Completed 02-02-PLAN.md

Progress: [████████░░] 83%

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

## Decisions

- [Phase ?]: Phase 2 Plan 1: EventEnvelope re-implemented consume-only (no build, D-03); five subscription.* payloads with plain Decimal (D-04); envelope type:str for forward-compat (D-05); EventConsumer Protocol is the consume-side seam (D-01)
- [Phase ?]: Phase 2 Plan 2: ProcessedEvent ORM on provisioning.processed_event with composite PK (event_id VARCHAR(26), consumer_group TEXT) + processed_at TIMESTAMPTZ (D-07, first table in single Alembic tree); consumer_reclaim_min_idle_ms Settings tunable default 60_000 ge 1_000 backing XAUTOCLAIM (D-08); env.py target_metadata=Base.metadata enables autogenerate
