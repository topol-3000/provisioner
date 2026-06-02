---
phase: 02-event-consumption-idempotency
verified: 2026-06-02T09:40:00Z
status: gaps_found
score: 3/4 must-haves verified
overrides_applied: 0
gaps:
  - truth: "Re-publishing the same envelope.id is a no-op: the handler short-circuits on provisioning.processed_event, inserted in the same transaction as the (stub) state change (SC-2 / CONS-03)"
    status: partial
    reason: >-
      The sequential-replay path is correct and proven (SELECT finds the row,
      short-circuits). But the dedupe guard performs a bare SELECT-then-INSERT
      with no handling of the IntegrityError the composite PK exists to raise.
      Independently confirmed by probe: when a duplicate processed_event row is
      committed between the guard's SELECT and its INSERT/commit (the
      at-least-once reclaim-race / multi-consumer scenario the design explicitly
      anticipates via XAUTOCLAIM and a configurable consumer_name), commit()
      raises psycopg UniqueViolation -> IntegrityError. That exception
      propagates out of the wrapped handler -> _dispatch -> the run() poll loop
      -> the TaskGroup, with NO XACK issued, crashing the consumer task and
      exiting the process non-zero. This contradicts both the ProcessedEvent
      docstring contract (models.py:30-37: "a re-delivered event conflicts on
      INSERT, the transaction rolls back, and the dedupe guard short-circuits on
      the re-query") and the SC-2 reliability promise under at-least-once
      delivery. Under Phase-2 no-op handlers the double side-effect is harmless,
      but the consumer-crash-on-duplicate is a denial-of-availability today.
    artifacts:
      - path: "src/provisioning_worker/shared/event_consumer.py"
        issue: >-
          handle_with_dedupe (lines 73-80) has no try/except around
          session.commit(); the conflict path the composite PK is designed to
          produce is unhandled. The re-query / short-circuit-on-conflict the
          docstring promises is never implemented.
      - path: "src/provisioning_worker/adapters/valkey_streams.py"
        issue: >-
          _dispatch (lines 214-218) invokes the wrapped handler with no error
          boundary; any handler exception (including this IntegrityError)
          escapes run() and kills the consumer task with no XACK and no log at
          the dispatch boundary (also flagged as WR-01).
      - path: "tests/provisioning/test_idempotency.py"
        issue: >-
          test_replay_short_circuits (lines 159-171) exercises only the
          sequential SELECT-guard path; no test drives a committed-duplicate
          INSERT conflict, so this defect is invisible to the suite.
    missing:
      - "Catch IntegrityError around session.commit() in handle_with_dedupe; roll back and treat the conflict as an idempotent short-circuit (return) so the caller proceeds to XACK."
      - "Add a unit/integration test that pre-commits the ledger row (or races two commits) and asserts the second handle_with_dedupe call returns without raising."
      - "Decide and document a handler-failure policy in _dispatch (transient failure -> log + leave unacked for reclaim; never let a handler exception crash the poll loop)."
deferred: []
---

# Phase 2: Event Consumption & Idempotency Verification Report

**Phase Goal:** The worker reads `subscription.*` envelopes off `events.subscription`, parses them into typed models, dedupes replays, and survives malformed messages — with handlers as observable no-op stubs. (Roadmap; `mode: mvp`.)
**Verified:** 2026-06-02T09:40:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Mode Note (MVP)

The phase carries `mode: mvp`, but its ROADMAP goal is a technical statement, not a User Story (`gsd-sdk user-story.validate` → `valid: false`). Per the MVP guard, the User-Flow-Coverage structure is not forced onto a non-User-Story goal. Verification proceeds against the four ROADMAP Success Criteria — which are the contract — and the plan `must_haves`. The criteria are well-formed and testable, so verification quality is unaffected. (Surface for the developer: the goal could be re-stated as a User Story via `/gsd mvp-phase 2` if strict MVP-mode output is desired.)

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria — the contract)

