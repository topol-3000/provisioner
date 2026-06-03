---
phase: 04
slug: event-production-outbox-relay
status: ready
nyquist_compliant: true
wave_0_complete: true
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
| 04-01-T1 | 04-01 | 1 | EVT-01, EVT-02 | T-04-SC | No imports from unbuilt modules | unit (skipped stubs) | `.venv/bin/pytest tests/provisioning/test_outbox.py tests/provisioning/test_tasks.py -m "not integration" -v` | ❌ Wave 0 | ⬜ pending |
| 04-01-T2 | 04-01 | 1 | EVT-01, EVT-02 | T-04-01 | InstanceProvisionedPayload has no credential fields; frozen+extra="forbid" | unit (import + field check) | `.venv/bin/python -c "from provisioning_worker.events.instance import InstanceProvisionedPayload; assert 'admin_password' not in InstanceProvisionedPayload.model_fields; print('OK')"` | ❌ Wave 0 | ⬜ pending |
| 04-02-T1 | 04-02 | 2 | EVT-01, EVT-02 | T-04-08, T-04-11 | Emit guarded by is_first_ready; source_event_id captured before session closes | unit | `.venv/bin/pytest tests/provisioning/test_tasks.py -m "not integration" -v -x` | ✅ (exists, stubs) | ⬜ pending |
| 04-02-T2 | 04-02 | 2 | EVT-01 | T-04-04, T-04-07, T-04-09 | Relay never dies; last_error uses str(exc) in log not repr; stuck row skipped via SKIP LOCKED | unit + integration | `.venv/bin/pytest tests/provisioning/test_outbox.py -v` | ✅ (exists, stubs) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/provisioning/test_outbox.py` — 5 stubs covering EVT-01 unit+integration behaviors (test_drain_once_marks_sent, test_drain_once_records_failure, test_enqueue_idempotent, test_outbox_row_written_atomically, test_relay_xadd_roundtrip)
- [ ] 3 stubs added to `tests/provisioning/test_tasks.py` — EVT-02 payload and hostname behaviors (test_emit_instance_provisioned_fields, test_hostname_derivation, test_no_duplicate_emit_on_retry)
- [ ] `tests/conftest.py` extended with Valkey container fixture (`valkey_container`, `async_redis_client`) for the relay XADD round-trip integration test

Both test files already have Postgres container fixtures in conftest.py (`pg_engine`, `pg_session`). The Valkey fixture is the only addition required for this phase.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `valkey-cli XRANGE events.instance - +` shows envelope with `producer="provisioning-worker"`, fresh ULID, `causation_id` = triggering `subscription.activated` id | EVT-02 | Cross-process smoke against live Valkey | Drive a `subscription.activated` through to `ready` (see ROADMAP §Phase 4 smoke check), then inspect the stream with `valkey-cli XRANGE events.instance - +` |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (test_outbox.py + test_tasks.py stubs)
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ready for execution
