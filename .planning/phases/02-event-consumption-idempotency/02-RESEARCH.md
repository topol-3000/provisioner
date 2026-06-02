# Phase 2: Event Consumption & Idempotency — Research

**Researched:** 2026-06-02
**Domain:** Valkey Streams consumer (redis.asyncio), Pydantic v2 generic envelope,
transactional idempotency dedupe, Alembic single-tree migration
**Confidence:** HIGH — all findings verified directly against installed library source
(`redis==7.4.0`), platform-api source code, and this repo's own Phase-1 source.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01 — Consumer seam:** Extract `main.py:_run_consumer` into `EventConsumer` port
(`ports/`), `ValkeyStreamsConsumer` adapter (`adapters/`), and
`shared/event_consumer.py` idempotency wrapper. `main.py` shrinks to wiring only.

**D-02 — Supervision shape unchanged:** Single `asyncio.TaskGroup`, crash-only, clean
SIGTERM drain from Phase 1. Consumer concern gains a body; no new lifecycle model.

**D-03 — Re-implement `EventEnvelope[P]`:** Generic, frozen, `extra="forbid"`, 26-char
ULID `id`. Consume path parses outer envelope first (payload as raw mapping), reads
`type`, looks up payload model in type→model registry, then validates payload.
No `build()` classmethod in this consume-only phase.

**D-04 — Five consumed payload models:** Copied exactly from `docs/events.md`. Frozen,
`extra="forbid"`, `snake_case`, RFC-3339 UTC timestamps. Live in
`src/provisioning_worker/events/`.

**D-05 — Two distinct outcomes for bad messages:**
- Malformed (bad JSON / missing `envelope` field / `extra="forbid"` violation): log
  `error`, `XACK`, no `processed_event` row, no instance touched.
- Unknown-but-valid `type`: log `warning`, `XACK`, no `processed_event` row.

**D-06 — `processed_event` insert is the committed unit of work:** dedupe guard inside
`session_scope()`: check `(event_id, consumer_group)` → if present, short-circuit;
else handler work + insert row → `commit` → then `XACK`.

**D-07 — `processed_event` schema:** composite PK `(event_id, consumer_group)` +
`processed_at timestamptz`. Via `make revision` on the single `provisioning` Alembic
tree. First table in the tree.

**D-08 — XAUTOCLAIM periodic reclaim:** Every N poll cycles, ~60s min-idle tunable.
Reclaimed entries flow through the same parse → dispatch → dedupe path.

**D-09 — Round-trip test fixtures:** Author canonical JSON fixtures from
`docs/events.md`. Cross-reference field-by-field against platform-api's subscription.py.
No cross-repo import.

### Claude's Discretion

- Exact `EventConsumer` Protocol surface (`read`/`ack`/`autoclaim` granular vs
  higher-level `run(handlers)`).
- Type→model registry expression (dict literal vs decorator).
- Whether handlers are functions or a tiny class.
- Poll `block` timeout and `count`; `XAUTOCLAIM` cadence and exact min-idle default
  within ~60s ballpark.
- `processed_event` column niceties beyond composite PK (index choices,
  `processed_at` default `now()`, optional `event_type` column for debugging).
- `structlog.bind_contextvars` keys at handler top (wire `envelope_id`,
  `subscription_id`, `correlation_id`; `instance_id` Phase 3).
- Whether no-op handler logs at `debug` or `info`.
- Test fixture file layout and whether real-Valkey redelivery is unit (in-memory)
  vs `@pytest.mark.integration` (testcontainers).

### Deferred Ideas (OUT OF SCOPE)

- `change_set_id` second-layer dedupe — Phase 3.
- Real handler bodies (instance rows, state machine) — Phase 3.
- `event_outbox` + relay publishing + `instance.*` catalog — Phase 4.
- Dead-letter stream — not milestone 1.
- Metrics — Phase 5.
- Out-of-order tolerance — Phase 3 concern.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CONS-01 | Valkey Streams consumer reads `events.subscription` via `cg.provisioning-convergence` (`XREADGROUP`, `XACK`, `XAUTOCLAIM`), dispatches on envelope `type` | §Standard Stack, §redis.asyncio API, §XAUTOCLAIM Pattern |
| CONS-02 | `EventEnvelope` and five `subscription.*` payload models re-implemented (frozen, `extra="forbid"`) byte-matching `docs/events.md` / platform-api | §Envelope Model, §Five Payload Models, §Drift Analysis |
| CONS-03 | Handlers idempotent — replayed `envelope.id` short-circuits via `provisioning.processed_event(event_id, consumer_group)` in same transaction | §Same-Transaction Dedupe, §processed_event Migration |
| CONS-04 | Malformed envelope logged at `error` and `XACK`'d as poison — never crashes consumer, never creates/advances instance | §Poison vs Unknown-type Policy |
</phase_requirements>

---

## Summary

Phase 2 builds the full event consumption seam for the provisioning-worker: a
`ValkeyStreamsConsumer` adapter, re-implemented `EventEnvelope[P]` generic, five
`subscription.*` payload models, transactional idempotency via `processed_event`,
`XAUTOCLAIM` stuck-entry reclaim, and poison/unknown-type message handling — all with
no-op handler stubs (no instance rows, no state machine).

All critical API shapes have been confirmed directly against the installed
`redis==7.4.0` library source code. The envelope model was verified against
platform-api's `events/envelope.py` (the producer that wrote it). The five payload
models were cross-referenced against platform-api's `events/subscription.py` with a
field-by-field drift check. The `session_scope()` contract was verified against the
Phase-1 `infrastructure/db.py`. The Pydantic 2.13 `Decimal` round-trip behavior
(parses from JSON string, serializes back to string) was confirmed by running the
installed library.

**Primary recommendation:** Implement as three vertical slices:
1. Thin end-to-end: `XADD subscription.activated` → `XREADGROUP` → parse → no-op
   handler → write `processed_event` → `XACK`.
2. Dedupe replay: same `envelope.id` re-submitted → short-circuit on
   `processed_event`.
3. Robustness: malformed poison (log error + `XACK` + no row), unknown type (log
   warning + `XACK` + no row), XAUTOCLAIM reclaim through same path.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Stream read + ack/reclaim | Adapter (`adapters/valkey_streams.py`) | — | Only adapters import `redis.asyncio` (CLAUDE.md §4 dependency rule) |
| Idempotency guard / dedupe | `shared/event_consumer.py` | — | Cross-cutting, wraps all handler registrations; not domain-specific |
| Envelope + payload parse | `events/` package | `shared/event_consumer.py` (dispatch) | Models are data-only; dispatch logic lives in the wrapper |
| No-op handler bodies | `modules/provisioning/handlers.py` | — | CLAUDE.md §6.1.1 file layout; domain modules own handlers |
| `processed_event` DB write | `shared/event_consumer.py` via `session_scope()` | — | Dedupe guard owns the DB insert, not the handler |
| EventConsumer Protocol | `ports/event_consumer.py` | — | Port = stable interface; adapter = impl; domain talks to port |
| Consumer loop lifecycle (start/stop) | `main.py` | `adapters/valkey_streams.py` | Composition root wires; adapter runs |
| `processed_event` migration | `migrations/provisioning/versions/` | — | Single Alembic tree; first domain table |