| #  | Truth | Status | Evidence |
| -- | ----- | ------ | -------- |
| SC-1 | XADD → `XREADGROUP` on `cg.provisioning-convergence` → parse into frozen `extra="forbid"` model → dispatch on `type` → `XACK` (CONS-01) | ✓ VERIFIED | `valkey_streams.py` `run()` uses `xreadgroup(groupname=self._group, …, streams={"events.subscription": ">"})`; `_dispatch` two-phase-parses via `_RawEnvelope` (`frozen=True, extra="forbid"`) + `payload_class_for(type)`; happy path awaits handler then `_ack`. Protocol conformance `isinstance(ValkeyStreamsConsumer(...), EventConsumer)` → True (verified). `start()` XGROUP-CREATE tolerates BUSYGROUP. Unit test `test_dispatch_happy_path_calls_handler_then_acks` passes. |
| SC-2 | Re-publishing the **same** `envelope.id` is a no-op; short-circuits on `provisioning.processed_event`, inserted in the **same transaction** as the (stub) state change (CONS-03) | ✗ FAILED (partial) | Sequential replay VERIFIED: `handle_with_dedupe` SELECTs `ProcessedEvent`, returns on hit; insert + commit are the last statements in one `session_scope()`; integration `test_replay_short_circuits` → 1 row, handler called once (passes against real Postgres 18). **Concurrent/reclaim-race duplicate FAILS**: independent probe confirmed `commit()` raises `IntegrityError` (psycopg UniqueViolation) when the row is committed between SELECT and INSERT — unhandled, crashes the consumer task with no XACK. See Gaps. |
| SC-3 | A malformed envelope (bad JSON / unknown field) is logged at `error` and `XACK`'d **without crashing** the consumer; a valid envelope published afterward is **still processed** (CONS-04) | ✓ VERIFIED | `_dispatch` four-stage policy: bad JSON / missing field → `log.error` + ack + return; envelope `extra="forbid"` drift → `log.error` + ack; unknown-but-valid type → `log.warning` + ack; payload drift → `log.error` + ack. Independent probe: poison (`{"envelope":"not-json"}`) then valid → poison logged at error + acked, no crash, next valid handler awaited, both acked. Unit tests `test_dispatch_bad_json_is_poison`, `_unknown_type_warns_and_acks`, `_payload_validation_error_is_poison`, `_envelope_validation_error_is_poison` all pass. CR-01 does not touch this path (poison never reaches the dedupe guard). |
| SC-4 | All five consumed payload models round-trip a platform-api-shaped envelope; `extra="forbid"`; unit tests against `docs/events.md` fixtures (CONS-02) | ✓ VERIFIED | `events/subscription.py` defines all five payloads + `LineDelta`, each `frozen=True, extra="forbid"`. `EventEnvelope[P: BaseModel]` frozen/extra=forbid with `id` len-26, `version>=1`, `producer` Literal. `test_subscription_payloads.py` (5 tests) round-trips each fixture (`total_amount` "129.99" → Decimal → str) + extra-field rejection; `test_envelope.py` pins the 8-field set. 22 events/handler tests pass. Registry maps all five types; unknown raises `UnknownEnvelopeType` (verified). |

