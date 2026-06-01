---
phase: "01-repo-scaffold-worker-skeleton"
plan: 3
subsystem: "container-ci"
tags: ["dockerfile", "docker-compose", "github-actions", "uv", "non-root", "platform-net"]
dependency_graph:
  requires:
    - "01-01: pyproject.toml, uv.lock, Makefile, alembic.ini"
    - "01-02: python -m provisioning_worker entrypoint, src/ tree"
  provides:
    - "Dockerfile: two-stage build, python:3.14-slim-trixie, ENTRYPOINT python -m provisioning_worker, EXPOSE 8001, non-root platform/10001"
    - "docker-compose.yml: worker service on external platform-net, env_file .env, DNS overrides, port 8001"
    - ".github/workflows/ci.yml: PR gate lint + unit-tests + build-only Docker, no push"
  affects:
    - "CI pipeline: every PR now runs ruff + pytest + docker build"
    - "make docker-build / docker-up / docker-migrate targets now functional"
tech_stack:
  added:
    - "docker/build-push-action@v6 (build-only, no push in M1)"
    - "docker/setup-buildx-action@v3"
    - "astral-sh/setup-uv@v6"
  patterns:
    - "Two-stage Dockerfile (builder/runtime) with uv:0.11 pinned installer"
    - "WORKDIR /app in both stages (shebang validity — RESEARCH Pitfall 6)"
    - "Non-root uid/gid 10001 platform user in runtime stage"
    - "env_file secrets boundary (no Docker ARG secrets, no hardcoded ENV secrets)"
    - "External platform-net network (platform-infra owns lifecycle)"
    - "D-15: testcontainers off the PR gate; build job needs: [lint]"
key_files:
  created:
    - "Dockerfile"
    - "docker-compose.yml"
    - ".github/workflows/ci.yml"
  modified: []
decisions:
  - "D-12: Two-stage Dockerfile, python:3.14-slim-trixie, non-root uid/gid 10001, ENTRYPOINT python -m provisioning_worker, EXPOSE 8001, HEALTHCHECK /healthz"
  - "D-13: docker-compose on external platform-net, in-network DNS overrides, no Keycloak"
  - "D-14: docker-migrate one-shot single provisioning tree"
  - "D-15: CI PR gate: make check + make test + build-only Docker, no push, testcontainers off the gate"
metrics:
  duration_seconds: 420
  completed_date: "2026-06-01"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 0
---

# Phase 01 Plan 03: Container + CI Summary

**One-liner:** Two-stage Dockerfile (uv:0.11, python:3.14-slim-trixie, non-root platform/10001, ENTRYPOINT python -m provisioning_worker, EXPOSE 8001), docker-compose.yml on external platform-net, and GitHub Actions CI (lint + unit-tests + build-only, no push, no testcontainers gate per D-15).

## What Was Built

This plan delivers the container layer and CI contract for the provisioning-worker. The image
builds from the Phase 01-02 source in a two-stage Docker build mirroring platform-api's
pattern with the worker-specific deltas applied. The CI workflow establishes the PR gate.

### Task 1: Dockerfile and docker-compose.yml

