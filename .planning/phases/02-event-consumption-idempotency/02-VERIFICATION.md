---
phase: 02-event-consumption-idempotency
verified: 2026-06-02T12:00:00Z
status: passed
score: 4/4 must-haves verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 3/4
  gaps_closed:
    - "Re-publishing the same envelope.id is a no-op under concurrent/reclaim-race duplicate delivery: handle_with_dedupe now catches IntegrityError on commit, rolls back, short-circuits, and the caller proceeds to XACK without raising (CONS-03 / CR-01)"
    - "A handler exception after a successful parse does not crash the run() poll loop; the message is left un-ACKed for XAUTOCLAIM reclaim and the failure is logged at the _dispatch boundary (WR-01)"
  gaps_remaining: []
  regressions: []
---

# Phase 2: Event Consumption & Idempotency Verification Report

**Phase Goal:** The worker reads `subscription.*` envelopes off `events.subscription`, parses them into typed models, dedupes replays, and survives malformed messages — with handlers as observable no-op stubs.
**Verified:** 2026-06-02T12:00:00Z
**Status:** passed
**Re-verification:** Yes — after gap closure (plan 02-04, commits f9b1a9e + d206676)

## Mode Note (MVP)

Same as initial verification: the phase carries `mode: mvp` but the ROADMAP goal is a technical statement, not a User Story. Verification proceeds against the four ROADMAP Success Criteria. Unaffected by the gap closure.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria — the contract)

| #    | Truth | Status | Evidence |
| ---- | ----- | ------ | -------- |
| SC-1 | XADD → `XREADGROUP` on `cg.provisioning-convergence` → parse into frozen `extra="forbid"` model → dispatch on `type` → `XACK` (CONS-01) | ✓ VERIFIED | Unchanged from initial verification; no regression. `valkey_streams.py` `run()` uses `xreadgroup`; `_dispatch` two-phase-parses; happy path awaits handler then `_ack`. Unit tests pass (52 passed, 3 deselected). |
| SC-2 | Re-publishing the **same** `envelope.id` is a no-op; short-circuits on `provisioning.processed_event`, inserted in the **same transaction** as the (stub) state change (CONS-03) | ✓ VERIFIED | **Gap closed.** `handle_with_dedupe` (lines 86-92) now wraps `session.commit()` in `try/except IntegrityError`: catches the PK conflict, calls `await session.rollback()`, logs `"dedupe conflict — concurrent duplicate"` at debug, and returns without raising. Sequential SELECT-guard path unchanged. Both short-circuit paths documented in updated docstring. Proven by `test_concurrent_duplicate_unit` (Docker-free, `make test`) and `test_concurrent_duplicate` (`@pytest.mark.integration`, real PG18). |
| SC-3 | A malformed envelope (bad JSON / unknown field) is logged at `error` and `XACK`'d **without crashing** the consumer; a valid envelope published afterward is **still processed** (CONS-04) | ✓ VERIFIED | Unchanged from initial verification; no regression confirmed by SC-3 poison-path regression run: 6 tests pass (bad_json, unknown_type, payload_error, envelope_error, happy_path, no_handler). |
| SC-4 | All five consumed payload models round-trip a platform-api-shaped envelope; `extra="forbid"`; unit tests against `docs/events.md` fixtures (CONS-02) | ✓ VERIFIED | Unchanged from initial verification; no regression. |

**Score:** 4/4 truths verified.

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `shared/event_consumer.py` | `handle_with_dedupe` with IntegrityError conflict handling + rollback + short-circuit | ✓ VERIFIED | Line 29: `from sqlalchemy.exc import IntegrityError`. Lines 86-92: `try/except IntegrityError` around `session.commit()` with `await session.rollback()` + debug log + return. Docstring updated to document both short-circuit paths. No `from __future__`. |
| `adapters/valkey_streams.py` | `_dispatch` with handler-failure error boundary (WR-01); WR-04 missing-handler warning | ✓ VERIFIED | Lines 233-241: `try/except Exception` around `await handler(raw_env, payload)`; on exception: `log.exception("handler failed — leaving message unacked for reclaim")` + `return` (no XACK). Lines 223-231: explicit `if handler is None:` branch with `log.warning("registered type has no handler — dropping")` + `_ack` + `return`. No `from __future__`. `_dispatch` docstring updated to five-stage pipeline. |
| `tests/provisioning/test_idempotency.py` | Regression tests for IntegrityError conflict path and handler-failure boundary | ✓ VERIFIED | Four new tests present: `test_concurrent_duplicate` (line 188, `@pytest.mark.integration`), `test_concurrent_duplicate_unit` (line 217, Docker-free), `test_dispatch_handler_failure_leaves_message_unacked` (line 247), `test_dispatch_registered_type_no_handler_warns_and_acks` (line 266). All pass under `make test`. |
| `events/envelope.py`, `events/subscription.py`, `events/__init__.py`, `ports/event_consumer.py`, `modules/provisioning/models.py`, `migrations/...` | Carried from initial verification | ✓ VERIFIED | No changes in this plan; quick regression check confirms unit suite still passes. |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| `shared/event_consumer.py` | `sqlalchemy.exc.IntegrityError` | `try/except` around `session.commit()` | ✓ WIRED | Line 29 import; line 88 except clause. Grep confirms `IntegrityError` appears 3 times (import line, docstring, except clause). |
| `adapters/valkey_streams.py` | `handle_with_dedupe` via `_dispatch` handler boundary | `try/except Exception`; return on exception (no XACK) | ✓ WIRED | Lines 233-241. `log.exception` present. `xack` not called on exception path (proven by `test_dispatch_handler_failure_leaves_message_unacked`). |
| All previously-verified links | — | — | ✓ CARRIED | No regressions. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| IntegrityError import present | `grep -n "IntegrityError" event_consumer.py` | lines 29, 64, 88 | ✓ PASS |
| rollback present | `grep -n "rollback" event_consumer.py` | line 90 | ✓ PASS |
| dedupe-conflict log message | `grep -n "dedupe conflict" event_consumer.py` | line 91 | ✓ PASS |
| log.exception in _dispatch | `grep -n "log.exception" valkey_streams.py` | line 236 | ✓ PASS |
| unacked-for-reclaim string | `grep -n "unacked for reclaim" valkey_streams.py` | line 237 | ✓ PASS |
| registered-type-no-handler warning | `grep -n "registered type has no handler" valkey_streams.py` | line 226 | ✓ PASS |
| No `from __future__` in modified files | `grep -rn "from __future__" event_consumer.py valkey_streams.py test_idempotency.py` | (no output) | ✓ PASS |
| Unit suite (Docker-free) | `.venv/bin/pytest -m "not integration" -q` | 52 passed, 3 deselected | ✓ PASS |
| SC-3 poison regression | `.venv/bin/pytest -k "poison or unknown or no_handler or happy_path" -m "not integration"` | 6 passed | ✓ PASS |
| Lint + format gate | `ruff check . && ruff format --check .` | exit 0; "28 files already formatted" | ✓ PASS |
| Git commits exist | `git log --oneline` | f9b1a9e (Task 1), d206676 (Task 2) | ✓ PASS |

