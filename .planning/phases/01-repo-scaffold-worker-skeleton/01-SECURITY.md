---
phase: 01
slug: repo-scaffold-worker-skeleton
status: verified
threats_open: 0
asvs_level: none
created: 2026-06-01
---

# Phase 01 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Register authored at plan time (`register_authored_at_plan_time: true`); this run
> **verified** each mitigation/disposition against the implementation — it did not
> scan for new threats. ASVS level: none configured (`block_on: high`).

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| ENV → Settings | Untrusted string env vars cross into typed pydantic-settings — validation at the boundary | Config strings (DSNs, ports, adapter selectors) |
| `.env` file → git | `.env` must never be committed; `.gitignore` is the barrier | Dev/prod secrets |
| Docker build context → image | `.dockerignore` prevents `.env`/`.venv` from entering the build context | Secrets + local virtualenv |
| docker-compose `env_file` → container | Secrets arrive at runtime via `env_file: .env`, never as Docker ARG/ENV | Runtime secrets |
| OS signal → asyncio loop | SIGTERM translated into an `asyncio.Event` via `loop.add_signal_handler` | Shutdown control signal |
| Valkey stream → consumer | External stream data enters the consumer loop (M1: no-op handlers, no untrusted payload) | Event envelopes (none processed in M1) |
| localhost → `/healthz` | Unauthenticated HTTP liveness probe on `HEALTH_PORT` (internal-only in prod) | Static liveness status |
| CI runner → Docker registry | No push in M1; image stays local/CI-ephemeral — no registry credential exposure | (none — `push: false`) |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-01-01 | Information Disclosure | `.env` file | mitigate | `.gitignore:26-29` ignores `.env`/`.env.*` (with `!.env.example`); `.dockerignore:19-21` excludes `.env`, `.env.local`, `.env.example`; `.env.example` carries dev defaults only, M2 secrets commented out (`.env.example:42-48`) | closed |
| T-01-02 | Information Disclosure | Dockerfile build ARGs | accept | Sole ARG is `PYTHON_VERSION=3.14` (`Dockerfile:18`); secrets enter only via `env_file: .env` (`docker-compose.yml:25-26`); grep for secret-bearing ARG/ENV → none | closed |
| T-01-03 | Tampering | Dependency supply chain | accept | `uv.lock` pins 66 packages + 506 sha256 hashes; `pyproject.toml:17-56` pins exact CLAUDE.md §3 versions; no `[ASSUMED]`/`[SUS]` packages | closed |
| T-01-04 | Denial of Service | Consumer XREADGROUP `block=0` | mitigate | `main.py:116` uses `block=1000` (1s, non-infinite); loop guarded by `while not shutdown.is_set()` (`main.py:110`) — re-checks shutdown within ~1s | closed |
| T-01-05 | Denial of Service | Health server `web.run_app()` | mitigate | `health_server.py:33-36` uses `AppRunner` + `TCPSite` + `await shutdown.wait()`; no `web.run_app` anywhere (grep → 0) — non-blocking on the loop | closed |
| T-01-06 | Information Disclosure | Logs containing secrets | mitigate | All 9 `log.*` sites in `src/` use structured kwargs; zero f-string/`%`/`.format()` interpolation; `logging.py:60-71` structlog `ProcessorFormatter` with explicit fields (see informational note re `main.py:146`) | closed |
| T-01-07 | Elevation of Privilege | Container runs as root | mitigate | `Dockerfile:65-66` creates `platform` uid/gid 10001; `Dockerfile:80` `USER platform` before `ENTRYPOINT:87` (same evidence as T-01-12) | closed |
| T-01-08 | Spoofing | Unauthenticated `/healthz` | accept | `health_server.py:43-52` `_healthz` returns static `{"status":"ok"}` only — no instance data, settings, or secrets; intentional internal-only liveness probe | closed |
| T-01-09 | Tampering | ExceptionGroup not caught with `except*` | mitigate | `main.py:71-72` `except* Exception as eg: raise SystemExit(1) from eg.exceptions[0]`; `__main__.py:14-15` also wraps `asyncio.run` — any concern crash → non-zero exit | closed |
| T-01-10 | Denial of Service | Unreachable Postgres/Valkey at boot | mitigate | `main.py:56-57` runs `_check_postgres`/`_check_valkey` BEFORE the TaskGroup (`:66`); both `log.error(...)` + `raise` on failure (`:152-170`, `:173-193`) — fail-fast, no silent retry loop | closed |
| T-01-11 | Information Disclosure | `.env` baked into Docker image | mitigate | `.dockerignore:19-21` excludes `.env*` from the build context; `Dockerfile` never `COPY`s `.env`; secrets injected at runtime via `env_file: .env` (`docker-compose.yml:25-26`) | closed |
| T-01-12 | Elevation of Privilege | Container running as root (runtime) | mitigate | `Dockerfile:65-66` `groupadd`/`useradd` uid/gid 10001 `platform`; `Dockerfile:80` `USER platform` precedes `ENTRYPOINT:87` — runtime stage runs non-root | closed |
| T-01-13 | Information Disclosure | Docker ARG leaking secrets in layers | accept | Only ARG is `PYTHON_VERSION` (`Dockerfile:18`); no secret-bearing ARG/ENV (grep → none); `env_file:` is the sole secrets boundary | closed |
| T-01-14 | Tampering | CI pushing to registry without review | mitigate | `ci.yml:104` `push: false` in `docker/build-push-action@v6`; `:105` `load: true` keeps the image local; no registry login step or credentials in the workflow | closed |
| T-01-SC | Tampering | npm/pip/cargo installs | accept | `uv sync --frozen` in CI (`ci.yml:43,73`) and both Dockerfile stages (`Dockerfile:45,53`) — all installs from the committed, hash-pinned `uv.lock`; no unpinned installs | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

