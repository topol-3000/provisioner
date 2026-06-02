# Deferred Items — Phase 02

Out-of-scope discoveries logged during execution. Not fixed (SCOPE BOUNDARY:
only auto-fix issues directly caused by the current task's changes).

## Pre-existing: `make test` fails `test_env_file_loading` when a repo-root `.env` exists

- **Discovered during:** Plan 02-03 final verification.
- **Symptom:** `make test` (i.e. `uv run pytest -m "not integration"`) fails
  `tests/test_settings.py::test_env_file_loading` with
  `assert 'worker-1' == 'worker-from-env-file'`. The test writes a tmp `.env`
  and constructs `Settings(_env_file=<tmp>)`, but pydantic-settings loads the
  **real repo-root `.env`** (present at `./.env`, committed 2026-06-01) over the
  explicit `_env_file` arg under `make`'s invocation context.
- **Pre-existing proof:** Reproduced on commit `061c000` (Plan 02-02's
  completion, before any 02-03 work) — same single failure. Not caused by this
  plan; this plan touches none of the settings machinery.
- **Passes under:** `.venv/bin/pytest -m "not integration"` (49 passed, stable
  over 5 runs) and `uv run pytest -m "not integration"` invoked directly (49
  passed) and the test in isolation — the failure only manifests through the
  `make test` target's environment.
- **Likely fix (future):** make `test_env_file_loading` hermetic against a
  repo-root `.env` — e.g. `monkeypatch.chdir(tmp_path)` before constructing
  `Settings`, or pass `_env_file` with `model_config` overridden so the default
  `env_file=".env"` does not also apply. Owner: whoever owns `settings.py` tests
  (Phase 1 scaffold).
