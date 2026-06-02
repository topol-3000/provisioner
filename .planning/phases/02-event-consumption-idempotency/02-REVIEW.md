---
phase: 02-event-consumption-idempotency
reviewed: 2026-06-02T00:00:00Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - migrations/provisioning/env.py
  - migrations/provisioning/versions/20260602_0905_add_processed_event.py
  - src/provisioning_worker/adapters/valkey_streams.py
  - src/provisioning_worker/events/__init__.py
  - src/provisioning_worker/events/envelope.py
  - src/provisioning_worker/events/subscription.py
  - src/provisioning_worker/main.py
  - src/provisioning_worker/modules/provisioning/handlers.py
  - src/provisioning_worker/modules/provisioning/models.py
  - src/provisioning_worker/ports/event_consumer.py
  - src/provisioning_worker/settings.py
  - src/provisioning_worker/shared/event_consumer.py
  - tests/conftest.py
  - tests/events/__init__.py
  - tests/events/test_envelope.py
  - tests/events/test_subscription_payloads.py
  - tests/provisioning/test_handlers.py
  - tests/provisioning/test_idempotency.py
  - tests/provisioning/test_models.py
  - tests/test_settings.py
findings:
  critical: 1
  warning: 5
  info: 4
  total: 10
status: issues_found
---

# Phase 2: Code Review Report

**Reviewed:** 2026-06-02
**Depth:** standard
**Files Reviewed:** 20
**Status:** issues_found

## Summary

Phase 2 ships the consume-side data layer (envelope + five `subscription.*`
payloads + registry), the Valkey Streams consumer adapter, the
`processed_event` idempotency ledger + migration, the same-transaction dedupe
guard, and five no-op handlers wired in `main.py`. The contract models match
`docs/events.md` field-for-field, the layering respects the CLAUDE.md
dependency rule, and the happy-path dispatch pipeline is correct.

The dominant correctness gap is in the idempotency guard: it is a bare
SELECT-then-INSERT with no handling of the `IntegrityError` that the composite
primary key is explicitly there to raise. Under the at-least-once delivery the
project guarantees, a concurrent or reclaim-vs-fresh-read duplicate crashes the
consumer task rather than short-circuiting — which directly contradicts the
guarantee the `ProcessedEvent` docstring claims to provide. Several warnings
around the `XACK`-on-handler-exception flow, an env.py version-table
inconsistency, and a confusing-but-legal `except A, B:` form round out the
findings.

No structural-findings substrate was supplied with this review.

## Critical Issues

### CR-01: Dedupe guard cannot survive a concurrent/replayed duplicate — unhandled `IntegrityError` crashes the consumer

**File:** `src/provisioning_worker/shared/event_consumer.py:73-80`
**Issue:**
`handle_with_dedupe` performs a single SELECT, and if no row is found, runs the
handler, INSERTs the ledger row, and commits — with no protection against the
unique-key conflict the composite PK exists to produce:

```python
async with session_scope() as session:
    existing = await _select_processed_event(session, raw_env.id, consumer_group)
    if existing is not None:
        return
    await handler_fn(raw_env, payload, session)
    await _insert_processed_event(session, raw_env.id, consumer_group)
    await session.commit()   # can raise IntegrityError
```

The `ProcessedEvent` docstring (`models.py:29-37`) asserts the intended
behavior: *"a re-delivered event conflicts on INSERT, the transaction rolls
back, and the dedupe guard short-circuits on the re-query."* The code never
implements the re-query or the conflict handling. Two realistic at-least-once
paths defeat it:

1. **Reclaim races a fresh read.** `_reclaim()` (`valkey_streams.py:220-245`)
   routes a still-pending PEL entry through `_dispatch` → the same handler.
   If the original delivery is mid-flight (handler running, not yet
   committed/ACKed), both calls pass the `existing is None` check, both run the
   handler, and the second `commit()` raises `IntegrityError`.
2. **Multiple consumers** (`consumer_name` is configurable; the design
   anticipates a consumer group with >1 member) processing the same message id
   produce the same conflict.

When the conflict raises, the exception propagates out of `handle_with_dedupe`
→ out of `handler(...)` at `valkey_streams.py:216` → out of `_dispatch` →
out of the `for` loop in `run()` (`valkey_streams.py:144-146`), with **no
`XACK`** issued. The consumer task dies, the `TaskGroup` in `main.py:74-75`
raises, and the process exits non-zero. Because the message was never ACKed, on
restart it is redelivered and — if the conflicting row committed — now
short-circuits, but the crash/restart churn is a denial-of-availability under
nothing more than ordinary duplicate delivery. Worse, the handler side effect
(Phase 3 will add real writes here) ran twice before the second transaction
rolled back its ledger insert, so "exactly once" is not actually guaranteed for
the side effect on the losing transaction.