- `Dockerfile`: Two-stage build mirroring `../platform-api/Dockerfile` exactly, with deltas:
  - `ENTRYPOINT ["python", "-m", "provisioning_worker"]` (no Granian, no ASGI)
  - `EXPOSE 8001` (worker health port, not platform-api's 8000)
  - `HEALTHCHECK` targets `http://127.0.0.1:8001/healthz` (port 8001)
  - `alembic.ini` + `migrations/` copied into runtime stage for `docker-migrate` one-shot
  - `WORKDIR /app` in both stages (D-12 / RESEARCH Pitfall 6: shebang validity)
  - Non-root user `platform` uid/gid 10001 via `groupadd --system --gid 10001`
  - No `CMD` (worker takes no positional args — unlike granian which uses `--host/--port`)
- `docker-compose.yml`: Mirrors `../platform-api/docker-compose.yml` with deltas:
  - `name: provisioning-worker`, service key `worker`, image `provisioning-worker:dev`
  - Port `${HEALTH_HOST_PORT:-8001}:8001`
  - No `KEYCLOAK_BASE_URL` override (worker uses no Keycloak realm)
  - `env_file: .env` as secrets boundary (no Docker ARG secrets)
  - `external: true` on `platform-net` (platform-infra owns lifecycle)
- Verification: `docker build --no-cache -t provisioning-worker:test .` → exit 0
  - `User: platform`, `Entrypoint: ['python', '-m', 'provisioning_worker']`, `ExposedPorts: ['8001/tcp']`

### Task 2: GitHub Actions CI workflow

- `.github/workflows/ci.yml`: Three jobs mirroring `../platform-api/.github/workflows/ci.yml`:
  - `lint`: `uv sync --frozen --extra dev` → `ruff check .` + `ruff format --check .`
  - `test`: unit tests only (`-m "not integration"`, `--cov=provisioning_worker`) — no testcontainers step (D-15)
  - `build`: `docker/setup-buildx-action@v3` + `docker/build-push-action@v6` with `push: false`, `load: true`, tag `provisioning-worker:ci-{sha}`, GHA layer cache
  - `build` job `needs: [lint]` (skip Docker burn on syntax-broken PRs)
  - `env`: `PYTHON_VERSION: "3.14"`, `UV_VERSION: "0.11.14"`
  - `concurrency`: cancel-in-progress on pull_request
- Verification: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` → no error

## Verification Evidence

1. `docker build --no-cache -t provisioning-worker:test .` → exit 0
2. `docker inspect provisioning-worker:test` → User: platform, Entrypoint: ['python', '-m', 'provisioning_worker'], ExposedPorts: ['8001/tcp']
3. `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` → no error (YAML valid)
4. `grep "push: false" .github/workflows/ci.yml` → found
5. `grep "not integration" .github/workflows/ci.yml` → found (in test job run command)
6. `grep "provisioning-worker:ci-" .github/workflows/ci.yml` → found
7. `make check` → All checks passed; 23 files already formatted
8. `make test` → 10 passed in 0.81s

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | 69d5036 | chore(01-03): add Dockerfile and docker-compose.yml (Task 1) |
| Task 2 | 6f4e508 | chore(01-03): add GitHub Actions CI workflow (Task 2) |

## Deviations from Plan

None — plan executed exactly as written.

The Dockerfile was authored from the platform-api analog with all enumerated deltas applied
per PATTERNS.md §Dockerfile. The docker-compose.yml was authored from the platform-api analog
with all enumerated deltas applied per PATTERNS.md §docker-compose.yml. The CI workflow was
authored from the platform-api analog with the D-15 deltas applied (no testcontainers step,
worker coverage source, provisioning-worker image tag).

Docker was available in the execution environment, so the build smoke test ran against the real
Docker daemon (no static-analysis fallback needed).

## Known Stubs

None — this plan is container/CI infrastructure with no Python source code.

## Threat Flags

T-01-11 mitigated: `.dockerignore` (created in Plan 01) excludes `.env` and `.env.*` from
the build context. `env_file: .env` in docker-compose.yml passes secrets at runtime.

T-01-12 mitigated: Runtime stage creates user `platform` uid/gid 10001 and sets `USER platform`
before `ENTRYPOINT`. Container runs as non-root.

T-01-13 accepted: No secrets are passed as Docker ARGs. Only `ARG PYTHON_VERSION=3.14` appears
(a tool version, not a secret). `env_file` is the secrets boundary.

T-01-14 mitigated: `push: false` in `docker/build-push-action@v6`. No registry credentials
configured in M1. Build proves the image builds from committed source; nothing more.

## Self-Check: PASSED

- Dockerfile: FOUND
- docker-compose.yml: FOUND
- .github/workflows/ci.yml: FOUND
- 69d5036: FOUND in git log
- 6f4e508: FOUND in git log

## Human-Verify Checkpoint (Task 3) — APPROVED

Resolved 2026-06-01 by user ("Approve & continue"). Accepted on build-time evidence:

- Orchestrator re-inspected the built image: `Entrypoint=[python -m provisioning_worker]`,
  `User=platform` (non-root), `ExposedPorts={8001/tcp}`, healthcheck hits `/healthz` on 8001.
- `docker build` exit 0; `make check` + `make test` (10 passed) green post-Wave-3.

Deferred (not blocking — runtime-against-live-infra, user's discretion):
- `make docker-up` against a running `platform-infra` → four boot lines → `curl /healthz` → `make docker-down`.
- Push branch to confirm the GitHub Actions PR gate (lint + test + build) goes green.
