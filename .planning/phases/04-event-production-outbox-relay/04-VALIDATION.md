---
phase: 04
slug: event-production-outbox-relay
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-03
---

# Phase 04 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + pytest-asyncio 1.3.x (`asyncio_mode=auto`) |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `make test` (`-m "not integration"` — Docker-free) |
| **Full suite command** | `make test-integration` (testcontainers `[postgres,redis]`) |
| **Estimated runtime** | ~5s quick · ~60s+ integration (container spin-up) |

---

## Sampling Rate

- **After every task commit:** Run `make test`
- **After every plan wave:** Run `make test-integration`
- **Before `/gsd-verify-work`:** Full suite + `make check` must be green
- **Max feedback latency:** ~5 seconds (quick path)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| _to be filled by planner_ | | | EVT-01 / EVT-02 | | | | | | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] _to be filled by planner_ — test stubs for EVT-01 (same-txn outbox enqueue) and EVT-02 (`InstanceProvisionedPayload`)
- [ ] Confirm `tests/conftest.py` testcontainers Postgres + Valkey fixtures cover the relay drain + `XADD` round-trip

*If none: "Existing infrastructure covers all phase requirements."*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `valkey-cli XRANGE events.instance - +` shows envelope with `producer="provisioning-worker"`, fresh ULID, `causation_id` = triggering `subscription.activated` id | EVT-02 | Cross-process smoke against live Valkey | Drive a `subscription.activated` through to `ready`, then inspect the stream |

*If none: "All phase behaviors have automated verification."*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