**Fix:** Catch the integrity conflict, roll back, and treat it as a
short-circuit (idempotent success), so the caller proceeds to `XACK`:

```python
from sqlalchemy.exc import IntegrityError

async with session_scope() as session:
    existing = await _select_processed_event(session, raw_env.id, consumer_group)
    if existing is not None:
        log.debug("dedupe short-circuit", envelope_id=raw_env.id)
        return
    await handler_fn(raw_env, payload, session)
    await _insert_processed_event(session, raw_env.id, consumer_group)
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent duplicate won the race; its commit is authoritative.
        await session.rollback()
        log.debug("dedupe conflict — concurrent duplicate", envelope_id=raw_env.id)
        return
```

Add a unit test that drives two `handle_with_dedupe` calls whose commits race
(or simulate the conflict by pre-inserting the row before the second commit)
and assert the second call returns without raising. The current
`test_replay_short_circuits` only exercises the *sequential* replay path, which
hits the SELECT guard and never the INSERT conflict — so this defect is
invisible to the suite.

## Warnings

### WR-01: Handler exception skips `XACK` silently — no error boundary in the dispatch loop

**File:** `src/provisioning_worker/adapters/valkey_streams.py:214-218`
**Issue:**
```python
handler = handlers.get(raw_env.type)
if handler is not None:
    await handler(raw_env, payload)
await self._ack(msg_id)
```
Any exception raised by the wrapped handler (the `IntegrityError` of CR-01, a
DB outage, a bug in Phase 3 convergence) propagates straight out of `_dispatch`
and `run()`, killing the consumer task. There is no logging at this boundary
and no decision about whether the message should be retried (left in PEL,
reclaimed later) versus dead-lettered. The poison/unknown branches are handled
with care; the *handler-failure* branch — the most operationally important one
once handlers do real work — has no policy at all. At minimum, transient
handler failures should be logged and left un-ACKed (so reclaim retries them)
rather than crashing the loop.
**Fix:** Wrap the handler call so transient failures are logged and the message
is intentionally left unacked (no `XACK`) for later reclaim, while the loop
survives:

```python
handler = handlers.get(raw_env.type)
if handler is not None:
    try:
        await handler(raw_env, payload)
    except Exception:
        log.exception("handler failed — leaving message unacked for reclaim",
                      msg_id=msg_id, envelope_type=raw_env.type)
        return  # no XACK; reclaim path retries
await self._ack(msg_id)
```
Decide the retry-vs-dead-letter policy explicitly and document it; do not let
"crash the worker" be the default for every handler error.

### WR-02: `run_migrations_online` ignores the configurable version-table name

**File:** `migrations/provisioning/env.py:48,68`
**Issue:** Offline mode reads the version-table name from config
(`version_table=config.get_section_option(SCHEMA, "version_table", "alembic_version")`,
line 48) but online mode hardcodes `version_table="alembic_version"` (line 68).
If `alembic.ini`'s `provisioning` section ever sets a custom `version_table`,
offline and online migrations will track revisions in *different* tables,
silently re-running or skipping migrations. Since the documented workflow
(`make migrate`) uses online mode, a config drift here is a latent data-safety
issue.
**Fix:** Read the same option in both functions:
```python
version_table = config.get_section_option(SCHEMA, "version_table", "alembic_version")
context.configure(
    ...,
    version_table=version_table,
    version_table_schema=SCHEMA,
)
```

### WR-03: `except json.JSONDecodeError, KeyError:` is the unparenthesized tuple form — reads like the removed Python 2 syntax

**File:** `src/provisioning_worker/adapters/valkey_streams.py:176`
**Issue:** `except json.JSONDecodeError, KeyError:` parses in Python 3.14 as
`except (json.JSONDecodeError, KeyError):` (a tuple of types) and *does* catch
both exceptions — verified. But it is visually identical to the Python-2
`except E, name:` capture form that was removed in Python 3, so any reader will
flag it as a bug, and a future edit adding an `as exc:` next to it would break.
This is a correctness-adjacent readability hazard in the single most
safety-critical parse path. Ruff does not flag it.
**Fix:** Always parenthesize multi-exception tuples:
```python
except (json.JSONDecodeError, KeyError):
```

### WR-04: `_dispatch` ACKs known-type messages with no registered handler and writes no ledger row — silent drop

