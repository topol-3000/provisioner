# Phase 3: Registry & create-path (fake adapter) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-02
**Phase:** 3-registry-create-path-fake-adapter
**Areas discussed:** Entitlement resolution, Convergence execution, Retry & failure semantics, Credential delivery

---

## Entitlement resolution

### How does InstanceSpec obtain module_set/seat_cap/resource_caps?
| Option | Description | Selected |
|--------|-------------|----------|
| M1 placeholder spec | Deterministic spec from activated fields + Settings defaults; module resolution deferred behind a seam | ✓ |
| Cross-schema read-back | Worker reads subscription/catalog schema now — heavy, couples to platform-api schema | |
| Reconstruct from deltas | Not viable — activated doesn't carry the initial line set | |

### Explicit seam now, or inline and extract later?
| Option | Description | Selected |
|--------|-------------|----------|
| EntitlementResolver port | Protocol + default M1 impl; M2 swaps read-back without touching spec.py | ✓ |
| Inline in spec.py | Plain function now; extract a port when M2 needs it | |

### What populates the placeholder seat_cap/resource_caps?
| Option | Description | Selected |
|--------|-------------|----------|
| Settings defaults | Fixed typed-Settings defaults; module_set empty/base | ✓ |
| Derive from activated | Map line_count/total_amount into caps — invents an undefined mapping | |

**User's choice:** M1 placeholder spec, behind an EntitlementResolver port, populated from Settings defaults.
**Notes:** Settles the `docs/events.md §Resolving entitlements` open question for M1 as "placeholder now, read-back in M2". No cross-schema coupling in M1.

---

## Convergence execution

### How to split the create-path into Taskiq tasks?
| Option | Description | Selected |
|--------|-------------|----------|
| One create task, all edges | Single create task drives pending→ready; idempotent re-run on failure | ✓ |
| Task per state edge | Separate create/wait-healthy/configure tasks — invents task_types, more surface | |

### Handler vs Taskiq job split?
| Option | Description | Selected |
|--------|-------------|----------|
| Handler opens, job converges | Handler inserts row+task+processed_event and enqueues; job does adapter work + transitions | ✓ |
| Converge inline in handler | Full convergence synchronously before XACK — blocks the consumer | |

### How does wait-until-healthy work against the fake?
| Option | Description | Selected |
|--------|-------------|----------|
| Injected Clock, poll status | Poll get_instance_status with clock-driven waits; fake healthy instantly in tests | ✓ |
| Fake returns ready immediately | No poll loop — wait-for-healthy path never built/exercised | |

### enforcement_snapshot scope in Phase 3?
| Option | Description | Selected |
|--------|-------------|----------|
| Write a minimal v1 snapshot | Write v1 row at configuring + set instance.snapshot_version | ✓ |
| Table only, no rows | Create table, no rows until Phase 5; snapshot_version stays NULL | |

**User's choice:** One idempotent create task; handler opens + enqueues, job converges; injected Clock + poll; write a minimal v1 snapshot.
**Notes:** Resolves the roadmap "SNAP-01 (table)" vs architecture "configuring writes snapshot" tension toward a single initial write; full recompute is Phase 5.

---

## Retry & failure semantics

### How to express backoff parameters?
| Option | Description | Selected |
|--------|-------------|----------|
| Settings-tunable backoff | max_attempts/base_delay_s/multiplier/cap_s typed Settings (5/2s/×2/60s); tests override | ✓ |
| Hardcoded constants | Fixed in tasks.py — tests must monkeypatch, no operator tuning | |

### Instance.status while failing and retrying?
| Option | Description | Selected |
|--------|-------------|----------|
| failed each attempt, re-enter on retry | status=failed + failed_step/failure_reason each attempt; retry re-enters deploying | ✓ |
| stay in-progress, failed only on exhaustion | Hide transient failures; only terminal sets failed — diverges from documented machine | |

### What durably drives the retry?
| Option | Description | Selected |
|--------|-------------|----------|
| provisioning_task ledger | Persisted attempt_count/next_attempt_at/last_error; delayed Taskiq re-kick | ✓ |
| Taskiq retry middleware | In-memory SmartRetry; doesn't survive restart | |

**User's choice:** Settings-tunable exp backoff; failed each attempt then re-enter; provisioning_task is the durable ledger.
**Notes:** instance.failed *event* emission is Phase 4+ — Phase 3 only sets DB columns/status. The ledger is what M2 operator-retry will read.

---

## Credential delivery

### Who generates credentials and how do they reach the service?
| Option | Description | Selected |
|--------|-------------|----------|
| Adapter generates, returns in create-result | Fake adapter mints secrets, returns via CreateResult — per deployment-adapter.md | ✓ |
| Service generates, passes to adapter | Domain mints the password — contradicts the adapter contract | |

### Where does plaintext live between create-result and notification?
| Option | Description | Selected |
|--------|-------------|----------|
| In-memory pass-through, never persisted | Job carries secret in-memory to NotificationTransport, then drops it | ✓ |
| Persist a hash now | Build a slice of instance_credential — pulls M2 scope forward | |

### How is 'deliver on first ready' guarded?
| Option | Description | Selected |
|--------|-------------|----------|
| Guard on first ready_at set | Send once when ready_at goes null→now; re-converge/retry doesn't re-notify | ✓ |
| Send on every ready transition | Re-send on any ready transition — spammy, wrong on re-converge | |

**User's choice:** Adapter generates secrets in the create-result; in-memory pass-through, never persisted/logged/evented; deliver once on first ready_at.
**Notes:** Same adapter contract holds for the real Coolify adapter in M2; all credential storage (instance_credential hash) is M2.

---

## Claude's Discretion

- Exact `DeploymentAdapter` Protocol surface + `CreateResult` shape (dataclass vs tuple).
- `DeploymentStatus` enum shape and poll interval/visibility constants.
- ID strategy (uuid7 instance.id, ULID processed_event.event_id) — per docs.
- `EntitlementResolver` method signature + where the default M1 impl lives.
- `NotificationTransport` method name + `CredentialNotification` shape; console impl writes to stdout, not structlog.
- Fast unit vs `@pytest.mark.integration` test boundary (keep fast path Docker-free).
- Alembic revision content (review autogenerated SQL — CHECK constraints / enums).
- Binding `instance_id` into structlog context now (deferred in Phase 2).

## Deferred Ideas

- Cross-schema entitlement read-back — M2 (behind the EntitlementResolver port).
- instance_credential hash storage + per-instance bearer token — M2.
- enforcement_snapshot recompute + version bump — Phase 5.
- instance.provisioned / instance.failed events + event_outbox → relay — Phase 4.
- lines_changed / suspended / reinstated / cancelled convergence — Phase 5.
- Operator-triggered retry (reads the provisioning_task ledger) — M2.
- change_set_id second-layer dedupe constraint — created with the table, exercised in Phase 5.
