# Local development guide

End-to-end instructions for getting `provisioner` (the
**provisioning-worker**) running on your laptop against the local
`platform-infra` stack, plus the iteration loop for day-to-day work.

Unlike `platform-api`, this is a **worker**, not an HTTP service. There
is no Granian, no Swagger UI, and no Keycloak token dance — the process
consumes `events.subscription` from Valkey Streams, converges the
`provisioning` schema, and publishes `events.instance`. The only HTTP it
serves is `GET /healthz` for orchestrator liveness.

> The scaffold this guide describes (`pyproject.toml`, `Makefile`,
> `settings.py`, the `python -m provisioning_worker` entry point, the
> Alembic `provisioning` tree) is created in the first GSD phase. This
> guide is written as the intended steady state.

## One-time setup

### 1. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Or: brew install uv
# Or on Windows / WSL: see https://docs.astral.sh/uv/getting-started/installation/
uv --version    # should print 0.11.x
```

### 2. Install Python 3.14

```bash
uv python install 3.14
```

uv keeps its own Python toolchain — no conflict with the system Python.

### 3. Install platform-infra

This worker shares the same Postgres + Valkey as `platform-api`. Bring
the infra up once for the whole workspace. From the root of the platform
workspace:

```bash
cd platform-infra
cp .env.example .env
make up && make check
```

`make check` should return OK for postgres and valkey. The worker uses
**neither** Keycloak realm (it authenticates to nothing in MVP — see
[architecture.md](architecture.md) §Authentication), so you can ignore
the Keycloak lines for this repo. The `provisioning` schema already
exists in the `platform` database but is **empty** — it is created by
`platform-infra/postgres/init/01-init.sql`; this repo's Alembic tree
creates the tables (next section).

### 4. Configure provisioner

```bash
cd provisioner
cp .env.example .env
# .env.example points at platform-infra's default ports/passwords, so
# unless you changed those you can leave it.
uv sync --frozen --extra dev
```

`uv sync` creates `.venv/` in this repo and installs everything from
`uv.lock`. CI uses `--frozen` so any drift between the local and CI
lockfile fails the build, not your laptop.

The defaults in `.env.example` are the milestone-1 set — no Coolify, no
SMTP:

| Var | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://platform:platform_dev_password@localhost:5432/platform` | async (psycopg) URL the worker uses |
| `DATABASE_URL_SYNC` | same host, `postgresql+psycopg://…` | used by Alembic only |
| `VALKEY_URL` | `redis://localhost:6379/0` | same Valkey as platform-api (Taskiq broker + Streams bus) |
| `PROVISIONING_CONSUMER_GROUP` | `cg.provisioning-convergence` | the read loop's group on `events.subscription` |
| `CONSUMER_NAME` | e.g. `worker-1` | unique per replica; bump it if two local processes ever run |
| `DEPLOYMENT_ADAPTER` | `fake` | `FakeDeploymentAdapter` — milestone 1; `coolify` is milestone 2 |
| `NOTIFICATION_TRANSPORT` | `console` | `ConsoleNotificationTransport` prints credential emails to the log |
| `HEALTH_PORT` | `8001` | the `/healthz` aiohttp server (kept off platform-api's 8000) |
| `OUTBOX_POLL_SECONDS` / `OUTBOX_BATCH_SIZE` | `1.0` / `100` | relay cadence |
| `OTEL_SERVICE_NAME` | `provisioning-worker` | |
| `LOG_LEVEL` | `INFO` | |

`COOLIFY_API_URL`, `COOLIFY_API_TOKEN`, and `SMTP_*` are **milestone-2**
vars; they are absent (or commented) in the milestone-1 `.env.example`.

## Running the worker

```bash
make run    # == python -m provisioning_worker
```

On a healthy boot you should see, in order:

```text
INFO  provisioning-worker starting environment=dev deployment_adapter=fake
INFO  joined consumer group group=cg.provisioning-convergence stream=events.subscription consumer=worker-1
INFO  outbox relay started poll_seconds=1.0
INFO  health server listening port=8001
```

The process boots **four concurrent concerns on one asyncio loop** (see
[architecture.md](architecture.md) §Process model): the Valkey Streams
consumer, the convergence service + instance state machine + Taskiq
retry jobs, the outbox relay, and the tiny `/healthz` server. It then
blocks until SIGTERM (`Ctrl-C`), drains in-flight tasks, and closes the
Valkey/Postgres pools cleanly.

Confirm liveness from another terminal:

```bash
curl -s http://localhost:8001/healthz
# → {"status":"ok"}
```

There is **no** `/docs`, `/openapi.json`, or any domain endpoint — those
live in `platform-api`, which reads `provisioning.*` for the
customer/operator views.

## Iteration loop

```bash
make run         # one terminal — leave it tailing logs
make test        # another terminal, after each change (Docker-free)
make lint-fix    # auto-fix trivial lint; `make format` rewrites formatting
make check       # before committing (ruff check + ruff format --check)
make migrate     # whenever you add a migration
```

`make test` runs `pytest -m "not integration"`: the full convergence
pipeline against the in-memory `FakeDeploymentAdapter` + an in-memory
bus, no Docker. `make test-integration` runs the `integration`-marked
tests that spin up Postgres + Valkey via testcontainers (needs Docker).

## Feeding it an event by hand

The worker only does something when a `subscription.*` envelope lands on
`events.subscription`. In a full local stack `platform-api` emits those
via its own outbox → relay — but you often want to drive **this** repo
without running platform-api at all. Publish an envelope by hand and
watch the pipeline run end-to-end against the fake adapter.

The wire format (see [events.md](events.md) §Wire format) is a single
stream field named `envelope` holding the envelope JSON. The `id` must
be a 26-char ULID and is the idempotency key; `producer` must be
`platform-api` for a consumed event.

### 1. XADD a `subscription.activated` envelope

```bash
valkey-cli XADD events.subscription MAXLEN '~' 100000 '*' envelope '{
  "id": "01J9ZX4M7Q0K2H8R5T3V6W9N1C",
  "type": "subscription.activated",
  "version": 1,
  "occurred_at": "2026-06-01T12:00:00Z",
  "producer": "platform-api",
  "correlation_id": "local-smoke-1",
  "causation_id": null,
  "payload": {
    "subscription_id": "11111111-1111-4111-8111-111111111111",
    "customer_id": "22222222-2222-4222-8222-222222222222",
    "quote_id": "33333333-3333-4333-8333-333333333333",
    "stripe_subscription_id": "sub_local_smoke",
    "billing_cycle": "monthly",
    "currency": "EUR",
    "line_count": 1,
    "total_amount": "49.00",
    "activated_at": "2026-06-01T12:00:00Z",
    "current_period_start": "2026-06-01T12:00:00Z",
    "current_period_end": "2026-07-01T12:00:00Z"
  }
}'
```

The worker's consumer picks the entry up via `XREADGROUP`, dispatches on
`type`, opens a `provisioning.instance` row (`pending`) plus a `create`
task, and drives the state machine `pending → deploying → configuring →
ready` against the fake adapter. Watch the `make run` terminal — you
should see the handler log the transitions and, because
`NOTIFICATION_TRANSPORT=console`, the credentials email printed to the
log rather than sent.

To re-run the smoke without it being a no-op, use a **fresh** ULID `id`
each time — replaying the same `id` short-circuits on `processed_event`
(at-least-once delivery + idempotency, by design).

### 2. Verify the instance row was created

```bash
make psql
```

```sql
SELECT id, subscription_id, status, url, version
  FROM provisioning.instance
 ORDER BY created_at DESC
 LIMIT 5;
```

You should see one row for `subscription_id =
11111111-…` with `status = 'ready'` and a populated `url` (the fake
adapter mints a `{slug}.{INSTANCE_DOMAIN_SUFFIX}` host). If it's still
`pending`/`deploying`, the consumer hasn't drained yet or a step
failed — check the `make run` log and the `provisioning_task` row:

```sql
SELECT task_type, status, attempt_count, last_error
  FROM provisioning.provisioning_task
 ORDER BY created_at DESC LIMIT 5;
```

### 3. Verify the `instance.provisioned` envelope was emitted

Reaching `ready` writes an `instance.provisioned` row to the
`event_outbox` **in the same transaction** as the state change; the
relay publishes it to `events.instance`:

```bash
valkey-cli XRANGE events.instance - + COUNT 5
```

You should see an entry whose `envelope` field has
`"type":"instance.provisioned"`, `"producer":"provisioning-worker"`, a
fresh ULID `id`, and `"causation_id"` set to the
`01J9ZX4M7Q0K2H8R5T3V6W9N1C` you published in step 1 (the produced
envelope's `causation_id` is the triggering event's `id`; see
[events.md](events.md) §Envelope). The `payload` carries
`instance_id`, `url`, `admin_email`, `snapshot_version`, etc. — and
**never** credentials, which go out-of-band via the notification
transport.

### Driving the other lifecycle paths

The same `XADD` shape drives the rest of the state machine — swap the
`type` and `payload` to match the schemas in [events.md](events.md)
§Events this service CONSUMES:

| Publish | Drives | Produces |
|---|---|---|
| `subscription.lines_changed` | update (keyed on `change_set_id`) | `instance.updated` |
| `subscription.suspended` | soft suspend | `instance.suspended` |
| `subscription.reinstated` | reinstate | `instance.reinstated` |
| `subscription.cancelled` | deprovision (`immediate` now / `at_period_end` at `grace_until`) | `instance.deprovisioned` |

To exercise the **retry/backoff + `instance.failed`** path
deterministically, the `FakeDeploymentAdapter` has a fault-injection
mode (`fail_on={"create"}`, latency, partial failure) — used in
`tests/`; see [deployment-adapter.md](deployment-adapter.md)
§FakeDeploymentAdapter.

## Adding a migration

This repo has a **single** Alembic tree for the `provisioning` schema
(`alembic.ini` section `provisioning`, `version_table_schema=provisioning`),
simpler than platform-api's three-tree setup — so the Make targets carry
no schema suffix.

```bash
make revision name="add instance_credential"
# → Generating migrations/provisioning/versions/20260601_1410_add_instance_credential.py ... done
```

Never write the file by hand. Edit the autogenerated revision — review
the SQL, since autogenerate loses CHECK constraints and mishandles enums
(see [architecture.md](architecture.md) §Migrations) — then apply it:

```bash
make migrate          # alembic upgrade head against the local DB
make psql
```

```sql
\dt provisioning.*
```

You should see the new table alongside `instance`, `provisioning_task`,
`enforcement_snapshot`, `event_outbox`, and `processed_event`. Commit
the revision file alongside the SQLAlchemy model in `models.py` that
uses it. Migrations are forward-only.

## Resetting local data

```bash
cd ../platform-infra
make reset    # drops the Postgres + Valkey volumes and re-creates them
```

This wipes the `provisioning` tables (the schema returns to the empty
state `01-init.sql` leaves it in) **and** the Valkey streams + consumer
groups. After a reset, re-create the tables and you're clean:

```bash
cd ../provisioner
make migrate
```

The worker keeps no state of its own outside Postgres and Valkey, so
nothing else needs cleaning up here.

## Troubleshooting

**`psycopg.OperationalError: password authentication failed for user
"platform"`** — your `.env`'s `DATABASE_URL` / `DATABASE_URL_SYNC`
user/password doesn't match what platform-infra is running with. Confirm
the `provisioner/.env` and `platform-infra/.env` agree (default role is
`platform` / `platform_dev_password`). Note this repo uses the **psycopg**
driver, not asyncpg.

**`BUSYGROUP Consumer Group name already exists`** — the consumer group
`cg.provisioning-convergence` was already created on `events.subscription`
(by a previous run, or by platform-api's relay creating the stream). This
is expected and harmless: group creation is `XGROUP CREATE … MKSTREAM`
treated as idempotent, so the worker logs it and continues. If you want a
truly clean slate, `make reset` in `platform-infra` drops the stream.

**`redis.exceptions.ConnectionError: Error connecting to localhost:6379`**
— Valkey isn't up, or `VALKEY_URL` points at the wrong host/port. Check
`platform-infra` is running (`cd ../platform-infra && make check`) and
that `VALKEY_URL` is `redis://localhost:6379/0` (in-network, the
hostname is `valkey`, not `localhost`). Use `redis://`, not `valkey://`
— the scheme is the redis client's, the server is Valkey 8.

**Port 8001 already in use** — `lsof -i :8001` to find the squatter, or
set `HEALTH_PORT=8002` for this session. The health server is liveness
only; nothing else binds a port.

**The worker logs `starting` but nothing happens when I XADD** — confirm
you published to `events.subscription` (not `events.instance`), that the
single field is named exactly `envelope`, and that the JSON parses
(`extra="forbid"` rejects unknown fields). A malformed envelope (bad JSON
or an unknown field) is a poison message: the consumer logs it at `error`
and `XACK`s it so it can't block the group, but it never creates or
advances an instance — check the `make run` output for the parse error.
See [architecture.md](architecture.md) §Event consumption and idempotency.
