---
phase: 02-event-consumption-idempotency
reviewed: 2026-06-02T00:00:00Z
depth: standard
files_reviewed: 4
files_reviewed_list:
  - src/provisioning_worker/shared/event_consumer.py
  - src/provisioning_worker/adapters/valkey_streams.py
  - tests/provisioning/test_idempotency.py
  - tests/test_settings.py
findings:
  critical: 0
  warning: 4
  info: 3
  total: 7
status: issues_found
---

# Phase 2: Code Review Report (gap-closure 02-04)

**Reviewed:** 2026-06-02T00:00:00Z
**Depth:** standard
**Files Reviewed:** 4
**Status:** issues_found

## Summary

Re-review scoped to the phase-02 gap-closure changes (plan 02-04): the
`IntegrityError` catch/rollback hardening in `handle_with_dedupe` (CONS-03), the
handler-failure error boundary and missing-handler warning in
`ValkeyStreamsConsumer._dispatch` (WR-01, WR-04), and a test-isolation fix in
`test_settings.py`.

The diff is small and the happy-path behavior is correct: commit-then-ack
ordering is preserved, the transient-failure boundary keeps the poll loop alive,
and the concurrent-duplicate race is caught and treated as idempotent. Unit
tests pass (16 passed, 3 integration deselected) and `ruff check` /
`ruff format --check` are clean.

No BLOCKER-tier defects. Four WARNING-tier issues degrade robustness or
maintainability and should be addressed: (1) the `IntegrityError` catch is too
broad and will swallow unrelated integrity violations as "duplicates" the moment
Phase 3 adds the second handler-side unique constraint; (2) deterministically-
failing handlers reclaim forever with no delivery cap or dead-letter path;
(3) the `except json.JSONDecodeError, KeyError:` clause relies on the new,
non-obvious PEP 758 bare-tuple syntax and its `KeyError` (missing-`envelope`-
field) arm is untested; (4) `log.exception` at the handler boundary captures a
traceback that may surface payload field values.

## Warnings

### WR-01: Broad `IntegrityError` catch will swallow non-dedupe constraint violations

**File:** `src/provisioning_worker/shared/event_consumer.py:86-92`
**Issue:** The `except IntegrityError` around `session.commit()` assumes *any*
integrity violation is a `processed_event` composite-PK conflict from a
concurrent duplicate, rolls back, logs at `debug`, and returns normally — which
causes the caller to `XACK` (permanently dropping the event). In Phase 2 this is
safe because handlers are no-ops and the only staged row is the
`processed_event` insert. But CLAUDE.md §6.4 commits Phase 3 to a *second*
handler-side unique constraint — `UNIQUE (instance_id, change_set_id)` on
`provisioning_task`. Once a handler stages a row that violates that (or any FK),
`commit()` raises `IntegrityError`, this code mis-classifies it as a duplicate,
silently swallows it at `debug` level, and ACKs — losing the event with no
error-level signal and no reclaim. The catch does not discriminate on which
constraint was violated.
**Fix:** Narrow the catch to the dedupe-ledger conflict only. After rollback,
re-`SELECT` the `processed_event` row in a fresh scope and confirm it now exists
before treating the conflict as idempotent; otherwise re-raise so the handler
boundary leaves the message unacked for reclaim:
```python
try:
    await session.commit()
except IntegrityError:
    await session.rollback()
    async with session_scope() as verify:
        if await _select_processed_event(verify, raw_env.id, consumer_group) is None:
            raise  # genuine integrity failure — do not ACK; let reclaim retry
    log.debug("dedupe conflict — concurrent duplicate", envelope_id=raw_env.id)
    return
```
Alternatively, inspect `exc.orig` / the violated constraint name (psycopg
`UniqueViolation`, `constraint_name == "processed_event_pkey"`) and re-raise on
anything else.

### WR-02: Deterministically-failing handler reclaims forever — no delivery cap or dead-letter

**File:** `src/provisioning_worker/adapters/valkey_streams.py:233-241`
**Issue:** The new handler-failure boundary returns without `XACK` on any
exception so `XAUTOCLAIM` reclaims the entry (correct for *transient* failures).
But there is no max-delivery counter and no dead-letter path (grep for
`max_deliver` / `delivery` / `dead.letter` / `attempt` finds none). A handler
that fails *deterministically* — a logic-poison payload that raises every time,
or a persistent downstream outage — is reclaimed indefinitely: never ACKs,
re-enters `_dispatch` on every reclaim scan, re-runs the handler, fails, repeats.
This is an unbounded reprocessing loop that also prevents the PEL from draining.
The WR-01 fix correctly distinguishes "leave unacked" from "ACK" but conflates a
transient failure with a deterministic one.
**Fix:** Bound retries using the entry's delivery count. On reclaim, read the
count (via `XPENDING`, or track `msg_id -> attempts`) and, past a threshold,
route the entry to a dead-letter stream (e.g. `events.subscription.dlq`) and
`XACK` so the PEL drains and a human can inspect the poison. At minimum, log the
delivery count at `warning`/`error` so the loop is observable rather than silent.

