# Domain events

The provisioning-worker **consumes** `subscription.*` events from
`events.subscription` and **produces** `instance.*` events on `events.instance`,
both over Valkey Streams. This document is the source of truth for the events
this service consumes and produces.

Contracts are **per-repo, not shared.** The consumed payloads below are
re-implemented here against platform-api's `docs/events.md` (the producer's
contract); the produced `instance.*` payloads are authored here and are the
contract platform-api re-implements on its consumer side. There is no shared
`platform-contracts` package — drift is caught by review and the evolution
discipline in this file.

## Envelope

Every event — consumed or produced — uses the same envelope; only `payload`
varies. It is re-implemented here exactly as platform-api defines it, so
envelopes round-trip byte-identically across services.

```python
class EventEnvelope[P: BaseModel](BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., min_length=26, max_length=26)   # 26-char ULID, idempotency key
    type: str                                            # e.g. "instance.provisioned"
    version: int = Field(..., ge=1)                      # payload schema version
    occurred_at: datetime                                # UTC, producer wall-clock
    producer: Literal[
        "platform-api", "provisioning-worker", "telemetry-worker",
    ]
    correlation_id: str | None = None                    # upstream request/trace id
    causation_id: str | None = None                      # id of the immediately-preceding event
    payload: P
```

When this service **produces** an event it mints a fresh ULID `id`, sets
`producer="provisioning-worker"`, `occurred_at=datetime.now(tz=UTC)`, and sets
`causation_id` to the `id` of the triggering `subscription.*` envelope (and
`correlation_id` to that envelope's `correlation_id`, propagating the trace).

### Wire format

A producer appends the envelope to its stream with a single field named
`envelope` holding `envelope.model_dump_json()` bytes:

```text
XADD events.instance MAXLEN ~ 100000 * envelope <json-bytes>
```

Consumers read the `envelope` field, `model_validate` it back into a typed
`EventEnvelope`, and dispatch on `type`. Use `redis.asyncio` (the modern
unified client, `redis>=5`) — **not** the unmaintained standalone `aioredis`
package — for parity with platform-api's `ValkeyStreamsBus`.

### Stream routing

Stream name = `events.<prefix>` where `<prefix>` is the segment before the
first dot of `type`. So every `subscription.*` event is on `events.subscription`
and every `instance.*` event is on `events.instance`. This rule is shared by
the publisher and the outbox relay so a re-publish lands on the same stream as
a direct publish.

## Streams and consumer groups

| Stream | This service | Group / role |
|---|---|---|
| `events.subscription` | **consumes** | `cg.provisioning-convergence` (our read loop) |
| `events.instance` | **produces** | read by platform-api's `cg.subscription-convergence` (its Phase 6) |

Consumer-group names describe *what the consumer does*, not which service runs
it. Delivery is at-least-once; handlers MUST be idempotent on `envelope.id`
(see [architecture.md](architecture.md) §Event consumption).

## Evolution rules

Identical discipline to platform-api. Integer `version` per payload.

**Backward-compatible (do not bump version):** add a field with a default /
`None`; add an enum value *only if* every consumer treats unknown values as a
no-op (strict enum parsing on consumers is forbidden); widen a numeric range;
relax a regex.

**Breaking (new version):** rename or remove a field; narrow a type; change a
field's unit; change a field's meaning while keeping its name (don't — pick a
new name).

**Migration:** author `vN+1` beside `vN`; producer dual-publishes for one
release; upgrade each consumer to read `vN+1`; after a soak, producer stops
emitting `vN`; remove `vN` next release. Old backlog entries keep their
original `version`.

---

# Events this service CONSUMES

All on `events.subscription`, producer `platform-api`, version `1`. These
models are **re-implemented** here (frozen, `extra="forbid"`) to match
platform-api's `docs/events.md`. They are all **implemented and emitting** in
platform-api today (via its transactional outbox → relay).

### `subscription.activated` → **create instance**

Triggered when a `draft` subscription goes `active` after Stripe confirms first
payment. This is the create trigger: open an `instance` row (`pending`) and a
`create` task.

```python
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
```

> The payload carries no line detail. The entitled module/seat/resource picture
> needed to build the `InstanceSpec` is **not** in this event — see "Resolving
> entitlements" below.

### `subscription.lines_changed` → **update instance**

Triggered when an `active` subscription's lines change. Idempotency key is
`change_set_id`; convergence keys on it.

```python
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
    change_set_id: UUID            # idempotency key for downstream provisioning
    deltas: list[LineDelta] = Field(..., min_length=1)
    effective_at: datetime
    triggered_by: Literal["operator", "customer", "system"]
    actor_id: str | None = None
```

### `subscription.suspended` → **suspend instance**

```python
class SubscriptionSuspendedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    reason: Literal["dunning_exhausted", "manual_operator", "policy_violation"]
    previous_status: Literal["active", "past_due"]
    suspended_at: datetime
    actor_id: str | None = None
    note: str | None = None
```

Soft suspension only in MVP (PRD §10.3): the instance stays running,
login-blocked. Hard suspension (scale-to-zero) is deferred.

### `subscription.reinstated` → **reinstate instance**

```python
class SubscriptionReinstatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subscription_id: UUID
    customer_id: UUID
    reason: Literal["payment_recovered", "manual_operator"]
    previous_status: Literal["past_due", "suspended"]
    reinstated_at: datetime
    actor_id: str | None = None
```

### `subscription.cancelled` → **deprovision instance**

```python
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

`immediate` deprovisions now; `at_period_end` schedules deprovision for
`grace_until` (delayed Taskiq job), with the data-export/backup generated in
the window (PRD §10.4).

### Also on the bus (not acted on)

`events.payment` carries `payment.succeeded` / `payment.failed`. The worker
does **not** consume `events.payment` — payment outcomes reach this service
only via the `subscription.*` lifecycle events platform-api derives from them.
Listed here only so it's clear the worker subscribes to `events.subscription`
and nothing else.

### Resolving entitlements

`subscription.*` events identify *what changed* but do not carry the full
entitled module/seat/resource set. To build an `InstanceSpec` the worker needs
the current entitlement picture. Two options, to settle in milestone 1 design
(coordinate with platform-api):

1. **Reconstruct from deltas** — fold `lines_changed` deltas onto the set
   established at `activated`. Self-contained but requires the `activated`
   payload to include the initial line set (it currently does not — a possible
   `v2` or a companion lookup).
2. **Read-back** — read the entitled set from the `subscription`/`catalog`
   schema (cross-schema read, as platform-api already reads `provisioning`).
   Simplest and consistent with the read-seam model.

This is an open contract question flagged for milestone-1 planning, not a
blocker for the consumer/state-machine skeleton.

---

# Events this service PRODUCES

All on `events.instance`, producer `provisioning-worker`, version `1`, frozen +
`extra="forbid"`. Today only `instance.deprovisioned` has a consumer
(platform-api Phase 6); the rest are authored for future consumers (operator
console, telemetry, a notification service) and are part of the worker's owned
contract now so they don't get retrofitted later.

### `instance.provisioned`

Emitted when an instance first reaches `ready`. Signals readiness; **never
carries credentials** (those go out-of-band via `NotificationTransport`).

```python
class InstanceProvisionedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    hostname: str
    url: str
    admin_email: str
    snapshot_version: int
    provisioned_at: datetime
```

### `instance.updated`

Emitted after a `lines_changed` convergence is applied.

```python
class InstanceUpdatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    change_set_id: UUID
    applied_changes: list[str]        # e.g. ["module:crm_extended added", "seat_cap 10->25"]
    snapshot_version: int
    updated_at: datetime
```

### `instance.suspended` / `instance.reinstated`

```python
class InstanceSuspendedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    reason: Literal["dunning_exhausted", "manual_operator", "policy_violation"]
    suspended_at: datetime

class InstanceReinstatedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    reinstated_at: datetime
```

### `instance.failed`

Emitted when a convergence step fails (for operator alerting / telemetry).
`will_retry` distinguishes a backoff retry from terminal failure.

```python
class InstanceFailedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    task_type: Literal["create", "update", "suspend", "reinstate", "delete"]
    failed_step: str
    attempt_count: int = Field(..., ge=1)
    will_retry: bool
    failure_code: str
    failure_message: str | None = None
    failed_at: datetime
```

### `instance.deprovisioned`

The one event platform-api consumes today (`cg.subscription-convergence`, its
Phase 6 / EVT-03), to close out the subscription record. **The field set must
match platform-api's `docs/events.md` exactly** — platform-api's envelope is
`extra="forbid"`, so an extra field would be rejected once its consumer ships.

```python
class InstanceDeprovisionedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    backup_artifact_ref: str | None = None
    backup_expires_at: datetime | None = None
    deprovisioned_at: datetime
```

> platform-api's sketch of this payload omits a `version` and `extra="forbid"`;
> we add both. Confirm the exact shape with platform-api when its consumer
> lands so the contract is symmetric.

## Producer / consumer summary

| Event | We produce | We consume |
|---|---|---|
| `subscription.activated` | | ✅ |
| `subscription.lines_changed` | | ✅ |
| `subscription.suspended` | | ✅ |
| `subscription.reinstated` | | ✅ |
| `subscription.cancelled` | | ✅ |
| `instance.provisioned` | ✅ | |
| `instance.updated` | ✅ | |
| `instance.suspended` | ✅ | |
| `instance.reinstated` | ✅ | |
| `instance.failed` | ✅ | |
| `instance.deprovisioned` | ✅ | |

## Retention

`events.instance` — retain 7 days, `MAXLEN ~ 100000` (approximate trim), sized
for incident replay. We read the tail of `events.subscription`, which
platform-api retains on the same terms.

## Handler idempotency

A handler MUST be safe to invoke twice with the same `envelope.id`. The
recommended pattern persists `(event_id, consumer_group)` in
`provisioning.processed_event` **in the same transaction** as the state change
and short-circuits on replay. The helper lives in `shared/event_consumer.py`
and wraps every consumer registration. See
[architecture.md](architecture.md) §Event consumption and idempotency.
