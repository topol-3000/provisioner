---
phase: 01-repo-scaffold-worker-skeleton
reviewed: 2026-06-01T15:25:00Z
depth: standard
files_reviewed: 31
files_reviewed_list:
  - pyproject.toml
  - Makefile
  - alembic.ini
  - migrations/provisioning/env.py
  - migrations/provisioning/script.py.mako
  - Dockerfile
  - docker-compose.yml
  - .github/workflows/ci.yml
  - .env.example
  - .dockerignore
  - .gitignore
  - src/provisioning_worker/__main__.py
  - src/provisioning_worker/main.py
  - src/provisioning_worker/settings.py
  - src/provisioning_worker/infrastructure/db.py
  - src/provisioning_worker/infrastructure/health_server.py
  - src/provisioning_worker/infrastructure/logging.py
  - src/provisioning_worker/infrastructure/observability.py
  - src/provisioning_worker/infrastructure/outbox_relay.py
  - src/provisioning_worker/modules/provisioning/models.py
  - src/provisioning_worker/modules/provisioning/schemas.py
  - src/provisioning_worker/modules/provisioning/repository.py
  - src/provisioning_worker/modules/provisioning/service.py
  - src/provisioning_worker/modules/provisioning/handlers.py
  - src/provisioning_worker/modules/provisioning/tasks.py
  - src/provisioning_worker/modules/provisioning/spec.py
  - tests/conftest.py
  - tests/test_boot.py
  - tests/test_health.py
  - tests/test_logging.py
  - tests/test_observability.py
  - tests/test_settings.py
findings:
  critical: 0
  warning: 6
  info: 5
  total: 11
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-06-01T15:25:00Z
**Depth:** standard
**Files Reviewed:** 31
**Status:** issues_found

## Summary

Reviewed the Phase-01 walking skeleton: boot path, four async concerns
(consumer, convergence, outbox relay, health server), settings,
infrastructure plumbing (db/logging/observability/health), Docker/CI/Makefile,
the single Alembic tree, and the unit tests. The domain stubs under
`modules/provisioning/` are intentionally docstring-only and were checked only
for real defects (none present).

Overall the skeleton is well-built and convention-compliant: tests pass
(10 passed), `ruff check` and `ruff format --check` are clean, the
`script.py.mako` template correctly omits `from __future__ import annotations`,
secrets are never logged, settings parse into typed DSNs, the JSON renderer is
selected for non-dev and emits no leaked `ProcessorFormatter` meta keys
(verified empirically), and OTel always installs a `TracerProvider` with the
OTLP exporter gated on the endpoint env var.

No BLOCKERs. The crash-only exit-code contract works end to end (verified:
crash → exit 1, clean → exit 0). The findings below are all WARNING/INFO and
center on async resource cleanup on failure paths, a dead `except*` clause, a
fail-fast engine leak, and a globally-monkeypatched test that is fragile.

## Narrative Findings (AI reviewer)

## Warnings

### WR-01: `except* Exception` in `__main__.main()` never catches the crash it targets

**File:** `src/provisioning_worker/__main__.py:14-15`
**Issue:** On a concern crash, `main.run()` raises `SystemExit(1)`
(`main.py:72`). `SystemExit` derives from `BaseException`, **not** `Exception`,
so the `except* Exception` clause here does not catch it — the `SystemExit`
simply propagates through and the interpreter exits 1. The exit code happens to
be correct, but only by accident: the `except*`/`sys.exit(1)` body is dead code
for the very case it was written for. It *does* catch a plain `Exception`
escaping `run()` (e.g. a fail-fast connectivity error that re-raises before the
TaskGroup), so it is not entirely dead — but the dual intent is confusing and
the `SystemExit` path silently bypasses it. Verified empirically on Python
3.14.4: TaskGroup crash → exit 1 without entering the handler.
**Fix:** Make the intent explicit. Either catch the base type, or let
`SystemExit` flow and only convert genuine `Exception`s:
```python
def main() -> None:
    """Boot the worker. Exits non-zero on any unhandled failure."""
    try:
        asyncio.run(run(get_settings()))
    except SystemExit:
        raise  # already carries the intended non-zero code
    except BaseExceptionGroup:
        sys.exit(1)
    except Exception:
        sys.exit(1)
```
Or, simpler: drop the `except*` here entirely and rely on `run()` raising
`SystemExit(1)` (which already exits 1) plus a top-level `Exception` guard.

