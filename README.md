# provisioner (provisioning-worker)

`provisioner` is the **provisioning-worker** for the **Odoo Entitlements SaaS
Platform** — a long-running, event-driven Python worker (package
`provisioning_worker`) that converges each customer's **dedicated Odoo
instance** to the state their subscription entitles them to. It consumes
`subscription.*` events off Valkey Streams, drives a pluggable
`DeploymentAdapter` (an in-memory fake in milestone 1, Coolify in milestone 2)
to create/update/suspend/deprovision the instance, and publishes `instance.*`
events back onto the bus via a transactional outbox. It exposes **no HTTP
request API** — only `GET /healthz` for orchestrator liveness; customer- and
operator-facing instance views are served by `platform-api` reading the
`provisioning` schema this worker owns.

## Position

```text
  platform-api ──publishes──► events.subscription ──XREADGROUP──► provisioning-worker
                              (Valkey Streams,                    (this repo)
                               cg.provisioning-convergence)            │
                                                                       ├─► DeploymentAdapter
                                                                       │   (Fake v1 → Coolify)
                                                                       │     └─► customer Odoo instance
                                                                       │
                                                                       └─► events.instance
                                                                           (producer=provisioning-worker)
                                                                              └─► platform-api (its Phase 6)
```

The dependency arrow is **one-way**: the worker consumes what `platform-api`
publishes and publishes `instance.*` back; it never calls `platform-api`'s HTTP
API and `platform-api` never calls this worker. The only shared state is the
Postgres cluster (this worker writes the `provisioning` schema; `platform-api`
reads it) and the Valkey event bus. See [docs/architecture.md](docs/architecture.md)
for the full picture.

## Quick start

Requires the sibling **`platform-infra`** stack up (Postgres 18 + Valkey 8 on
the `platform-net` bridge; the `provisioning` schema is created empty by
`platform-infra` and this repo's Alembic tree fills it).

```bash
cp .env.example .env             # adjust if your platform-infra ports differ
uv sync --frozen --extra dev     # Python 3.14, locked deps
make migrate                     # create the provisioning.* tables
make run                         # boots the worker: consumer + convergence + relay + /healthz
```

Liveness check (the only HTTP surface):

```bash
curl http://localhost:8001/healthz   # → 200 {"status":"ok"}  (port = HEALTH_PORT, default 8001)
```

In milestone 1 `DEPLOYMENT_ADAPTER=fake` and `NOTIFICATION_TRANSPORT=console`,
so the full pipeline runs with **no Coolify and no real Odoo**.

## Docs map

| Doc | What it covers |
|---|---|
| [docs/overview.md](docs/overview.md) | What this service is, product context, MVP scope. Read first. |
| [docs/architecture.md](docs/architecture.md) | Process model, code layout, ports/adapters, the `provisioning` schema, the instance state machine, cross-cutting concerns. The repo's architectural source of truth. |
| [docs/events.md](docs/events.md) | The event contract: envelope, consumed `subscription.*` and produced `instance.*` payloads, evolution rules, idempotency. |
| [docs/deployment-adapter.md](docs/deployment-adapter.md) | The `DeploymentAdapter` port, `InstanceSpec`, the `FakeDeploymentAdapter` (M1) and `CoolifyAdapter` sketch (M2). |
| [docs/conventions.md](docs/conventions.md) | Coding standards not enforced by ruff (file naming, dependency rule, sessions, errors). |
| [docs/local-development.md](docs/local-development.md) | Getting set up against `platform-infra` and the iteration loop. |
| [docs/python-style.md](docs/python-style.md) | Python 3.14 style and design rules (typing, data modeling, SOLID, error handling). |
| [CLAUDE.md](CLAUDE.md) | Repo guidance for Claude Code agents (and a lean human onboarding doc). |

## Status

**Milestone 1 — the fake-adapter pipeline — is the current target**, and the
repo is being **scaffolded under GSD** (phases/plans live under `.planning/`).
M1 builds the entire pipeline against `FakeDeploymentAdapter` +
`ConsoleNotificationTransport`: the Valkey Streams consumer + idempotency, the
`provisioning` schema, the convergence service + 8-state instance machine
(`pending`/`deploying`/`configuring`/`ready`/`suspended`/`failed`/
`deprovisioning`/`deprovisioned`), the Taskiq retry/backoff path, the outbox +
relay, and the full `instance.*` event catalog — all unit-testable, with no
Coolify and no real Odoo. **Milestone 2** (Coolify-API spike + `CoolifyAdapter`,
the Odoo stack template, `enforcement_snapshot` served to a real plugin, the
per-instance bearer token, SMTP, operator retry) is explicitly out of scope for
now; items marked milestone-2 in the docs are not yet built.

## Make targets

| Target | Use |
|---|---|
| `make help` | List every available target. |
| `make dev` | Install deps (`uv sync --frozen --extra dev`) and prepare the dev environment. |
| `make run` | Boot the worker (`python -m provisioning_worker`): Streams consumer + convergence + outbox relay + `/healthz`. |
| `make test` | Unit tests, Docker-free (`-m "not integration"`, fake adapter + in-memory bus). |
| `make check` | CI gate: `ruff check` + `ruff format --check`. |
| `make migrate` | Alembic upgrade head on the single `provisioning` tree. |
| `make infra-up` | Bring the sibling `platform-infra` stack (Postgres + Valkey) up for local dev. |

Integration tests (testcontainers Postgres + Valkey, `-m integration`) need
Docker and are not part of the default `make test`.
