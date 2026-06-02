---
status: partial
phase: 03-registry-create-path-fake-adapter
source: [03-VERIFICATION.md]
started: 2026-06-02T14:00:35Z
updated: 2026-06-02T14:00:35Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Integration test suite passes (Docker required)
expected: `make test-integration` passes all phase-03 integration tests — `test_create_path_succeeds_integration`, `test_enforcement_snapshot_written`, `test_create_fails_then_retries`, `test_no_credential_resend_on_retry`, `test_uuid_pk_version`, `test_subscription_id_unique_violation`. The pre-existing `test_concurrent_duplicate` failure (originates from Phase 02, not this phase) is expected and excluded from this criterion.
result: [pending]

### 2. WR-05 risk acknowledgement — credential delivery is not crash-safe
expected: Human confirms awareness that a worker crash between the `ready` commit and `transport.send_credentials()` permanently loses credential delivery (no `credentials_delivered_at` column yet). Deferred deliberately; load-bearing for M2 SMTP. Acknowledge or schedule a follow-up to add the column via a reviewed migration.
result: [pending]

### 3. CR-03 part 2 product decision — instance flips to `failed` on transient retries
expected: Human confirms that setting `instance.status = failed` on a transient (retryable) failure is the intended, customer-visible product behavior. The PROV-04 integration test currently asserts this. Changing it (only flip to `failed` on terminal failure) is a product decision, not a bug.
result: [pending]

## Summary

total: 3
passed: 0
issues: 0
pending: 3
skipped: 0
blocked: 0

## Gaps