---

## Standard Stack

### Core (all already in `pyproject.toml` / `uv.lock`)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `redis` (unified) | 7.4.0 (pinned in `uv.lock`) | `XREADGROUP`, `XACK`, `XAUTOCLAIM`, `XGROUP CREATE` | Pinned in CLAUDE.md §3; NOT standalone `aioredis` |
| `pydantic` | 2.13.* | `EventEnvelope[P]`, payload models, `ConfigDict(frozen=True, extra="forbid")` | Pinned in CLAUDE.md §3; platform-api parity |
| `sqlalchemy[asyncio]` | 2.0.* | `session_scope()` for same-transaction dedupe | Already in Phase 1 |
| `alembic` | 1.18.* | `processed_event` migration in `provisioning` tree | Already wired; `make revision` |
| `python-ulid` | 3.1.* | `id` field validation (26-char ULID) | CLAUDE.md §3 |
| `structlog` | 25.* | `bind_contextvars` in handlers | CLAUDE.md §6.6 |

**No new packages** are required for Phase 2. The entire stack is already in `pyproject.toml` and committed to `uv.lock`.

### Alerting: fakeredis Not Present

`fakeredis` is **not** in `pyproject.toml` or `uv.lock`. Unit tests that need an
in-memory Redis must either:
(a) use `testcontainers[redis]` (already present) and mark `@pytest.mark.integration`, or
(b) mock the `ValkeyStreamsConsumer` adapter at the port level (preferred for unit tests).

The unit-test approach for Phase 2 should mock the `EventConsumer` port rather than
spin up a fake Redis — this matches the hexagonal architecture (domain tests use fake
ports, not fake infrastructure). Only the integration test verifies the real
`ValkeyStreamsConsumer` against a real Valkey container.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `testcontainers[redis]` | 4.14.2 | Real Valkey for `@pytest.mark.integration` | Redelivery / XAUTOCLAIM integration tests |
| `pytest-asyncio` | 1.3.0 | `asyncio_mode=auto` | All async tests |

### Installation

No new packages needed. All dependencies are already installed via `uv sync --frozen --extra dev`.

---

## Package Legitimacy Audit

> Phase 2 introduces **zero new packages**. The existing `redis==7.4.0` package
> was verified against the installed library source and the `uv.lock` lockfile.
> All packages below are from Phase 1 and were already approved.

| Package | Registry | Disposition |
|---------|----------|-------------|
| `redis==7.4.0` | PyPI | Approved (Phase 1; installed and running) |
| All other deps | PyPI | Approved (Phase 1 audit) |

**No new packages to audit.**

*slopcheck was not installable in this environment (permission denied). Since no new
packages are introduced in Phase 2, this is not a blocker.*

---

## Architecture Patterns

### System Architecture Diagram

```text
  Valkey Stream: events.subscription
          │
          │  XREADGROUP GROUP cg.provisioning-convergence <consumer-name>
          │  (or XAUTOCLAIM on stuck entries, every N cycles)
          ▼
  ┌──────────────────────────────────────────────────────────┐
  │  ValkeyStreamsConsumer  (adapters/valkey_streams.py)      │
  │                                                          │
  │  raw entry: { "envelope": "<json-bytes>" }               │
  │       │                                                  │
  │       │  read "envelope" field, JSON-parse               │
  │       ▼                                                  │
  │  outer envelope parse (payload as raw dict)              │
  │  read envelope.type                                      │
  │       │                                                  │
  │       ├─── malformed? ──► log error + XACK (no row)      │
  │       │                                                  │
  │       ├─── unknown type? ► log warning + XACK (no row)   │
  │       │                                                  │
  │       ▼                                                  │
  │  registry lookup: type → payload model class             │
  │  validate payload dict → typed payload instance          │
  │  construct EventEnvelope[P]                              │
  └──────────────────────────────────────────────────────────┘
          │  typed EventEnvelope[P]
          │
          ▼
  ┌──────────────────────────────────────────────────────────┐
  │  shared/event_consumer.py  (idempotency wrapper)         │
  │                                                          │
  │  open session_scope()                                    │
  │  SELECT processed_event WHERE event_id=X AND group=Y     │
  │       │                                                  │
  │       ├─── found? ──────► short-circuit (already done)   │
  │       │                                                  │
  │       ▼                                                  │
  │  dispatch to handler(envelope, session)                  │
  │  INSERT processed_event (event_id, consumer_group,       │
  │                          processed_at)                   │
  │  session.commit()                                        │
  └──────────────────────────────────────────────────────────┘
          │  commit done
          │
          ▼
  client.xack(stream, group, msg_id)    ← AFTER commit
          │
          ▼
  modules/provisioning/handlers.py
    handle_subscription_activated(envelope, session) → no-op + log
    handle_subscription_lines_changed(envelope, session) → no-op + log
    handle_subscription_suspended(envelope, session) → no-op + log
    handle_subscription_reinstated(envelope, session) → no-op + log
    handle_subscription_cancelled(envelope, session) → no-op + log
```

### Recommended Project Structure Changes (Phase 2)

```
src/provisioning_worker/
├── events/
│   ├── __init__.py            # re-export EventEnvelope + all 5 payload types
│   ├── envelope.py            # EventEnvelope[P: BaseModel] (no build() classmethod)
│   └── subscription.py        # 5 consumed payload models + LineDelta
├── ports/
│   └── event_consumer.py      # EventConsumer Protocol (replaces __init__.py placeholder)
├── adapters/
│   └── valkey_streams.py      # ValkeyStreamsConsumer (replaces __init__.py placeholder)
├── shared/
│   └── event_consumer.py      # idempotency wrapper + type→model registry
└── modules/provisioning/
    └── handlers.py            # 5 no-op handler functions

migrations/provisioning/versions/
    └── YYYYMMDD_HHMM_add_processed_event.py   # from `make revision`

tests/
├── conftest.py                # add async_redis_client / session fixtures
├── events/
│   ├── __init__.py
│   ├── test_envelope.py       # envelope field-list pinning + round-trip
│   └── test_subscription_payloads.py  # all 5 payload round-trips + extra-field rejection
└── provisioning/
    ├── test_handlers.py       # no-op handler dispatch (unit, no DB)
    └── test_idempotency.py    # dedupe guard (integration: real Postgres)
```

---

## redis.asyncio API — Verified Signatures

### XGROUP CREATE (`xgroup_create`)