**Score:** 3/4 truths verified (SC-2 partial → counts as failed).

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `events/envelope.py` | `EventEnvelope[P]` generic, frozen/extra=forbid; `stream_for_envelope_type` | ✓ VERIFIED | Exists, substantive, exercised by tests. `id` min/max=26, `version` ge=1, `producer` Literal. |
| `events/subscription.py` | Five payloads + `LineDelta`, all frozen/extra=forbid | ✓ VERIFIED | All six classes present; field sets match plan/`docs/events.md`; `total_amount` plain `Decimal`. |
| `events/__init__.py` | `UnknownEnvelopeType`, `payload_class_for`, `_PAYLOAD_REGISTRY` | ✓ VERIFIED | All five types registered; unknown raises. Imported by adapter. |
| `ports/event_consumer.py` | `EventConsumer` Protocol (start/run/close) + `HandlerFn` | ✓ VERIFIED | `runtime_checkable`; adapter satisfies via `isinstance`. |
| `modules/provisioning/models.py` | `ProcessedEvent` ORM, composite PK, `Base` | ✓ VERIFIED | `__tablename__="processed_event"`, `__table_args__={"schema":"provisioning"}`, PK `(event_id, consumer_group)`, `String(26)`/`Text`/`TIMESTAMP`. |
| `migrations/.../*_add_processed_event.py` | Hand-authored DDL, `schema="provisioning"` ×2, no `from __future__`, composite PK, `down_revision=None` | ✓ VERIFIED | `schema="provisioning"` count=2; `from __future__` count=0; `sa.PrimaryKeyConstraint("event_id","consumer_group")`; `down_revision: str \| None = None`; columns `event_id String(26)` / `consumer_group Text` / `processed_at TIMESTAMP(tz)`. |
| `migrations/provisioning/env.py` | `target_metadata = Base.metadata` | ✓ VERIFIED | Set (not None). |
| `shared/event_consumer.py` | `handle_with_dedupe` + `make_handler_registry` | ⚠️ STUB (incomplete) | Exists and wired, sequential dedupe correct, but missing the `IntegrityError` conflict handling its contract promises (CR-01). Not a file-existence stub; a behavioral gap. |
| `adapters/valkey_streams.py` | `ValkeyStreamsConsumer` (EventConsumer impl) | ✓ VERIFIED (with WR-01 caveat) | Full XREADGROUP/XAUTOCLAIM/poison dispatch; 3-element XAUTOCLAIM unpack; commit-then-ack ordering. No handler-failure error boundary (WR-01) — see warnings. |
| `modules/provisioning/handlers.py` | Five no-op handlers | ✓ VERIFIED | All five present, bind `envelope_id`/`subscription_id`/`correlation_id`, debug log, no DB writes (intentional Phase-2 no-op). |
| `main.py` | `_run_consumer` wired to consumer + 5 handlers + registry | ✓ VERIFIED | Constructs `ValkeyStreamsConsumer`, `make_handler_registry` with all five handlers, runs in try/finally with `close()`. |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| `events/__init__.py` | `events/subscription.py` | `_PAYLOAD_REGISTRY[type]` | ✓ WIRED | `payload_class_for` returns each of the five types; unknown raises. |
| `adapters/valkey_streams.py` | `shared/event_consumer.py` | dedupe-wrapped handler (registry) | ✓ WIRED | Plan deviation (documented): adapter calls a pre-wrapped handler; `handle_with_dedupe` runs inside `make_handler_registry`'s closure. Single-wrap, commit-then-ack preserved; integration SC-1/SC-2 prove the guard runs. |
| `shared/event_consumer.py` | `models.py` | SELECT/INSERT `ProcessedEvent` in `session_scope()` | ✓ WIRED (incomplete) | SELECT + INSERT present; conflict path unhandled (CR-01). |
| `main.py` | `adapters/valkey_streams.py` | `ValkeyStreamsConsumer(settings); run(map, shutdown)` | ✓ WIRED | Confirmed in `_run_consumer`. |
| `models.py` | `migrations/.../env.py` | `target_metadata = Base.metadata` | ✓ WIRED | Confirmed. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Events package + registry import | `python -c "payload_class_for(...) / stream_for_envelope_type(...)"` | all 5 registered, unknown raises | ✓ PASS |
| Protocol conformance | `isinstance(ValkeyStreamsConsumer(...), EventConsumer)` | True | ✓ PASS |
| Unit suite (Docker-free) | `.venv/bin/pytest -m "not integration"` | 49 passed, 2 deselected | ✓ PASS |
| Integration dedupe (real PG18) | `.venv/bin/pytest -m integration test_idempotency.py` | 2 passed (SC-1, SC-2 sequential) | ✓ PASS |
| SC-3 poison-then-valid (probe) | custom `_dispatch` probe | poison logged+acked, no crash, next valid handler awaited, 2 acks | ✓ PASS |
| `except A, B:` form catches both (Py 3.14) | inline probe | catches `JSONDecodeError` + `KeyError` (parses as tuple) | ✓ PASS (not a SyntaxError on 3.14) |
| CR-01 committed-duplicate conflict (probe, real PG18) | custom guard probe | **`IntegrityError` raised — guard crashes** | ✗ FAIL |
| CI gate (configured excludes) | `ruff check .` + `ruff format --check .` | exit 0; "28 files already formatted" | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| CONS-01 | 02-01, 02-03 | Valkey Streams consumer reads `events.subscription` via `cg.provisioning-convergence` (XREADGROUP/XACK/XAUTOCLAIM), dispatches on `type` | ✓ SATISFIED | SC-1 verified; XAUTOCLAIM reclaim present with correct 3-element unpack. |
| CONS-02 | 02-01 | `EventEnvelope` + five payloads re-implemented frozen/`extra="forbid"`, byte-matching `docs/events.md` | ✓ SATISFIED | SC-4 verified; field sets confirmed against plan + docs. |
| CONS-03 | 02-02, 02-03 | Handlers idempotent — replayed `envelope.id` short-circuits via `processed_event(event_id, consumer_group)` in the **same transaction** | ✗ BLOCKED | Sequential path satisfied; concurrent/reclaim-race duplicate crashes the consumer (CR-01). The "same transaction" and short-circuit hold for sequential delivery only; the at-least-once conflict path the design anticipates is unhandled. |
| CONS-04 | 02-03 | Malformed envelope logged at `error` + `XACK`'d as poison; never crashes the consumer; never creates/advances an instance | ✓ SATISFIED | SC-3 verified; no `processed_event` row on any poison branch; consumer survives. |

