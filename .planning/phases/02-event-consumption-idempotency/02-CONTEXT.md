# Phase 2: Event consumption & idempotency - Context

**Gathered:** 2026-06-02
**Status:** Ready for planning

<domain>
## Phase Boundary

The worker reads `subscription.*` envelopes off `events.subscription` via
`XREADGROUP` on `cg.provisioning-convergence`, parses each into a **frozen,
`extra="forbid"`** typed model, dispatches on `envelope.type`, dedupes replays
on `processed_event` **in the same transaction**, reclaims stuck entries with
`XAUTOCLAIM`, and survives malformed messages — **with handlers as observable
no-op stubs**. This phase proves *delivery + dedupe + poison-survival*, not
domain behavior.

**In scope (CONS-01..04):**
- The re-implemented `EventEnvelope[P]` (generic, frozen, `extra="forbid"`) and
  the **five** consumed `subscription.*` payload models (`activated`,
  `lines_changed`, `suspended`, `reinstated`, `cancelled`) — byte-matching
  platform-api's `docs/events.md`.
- The `EventConsumer` port + `ValkeyStreamsConsumer` adapter (consume side) +
  `shared/event_consumer.py` idempotency wrapper — the consumer loop extracted
  out of `main.py` into the documented seam.
- Dispatch-on-`type` to one no-op handler per consumed type; each handler is
  wrapped by the dedupe guard.
- The **first domain table**: `processed_event` (composite PK
  `(event_id, consumer_group)`), via the single `provisioning` Alembic tree.
- Poison-message handling (malformed → `error` + `XACK`, no instance touched)
  and unknown-but-valid-`type` handling (skip + `XACK`).
- `XAUTOCLAIM` stuck-entry reclaim through the same dispatch+dedupe path.
- Round-trip unit tests for all five payload models against canonical fixtures.

**Explicitly NOT in scope (deferred to later phases):**
- Any `provisioning.instance` / `provisioning_task` / `enforcement_snapshot`
  tables and the instance state machine — **Phase 3** (handlers stay no-op here;
  they do NOT open instance rows or tasks).
- The `change_set_id` second-layer dedupe (`UNIQUE (instance_id, change_set_id)`
  on `provisioning_task`) — depends on the `provisioning_task` table, **Phase 3**.
- Real Taskiq retry/backoff tasks and adapter calls — **Phase 3**.
- `event_outbox` + relay publishing + the `instance.*` produced catalog —
  **Phase 4**.