```python
# [VERIFIED: redis 7.4.0 installed library source]
# Signature:
async def xgroup_create(
    self,
    name: bytes | str | memoryview,       # stream name
    groupname: bytes | str | memoryview,  # consumer group name
    id: int | bytes | str | memoryview = "$",  # start ID ("0" = all history)
    mkstream: bool = False,               # create stream if missing
    entries_read: int | None = None,
) -> bool: ...

# Usage (already in main.py Phase 1 — transfer unchanged):
try:
    await client.xgroup_create(
        name="events.subscription",
        groupname=settings.provisioning_consumer_group,
        id="0",
        mkstream=True,
    )
except aioredis.ResponseError as exc:
    if "BUSYGROUP" not in str(exc):
        raise
```

### XREADGROUP

```python
# [VERIFIED: redis 7.4.0 installed library source]
# Signature:
async def xreadgroup(
    self,
    groupname: str,
    consumername: str,
    streams: dict[bytes | str | memoryview, int | bytes | str | memoryview],
    count: int | None = None,      # max messages per call
    block: int | None = None,      # milliseconds to block; 0 = forever
    noack: bool = False,
    claim_min_idle_time: int | None = None,
) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
    ...

# Return shape (with decode_responses=True):
# [
#   ("events.subscription", [
#     ("1234567890123-0", {"envelope": "<json-bytes-as-str>"}),
#     ("1234567890124-0", {"envelope": "<json-bytes-as-str>"}),
#   ])
# ]
# Returns [] (not None) when nothing available in the block window.

# Usage (Phase 1 main.py pattern — transfer to ValkeyStreamsConsumer):
results = await client.xreadgroup(
    groupname=settings.provisioning_consumer_group,
    consumername=settings.consumer_name,
    streams={"events.subscription": ">"},  # ">" = only new (not PEL) entries
    count=10,
    block=1000,  # 1 second block; allows shutdown check on each iteration
)
# results is [] when nothing arrives
if results:
    for _stream_name, messages in results:
        for msg_id, fields in messages:
            raw_json = fields["envelope"]  # str when decode_responses=True
            # ... parse + dispatch + dedupe ...
            await client.xack(stream_name, groupname, msg_id)
```

### XACK

```python
# [VERIFIED: redis 7.4.0 installed library source]
async def xack(
    self,
    name: bytes | str | memoryview,       # stream name
    groupname: bytes | str | memoryview,  # consumer group name
    *ids: int | bytes | str | memoryview, # one or more message IDs
) -> int: ...  # number of messages acked
```

### XAUTOCLAIM

```python
# [VERIFIED: redis 7.4.0 installed library source]
# Signature:
async def xautoclaim(
    self,
    name: bytes | str | memoryview,         # stream name
    groupname: bytes | str | memoryview,    # consumer group name
    consumername: bytes | str | memoryview, # claiming consumer
    min_idle_time: int,                     # milliseconds; messages idle < this are skipped
    start_id: int | bytes | str | memoryview = "0-0",  # scan cursor
    count: int | None = None,               # default 100 per call
    justid: bool = False,
) -> list: ...

# Return shape (verified via parse_xautoclaim in redis/_parsers/helpers.py):
# [
#   next_cursor,                  # str, e.g. "0-0" means no more PEL entries
#   list[tuple[msg_id, dict]],    # same shape as XREADGROUP message tuples
#   list[str],                    # deleted entry IDs (trimmed while idle)
# ]
# Iteration pattern:
cursor = "0-0"
while True:
    result = await client.xautoclaim(
        name="events.subscription",
        groupname=settings.provisioning_consumer_group,
        consumername=settings.consumer_name,
        min_idle_time=settings.consumer_reclaim_min_idle_ms,  # ~60_000
        start_id=cursor,
        count=10,
    )
    cursor, messages, _deleted = result
    for msg_id, fields in (messages or []):
        # route through the same parse → dispatch → dedupe path
        ...
    if cursor == "0-0":
        break  # no more pending entries with this idle time
```

**Key insight for XAUTOCLAIM:** When `start_id` returns to `"0-0"`, the reclaim scan
is complete for this cycle. The `_deleted` list contains IDs of entries that were
trimmed from the stream (e.g. by `MAXLEN`) while still in the PEL — ignore or log them.

---

## Envelope Model Pattern

### Re-implementing `EventEnvelope[P]` (consume-only)

[VERIFIED: platform-api `src/platform_api/events/envelope.py`]

```python
# src/provisioning_worker/events/envelope.py
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["EventEnvelope", "stream_for_envelope_type"]


class EventEnvelope[P: BaseModel](BaseModel):
    """Wire envelope for every domain event consumed or produced.

    Re-implemented per repo (no shared package — CLAUDE.md §6.2).
    Frozen, extra="forbid" — contract drift is a validation failure, not silent.

    Attributes:
        id: 26-char ULID; consumer-side idempotency key.
        type: Dotted event type, e.g. "subscription.activated".
        version: Payload schema version, >= 1.
        occurred_at: UTC producer wall-clock.
        producer: Which service minted the event.
        correlation_id: Upstream request/trace id. Optional.
        causation_id: id of the immediately-preceding event. Optional.
        payload: The typed event-specific body.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=26, max_length=26)
    type: str
    version: int = Field(..., ge=1)
    occurred_at: datetime
    producer: Literal["platform-api", "provisioning-worker", "telemetry-worker"]
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: P

    # NOTE: No build() classmethod — that is a producer concern (Phase 4+).
    # This consume-only re-implementation validates inbound envelopes only.


def stream_for_envelope_type(envelope_type: str) -> str:
    """Return the Valkey stream name for a given envelope type.

    Args:
        envelope_type: Dotted type, e.g. "subscription.activated".

    Returns:
        "events.<prefix>", e.g. "events.subscription".
    """
    return f"events.{envelope_type.split('.', 1)[0]}"
```

### Two-Phase Parse Pattern (type→model registry)

[VERIFIED: platform-api `src/platform_api/events/__init__.py` + `test_envelope_registry.py`]

The key challenge: `EventEnvelope[P]` is generic, but `P` is not known until
`envelope.type` is read. The two-phase parse solves this:

```python
# Phase 1: Parse outer envelope with payload as raw dict
class _RawEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(..., min_length=26, max_length=26)
    type: str
    version: int = Field(..., ge=1)
    occurred_at: datetime
    producer: Literal["platform-api", "provisioning-worker", "telemetry-worker"]
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict  # raw dict — type not yet known

# Phase 2: Look up payload class, rebuild typed envelope
_REGISTRY: dict[str, type[BaseModel]] = {
    "subscription.activated": SubscriptionActivatedPayload,
    "subscription.lines_changed": SubscriptionLinesChangedPayload,
    "subscription.suspended": SubscriptionSuspendedPayload,
    "subscription.reinstated": SubscriptionReinstatedPayload,
    "subscription.cancelled": SubscriptionCancelledPayload,
}

def parse_envelope(raw_json: str) -> EventEnvelope:
    raw = _RawEnvelope.model_validate_json(raw_json)
    payload_cls = _REGISTRY.get(raw.type)
    if payload_cls is None:
        raise UnknownEnvelopeType(raw.type)
    payload = payload_cls.model_validate(raw.payload)
    # Re-construct a typed envelope from the raw fields
    return EventEnvelope[payload_cls].model_validate({
        **raw.model_dump(mode="json"),
        "payload": payload.model_dump(mode="json"),
    })
```