### WR-02: Fail-fast connectivity checks leak the engine on failure

**File:** `src/provisioning_worker/main.py:55-74`
**Issue:** `_check_postgres()` (line 56) calls `get_engine(settings)`
(`main.py:164`), which builds and caches the process-wide engine. But the
`try/finally` that guarantees `dispose_engine()` does not start until line 65 —
*after* the connectivity checks. If `_check_postgres` or `_check_valkey` raises
(the fail-fast path, D-05), `run()` propagates the exception without ever
calling `dispose_engine()`, leaking the engine's connection pool. The process
is exiting anyway so impact is low, but a clean shutdown contract should not
depend on "the process dies before it matters," and this pattern will be copied
into later phases that may not exit.
**Fix:** Move the connectivity checks inside the `try` whose `finally` disposes
the engine:
```python
get_engine(settings)
try:
    await _check_postgres(settings)
    await _check_valkey(settings)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)
    async with asyncio.TaskGroup() as tg:
        ...
except* Exception as eg:
    raise SystemExit(1) from eg.exceptions[0]
finally:
    await dispose_engine()
```
(Note this also requires handling the non-group exceptions from the checks; see
WR-03.)

### WR-03: `_check_postgres`/`_check_valkey` errors are not an ExceptionGroup — `except*` would not catch them if moved inside

**File:** `src/provisioning_worker/main.py:56-72`
**Issue:** Related to WR-02. The connectivity checks raise plain exceptions, not
`ExceptionGroup`s. The `except* Exception` clause (line 71) only catches grouped
exceptions raised by the `TaskGroup`. A bare `raise` of a `RuntimeError` from
`_check_postgres` would *not* be caught by `except*` — it would propagate as-is
(and currently does, because the checks sit outside the `try`). If WR-02's fix
moves them inside the `try`, the `except* Exception` would silently fail to
match them and they would propagate uncaught (still exit non-zero via
`__main__`, but bypassing the intended `SystemExit(1)` normalization). Decide
one error-shaping strategy.
**Fix:** Either keep the checks outside the TaskGroup `try` but inside a `try`
that disposes the engine (WR-02), and normalize their exit there; or wrap the
whole body and handle both `ExceptionGroup` and plain `Exception`:
```python
except* Exception as eg:
    raise SystemExit(1) from eg.exceptions[0]
except Exception as exc:        # plain errors from the fail-fast checks
    raise SystemExit(1) from exc
```

### WR-04: Consumer leaks its redis client if `xreadgroup`/`xgroup_create` raises

**File:** `src/provisioning_worker/main.py:90-128`
**Issue:** `client = aioredis.from_url(...)` (line 90) is only closed by the
single `await client.aclose()` at line 128, reached only on a clean loop exit.
The `try/except` at lines 92-101 wraps just `xgroup_create` and re-raises on a
non-`BUSYGROUP` error without closing the client. Likewise, if `xreadgroup`
(line 111) or `xack` (line 122) raises mid-loop (connection reset, etc.), the
client is never closed. Under crash-only (D-01) the process dies, but an
unclosed `redis.asyncio` client emits an unclosed-connection warning and, with
`filterwarnings=["error"]` in tests, would turn a future test into a failure.
**Fix:** Wrap the lifecycle in `try/finally`:
```python
client = aioredis.from_url(str(settings.valkey_url), decode_responses=True)
try:
    try:
        await client.xgroup_create(...)
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    ...
    while not shutdown.is_set():
        ...
finally:
    await client.aclose()
```

### WR-05: `_run_convergence` leaks the broker if `broker.startup()` raises

**File:** `src/provisioning_worker/main.py:143-149`
**Issue:** `broker = RedisStreamBroker(...)` then `await broker.startup()`. If
`startup()` raises, `broker.shutdown()` (line 149) is never called and any
connection the broker opened is leaked. Same crash-only caveat as WR-04, but the
symmetric `startup()/shutdown()` should be guarded so the failure path cleans
up.
**Fix:**
```python
broker = RedisStreamBroker(url=str(settings.valkey_url))
await broker.startup()
try:
    log.info("taskiq broker connected", url=str(settings.valkey_url))
    await shutdown.wait()
finally:
    await broker.shutdown()
```

