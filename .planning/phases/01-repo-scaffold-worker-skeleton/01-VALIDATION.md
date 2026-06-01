---
phase: 1
slug: repo-scaffold-worker-skeleton
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-01
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `01-RESEARCH.md` §Validation Architecture. Per-task IDs are
> bound during planning / the Nyquist audit; the requirement-level map below
> is the authoritative coverage contract.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.* with pytest-asyncio 1.3.* (`asyncio_mode=auto`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` — none yet; Wave 0 creates it |
| **Quick run command** | `.venv/bin/pytest -m "not integration" -x` |
| **Full suite command** | `make test` (`-m "not integration"`, Docker-free) |
| **Estimated runtime** | ~5 seconds (empty/skeleton suite) |

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

> Task IDs (`1-NN-NN`) are assigned during planning; map each plan task back to
> the requirement row it satisfies. Threat refs link to `01-RESEARCH.md` §Security Domain.

| Requirement | Behavior | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|-------------|----------|------------|-----------------|-----------|-------------------|-------------|--------|
| SCAF-01 | `make check` passes (ruff check + format --check) | — | N/A | smoke / lint | `make check` | ❌ W0 (pyproject ruff config) | ⬜ pending |
| SCAF-01 | `make test` passes on empty suite | — | N/A | smoke | `make test` | ❌ W0 (conftest + pkg dirs) | ⬜ pending |
| SCAF-02 | Worker logs the four boot lines in order | — | Fail-fast on unreachable infra (D-05) | process smoke | `tests/test_boot.py` (subprocess; assert log lines) | ❌ W0 | ⬜ pending |
| SCAF-02 | SIGTERM → clean drain → exit 0 | T-1 (V7 error handling) | Drain in-flight, close pools, exit 0 | process smoke | `tests/test_boot.py` (send SIGTERM; assert exit 0) | ❌ W0 | ⬜ pending |
| SCAF-03 | `Settings` raises on missing required var | T-2 (V5/V14 config) | Fail-fast at startup, no silent default | unit | `tests/test_settings.py::test_missing_required_var` | ❌ W0 | ⬜ pending |
| SCAF-03 | `Settings` loads from `.env` | — | Secrets via env, not code | unit | `tests/test_settings.py::test_env_file_loading` | ❌ W0 | ⬜ pending |
| SCAF-04 | `GET /healthz` → 200 `{"status":"ok"}` | — | Unauthenticated liveness probe (intentional) | unit (in-process) | `tests/test_health.py::test_healthz_returns_ok` | ❌ W0 | ⬜ pending |
| SCAF-05 | `make migrate` creates `provisioning.alembic_version` | — | N/A | integration | `tests/test_migrations.py` (`@pytest.mark.integration`) | ❌ W0 (off PR gate) | ⬜ pending |
| SCAF-05 | `make revision` emits file with no `from __future__ import annotations` | — | N/A | smoke / lint | `make check` over `migrations/` (custom `script.py.mako`) | ❌ W0 | ⬜ pending |
| OBS-01 | structlog emits JSON when `ENVIRONMENT` ≠ `dev` | — | No secrets in logs | unit | `tests/test_logging.py::test_json_output_non_dev` | ❌ W0 | ⬜ pending |
| OBS-01 | OTel TracerProvider installed without OTLP endpoint | — | OTLP exporter only when endpoint set | unit | `tests/test_observability.py::test_tracing_no_backend` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Test infrastructure that must exist before (or in Wave 1 of) implementation:

- [ ] `pyproject.toml` `[tool.pytest.ini_options]` — `asyncio_mode=auto`, `--strict-markers --strict-config`, `filterwarnings=["error", ...]`, `markers = ["integration: ...", "slow: ..."]`
- [ ] `tests/conftest.py` — minimal (no fixtures needed in Phase 1; stubs for later phases)
- [ ] `tests/__init__.py` and `tests/provisioning/__init__.py`
- [ ] `tests/test_health.py` — `test_healthz_returns_ok` (in-process aiohttp `AppRunner`/`TestClient`)
- [ ] `tests/test_settings.py` — env-var validation tests
- [ ] `tests/test_logging.py` — JSON vs `ConsoleRenderer` selection test
- [ ] `tests/test_observability.py` — `TracerProvider` installed test
- [ ] `tests/test_boot.py` — boot-log + SIGTERM-drain test (mock infra, fire `shutdown_event`)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| CI runs lint + test + Docker build and is green | SCAF-01 / D-15 | Requires GitHub Actions runner; not reproducible in local unit suite | Push PR branch; confirm the GitHub Actions PR-gate workflow goes green (`make check` + `make test` + build-only Docker job, no push) |
| `make migrate` against the live empty `provisioning` schema | SCAF-05 | Needs real Postgres (platform-infra); marked `@pytest.mark.integration`, off the PR gate | `platform-infra` up → `make migrate` → `make psql` → `\dt provisioning.*` shows `alembic_version` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