**Alternative simpler approach (recommended for consume-only):** Since we only need
to dispatch and never re-serialize the envelope, we can skip re-wrapping in a fully
typed generic and just pass the payload instance to the handler:

```python
# Simpler: parse raw, lookup model, validate payload, dispatch
def _parse_and_dispatch(raw_json: str, handlers: dict) -> tuple[str, BaseModel]:
    """Returns (event_type, typed_payload) or raises."""
    data = json.loads(raw_json)
    raw_type = data.get("type")  # read before full validation
    # Now validate the outer envelope (payload still as raw dict)
    raw_env = _RawEnvelope.model_validate(data)
    payload_cls = _REGISTRY.get(raw_env.type)  # None = unknown type
    if payload_cls is None:
        raise UnknownEnvelopeType(raw_env.type)
    payload = payload_cls.model_validate(raw_env.payload)
    return raw_env, payload
```

The planner should pick one consistent pattern; the registry-dict approach (dict
literal keyed by dotted type) is simpler than decorator registration and aligns with
platform-api's `_ENVELOPE_TYPE_REGISTRY` pattern.

---

## Five Consumed Payload Models

### Drift Analysis: `docs/events.md` vs platform-api `events/subscription.py`

[VERIFIED: read both files in this session — no drift found]

The five models in `docs/events.md` match platform-api's `events/subscription.py`
**field-for-field** with one important note:

| Model | Drift? | Note |
|-------|--------|------|
| `SubscriptionActivatedPayload` | **NONE** | Exact match. `total_amount` uses `Decimal` in docs/events.md; platform-api uses `MoneyDecimal = Annotated[Decimal, PlainSerializer(str)]`. On the consume side, plain `Decimal` field is sufficient — Pydantic 2.13 parses the JSON string `"129.99"` to `Decimal` and serializes back to `"129.99"` in JSON mode [VERIFIED: runtime test in this session]. No `MoneyDecimal` needed for consume-only. |
| `LineDelta` | **NONE** | Exact match. |
| `SubscriptionLinesChangedPayload` | **NONE** | Exact match. |
| `SubscriptionSuspendedPayload` | **NONE** | Exact match. |
| `SubscriptionReinstatedPayload` | **NONE** | Exact match. |
| `SubscriptionCancelledPayload` | **NONE** | Exact match. |

**Critical implementation note for `SubscriptionActivatedPayload`:**
Platform-api's `total_amount` uses `MoneyDecimal` (a `PlainSerializer` that emits
strings in JSON mode). Our `docs/events.md` declares it `Decimal`. Testing confirms
[VERIFIED: runtime test]: `Pydantic 2.13` with `total_amount: Decimal` parses the
wire string `"129.99"` correctly, `model_dump(mode="json")` returns `"129.99"` as
a string, and round-trip equality holds. **Use plain `Decimal` as documented.**

### Copy-paste models for `src/provisioning_worker/events/subscription.py`

[VERIFIED: exact field sets from `docs/events.md` in this repo; cross-checked against platform-api source]

```python
# No MoneyDecimal annotated type needed (consume-only path; Pydantic handles Decimal serialization)
from decimal import Decimal
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class SubscriptionActivatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    quote_id: UUID
    stripe_subscription_id: str
    billing_cycle: Literal["monthly", "annual"]
    currency: str = Field(..., min_length=3, max_length=3)
    line_count: int = Field(..., ge=1)
    total_amount: Decimal
    activated_at: datetime
    current_period_start: datetime
    current_period_end: datetime


class LineDelta(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    line_id: UUID
    sku_id: UUID
    sku_key: str
    change: Literal["added", "removed", "qty_changed", "price_changed", "params_changed"]
    previous: dict[str, str] | None = None
    current: dict[str, str] | None = None


class SubscriptionLinesChangedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    change_set_id: UUID
    deltas: list[LineDelta] = Field(..., min_length=1)
    effective_at: datetime
    triggered_by: Literal["operator", "customer", "system"]
    actor_id: str | None = None


class SubscriptionSuspendedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    reason: Literal["dunning_exhausted", "manual_operator", "policy_violation"]
    previous_status: Literal["active", "past_due"]
    suspended_at: datetime
    actor_id: str | None = None
    note: str | None = None


class SubscriptionReinstatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    reason: Literal["payment_recovered", "manual_operator"]
    previous_status: Literal["past_due", "suspended"]
    reinstated_at: datetime
    actor_id: str | None = None


class SubscriptionCancelledPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    cancellation_kind: Literal["immediate", "at_period_end"]
    cancelled_at: datetime
    requested_at: datetime
    grace_until: datetime
    requested_by: Literal["customer", "operator", "stripe_dunning"]
    actor_id: str | None = None
    reason_code: str | None = None
```

---

## Same-Transaction Dedupe Pattern

[VERIFIED: `infrastructure/db.py:session_scope()` in this repo]

```python
# shared/event_consumer.py — the idempotency wrapper
# session_scope() yields an AsyncSession; caller commits explicitly

from provisioning_worker.infrastructure.db import session_scope

async def _handle_with_dedupe(
    envelope_id: str,
    consumer_group: str,
    typed_envelope,
    handler_fn,
) -> None:
    """Run handler inside session_scope(); dedupe on (event_id, consumer_group).

    Crash semantics:
      - Crash BEFORE commit → message re-delivered → reprocesses cleanly.
      - Crash AFTER commit, BEFORE xack → message re-delivered →
        processed_event row found → short-circuits safely.
    """
    async with session_scope() as session:
        # Check for existing processed_event row
        existing = await _select_processed_event(session, envelope_id, consumer_group)
        if existing is not None:
            log.debug("dedupe short-circuit", envelope_id=envelope_id)
            return  # do NOT xack here — caller xacks after this returns

        # Run the (no-op) handler
        await handler_fn(typed_envelope, session)

        # Insert processed_event in the SAME transaction
        await _insert_processed_event(session, envelope_id, consumer_group)
        await session.commit()

    # xack happens AFTER this function returns, in the caller
```

**Critical ordering:** `XACK` must happen **after** `session.commit()` returns
successfully, in the caller (the consumer loop), not inside `session_scope()`.
This is what makes the crash-window guarantee real.

