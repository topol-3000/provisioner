---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone-1-fake-adapter-pipeline
status: executing
last_updated: "2026-06-01T14:52:06.566Z"
last_activity: 2026-06-01
progress:
  total_phases: 5
  completed_phases: 0
  total_plans: 3
  completed_plans: 1
  percent: 0
---

# Project State

## Project Reference

See: .planning/PROJECT.md (created 2026-06-01)

**Core value:** A paid subscription becomes a running, correctly-entitled, dedicated
Odoo instance — automatically, idempotently, and observably — by consuming
`subscription.*` lifecycle events and converging the instance through a pluggable
deployment adapter.
**Current focus:** Phase 01 — repo-scaffold-worker-skeleton
`FakeDeploymentAdapter` (no Coolify, no real Odoo). Phase 1 — repo scaffold & worker skeleton.

## Current Position

Phase: 01 (repo-scaffold-worker-skeleton) — EXECUTING
Plan: 2 of 3
Status: Ready to execute
Last activity: 2026-06-01
Resume file: None
Stopped at: Phase 1 context gathered (15 decisions D-01..D-15; mirror platform-api for Docker/CI/Settings/OTel)

Progress: [███░░░░░░░] 33%

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