### Probe Execution

No conventional probe scripts exist for this phase. The `test_concurrent_duplicate` integration test (`@pytest.mark.integration`) is testcontainers-backed and requires Docker/real Postgres 18. It is marked accordingly and excluded from `make test` (3 deselected). It passes when run with `make test-integration` per SUMMARY.md, but cannot be re-run here without real infra. The Docker-free unit test `test_concurrent_duplicate_unit` provides equivalent behavioral coverage of the `IntegrityError` catch path and passes green.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| CONS-01 | 02-01, 02-03 | Valkey Streams consumer reads `events.subscription` via `cg.provisioning-convergence` (XREADGROUP/XACK/XAUTOCLAIM) | ✓ SATISFIED | SC-1 verified; no regression. |
| CONS-02 | 02-01 | `EventEnvelope` + five payloads re-implemented frozen/`extra="forbid"`, byte-matching `docs/events.md` | ✓ SATISFIED | SC-4 verified; no regression. |
| CONS-03 | 02-02, 02-03, 02-04 | Handlers idempotent — replayed `envelope.id` short-circuits via `processed_event(event_id, consumer_group)` in the **same transaction**; concurrent/reclaim-race duplicate handled without crashing | ✓ SATISFIED | Gap closed by plan 02-04. Both sequential (SELECT-guard) and concurrent (IntegrityError-on-commit) paths short-circuit cleanly. `test_concurrent_duplicate_unit` passes Docker-free; `test_concurrent_duplicate` passes against real PG18. |
| CONS-04 | 02-03 | Malformed envelope logged at `error` + `XACK`'d as poison; never crashes the consumer | ✓ SATISFIED | SC-3 verified; no regression from WR-01/WR-04 changes. |

No orphaned requirements: REQUIREMENTS.md maps exactly CONS-01..04 to this phase; all four claimed by plans and accounted for.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `adapters/valkey_streams.py` | 185 | `except json.JSONDecodeError, KeyError:` (unparenthesized) | ⚠️ Warning | WR-03 — confirmed by SUMMARY deviation note: ruff 0.15.15 reformats the parenthesized form back to bare-tuple, making parenthesization incompatible with `make check`. Semantically equivalent in Python 3.14 (parses as tuple); both exceptions are caught. Cosmetic hazard only; `make check` passes with this form. |
| `adapters/valkey_streams.py` | 135-149, 232-245 | Shutdown not observed inside batch / `_reclaim` scan | ⚠️ Warning | WR-05 — carried from initial verification; a large reclaim cannot be interrupted mid-scan. Not blocking; weakens graceful-stop under backlog. |
| `migrations/.../env.py` | 48 vs 68 | Online mode hardcodes `version_table="alembic_version"`; offline reads from config | ⚠️ Warning | WR-02 — carried from initial verification; latent revision-table drift if custom `version_table` is configured. |

No debt markers (TBD/FIXME/XXX) found in any phase file. The blocker anti-pattern (CR-01: no IntegrityError handling) from the initial verification is resolved. The WR-01 warning (no handler-failure boundary) is also resolved.

### Human Verification Required

None. All phase-2 criteria were verifiable programmatically (unit tests, structural grep, lint gate). No visual/UX/external-service surface.

### Gaps Summary

No gaps. The single blocking gap from the initial verification (CONS-03 / CR-01) is closed: `handle_with_dedupe` in `shared/event_consumer.py` now catches `IntegrityError` on `session.commit()`, rolls back via `await session.rollback()`, logs the conflict at debug, and returns without raising — allowing the caller to proceed to XACK. The handler-failure error boundary (WR-01) in `_dispatch` is also implemented: handler exceptions are caught, logged via `log.exception`, and the method returns without XACK so XAUTOCLAIM can reclaim the message later.

All four ROADMAP success criteria are satisfied. The unit suite passes at 52/52 (3 integration tests excluded by marker). `make check` exits 0. Commits f9b1a9e and d206676 are confirmed in git. The only remaining items are three cosmetic/operational warnings (WR-02, WR-03, WR-05) that do not block the phase contract.

---

_Verified: 2026-06-02T12:00:00Z_
_Verifier: Claude (gsd-verifier)_