**`session_scope()` note:** The Phase-1 implementation has an explicit rollback on
exception and yields without auto-commit. The handler must call `await session.commit()`
explicitly — the context manager does NOT auto-commit.

---

## `processed_event` Migration

[VERIFIED: `alembic.ini`, `migrations/provisioning/env.py`, `script.py.mako` in this repo]

### Migration scaffolding

```bash
make revision name="add processed_event"
# → creates migrations/provisioning/versions/YYYYMMDD_HHMM_add_processed_event.py
```

### Expected migration content (autogenerate will NOT produce this)

Autogenerate requires the SQLAlchemy models to be imported into `env.py`
(`target_metadata = Base.metadata`). Phase-1 `env.py` has `target_metadata = None`.

**Two paths:**

**Path A (autogenerate):** Add a `ProcessedEvent` SQLAlchemy model to
`modules/provisioning/models.py`, update `env.py` to import it, then run
`make revision name="add processed_event"`. Review the generated SQL carefully —
autogenerate may not create the correct schema-qualified table or the composite PK.

**Path B (hand-authored upgrade/downgrade):** Scaffold an empty revision, then
write the `upgrade()` / `downgrade()` functions manually. This is safer for the
first migration since there's no existing metadata to diff against.

The Alembic `script.py.mako` template [VERIFIED: read in this session] correctly
has **no** `from __future__ import annotations`. Generated files will be correct.

### Expected table DDL

```sql
CREATE TABLE provisioning.processed_event (
    event_id        VARCHAR(26)  NOT NULL,   -- 26-char ULID
    consumer_group  TEXT         NOT NULL,
    processed_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- optional: event_type TEXT (helpful for debugging; Claude's discretion)
    PRIMARY KEY (event_id, consumer_group)
);
```

**Schema qualifier:** The table must be in the `provisioning` schema. The Alembic
env.py has `include_schemas=True` and filters on `schema == "provisioning"`.
In the migration use:
```python
op.create_table(
    "processed_event",
    sa.Column("event_id", sa.String(26), nullable=False),
    sa.Column("consumer_group", sa.Text(), nullable=False),
    sa.Column("processed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    sa.PrimaryKeyConstraint("event_id", "consumer_group"),
    schema="provisioning",
)
```

**No autogenerate drift risk** because this is a hand-authored `CREATE TABLE` with
no CHECK constraints or enums on the first table.

---

## Poison vs Unknown-Type Policy

[VERIFIED: `docs/architecture.md §Event consumption`, `docs/events.md §Evolution rules`, CONTEXT.md D-05]

```python
# In the consumer loop dispatch:

try:
    raw_data = json.loads(fields["envelope"])
except (json.JSONDecodeError, KeyError):
    # MALFORMED: bad JSON or missing "envelope" field
    log.error("poison message — bad JSON or missing envelope field",
               msg_id=msg_id, stream=stream_name)
    await client.xack(stream_name, groupname, msg_id)
    # NO processed_event row — retrying cannot fix this
    continue

try:
    raw_env = _RawEnvelope.model_validate(raw_data)
except ValidationError:
    # MALFORMED: extra="forbid" violation or missing required field
    log.error("poison message — envelope validation failed",
               msg_id=msg_id, envelope_type=raw_data.get("type", "<unknown>"))
    await client.xack(stream_name, groupname, msg_id)
    # NO processed_event row
    continue

payload_cls = _REGISTRY.get(raw_env.type)
if payload_cls is None:
    # UNKNOWN BUT VALID TYPE: forward-compat per docs/events.md §Evolution rules
    log.warning("unknown envelope type — skipping",
                msg_id=msg_id, envelope_type=raw_env.type)
    await client.xack(stream_name, groupname, msg_id)
    # NO processed_event row — nothing was handled
    continue

try:
    payload = payload_cls.model_validate(raw_env.payload)
except ValidationError:
    # MALFORMED: payload doesn't match the expected model (field drift, bad values)
    log.error("poison message — payload validation failed",
               msg_id=msg_id, envelope_type=raw_env.type)
    await client.xack(stream_name, groupname, msg_id)
    # NO processed_event row
    continue

# Happy path: dispatch with dedupe
await _handle_with_dedupe(raw_env.id, consumer_group, payload, handlers[raw_env.type])
await client.xack(stream_name, groupname, msg_id)
```

**Forward-compat rule from `docs/events.md §Evolution rules`:** "strict enum
parsing on consumers is forbidden" — the `_REGISTRY.get()` pattern (returns `None`
for unknown types) satisfies this. Do NOT use a `Literal` for `type` in the envelope.

---

## XAUTOCLAIM Reclaim Pattern

[VERIFIED: redis 7.4.0 `xautoclaim` signature + `parse_xautoclaim` return shape]

```python
# Periodic reclaim: every N poll cycles in the consumer loop
# N is Claude's discretion (e.g. every 60 cycles with 1s block = ~60s cadence)

async def _reclaim_stuck_entries(
    client,
    stream: str,
    groupname: str,
    consumername: str,
    min_idle_ms: int,
    dispatch_fn,
) -> None:
    """Reclaim PEL entries idle longer than min_idle_ms, route through same dispatch."""
    cursor = "0-0"
    while True:
        result = await client.xautoclaim(
            name=stream,
            groupname=groupname,
            consumername=consumername,
            min_idle_time=min_idle_ms,
            start_id=cursor,
            count=10,
        )
        cursor, messages, _deleted_ids = result

        for msg_id, fields in (messages or []):
            await dispatch_fn(msg_id, fields)
            # dispatch_fn handles its own xack after successful dedupe

        if cursor == "0-0":
            break  # full scan complete
```

**Settings addition needed:** `consumer_reclaim_min_idle_ms: int = 60_000` in
`Settings`. Phase-1 D-09 noted "a reclaim min-idle setting is the only likely
addition" — this is it.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Decimal from JSON string | Custom parser | `Pydantic 2.13 Decimal field` | Pydantic coerces `"129.99"` → `Decimal("129.99")` automatically [VERIFIED] |
| Stream entry field extraction | Custom `pairs_to_dict` | `decode_responses=True` on client | redis.asyncio already decodes bytes; fields arrive as `dict[str, str]` |
| Consumer group idempotent create | `EXISTS` check + conditional `CREATE` | `BUSYGROUP` exception handling | Already in Phase-1 `_run_consumer`; transfer verbatim |
| Envelope deduplication | Custom bloom filter / TTL cache | `provisioning.processed_event` composite PK | Transactional, crash-safe, queryable |
| Type→model dispatch | `match` statement on `type` string | Dict registry `_REGISTRY[type]` | O(1), easily extensible, mirrors platform-api |