No orphaned requirements: REQUIREMENTS.md maps exactly CONS-01..04 to this phase; all four are claimed by plans and accounted for above.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `shared/event_consumer.py` | 73-80 | SELECT-then-INSERT with no conflict handling | 🛑 Blocker | CR-01 — crashes consumer on at-least-once duplicate (see Gaps). |
| `adapters/valkey_streams.py` | 214-218 | Handler call with no error boundary in dispatch loop | ⚠️ Warning | WR-01 — any handler exception (incl. CR-01) kills the poll loop with no XACK and no boundary log; most operationally important once handlers do real work (Phase 3). |
| `adapters/valkey_streams.py` | 176 | `except json.JSONDecodeError, KeyError:` (unparenthesized) | ⚠️ Warning | WR-03 — verified to catch both on Python 3.14 (parses as a tuple), but reads like the removed Py2 `except E, name:` form; a future `as exc:` edit would break it. Readability hazard on the most safety-critical parse path. |
| `adapters/valkey_streams.py` | 214-218 | Registered-type-with-no-handler is acked silently (no row, no log) | ⚠️ Warning | WR-04 — latent silent-drop the moment a sixth type is registered without a handler. Today all five are wired, so not active. |
| `adapters/valkey_streams.py` | 135-149, 232-245 | Shutdown not observed inside batch / `_reclaim` scan | ⚠️ Warning | WR-05 — a shutdown during a large reclaim cannot interrupt it; weakens the graceful-stop contract. |
| `migrations/.../env.py` | 48 vs 68 | Online mode hardcodes `version_table="alembic_version"`; offline reads from config | ⚠️ Warning | WR-02 — latent revision-table drift if a custom `version_table` is ever configured. Excluded from ruff scope. |

No debt markers (TBD/FIXME/XXX) found in phase files. The five no-op handlers are intentional, documented Phase-2 stubs (the consume pipeline around them is fully real) — not data-flow stubs.

### Human Verification Required

None. All criteria were verifiable programmatically (unit tests, integration tests against real Postgres 18, and targeted probes). No visual/UX/external-service surface in this phase.

### Gaps Summary

Three of four ROADMAP success criteria are achieved and independently proven: the XREADGROUP→parse→dispatch→XACK pipeline (SC-1/CONS-01), the five-payload contract with `extra="forbid"` round-trips (SC-4/CONS-02), and poison-message survival with continued processing (SC-3/CONS-04). The adapter, ORM, migration, and wiring are all substantive and connected; `make check` passes on its configured scope.

The single blocking gap is in the idempotency dedupe guard (SC-2/CONS-03). `handle_with_dedupe` implements only the sequential SELECT-guard short-circuit and leaves the `IntegrityError` that the composite primary key is explicitly designed to raise completely unhandled. I independently reproduced the failure against a real Postgres 18 container: when a duplicate `processed_event` row commits between the guard's SELECT and its own commit — the at-least-once reclaim-race and multi-consumer scenarios the design anticipates (XAUTOCLAIM routing through the same path, a configurable `consumer_name`) — `commit()` raises `UniqueViolation`/`IntegrityError`, which propagates uncaught through the wrapped handler, `_dispatch`, the `run()` poll loop, and the `TaskGroup`, crashing the consumer with no XACK. This contradicts the `ProcessedEvent` docstring's stated contract and the SC-2 reliability promise under at-least-once delivery. Under the Phase-2 no-op handlers the duplicated side-effect is harmless, but the consumer-crash-on-ordinary-duplicate is a present denial-of-availability and an "exactly-once" hole for the losing transaction once Phase 3 adds real side-effects.

The fix is small and well-understood (catch `IntegrityError` around `commit()`, roll back, short-circuit so the caller XACKs) plus a regression test that drives the conflict — the existing `test_replay_short_circuits` only covers the sequential SELECT path and is blind to this defect. The related WR-01 (no handler-failure error boundary in `_dispatch`) should be resolved alongside it, since it is the same propagation path and becomes critical once handlers do real work. The four warnings (WR-01..05) and the pre-existing, environment-dependent `test_env_file_loading` failure under the `make test` target (a Phase-1 settings-test hermeticity issue, not introduced here, passes under direct pytest) are documented but do not by themselves block the phase contract.

---

_Verified: 2026-06-02T09:40:00Z_
_Verifier: Claude (gsd-verifier)_
