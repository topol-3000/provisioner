# Seed: Milestone 2 — Coolify & real deployment

**Captured:** 2026-06-01 (during project init)
**Promote when:** Milestone 1 (the fake-adapter pipeline) is complete and
verified — the convergence logic, registry, idempotency, and `instance.*` event
contract are proven against `FakeDeploymentAdapter`.

Milestone 1 deliberately stops at the in-memory fake adapter so the whole
pipeline is correct before the real-orchestrator risk is taken on. Milestone 2
swaps the fake for the real thing behind the **unchanged** `DeploymentAdapter`
port (`docs/deployment-adapter.md`).

## Work items

1. **Coolify-API spike (do this first).** Coolify is not configured anywhere in
   the platform yet (`platform-infra` lists it under "What's NOT here yet"; no
   API base URL, token, or Odoo stack template exists). Prototype the riskiest
   operations against the live API before committing to the adapter shape —
   PRD §16's #1 risk is exactly Coolify gaps around volumes, networking,
   per-instance Postgres, and suspension. Spike answers: how an `InstanceSpec`
   maps onto Coolify resources, the handle shape, health → `DeploymentStatus`,
   and where the Odoo base image lives.
2. **`CoolifyAdapter`** (aiohttp) implementing the port, gated by
   `DEPLOYMENT_ADAPTER=coolify`; config `COOLIFY_API_URL` / `COOLIFY_API_TOKEN`.
3. **Odoo stack template** — the Odoo container + per-instance Postgres +
   volumes + route for `{slug}.{INSTANCE_DOMAIN_SUFFIX}`, and the Odoo base
   image build (with the `platform_entitlements` plugin) + registry (likely
   GHCR). Decide: per-instance Postgres in the shared cluster vs Coolify-managed.
4. **Per-instance bearer token** — mint at provisioning time, store hashed in
   `provisioning.instance_credential`, 24h rotation grace. **Coordinate the
   exact validation mechanism with platform-api's `plugin_api`
   (`require_instance`)** before building — opaque-hash vs signed-JWT is an open
   joint decision (`docs/architecture.md` §Authentication).
5. **Serve `enforcement_snapshot` to a live plugin** — the table + computation
   ship in M1; M2 is the live Odoo plugin polling platform-api with
   `If-None-Match` on `version`.
6. **`SmtpNotificationTransport`** — replace the dev console transport;
   `SMTP_*` config.
7. **Operator-triggered retry** — `platform-api`'s
   `POST /api/ops/instances/{id}/retry` needs a signalling channel to the worker
   (platform-api writing `provisioning_task`, or an ops event stream). Design
   this jointly; M1 ships automatic backoff retry only.

## Also deferred (post-M2 / later)

- Hard suspension (scale-to-zero) behind a policy flag; M1/M2 are soft only.
- Resource-cap enforcement beyond seats.
- A dead-letter stream for poison messages (M1 logs + acks them).
- A second deployment adapter (Kubernetes/AWS) to prove the abstraction (PRD
  Phase 3).