**Key insight:** The `redis.asyncio` client with `decode_responses=True` handles all
byte decoding. The `envelope` field arrives as a Python `str` — `json.loads()` works
directly, no `.decode("utf-8")` needed. Platform-api's `ValkeyStreamsBus` uses
`decode_responses=False` (publish side), but the consume side should use
`decode_responses=True` for simplicity.

---

## Common Pitfalls

### Pitfall 1: XACK Before Commit (Crash Window Bug)

**What goes wrong:** Calling `xack()` inside the database transaction, before
`session.commit()`. If the process crashes after `XACK` but before commit, the event
is lost permanently.

**Why it happens:** Feels natural to ack immediately after "handling" the message.

**How to avoid:** `XACK` MUST happen after `session.commit()` returns, in the outer
consumer loop. The `_handle_with_dedupe` helper returns; the caller then calls `xack`.

**Warning signs:** Logic where `xack()` is inside `async with session_scope()`.

---

### Pitfall 2: `decode_responses` Mismatch

**What goes wrong:** Platform-api's `ValkeyStreamsBus` uses
`decode_responses=False` (bytes). If the consumer is copied verbatim and uses
`decode_responses=False`, then `fields["envelope"]` is `bytes`, not `str`, and
`json.loads(bytes)` still works, but the code is inconsistent.

**How to avoid:** Consumer should use `decode_responses=True` (as Phase-1
`_run_consumer` already does). The `"envelope"` field then arrives as `str`.

---

### Pitfall 3: Using `EventEnvelope.model_validate_json()` Without Two-Phase Parse

**What goes wrong:** If you call `EventEnvelope.model_validate_json(raw_json)` on
the bare unparameterized generic, Pydantic validates `payload` as an untyped `dict`
or `Any`. This bypasses the payload schema entirely — bad data passes through
silently, and the `extra="forbid"` on payload models is never triggered.

**How to avoid:** The two-phase parse (outer envelope with `payload: dict`, then
registry lookup and payload model validation) is the correct approach.

---

### Pitfall 4: Strict Enum on `envelope.type` Field

**What goes wrong:** Using `type: Literal["subscription.activated", ...]` in the
envelope model. When platform-api ships a new `subscription.*` event type, the
envelope validation raises `ValidationError` (treated as poison) instead of the
correct "unknown but valid type" path.

**How to avoid:** `type: str` in the envelope model. The registry handles dispatch
and returns `None` for unknown types.

---

### Pitfall 5: Forgetting `schema="provisioning"` in Alembic Migration

**What goes wrong:** `op.create_table("processed_event", ...)` without
`schema="provisioning"` creates the table in the default schema (`public`), not
in `provisioning`. The worker's SQLAlchemy models reference
`schema="provisioning"` and the table won't be found.

**How to avoid:** Always pass `schema="provisioning"` to `op.create_table()` and
`op.drop_table()` in migrations. The Alembic env.py already filters on this schema
for autogenerate, but hand-authored migrations need explicit schema qualification.

---

### Pitfall 6: `XAUTOCLAIM` Return Shape Index Error

**What goes wrong:** Unpacking `cursor, messages = result` from `xautoclaim()` —
the actual return is a 3-element list `[cursor, messages, deleted_ids]`.

**How to avoid:** Unpack as `cursor, messages, _deleted_ids = result`
[VERIFIED: `parse_xautoclaim` in redis/_parsers/helpers.py].

---

### Pitfall 7: `model_validate_json` vs `model_validate` on `dict`

**What goes wrong:** `model_validate_json(raw_json)` on the raw envelope calls
`json.loads` internally. If the JSON has already been parsed to a dict (e.g. via
`json.loads(fields["envelope"])`), passing the dict to `model_validate_json` will
fail (it expects a string). Use `model_validate(dict_obj)` when the JSON is already
parsed.

**How to avoid:** Two-phase parse calls `model_validate_json` on the raw string for
step 1, then `model_validate(raw_env.payload)` (already a dict) for step 2.

---

### Pitfall 8: `from __future__ import annotations` in Migration Files

**What goes wrong:** Alembic autogenerate may have injected this import in some
versions. The project **forbids** it (CLAUDE.md §6.1, Python 3.14 PEP 649).

**How to avoid:** The `script.py.mako` template [VERIFIED: read in this session]
has no such import. Review each generated migration file and delete the import if
it appears.

---

## Code Examples

### Consumer loop outline (ValkeyStreamsConsumer)

```python
# src/provisioning_worker/adapters/valkey_streams.py
# Source: verified against Phase-1 main.py + redis 7.4.0 API

import asyncio
import json
import structlog
import redis.asyncio as aioredis
from pydantic import ValidationError

log = structlog.get_logger(__name__)

class ValkeyStreamsConsumer:
    """Consume side of the event bus — Valkey Streams with consumer group.

    Only this adapter imports redis.asyncio (CLAUDE.md §4 dependency rule).
    """

    def __init__(self, settings) -> None:
        self._client = aioredis.from_url(
            str(settings.valkey_url),
            decode_responses=True,   # fields arrive as str, not bytes
        )
        self._group = settings.provisioning_consumer_group
        self._consumer = settings.consumer_name
        self._reclaim_min_idle_ms = settings.consumer_reclaim_min_idle_ms
        self._reclaim_every_n = 60  # Claude's discretion

    async def start(self) -> None:
        """Create consumer group idempotently; tolerate BUSYGROUP on restart."""
        try:
            await self._client.xgroup_create(
                name="events.subscription",
                groupname=self._group,
                id="0",
                mkstream=True,
            )
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        log.info("joined consumer group", group=self._group, consumer=self._consumer)

    async def run(self, handlers, shutdown: asyncio.Event) -> None:
        """Main poll loop; dispatches to handlers dict keyed by envelope type."""
        poll_count = 0
        while not shutdown.is_set():
            results = await self._client.xreadgroup(
                groupname=self._group,
                consumername=self._consumer,
                streams={"events.subscription": ">"},
                count=10,
                block=1000,
            )
            if results:
                for _stream, messages in results:
                    for msg_id, fields in messages:
                        await self._dispatch(msg_id, fields, handlers)
            poll_count += 1
            if poll_count % self._reclaim_every_n == 0:
                await self._reclaim(handlers)

    async def close(self) -> None:
        await self._client.aclose()
```

### Round-trip test fixture pattern