### WR-06: `test_boot` monkeypatches the global `asyncio.Event` class — fragile and over-broad

**File:** `tests/test_boot.py:52-64, 84-94`
**Issue:** Both boot tests do `patch("asyncio.Event", _AutoShutdownEvent)` where
the subclass schedules `self.set()` via `asyncio.get_event_loop().call_later(...)`
in `__init__`. This replaces the `Event` class **process-wide** for the duration
of the patch, so *any* `asyncio.Event()` constructed by patched-in code, library
internals, or the health/outbox concerns becomes an auto-firing event. It works
today only because `run()` constructs exactly one `Event`, but it couples the
test to that implementation detail: the moment `run()` (or a dependency) creates
a second `Event` for any reason, both events auto-fire at 0.1-0.15s and the test
semantics silently change. This tests timing, not behavior.
**Fix:** Inject shutdown deterministically instead of subclassing the global
class. Prefer refactoring `run()` to accept an optional `shutdown` event (clean
DI), then in the test create a real `asyncio.Event` and `call_later` its `set`,
or `create_task(run(...))`, sleep, and `shutdown.set()` explicitly. If `run()`'s
signature must stay fixed, patch `main.asyncio.Event` (module-scoped) rather than
the stdlib global, and assert on the observable outcome (clean return, log
emitted) rather than relying on a timer race.

## Info

### IN-01: Sibling exceptions discarded when multiple concerns crash together

**File:** `src/provisioning_worker/main.py:71-72`
**Issue:** `raise SystemExit(1) from eg.exceptions[0]` chains only the first
exception in the group. If two concerns crash in the same loop tick, the second
(and beyond) are dropped from the traceback chain (the original `ExceptionGroup`
is not preserved), costing debuggability.
**Fix:** Chain the group itself: `raise SystemExit(1) from eg`. The full group
(all sub-exceptions) is then visible in the traceback.

### IN-02: Signal handlers are never removed; assumes Unix loop

**File:** `src/provisioning_worker/main.py:60-62`
**Issue:** `loop.add_signal_handler(...)` is Unix-only (raises
`NotImplementedError` on the Windows proactor loop) and the handlers are never
removed on clean shutdown. For a Linux-only container worker this is acceptable,
but it is undocumented and would surprise anyone running the worker on Windows
for local dev.
**Fix:** Optionally `loop.remove_signal_handler(sig)` in a `finally`, and/or
guard with a comment noting the Unix-only assumption. Not required for the
container target.

### IN-03: Online-mode `version_table` hardcoded instead of read from section

**File:** `migrations/provisioning/env.py:66`
**Issue:** `run_migrations_offline` reads `version_table` from the
`[provisioning]` section (line 46) but `run_migrations_online` hardcodes
`version_table="alembic_version"` (line 66). Both currently resolve to the same
value, so there is no behavioral bug, but the asymmetry invites drift if the
section value is ever changed.
**Fix:** Read it once and pass the same value to both:
```python
version_table = config.get_section_option(SCHEMA, "version_table", "alembic_version")
```
and use `version_table=version_table` in the online `context.configure(...)`.

### IN-04: `_include_object` shadows the builtin `object`

**File:** `migrations/provisioning/env.py:30`
**Issue:** The first parameter is named `object`, shadowing the builtin. This is
the conventional Alembic signature, but per `docs/python-style.md` it reads
poorly; ruff does not flag it here because `migrations/` is excluded from lint.
**Fix:** Rename to `obj` (and update the `getattr(object, "schema", ...)`
reference). Cosmetic; safe to defer.

### IN-05: `__main__.main()` docstring claims "any unhandled exception" but `SystemExit` bypasses the handler

**File:** `src/provisioning_worker/__main__.py:11`
**Issue:** The docstring says "Exits non-zero on any unhandled exception," but as
shown in WR-01 the `SystemExit(1)` from `run()` never enters the `except*` body;
it just propagates. The exit code is correct, but the docstring overstates what
the handler does. Tighten the wording when applying the WR-01 fix.
**Fix:** Reword to reflect the actual contract (e.g. "Exits 1 on a concern crash
or a boot-time failure"), and ensure the code path matches after WR-01.

---

_Reviewed: 2026-06-01T15:25:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
