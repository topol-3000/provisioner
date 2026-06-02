---
phase: 1
slug: repo-scaffold-worker-skeleton
status: validated
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-01
validated: 2026-06-02
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `01-RESEARCH.md` §Validation Architecture. Reconciled against
> the executed code by the Nyquist audit on 2026-06-01 — the requirement-level
> map below is the authoritative coverage contract.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.* with pytest-asyncio 1.3.* (`asyncio_mode=auto`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` (strict-markers, strict-config, `filterwarnings=["error", ...]`) |
| **Quick run command** | `.venv/bin/pytest -m "not integration" -x` |
| **Full suite command** | `make test` (`-m "not integration"`, Docker-free) |
| **Current suite** | 14 tests, green in ~1.1s |

Note: Phase 1 ships **no** integration tests on the CI PR gate (D-15).
`make test-integration` (testcontainers) runs locally / on-demand only.

---

## Sampling Rate

- **After every task commit:** Run `.venv/bin/pytest -m "not integration" -x`
- **After every plan wave:** Run `make test`
- **Before `/gsd-verify-work`:** `make check` (ruff check + format --check) + `make test` green
- **Phase gate:** Full unit suite green + `make check` passes + `make docker-build` succeeds
- **Max feedback latency:** ~5 seconds

---

## Per-Requirement Verification Map

> Threat refs link to `01-RESEARCH.md` §Security Domain and `01-SECURITY.md`.

| Requirement | Behavior | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|------------|-----------------|-----------|-------------------|-------------|--------|
| SCAF-01 | `make check` passes (ruff check + format --check) | — | N/A | smoke / lint | `make check` | ✅ pyproject ruff config | ✅ green |
| SCAF-01 | `make test` passes | — | N/A | smoke | `make test` | ✅ conftest + pkg dirs | ✅ green |
| SCAF-02 | Worker logs the boot banner before the concern lines, in order | — | Fail-fast on unreachable infra (D-05) | unit (in-process) | `tests/test_boot.py::test_boot_log_lines_ordered` | ✅ | ✅ green |
| SCAF-02 | SIGTERM → clean drain (signal handler → shutdown event → concerns exit) | T-01-10 | Drain in-flight, close pools, exit cleanly | unit (real signal) | `tests/test_boot.py::test_sigterm_triggers_clean_shutdown` | ✅ | ✅ green (in-process drain; literal OS exit-0 → manual UAT #3) |
| SCAF-03 | `Settings` raises on missing required DSN var | T-2 (V5/V14 config) | Fail-fast at startup, no silent default | unit | `tests/test_settings.py::test_missing_required_var` | ✅ | ✅ green (impl fixed — DSN defaults removed) |
| SCAF-03 | `Settings` loads from `.env` | — | Secrets via env, not code | unit | `tests/test_settings.py::test_env_file_loading` | ✅ | ✅ green |
| SCAF-04 | `GET /healthz` → 200 `{"status":"ok"}` | — | Unauthenticated liveness probe (intentional) | unit (in-process) | `tests/test_health.py::test_healthz_returns_200` | ✅ | ✅ green |
| SCAF-05 | `make migrate` creates `provisioning.alembic_version` | — | N/A | integration | `tests/test_migrations.py` (`@pytest.mark.integration`) | ❌ off PR gate | 🔒 manual-only |
| SCAF-05 | `make revision` emits file with no `from __future__ import annotations` | — | N/A | smoke / lint | `make check` over `migrations/` (custom `script.py.mako`) | ✅ mako verified | ✅ green (template-level) |
| OBS-01 | structlog emits JSON when `ENVIRONMENT` ≠ `dev` | — | No secrets in logs | unit | `tests/test_logging.py::test_json_output_non_dev` | ✅ | ✅ green |
| OBS-01 | OTel TracerProvider installed without OTLP endpoint | — | OTLP exporter only when endpoint set | unit | `tests/test_observability.py::test_tracing_no_backend` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky · 🔒 manual-only*

---

## Wave 0 Requirements

Test infrastructure that had to exist before/within implementation — all delivered in Plan 01-02:

- [x] `pyproject.toml` `[tool.pytest.ini_options]` — `asyncio_mode=auto`, `--strict-markers --strict-config`, `filterwarnings=["error", ...]`, `markers = ["integration: ...", "slow: ..."]`
- [x] `tests/conftest.py` — minimal (no fixtures needed in Phase 1; stubs for later phases)
- [x] `tests/__init__.py` and `tests/provisioning/__init__.py`
- [x] `tests/test_health.py` — `test_healthz_returns_200` (in-process aiohttp `AppRunner`/`TCPSite`)
- [x] `tests/test_settings.py` — defaults, otel_enabled, missing-var, .env-loading
- [x] `tests/test_logging.py` — JSON vs `ConsoleRenderer` selection test
- [x] `tests/test_observability.py` — `TracerProvider` installed + idempotent test
- [x] `tests/test_boot.py` — clean-return, boot-log-ordering, real-SIGTERM-drain tests

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| CI runs lint + test + Docker build and is green | SCAF-01 / D-15 | Requires GitHub Actions runner; not reproducible in local unit suite | Push PR branch; confirm the GitHub Actions PR-gate workflow goes green (`make check` + `make test` + build-only Docker job, no push) |
| `make migrate` against the live empty `provisioning` schema | SCAF-05 | Needs real Postgres (platform-infra); marked `@pytest.mark.integration`, off the PR gate | `platform-infra` up → `make migrate` → `make psql` → `\dt provisioning.*` shows `alembic_version` |
| Live `make run` boot + Ctrl-C drain exits 0 (literal OS exit code) | SCAF-02 | Real process exit code can't be asserted in-process; `test_sigterm_triggers_clean_shutdown` covers the drain wiring in-process | `make run` against live infra → four boot lines → Ctrl-C → process exits 0 (see `01-HUMAN-UAT.md` #3) |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 5s (suite runs ~1.1s)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** validated 2026-06-01

---

## Validation Audit 2026-06-01

| Metric | Count |
|--------|-------|
| Gaps found | 4 |
| Resolved (automated) | 4 |
| Escalated → impl fix applied | 1 |
| Manual-only (carried) | 3 |

The VALIDATION.md was a planning-era draft (all rows `⬜ pending`); execution had
already created the test suite. The audit reconciled the map to reality (6 rows were
already green) and filled the 4 genuine gaps:

- `test_settings.py::test_env_file_loading` (SCAF-03) — new, green.
- `test_boot.py::test_boot_log_lines_ordered` (SCAF-02) — new, green (in-process subset;
  consumer/convergence lines require real infra and are out of scope here).
- `test_boot.py::test_sigterm_triggers_clean_shutdown` (SCAF-02) — new, green; exercises the
  real `loop.add_signal_handler(SIGTERM, ...)` wiring (strictly stronger than the prior
  monkeypatched-`asyncio.Event` test).
- `test_settings.py::test_missing_required_var` (SCAF-03, threat **T-2**) — surfaced an
  implementation divergence: `settings.py` carried hardcoded dev-cred defaults on
  `database_url` / `database_url_sync` / `valkey_url` (mirroring `platform-api`), so missing
  config silently fell back to a localhost dev database instead of failing fast. Per user
  decision the `default=` values were removed, making the three DSN fields **required** —
  closing T-2 (fail-fast, no silent default) and removing the committed `platform_dev_password`
  literal from source. (Note: `platform-api` still carries the same defaults; this intentionally
  diverges. Consider mirroring the fix there and running `/gsd-secure-phase 01` to record T-2.)

---

## Validation Audit 2026-06-02

| Metric | Count |
|--------|-------|
| Gaps found | 0 |
| Resolved | 0 |
| Escalated | 0 |
| Manual-only (carried) | 3 |

Re-validation of an already-compliant phase. The 2026-06-01 audit's work holds:
all 7 automated tests named in the Per-Requirement map exist and run green
(`.venv/bin/pytest -m "not integration"` → **14 passed in 1.29s**). Confirmed the
requirement set is complete against `ROADMAP.md` (SCAF-01..05, OBS-01 — all six
mapped). No MISSING or PARTIAL rows; no new gaps introduced since the last audit.
The three manual-only verifications (CI runner, live `make migrate`, literal OS
exit-0 on `make run` drain) remain legitimately out of the Docker-free unit suite.
`nyquist_compliant` stays `true`.