### WR-03: `except json.JSONDecodeError, KeyError:` relies on surprising PEP 758 syntax and its `KeyError` arm is untested

**File:** `src/provisioning_worker/adapters/valkey_streams.py:185`
**Issue:** The clause `except json.JSONDecodeError, KeyError:` reads like the
Python-2 `except E, name:` (bind-to-variable) form, which would be a bug. It only
works because Python 3.14 added PEP 758 (parenthesis-free multi-exception
except), so it parses as the tuple `(json.JSONDecodeError, KeyError)` and does
catch both (verified at runtime: bad JSON and a missing `envelope` field both hit
the branch; `ruff` accepts it). It is correct on 3.14 today, but the bare form is
fragile — easy to misread as a bug and "fix" by deleting `, KeyError`, and it
silently breaks on any pre-3.14 interpreter. Compounding the risk, the `KeyError`
(missing-`envelope`-field) arm has **no test**: `test_dispatch_bad_json_is_poison`
exercises only the `JSONDecodeError` arm, and no test feeds `fields` without an
`envelope` key. A regression dropping `KeyError` would pass the whole suite.
**Fix:** Parenthesize for clarity and add the missing test:
```python
except (json.JSONDecodeError, KeyError):
    log.error("poison message — bad JSON or missing envelope field", msg_id=msg_id)
    await self._ack(msg_id)
    return
```
```python
async def test_dispatch_missing_envelope_field_is_poison() -> None:
    consumer = _consumer_with_mock_client()
    handler = AsyncMock()
    await consumer._dispatch("2b-0", {}, {"subscription.activated": handler})
    handler.assert_not_awaited()
    consumer._client.xack.assert_awaited_once()
```

### WR-04: `log.exception` at the handler boundary may capture payload field values in the traceback

**File:** `src/provisioning_worker/adapters/valkey_streams.py:235-240`
**Issue:** The handler-failure boundary calls `log.exception(...)`, which records
the full traceback. A traceback raised from inside a handler can surface local
variables / payload field values depending on the structlog formatter and any
frame-capturing processor. The consumed `subscription.*` payloads carry
sensitive identifiers (`stripe_subscription_id`) today, and CLAUDE.md §6.6
forbids logging secrets/tokens — a constraint that bites harder in later phases
when handlers touch `instance_credential` / admin passwords. In Phase 2 handlers
are no-ops so the exposure is theoretical, but this boundary is exactly where a
future credential value could leak into logs.
**Fix:** Keep the explicit bound context (`msg_id`, `envelope_type`) and ensure
tracebacks render without frame locals (structlog's default `format_exc_info`
omits locals — verify no `dict_tracebacks`/`ExceptionRenderer` with locals is
configured). Consider logging the exception type/message rather than a full
`log.exception` for handler failures, e.g.
`log.error("handler failed ...", error=type(exc).__name__)`, reserving full
tracebacks for a layer guaranteed to be payload-free.

## Info

### IN-01: `_RawEnvelope` drops the canonical envelope's `id` length and `producer` Literal constraints

**File:** `src/provisioning_worker/adapters/valkey_streams.py:65-69`
**Issue:** The canonical `EventEnvelope` (events/envelope.py) constrains
`id: str = Field(min_length=26, max_length=26)` and `producer:
Literal["platform-api","provisioning-worker","telemetry-worker"]`. The adapter's
first-phase `_RawEnvelope` weakens both to bare `str`, so a wrong-length `id`
(used directly as the dedupe key) or an unknown `producer` passes the stage-2
"strict" validation. Pre-existing (outside the reviewed diff regions) but worth
tightening since `id` is the idempotency key.
**Fix:** Mirror the canonical constraints on `_RawEnvelope` (`Field(min_length=26,
max_length=26)` on `id`; the same `Literal` on `producer`) so envelope-level
drift is caught here too.

### IN-02: Handler-failure test does not assert the failure was logged

**File:** `tests/provisioning/test_idempotency.py:247-263`
**Issue:** `test_dispatch_handler_failure_leaves_message_unacked` asserts only
`xack.assert_not_awaited()`. It does not assert that `log.exception` was emitted,
so a regression that swallows the failure silently (no log) would still pass —
yet the WR-01 design goal is an *observable* unacked failure.
**Fix:** Add a structlog-capture assertion that the
`"handler failed — leaving message unacked for reclaim"` event was logged at
exception/error level.

### IN-03: Concurrent-duplicate conflict logged at `debug` may be too quiet

**File:** `src/provisioning_worker/shared/event_consumer.py:91`
**Issue:** The IntegrityError-conflict branch logs at `debug`. Once WR-01 is
addressed and the catch is narrowed to genuine dedupe races, a sustained stream
of these conflicts is a useful operational signal (e.g. a mis-tuned reclaim
window producing heavy duplicate delivery). At `debug` it is invisible in
non-dev JSON logging.
**Fix:** Consider `info` for the confirmed-duplicate path, or keep `debug` but
ensure the WR-01 re-raise path surfaces the genuinely-anomalous case at `error`.

---

*Reviewed: 2026-06-02T00:00:00Z*
*Reviewer: Claude (gsd-code-reviewer)*
*Depth: standard*