**File:** `src/provisioning_worker/adapters/valkey_streams.py:214-218`
**Issue:** If `raw_env.type` is in `_PAYLOAD_REGISTRY` (so it passes the
unknown-type guard and validates) but is absent from the `handlers` map,
`handlers.get(...)` returns `None`, the handler is skipped, and the message is
`XACK`ed with no `processed_event` row and no log line. Today `main.py` wires a
handler for all five registered types, so this is latent — but it is a silent
data-loss path the moment a sixth payload model is registered without its
handler (an easy omission given they live in different files). The unknown-type
branch at least warns; this branch is completely silent.
**Fix:** Log a warning when a registered type has no handler, and decide
whether to ACK (drop) or leave unacked:
```python
handler = handlers.get(raw_env.type)
if handler is None:
    log.warning("registered type has no handler — dropping",
                msg_id=msg_id, envelope_type=raw_env.type)
    await self._ack(msg_id)
    return
await handler(raw_env, payload)
await self._ack(msg_id)
```

### WR-05: Shutdown not observed during the `XAUTOCLAIM` scan or message processing

**File:** `src/provisioning_worker/adapters/valkey_streams.py:135-149,232-245`
**Issue:** The poll loop only checks `shutdown.is_set()` at the top of each
cycle. Inside a cycle it processes the full `results` batch and, every 60
cycles, runs `_reclaim`, whose `while True:` loop walks the entire PEL with
repeated `XAUTOCLAIM` calls and re-dispatches every reclaimed entry. A
shutdown signalled during a large reclaim cannot interrupt it; the worker keeps
dispatching until the full scan completes. This weakens the "graceful stop
after the in-flight poll cycle" contract documented in the port
(`ports/event_consumer.py:48-62`) into "after the in-flight reclaim scan," which
can be unbounded.
**Fix:** Check `shutdown.is_set()` inside the message loop and inside the
`_reclaim` `while` loop, breaking out early when set.

## Info

### IN-01: Redundant assertion in happy-path dispatch test; ordering claim not actually verified

**File:** `tests/provisioning/test_idempotency.py:80-83`
**Issue:** Line 83 (`assert handler.await_count == 1`) duplicates line 80
(`handler.assert_awaited_once()`). More importantly, the test's comment claims
"XACK happens after the handler returns" but nothing asserts the *ordering* of
`handler` vs `xack` — both are independent `AsyncMock`s with no shared call
recorder. The commit-then-ack guarantee (the entire point of the module) is not
tested.
**Fix:** Use a shared `MagicMock` parent (`mock_manager.attach_mock(...)`) and
assert `mock_calls` ordering, or have the handler mock record a timestamp/order
token the test compares against the `xack` call.

### IN-02: `_include_object` shadows the builtin `object`

**File:** `migrations/provisioning/env.py:32`
**Issue:** The first parameter is named `object`, shadowing the builtin
(ruff `A002`-class smell). Harmless here but a poor pattern to copy into future
autogenerate hooks.
**Fix:** Rename to `obj` (and `type_` is already disambiguated — keep that).

### IN-03: `get_settings()` is `lru_cache`d but Alembic env.py imports it at module load

**File:** `migrations/provisioning/env.py:25` / `settings.py:99-102`
**Issue:** `env.py` calls `get_settings()` at import time to set the SQLAlchemy
URL. Because `get_settings` is `@lru_cache(maxsize=1)`, the cached `Settings`
will be reused for the whole Alembic process. This is fine for the migration
CLI but worth a note: any tooling that imports `env.py` and later wants
different settings cannot, and the `# type: ignore[call-arg]` on the cached
constructor hides that all fields come from env. Not a defect; flagging the
coupling.
**Fix:** No change required for Phase 2; document the assumption if env-driven
migration overrides are ever needed.

### IN-04: `EventEnvelope` is defined and exported but never used on the consume path

**File:** `src/provisioning_worker/events/envelope.py:23-60`
**Issue:** The adapter validates with the adapter-local `_RawEnvelope`
(`valkey_streams.py:53-72`), not the public generic `EventEnvelope`. The latter
is exercised only by tests. This is a deliberate two-phase-parse design (the
docstrings explain it), but it means two envelope definitions must be kept in
sync by hand — `_RawEnvelope` drops the `id` min/max-length=26 constraint and
the `version >= 1` constraint that `EventEnvelope` enforces, so the adapter
accepts a malformed-length `id` or `version=0` that the public model would
reject. Low impact (the `id` is only used as a dedupe key string), but it is a
silent divergence in validation strictness between the two envelopes.
**Fix:** Either have `_RawEnvelope` carry the same `Field` constraints as
`EventEnvelope` (min/max length on `id`, `ge=1` on `version`), or add a test
asserting the two envelopes share the same outer-field constraints so drift is
caught.

---

_Reviewed: 2026-06-02_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
