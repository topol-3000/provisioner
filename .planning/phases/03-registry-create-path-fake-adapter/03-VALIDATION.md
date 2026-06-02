---
phase: 3
slug: registry-create-path-fake-adapter
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-02
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `03-RESEARCH.md §Validation Architecture`. Task IDs in the
> per-task map are filled in during execution as the planner's plans land.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + pytest-asyncio 1.3.x (`asyncio_mode=auto`) |
| **Config file** | `pyproject.toml [tool.pytest.ini_options]` |
| **Quick run command** | `make test` (`pytest -m "not integration"` — fast, Docker-free) |
| **Full suite command** | `make test-integration` (all markers incl. testcontainers Postgres) |
| **Estimated runtime** | quick ~5–15s · full ~60–120s (container pull/boot dominates) |

---

## Sampling Rate

- **After every task commit:** Run `make test` (fast, Docker-free; FakeDeploymentAdapter + in-memory/fakeredis)
- **After every plan wave:** Run `make test` + `make check`
- **Before `/gsd-verify-work`:** `make test-integration` full suite must be green
- **Max feedback latency:** ~15 seconds (quick path)

---

## Per-Task Verification Map

> Requirement-level map from research. `Task ID` / `Plan` / `Wave` are bound to
> concrete plan tasks at execution time; every row already has an automated
> command, so no row depends on a manual check.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | PROV-01 | — | Tables + columns/constraints exist | unit | `pytest tests/provisioning/test_models.py -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | PROV-01 | T-3 dup-provision | `subscription_id` UNIQUE; FK `provisioning_task.instance_id → instance.id` | integration | `pytest tests/provisioning/test_models.py -x -m integration` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | PROV-02 | — | `activated` → `instance` pending + `provisioning_task` pending → `ready` with populated `url` | integration | `pytest tests/provisioning/test_tasks.py::test_create_path_succeeds -x -m integration` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | PROV-02 | — | Convergence code unchanged across fake/real adapter (Protocol seam) | unit | `pytest tests/provisioning/test_tasks.py -x -m "not integration"` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | PROV-03 | V5 input-val | InstanceSpec built from Settings defaults; no `line_count→seat_cap` mapping | unit | `pytest tests/provisioning/test_spec.py -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | PROV-04 | T-3 consumer-crash | `fail_on={"create"}` → `status=failed`, `last_error`/`failed_step` set, backoff retry succeeds, consumer never crashes | unit | `pytest tests/provisioning/test_tasks.py::test_create_fails_then_retries -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | PROV-08 | T-3 secret-leak | `ConsoleNotificationTransport.send_credentials()` called once on first `ready`; no secret in any log line | unit | `pytest tests/provisioning/test_tasks.py::test_credentials_sent_once -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | PROV-08 | — | Re-converge to `ready` does NOT re-send credentials (`ready_at IS NULL` guard) | unit | `pytest tests/provisioning/test_tasks.py::test_no_credential_resend_on_retry -x` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SNAP-01 | — | `enforcement_snapshot` row at `configuring` with `version=1`; `instance.snapshot_version` set | integration | `pytest tests/provisioning/test_tasks.py::test_enforcement_snapshot_written -x -m integration` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/provisioning/test_models.py` — `Instance` / `ProvisioningTask` / `EnforcementSnapshot` schema + constraint assertions (PROV-01, SNAP-01)
- [ ] `tests/provisioning/test_spec.py` — `InstanceSpec` builder from Settings defaults (PROV-03)
- [ ] `tests/provisioning/test_tasks.py` — create task happy path, fault injection (PROV-04 canonical proof), credential-once + no-resend (PROV-08), snapshot write (SNAP-01)
- [ ] `tests/provisioning/test_service.py` — convergence service state-machine unit tests (PROV-02)
- [ ] `tests/provisioning/test_handlers.py` — upgrade existing no-op `handle_subscription_activated` test to real-body assertions (PROV-02)
- [ ] `tests/conftest.py` — extend with `FakeDeploymentAdapter`, `ConsoleNotificationTransport`, injected `Clock`, in-memory broker fixtures (reuse Phase-2 `pg_engine` testcontainers fixture)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live `make migrate` creates `provisioning.instance` / `provisioning_task` / `enforcement_snapshot` against real Postgres 18 | PROV-01, SNAP-01 | Requires running `platform-infra` Postgres; by-design manual (mirrors Phase-1 `01-HUMAN-UAT.md` discipline) | With `platform-infra` up: `make migrate`; then `make psql` → `\dt provisioning.*` shows the three new tables |

*Automated coverage exists for every behavior above via testcontainers; the manual check confirms the migration runs against the shared cluster.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s (quick path)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
