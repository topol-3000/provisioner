# Overview

`provisioner` (the **provisioning-worker**) is the backend service that
turns a paid subscription into a running, dedicated Odoo instance. It is
a long-running Taskiq / stream-consumer worker for the **Odoo
Entitlements SaaS Platform** — **not** an HTTP service. It exposes no
request API; it serves only `GET /healthz` (a tiny `aiohttp.web` server)
so the orchestrator can check liveness.

It **consumes** `subscription.*` events from `platform-api` over Valkey
Streams (consumer group `cg.provisioning-convergence`) and converges each
customer's dedicated Odoo deployment to the state their subscription
entitles them to, then **produces** `instance.*` events back onto the bus
via a transactional outbox + relay. Its event producer literal is
`provisioning-worker`.

The other two backend services live in sibling repos: `platform-api` (the
FastAPI control plane that publishes the `subscription.*` events) and
`telemetry-worker` (health/usage polling). Neither this worker nor
`telemetry-worker` exposes an HTTP request API (only a liveness `/healthz`).

## What the product sells

The platform sells **entitlements** — atomic, individually priced units
of value (module entitlements like `crm_extended`, seat packs, resource
caps, service tiers), composed per customer into a **subscription** billed
as one recurring charge. There are no fixed plans; per-line custom pricing
is first-class. The full business model lives in platform-api's
`docs/overview.md`.

What matters for **this** repo: each paying customer gets a **dedicated
Odoo instance**, one per subscription (a 1:1:1 customer → subscription →
instance invariant). Provisioning, configuring, suspending, and tearing
down that instance — in response to the subscription's lifecycle — is this
service's entire job. The instance is provisioned through a pluggable
**deployment adapter** (a `FakeDeploymentAdapter` in milestone 1; a
`CoolifyAdapter` in milestone 2), so the convergence logic is independent
of any one orchestrator.

## Place among the three services

```text
   platform-api ──publishes──► events.subscription ──► provisioning-worker
   (FastAPI control plane)        (Valkey Streams)        (this repo)
        ▲                                                      │
        └────────── events.instance ◄──────────────produces───┘
            (platform-api consumes instance.deprovisioned, its Phase 6)
```

| Service | Role | Owns |
|---|---|---|
| `platform-api` | HTTP control plane: portals, Stripe webhook, catalog/subscription/billing, plugin API. Publishes `subscription.*`. Reads `provisioning.*`. | `catalog` / `subscription` / `billing` schemas |
| **`provisioning-worker`** (this repo) | Consumes `subscription.*`, converges the dedicated Odoo instance via the deployment adapter, produces `instance.*`. No HTTP API. | `provisioning` schema |
| `telemetry-worker` | Per-instance health/usage polling, alert evaluation. | `telemetry` schema |

The dependency arrow is **one-way**. This worker consumes what
`platform-api` publishes and publishes events back; it does **not** call
platform-api's HTTP API, and platform-api does **not** call this worker.
The only shared state is the Postgres cluster (this service writes the
`provisioning` schema, platform-api reads it) and the Valkey event bus.
Contracts are **per-repo, not shared** — there is no `platform-contracts`
package; event/envelope/payload models are re-implemented here against
[events.md](events.md). See [architecture.md](architecture.md) for the
process model and module layout.

## Scope of this repo

| In scope | Out of scope (lives elsewhere) |
|---|---|
| Consume `subscription.*` from `events.subscription` (`cg.provisioning-convergence`) | HTTP request APIs / portals / Stripe (platform-api) |
| Converge a dedicated Odoo instance via the `DeploymentAdapter` port | Subscription / billing / catalog state (platform-api) |
| Own the `provisioning` schema (the instance registry, task ledger, snapshots) | The customer/operator views of instance status (platform-api reads `provisioning.*`) |
| Drive the 8-state instance state machine with Taskiq retry/backoff | The Odoo enforcement plugin itself (ships in the base image) |
| Produce the `instance.*` event catalog via a transactional outbox → relay | Health / usage polling (telemetry-worker) |
| Mint per-instance bearer tokens; serve `enforcement_snapshot` (milestone 2) | Notification delivery transport beyond the dev console (SMTP is milestone 2) |
| Send credentials out-of-band via the `NotificationTransport` port | A shared contracts package (re-implemented per repo) |

This service has **no Stripe / billing logic** (it reacts to
`subscription.*`; it never talks to Stripe), **never writes the
`subscription` schema**, and **never polls instance health/usage** (that
is telemetry-worker). It authenticates to nothing in MVP — there is no
Keycloak client for the worker. The one credential it *issues* is the
per-instance bearer token, a **milestone-2** concern. See
[architecture.md](architecture.md) §"What this service does NOT do".

## MVP boundary

The MVP is delivered in two milestones; the line between them is the
deployment adapter (see [deployment-adapter.md](deployment-adapter.md)).

- **Milestone 1 — the full pipeline against the fake adapter.** The
  Valkey Streams consumer + idempotency, the `provisioning` schema, the
  convergence service + the 8-state instance machine (`pending`,
  `deploying`, `configuring`, `ready`, `suspended`, `failed`,
  `deprovisioning`, `deprovisioned`), the Taskiq retry/backoff path, the
  outbox + relay, and the full `instance.*` event catalog — all driven by
  the in-memory `FakeDeploymentAdapter` and the
  `ConsoleNotificationTransport`. Entirely unit-testable; **no Coolify, no
  real Odoo**. This unblocks platform-api's Phase 5/6 reads and the
  customer portal against a real registry.

- **Milestone 2 — real deployment.** A Coolify-API spike (the platform's
  #1 risk), then the `CoolifyAdapter` + the Odoo stack template, the
  `enforcement_snapshot` served to a real in-Odoo plugin, the per-instance
  bearer-token mechanism, `SmtpNotificationTransport`, and operator retry.

Anything described below as Coolify, SMTP, per-instance tokens, or the
served `enforcement_snapshot` is **milestone 2** and is called out as such
where it appears; treat those as design sketches, not built behavior.

## Reading order

- [architecture.md](architecture.md) — process model, code layout,
  modules, ports/adapters, the `provisioning` schema, the instance and
  task state machines, and cross-cutting concerns (consumption,
  idempotency, production, observability, migrations).
- [events.md](events.md) — the event contract: envelope, the consumed
  `subscription.*` and produced `instance.*` payloads, evolution rules,
  idempotency.
- [deployment-adapter.md](deployment-adapter.md) — the load-bearing seam:
  the `DeploymentAdapter` port, `InstanceSpec`, the fake adapter, and the
  Coolify sketch.
- [conventions.md](conventions.md) — coding standards not enforced by
  ruff.
- [local-development.md](local-development.md) — getting set up and the
  iteration loop.
- [python-style.md](python-style.md) — Python 3.14 style and design
  rules.