### Informational note (not a gap)

- **T-01-06 / `main.py:146`** — `log.info("taskiq broker connected", url=str(settings.valkey_url))`
  is the one log line that emits a connection URL. The mitigated threat is
  *format-string interpolation of secrets*; this is a structured kwarg, not
  interpolation, so the mitigation holds. In M1 the Valkey DSN carries no
  credentials (`redis://…`). **Revisit in M2** if an authenticated `VALKEY_URL`
  (with a password component) is introduced — at that point this line would leak
  the password into logs and should switch to a redacted host/port. No action
  required for Phase 01.

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-01-A | T-01-02, T-01-13 | No secrets are passed as Docker ARGs — the only build-time ARG is `PYTHON_VERSION` (a tool version). Secrets enter only at runtime via `env_file: .env`. Confirmed: `Dockerfile:18` is the sole ARG. | gsd-security-auditor | 2026-06-01 |
| AR-01-B | T-01-03, T-01-SC | Dependency supply chain trusted: all packages well-established, `uv.lock` pins exact versions + sha256 hashes, installed everywhere via `uv sync --frozen`. No human-gate packages present. | gsd-security-auditor | 2026-06-01 |
| AR-01-C | T-01-08 | `/healthz` is intentionally unauthenticated — an internal-only liveness probe returning a static `{"status":"ok"}` body with no sensitive data. Confirmed: `health_server.py:43-52`. | gsd-security-auditor | 2026-06-01 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-06-01 | 14 | 14 | 0 | gsd-security-auditor (`/gsd-secure-phase 01`) |
| 2026-06-02 | 14 | 14 | 0 | re-verification (`/gsd-secure-phase 01`) — short-circuit (plan-time register, 0 open) + post-commit spot-check |

**Unregistered flags:** None. All three SUMMARY.md `## Threat Flags` sections
(01-01, 01-02, 01-03) map 1:1 to registered threat IDs. No new attack surface
appeared during implementation without a corresponding threat entry.

**2026-06-02 re-verification note.** Commits landed after the 2026-06-01 audit;
the security-relevant one is `b62ce54 fix(settings): require DATABASE_URL/VALKEY_URL,
drop dev-cred defaults`. This **strengthens** the `ENV → Settings` trust boundary —
`settings.py:40-48` now declares `database_url`/`valkey_url` as required `Field`s with
no dev-credential defaults, so a missing value fails fast at boundary validation
instead of silently using a baked-in default. No threat is opened by this change.
Spot-checked the load-bearing mitigations against current code and all hold:
`.gitignore:26-29` / `.dockerignore:19-21` (`.env` excluded), `main.py:116` (`block=1000`)
+ `main.py:110` (shutdown guard), `health_server.py:33-35` (`AppRunner`/`TCPSite`, no
`run_app`), `Dockerfile:65-66,80` (uid/gid 10001 + `USER platform`), `main.py:71` /
`__main__.py:14` (`except*`), `ci.yml:104-105` (`push: false` / `load: true`). T-01-06
holds — zero f-string/`%`/`.format()` interpolation across all `log.*` sites in `src/`;
the `main.py:146` informational note still applies unchanged (revisit in M2 only).

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-06-01