- Metrics (consumer lag, poison count, convergence duration) — **Phase 5**
  (OBS-01's metrics half; `instance.failed`-style counters not wired here).
- A dead-letter stream for poison forensics — explicitly *a later addition, not
  milestone 1* (`docs/architecture.md` §Event consumption).

</domain>

<decisions>
## Implementation Decisions

### Consumer seam (CONS-01)
- **D-01:** **Build the full documented seam now.** Extract the Phase-1 inline
  loop (`main.py:_run_consumer`, currently a no-op) into the `EventConsumer`
  **port** (`ports/`), a `ValkeyStreamsConsumer` **adapter** (`adapters/`, the
  only place `redis.asyncio` consume calls live), and the
  `shared/event_consumer.py` **idempotency wrapper** that wraps every registered
  handler. `main.py`'s consumer concern shrinks to wiring: construct the adapter,
  register the five handlers, run the loop until `shutdown`. This honors
  `docs/architecture.md` §Ports (the `EventConsumer (consume)` row) and makes the
  D-10 dependency rule real rather than placeholder. Phase 3 handlers then
  register against a clean, stable surface.
- **D-02:** The supervision shape from Phase 1 (D-01/D-02/D-03 of Phase 1 —
  single `TaskGroup`, crash-only, clean SIGTERM drain) is **unchanged**. The
  consumer concern gains a body; it does not get a new lifecycle model. On
  shutdown the loop stops reading, lets the in-flight batch settle, and the
  adapter closes its client.

### Envelope + payload models (CONS-02)
- **D-03:** Re-implement `EventEnvelope[P: BaseModel]` here (no shared package),
  mirroring platform-api's `events/envelope.py`: `frozen`, `extra="forbid"`,
  26-char ULID `id`, dotted `type`, `version >= 1`, UTC `occurred_at`,
  `producer` literal, `correlation_id`/`causation_id`, `payload: P`. The
  **consume path** parses the outer envelope first (payload as raw mapping),
  reads `type`, looks the payload model up in a **type→model registry**, then
  validates the payload — because `P` isn't known until `type` is read.
- **D-04:** The five consumed payload models are copied **exactly** from
  `docs/events.md` (frozen, `extra="forbid"`, `snake_case`, RFC-3339 UTC
  timestamps). They live in `src/provisioning_worker/events/`. (`build()`-style
  minting is a producer concern — **not** added in this consume-only phase.)

### Unknown-type vs poison policy (CONS-04)
- **D-05:** **Two distinct outcomes, not one path:**
  - **Malformed** (bad JSON, missing `envelope` field, or `extra="forbid"`
    violation): it's a **poison** message — log at `error`, `XACK` so it can't
    block the group, touch no instance, write **no** `processed_event` row.
    Retrying cannot fix it.
  - **Unknown-but-valid `type`** (a well-formed envelope whose `type` isn't one
    of the five we handle — e.g. a future `subscription.*`): log at `warning`,
    `XACK`, write **no** `processed_event` row (nothing was handled). This keeps
    the worker forward-compatible — `docs/events.md` §Evolution rules forbid
    strict enum parsing and require consumers to treat unknown values as a
    no-op.

### Idempotency / `processed_event` (CONS-03)
- **D-06:** **The `processed_event` insert IS the committed unit of work** for a
  no-op handler. The dedupe guard, inside `session_scope()`: check
  `(event_id, consumer_group)` → if present, short-circuit; else do the
  (no-op) handler work and insert the `processed_event` row, **commit**, then
  `XACK`. No separate "stub marker" table. This genuinely exercises the
  guarantee: crash *after commit, before `XACK`* → redelivery short-circuits on
  the existing row; crash *before commit* → redelivery reprocesses cleanly. Real
  state changes (Phase 3) join this **same** transaction.
- **D-07:** `processed_event` schema: composite **primary key
  `(event_id, consumer_group)`** (`event_id` = the 26-char ULID `envelope.id`;
  `consumer_group` = `cg.provisioning-convergence`). Plus a `processed_at`
  timestamptz. Created via `make revision` on the single `provisioning` Alembic
  tree — **review the autogenerated SQL** before committing (autogenerate drops
  CHECK constraints / mishandles enums). This is the first table the tree
  creates into the otherwise-empty `provisioning` schema.

### Stuck-entry reclaim — `XAUTOCLAIM` (CONS-01)
- **D-08:** **Periodic reclaim through the same dispatch+dedupe path.** Run
  `XAUTOCLAIM` on an interval (every N poll cycles) with a **min-idle /
  visibility timeout** (~60s, a tunable `Settings` value). Reclaimed entries flow
  through the **same** parse → dispatch → dedupe code path as a fresh
  `XREADGROUP '>'` read — `processed_event` makes re-processing a safe no-op, so
  there's a single code path and crash-recovery is actually exercised even with
  no-op handlers. (Cadence/min-idle constants are tunable; the *shape* — periodic,
  shared path — is the locked decision.)

### Round-trip test fixtures (CONS-02 / Success Criterion 4)
- **D-09:** **Author canonical JSON envelope fixtures from `docs/events.md`** —
  the per-repo contract source of truth (`CLAUDE.md` §6.2: "copy the Pydantic
  model from `docs/events.md` exactly"). Cross-reference each fixture
  field-by-field against platform-api's
  `src/platform_api/events/subscription.py` and `events/envelope.py` so drift is
  caught at authoring time. **No cross-repo import** — contracts are per-repo,
  not shared. Tests assert each of the five payloads round-trips a
  platform-api-shaped envelope (parse → re-serialize → equality) and that a
  rogue extra field is rejected.

### Claude's Discretion
- Exact module split inside the seam: whether the `EventConsumer` Protocol
  exposes `read`/`ack`/`autoclaim` granularly or a higher-level `run(handlers)`;
  how the type→model registry is expressed (dict literal vs decorator
  registration); whether handlers are functions or a tiny class.
- The poll `block` timeout and `count`, the `XAUTOCLAIM` cadence (every-N-cycles
  vs a separate timer) and the exact min-idle default within the ~60s ballpark.
- `processed_event` column niceties beyond the composite PK (index choices,
  `processed_at` default `now()`, optional `event_type` column for debugging).
- Structlog `bind_contextvars` keys bound at the top of each handler
  (`envelope_id`, `subscription_id`, `correlation_id`) — wire what's available
  from the parsed envelope; `instance_id` only lands in Phase 3.
- Whether the no-op handler logs at `info` (state-transition convention) given
  there's no real transition yet — pick the least-noisy honest level.
- Test fixture file layout under `tests/provisioning/` (or `tests/events/`) and
  whether integration coverage of real-Valkey redelivery is unit
  (in-memory/fakeredis) vs `@pytest.mark.integration` (testcontainers).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### This repo — authoritative specs
- `docs/events.md` §Envelope — the `EventEnvelope[P]` shape to re-implement
  (D-03); §Events this service CONSUMES — the **five** payload models to copy
  byte-for-byte (D-04); §Wire format — single `envelope` field holding
  `model_dump_json()`; §Stream routing — `events.<prefix-before-first-dot>`;
  §Evolution rules — unknown values are a no-op / strict enum parsing forbidden
  (grounds D-05); §Handler idempotency — `processed_event(event_id,
  consumer_group)` in the same transaction (grounds D-06).
- `docs/architecture.md` §Event consumption and idempotency — `XREADGROUP`,
  same-transaction dedupe, `XAUTOCLAIM` reclaim after a visibility timeout,
  poison = log `error` + `XACK`, *DLQ is not milestone 1* (grounds D-01, D-05,
  D-06, D-08).
- `docs/architecture.md` §Ports and adapters — the `EventConsumer (consume)` →
  `ValkeyStreamsConsumer` row (grounds D-01); only adapters import
  `redis.asyncio`.
- `docs/architecture.md` §Postgres schema — the `processed_event` ledger row
  (grounds D-07); the single-`provisioning`-schema rule and *no cross-schema
  FKs*.
- `docs/conventions.md` / `docs/python-style.md` — file-naming, encapsulation,
  Google docstrings, no `from __future__ import annotations`, modern typing.
- `CLAUDE.md` §6.1.1 — module file layout (`handlers.py` thin: validate →
  dedupe → return; `events/` holds payload models; `shared/event_consumer.py`).
- `CLAUDE.md` §6.2 — copy payload models from `docs/events.md` exactly
  (grounds D-04, D-09); §6.4 — the three idempotency layers (layer 2 /
  `change_set_id` is Phase 3).
- `.planning/REQUIREMENTS.md` — CONS-01..04 (the Phase-2 checklist).
- `.planning/ROADMAP.md` §Phase 2 — goal + the 4 success criteria.
- `.planning/phases/01-repo-scaffold-worker-skeleton/01-CONTEXT.md` — Phase-1
  decisions still binding here: D-01/02/03 (TaskGroup crash-only supervision),
  D-04 (the no-op consumer this phase replaces), D-09 (the `Settings` vars —
  `provisioning_consumer_group`, `consumer_name`, `valkey_url` already defined;
  a reclaim min-idle setting is the only likely addition), D-10 (placeholder
  module tree this phase fills in `events/` + `shared/`).

### Sibling repo — the producer contract to MIRROR (read before writing models)
- `../platform-api/src/platform_api/events/envelope.py` — the `EventEnvelope[P]`
  generic to mirror on the consume side (D-03); note `stream_for_envelope_type`.
- `../platform-api/src/platform_api/events/subscription.py` — the producer's
  `subscription.*` payload models to cross-check field-by-field (D-04, D-09).
- `../platform-api/docs/events.md` — the producer's contract; our `docs/events.md`
  is re-implemented against it.
- `../platform-api/tests/events/` (`test_subscription_payload.py`,
  `test_envelope_registry.py`) — reference test patterns; potential fixture
  cross-check (NOT imported — D-09).
- `../platform-api/src/platform_api/adapters/valkey_streams_bus.py` — the
  **publish-only** bus; confirms our consume-side adapter is new code, and shows
  the wire-format / client conventions to match.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`main.py:_run_consumer`** (`src/provisioning_worker/main.py:77`) — the
  Phase-1 no-op loop already does `XGROUP CREATE … MKSTREAM` (tolerating
  `BUSYGROUP`), the `XREADGROUP` block-loop, and `XACK`. Phase 2 lifts this into
  `ValkeyStreamsConsumer` and adds parse → dispatch → dedupe → poison/unknown
  handling. The group-create + block-loop logic transfers nearly verbatim.
- **platform-api `events/envelope.py`** — a near-exact template for the envelope
  (frozen generic, ULID id, routing helper). Copy-and-adapt; drop the
  producer-only `build()` classmethod for this consume phase.
- **`Settings`** already carries `provisioning_consumer_group`
  (`cg.provisioning-convergence`), `consumer_name`, and `valkey_url` (Phase-1
  D-09) — the consumer needs no new *required* env; only an optional reclaim
  min-idle/visibility tunable.
- **`infrastructure/db.py` `session_scope()`** (Phase 1) — the explicit async
  transaction boundary the dedupe guard commits within (D-06).

### Established Patterns
- **Hexagonal ports/adapters** — `events/` (payload models + envelope),
  `ports/event_consumer.py` (Protocol), `adapters/valkey_streams.py` (the only
  `redis.asyncio` consume code), `shared/event_consumer.py` (dedupe wrapper),
  `modules/provisioning/handlers.py` (thin no-op handlers). Domain talks to
  ports; only adapters import third-party clients.
- **Single `provisioning` Alembic tree** — `make revision name="…"` for the
  `processed_event` migration; review autogenerated SQL; forward-only.
- **At-least-once + idempotent handlers** — the design invariant the whole phase
  exists to prove.

### Integration Points
- Reads the **same** `events.subscription` stream platform-api **already emits
  to today** (via its outbox → relay). No HTTP to platform-api, ever.
- Writes the first row(s) into the **empty** `provisioning` schema created by
  `platform-infra/postgres/init/01-init.sql` — `processed_event` is the first
  table the Alembic tree materializes.
- `processed_event` rows are private to this worker; platform-api reads
  `provisioning.instance` / `enforcement_snapshot` only (neither exists until
  Phase 3).

</code_context>

<specifics>
## Specific Ideas

- **Smoke shape (from CLAUDE.md §5 / ROADMAP success criteria):** `valkey-cli
  XADD events.subscription '*' envelope '{…subscription.activated…}'` →
  consumer `XREADGROUP`s it on `cg.provisioning-convergence`, parses to a frozen
  `extra="forbid"` model, dispatches on `type`, writes `processed_event`, and
  `XACK`s. Re-`XADD` of the **same** `envelope.id` is a no-op (short-circuits on
  `processed_event`). A malformed envelope is logged at `error` and `XACK`'d
  without crashing; a valid one published afterward still processes. This is the
  acceptance shape for the four success criteria.
- **Forward-compat is a feature, not an accident** — D-05's skip-and-ack for
  unknown `type` is deliberate so platform-api can ship a new `subscription.*`
  before we handle it without flooding our error logs.

</specifics>

<deferred>
## Deferred Ideas

- **`change_set_id` second-layer dedupe** (`UNIQUE (instance_id,
  change_set_id)` on `provisioning_task`) — needs the `provisioning_task` table.
  **Phase 3** (CLAUDE.md §6.4 layer 2).
- **Real handler bodies** (open `instance` rows + `provisioning_task`, drive the
  state machine) — **Phase 3**. Handlers stay no-op stubs here.
- **`event_outbox` + relay publishing + the `instance.*` produced catalog** —
  **Phase 4**.
- **Dead-letter stream for poison forensics** — explicitly not milestone 1
  (`docs/architecture.md` §Event consumption); revisit if poison volume warrants
  replay tooling.
- **Metrics** (consumer lag = stream length − last-acked, poison count, reclaim
  count, convergence duration per `task_type`) — **Phase 5** (OBS-01 metrics
  half).
- **Out-of-order tolerance** (a `lines_changed` arriving before `activated`
  parking until the instance row exists) — a *Phase 3* convergence concern; in
  Phase 2 every handler is a no-op so ordering can't corrupt state.

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 2-Event consumption & idempotency*
*Context gathered: 2026-06-02*
