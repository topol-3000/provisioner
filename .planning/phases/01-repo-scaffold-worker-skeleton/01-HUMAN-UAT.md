---
status: partial
phase: 01-repo-scaffold-worker-skeleton
source: [01-VERIFICATION.md]
started: 2026-06-01T15:32:28Z
updated: 2026-06-01T15:32:28Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. `make migrate` against the live empty `provisioning` schema (SCAF-05)
expected: With `platform-infra` up, `make migrate` exits 0 and `make psql` → `\dt provisioning.*` shows the `alembic_version` table in the `provisioning` schema (version_table_schema=provisioning). Marked `@pytest.mark.integration`, off the PR gate.
result: [pending]

### 2. CI green on GitHub Actions (SCAF-01 / D-15)
expected: Push a PR branch; the GitHub Actions PR gate (`lint` = ruff check + format, `test` = unit-only `-m "not integration"`, `build` = Docker build with `push: false`) all pass green.
result: [pending]

### 3. Live end-to-end `make run` smoke (SCAF-02/03/04, advisory)
expected: With real Postgres + Valkey reachable, `make run` boots `python -m provisioning_worker`, logs the four ordered boot lines (including `health server listening port=8001`), `curl http://localhost:8001/healthz` → 200 `{"status":"ok"}`, and Ctrl-C drains cleanly and exits 0. (In-process tests `test_boot.py` + `test_health.py` already cover these behaviors; this is a belt-and-suspenders smoke test.)
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