```python
# tests/events/test_subscription_payloads.py
# Source: modeled on platform-api tests/events/test_subscription_payload.py
# D-09: canonical JSON fixtures from docs/events.md; NO cross-repo import

import json
from decimal import Decimal
from datetime import UTC, datetime
from uuid import UUID

from provisioning_worker.events.subscription import SubscriptionActivatedPayload
from provisioning_worker.events.envelope import EventEnvelope

_ACTIVATED_FIXTURE = {
    "id": "01JZQABCDE12345678901234AB",  # 26-char ULID
    "type": "subscription.activated",
    "version": 1,
    "occurred_at": "2026-06-01T00:00:00Z",
    "producer": "platform-api",
    "correlation_id": None,
    "causation_id": None,
    "payload": {
        "subscription_id": "018efa2c-0000-7000-8000-000000000001",
        "customer_id": "018efa2c-0000-7000-8000-000000000002",
        "quote_id": "018efa2c-0000-7000-8000-000000000003",
        "stripe_subscription_id": "sub_test_abc123",
        "billing_cycle": "monthly",
        "currency": "USD",
        "line_count": 2,
        "total_amount": "129.99",   # string on wire — Pydantic coerces to Decimal
        "activated_at": "2026-06-01T00:00:00Z",
        "current_period_start": "2026-06-01T00:00:00Z",
        "current_period_end": "2026-07-01T00:00:00Z",
    },
}

_ENVELOPE_FIELDS = frozenset({"id", "type", "version", "occurred_at", "producer",
                               "correlation_id", "causation_id", "payload"})
_ACTIVATED_FIELDS = frozenset({"subscription_id", "customer_id", "quote_id",
                                "stripe_subscription_id", "billing_cycle", "currency",
                                "line_count", "total_amount", "activated_at",
                                "current_period_start", "current_period_end"})

def test_subscription_activated_round_trip() -> None:
    """Payload round-trips from wire JSON; total_amount parses from string."""
    env = EventEnvelope[SubscriptionActivatedPayload].model_validate(_ACTIVATED_FIXTURE)
    assert env.type == "subscription.activated"
    assert env.payload.total_amount == Decimal("129.99")
    assert set(env.model_dump(mode="json").keys()) == _ENVELOPE_FIELDS
    assert set(env.model_dump(mode="json")["payload"].keys()) == _ACTIVATED_FIELDS
    # Round-trip: re-validate from dumped dict
    re_env = EventEnvelope[SubscriptionActivatedPayload].model_validate(
        env.model_dump(mode="json")
    )
    assert re_env == env

def test_extra_field_rejected() -> None:
    """extra="forbid" raises on unknown fields."""
    bad = {**_ACTIVATED_FIXTURE["payload"], "unexpected_field": "x"}
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):
        SubscriptionActivatedPayload.model_validate(bad)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Standalone `aioredis` package | Unified `redis.asyncio` (redis>=5) | ~2022 (aioredis deprecated) | `aioredis` is unmaintained; use `import redis.asyncio as aioredis` |
| `from __future__ import annotations` in all files | Python 3.14 PEP 649 deferred eval (no import needed) | Python 3.14 | Project forbids the import; generated Alembic files must not have it |
| Pydantic v1 validators | Pydantic v2 `model_config = ConfigDict(...)` | Pydantic 2.0 | All models use `ConfigDict(frozen=True, extra="forbid")` |

**Deprecated/outdated:**
- `aioredis` standalone package: unmaintained; `redis>=5` provides `redis.asyncio` namespace.
- Pydantic v1 `class Config`: replaced by `model_config = ConfigDict(...)`.
- `from __future__ import annotations`: unnecessary and forbidden in Python 3.14 projects.

---

## Runtime State Inventory

> This phase is NOT a rename/refactor/migration phase. This section is included to
> document the specific runtime state this phase CREATES.

| Category | Items Created by Phase 2 | Action Required |
|----------|--------------------------|-----------------|
| Stored data | `provisioning.processed_event` table — first rows written when events are consumed | Created via Alembic migration; `make migrate` before running |
| Live service config | `cg.provisioning-convergence` consumer group created on `events.subscription` stream | Created idempotently by `XGROUP CREATE … MKSTREAM` at boot |
| OS-registered state | None | — |
| Secrets/env vars | `CONSUMER_RECLAIM_MIN_IDLE_MS` (new optional Settings field) | Add to `settings.py` with default `60_000`; add to `.env.example` |
| Build artifacts | None | — |

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Valkey/Redis | `ValkeyStreamsConsumer` | ✓ (via platform-infra) | Redis protocol / Valkey 8 | testcontainers for tests |
| Postgres | `session_scope()` / `processed_event` | ✓ (via platform-infra) | Postgres 18 | testcontainers for integration tests |
| `redis` package | Consumer adapter | ✓ | 7.4.0 (in uv.lock) | — |
| `pydantic` | Envelope + payload models | ✓ | 2.13.* (in uv.lock) | — |
| `sqlalchemy[asyncio]` | `session_scope()` | ✓ | 2.0.* (in uv.lock) | — |
| `alembic` | `processed_event` migration | ✓ | 1.18.* (in uv.lock) | — |
| `pytest-asyncio` | All async tests | ✓ | 1.3.0 (in uv.lock) | — |
| `testcontainers[redis]` | `@pytest.mark.integration` redelivery tests | ✓ | 4.14.2 (in uv.lock) | — |

**Missing dependencies with no fallback:** None.
**fakeredis:** Not in `pyproject.toml` or `uv.lock`. Unit tests must mock at the port
level (mock `EventConsumer` Protocol) rather than use a fake Redis. Integration tests
use `testcontainers[redis]` marked `@pytest.mark.integration`.

---

## Validation Architecture

> `workflow.nyquist_validation` is `true` in `.planning/config.json` — section required.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.* + pytest-asyncio 1.3.0 |
| Config file | `pyproject.toml [tool.pytest.ini_options]` |
| Quick run command | `make test` (`-m "not integration"`) |
| Full suite command | `make test-integration` (testcontainers) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CONS-01 (SC-1) | `XADD subscription.activated` → `XREADGROUP` → parse → dispatch → `XACK` | integration | `pytest tests/ -m integration -k "test_consumer_reads_and_acks" -x` | ❌ Wave 0 |
| CONS-02 (SC-4) | All 5 payload models round-trip + extra field rejected | unit | `pytest tests/events/ -x` | ❌ Wave 0 |
| CONS-03 (SC-2) | Same envelope.id re-submitted → short-circuit on `processed_event` | integration | `pytest tests/ -m integration -k "test_dedupe_replay" -x` | ❌ Wave 0 |
| CONS-04 (SC-3) | Malformed envelope → `error` log + `XACK` + no crash; valid follows | unit + integration | `pytest tests/ -k "test_poison" -x` | ❌ Wave 0 |

### Success Criteria → Observable Check Mapping

| SC | Observable Check | Observation Points |
|----|------------------|--------------------|
| SC-1: XADD → XACK | `processed_event` row exists; `XPENDING` returns 0 for that msg_id after call | DB query + `client.xpending()` |
| SC-2: Replay no-op | Second call returns without INSERT; `processed_event` still 1 row; no error log | DB row count; log output |
| SC-3: Poison survival | `error` log emitted; no `processed_event` row; next valid message still processed | Log level assertion; DB row count |
| SC-4: Round-trip | Fixture `model_validate` → `model_dump(mode="json")` → `model_validate` equality | pytest assertion |

### Sampling Rate

- **Per task commit:** `make test` (unit, Docker-free, `-m "not integration"`)
- **Per wave merge:** `make test` + spot-run `make test-integration` on the consumer/dedupe tests
- **Phase gate:** Full suite (`make test` + `make test-integration`) green before `/gsd-verify-work`

### Wave 0 Gaps (test infrastructure to create before implementation)

- [ ] `tests/events/__init__.py` — new test package for envelope/payload tests
- [ ] `tests/events/test_envelope.py` — envelope field-list pinning + round-trip
- [ ] `tests/events/test_subscription_payloads.py` — all 5 payload round-trips + extra field rejection (D-09 canonical fixtures)
- [ ] `tests/provisioning/test_idempotency.py` — dedupe guard integration tests (`@pytest.mark.integration`)
- [ ] `tests/conftest.py` updates — async Postgres session fixture + Valkey container fixture for integration tests
- [ ] Framework install: already present (`pytest-asyncio 1.3.0`, `asyncio_mode=auto`)

---

## Security Domain

> `security_enforcement: true` in `.planning/config.json`.

### Applicable ASVS Categories (ASVS Level 1)

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Worker authenticates to no service in MVP (trusted internal network) |
| V3 Session Management | No | No user sessions |
| V4 Access Control | No | No user-facing endpoints in this phase |
| V5 Input Validation | **Yes** | Pydantic `extra="forbid"` on all envelope and payload models; two-phase parse rejects malformed messages |
| V6 Cryptography | No | No cryptographic operations in this phase |

### Known Threat Patterns for Valkey Streams Consumer

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed/crafted envelope injection | Tampering | `extra="forbid"` + typed models; poison path `XACK`s without processing |
| Unknown event type triggering unexpected behavior | Tampering | `_REGISTRY.get()` returns `None`; unknown types logged at warning + `XACK`'d |
| JSON injection (nested/recursive) | Tampering | Pydantic validates structure; `json.loads()` is safe against recursion by default |
| DoS via flood of poison messages | Denial of Service | Each poison is `XACK`'d immediately; no retry loop; consumer stays running |
| Credential exposure in logs | Information Disclosure | No-op handlers have no credentials to log; `bind_contextvars` binds only IDs |

**Note:** This phase introduces no new authentication surfaces. The worker reads from a
trusted internal Valkey instance and writes to a trusted internal Postgres schema. The
primary security concern is input validation — the `extra="forbid"` envelope model
satisfies ASVS V5.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `decode_responses=True` on the consumer client makes `fields["envelope"]` a `str` | redis.asyncio API | Low — verified by Phase-1 code using the same setting |
| A2 | Plain `Decimal` field (no `PlainSerializer`) is sufficient for consume-only path | Payload Models section | Low — verified by runtime Pydantic 2.13 test in this session |
| A3 | `XAUTOCLAIM` cursor `"0-0"` signals end of PEL scan | XAUTOCLAIM Pattern | Low — confirmed by redis docs reference in library docstring |
| A4 | `fakeredis` not being available is acceptable because port-level mocking covers unit tests | Environment Availability | Medium — if integration-level unit tests are preferred, `fakeredis` must be added as a dev dependency |

**A4 is the only assumption that could affect planning.** If the team wants in-memory
Redis unit tests (faster than testcontainers for replay/reclaim scenarios), `fakeredis`
would need to be added to `pyproject.toml [project.optional-dependencies] dev`. This
is Claude's discretion per the context decisions.

---

## Open Questions

1. **`XAUTOCLAIM` cadence: every-N-cycles vs a separate `asyncio.sleep` timer?**
   - What we know: D-08 says "periodic, shared path" is locked; cadence/period is discretion.
   - What's unclear: every-N-cycles couples reclaim frequency to poll frequency (block=1000ms).
     At 60 cycles × 1s block = ~60s cadence. A `asyncio.create_task` timer is more precise
     but adds complexity.
   - Recommendation: Start with every-N-cycles (simpler, auditable). Revisit if cadence needs
     to be independent of poll load.

2. **Should `handlers.py` receive the full `EventEnvelope[P]` or just the typed payload?**
   - What we know: Handlers need `envelope_id`, `subscription_id`, `correlation_id` for
     `bind_contextvars` (CLAUDE.md §6.6). These are on the envelope, not the payload.
   - What's unclear: Whether passing the full envelope or a structured "context + payload"
     tuple is cleaner.
   - Recommendation: Pass the full typed envelope (or a simple handler context dataclass
     carrying `envelope_id`, `correlation_id`, and the typed payload). Avoids leaking
     envelope internals into Phase 3 handler bodies.

---

## Sources

### Primary (HIGH confidence)
- `redis==7.4.0` — installed library source (`_parsers/helpers.py`, `commands/core.py`)
  — XAUTOCLAIM return shape, XREADGROUP return shape, all signatures
- `src/platform_api/events/envelope.py` — exact `EventEnvelope[P]` generic shape,
  `stream_for_envelope_type`, field names, `build()` producer-only method
- `src/platform_api/events/subscription.py` — all five payload models, field sets,
  `LineDelta` nested model
- `src/platform_api/events/__init__.py` — registry dict literal pattern,
  `envelope_class_for`, `UnknownEnvelopeType`
- `src/provisioning_worker/infrastructure/db.py` — `session_scope()` contract
- `src/provisioning_worker/main.py` — Phase-1 consumer loop to transfer
- `src/provisioning_worker/settings.py` — existing Settings vars
- `migrations/provisioning/script.py.mako` — confirms no `from __future__`
- `docs/events.md` — envelope spec, five payload models, evolution rules, idempotency
- `docs/architecture.md` — ports/adapters, `processed_event` ledger, event consumption
- `pyproject.toml` / `uv.lock` — confirmed package versions, confirmed `fakeredis` absent
- `.planning/config.json` — confirmed `nyquist_validation: true`,
  `security_enforcement: true`

### Secondary (MEDIUM confidence)
- `platform-api/tests/events/test_envelope_registry.py` — registry test pattern
- `platform-api/tests/events/test_subscription_payload.py` — round-trip test pattern
- `platform-api/adapters/valkey_streams_bus.py` — wire format conventions

### Tertiary (LOW confidence)
- None in this research.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages already in uv.lock; versions confirmed
- redis.asyncio API: HIGH — verified against installed 7.4.0 library source
- Architecture patterns: HIGH — verified against Phase-1 code and platform-api
- Five payload models: HIGH — read directly from both repo docs and platform-api source; no drift
- Pitfalls: HIGH — grounded in verified API shapes and known Pydantic 2 behavior
- Migration: HIGH — verified Alembic env.py + mako template

**Research date:** 2026-06-02
**Valid until:** 2026-09-02 (90 days — redis 7.x and Pydantic 2.x are stable)
