---
phase: 2
slug: event-consumption-idempotency
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-02
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `02-RESEARCH.md` § Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.* + pytest-asyncio 1.3.0 (`asyncio_mode=auto`) |
| **Config file** | `pyproject.toml [tool.pytest.ini_options]` |
| **Quick run command** | `make test` (`-m "not integration"`, Docker-free) |
| **Full suite command** | `make test-integration` (testcontainers Postgres+Valkey) |
| **Estimated runtime** | ~5s quick / ~60s+ integration (container spin-up) |

---

## Sampling Rate

- **After every task commit:** Run `make test` (unit, Docker-free)
- **After every plan wave:** Run `make test` + spot-run `make test-integration` on the consumer/dedupe tests
- **Before `/gsd-verify-work`:** Full suite (`make test` + `make test-integration`) green
- **Max feedback latency:** ~5 seconds (quick unit loop)

---

## Per-Task Verification Map

> Filled by the planner — one row per task. Requirement → success criterion mapping
> below is locked from RESEARCH.md; the planner assigns Task IDs / waves.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 2-XX-XX | XX | X | CONS-02 (SC-4) | — | N/A | unit | `pytest tests/events/ -x` | ❌ W0 | ⬜ pending |
| 2-XX-XX | XX | X | CONS-01 (SC-1) | — | poison cannot block group | integration | `pytest tests/ -m integration -k "test_consumer_reads_and_acks" -x` | ❌ W0 | ⬜ pending |
| 2-XX-XX | XX | X | CONS-03 (SC-2) | — | replay short-circuits, no double-effect | integration | `pytest tests/ -m integration -k "test_dedupe_replay" -x` | ❌ W0 | ⬜ pending |
| 2-XX-XX | XX | X | CONS-04 (SC-3) | — | malformed → error+XACK, no crash | unit + integration | `pytest tests/ -k "test_poison" -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

### Success Criteria → Observable Check Mapping (locked from RESEARCH.md)

| SC | Observable Check | Observation Points |
|----|------------------|--------------------|
| SC-1: XADD → XACK | `processed_event` row exists; `XPENDING` returns 0 for that msg_id after call | DB query + `client.xpending()` |
| SC-2: Replay no-op | Second call returns without INSERT; `processed_event` still 1 row; no error log | DB row count; log output |
| SC-3: Poison survival | `error` log emitted; no `processed_event` row; next valid message still processed | Log level assertion; DB row count |
| SC-4: Round-trip | Fixture `model_validate` → `model_dump(mode="json")` → `model_validate` equality | pytest assertion |

---

## Wave 0 Requirements

- [ ] `tests/events/__init__.py` — new test package for envelope/payload tests
- [ ] `tests/events/test_envelope.py` — envelope field-list pinning + round-trip
- [ ] `tests/events/test_subscription_payloads.py` — all 5 payload round-trips + extra-field rejection (D-09 canonical fixtures)
- [ ] `tests/provisioning/test_idempotency.py` — dedupe guard integration tests (`@pytest.mark.integration`)
- [ ] `tests/conftest.py` updates — async Postgres session fixture + Valkey container fixture for integration tests
- [ ] Framework install: already present (`pytest-asyncio 1.3.0`, `asyncio_mode=auto`) — no install needed

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live `valkey-cli XADD` smoke against a running worker | CONS-01..04 | End-to-end against real `platform-infra` Valkey + Postgres; covered by integration tests but the documented smoke (CLAUDE.md §5) is operator-run | `make run`; `valkey-cli XADD events.subscription '*' envelope '{…subscription.activated…}'`; observe `processed_event` row + `XACK` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
