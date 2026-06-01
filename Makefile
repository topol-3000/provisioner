# provisioner / Makefile
# ======================
# Wrapper around uv + alembic + docker compose. Run `make help` for the list.

SHELL := /bin/bash
UV    := uv

ifneq (,$(wildcard ./.env))
include .env
export
endif

# Default: print help when invoked without a target.
.DEFAULT_GOAL := help

# Phony declarations
.PHONY: help install dev sync lock \
        run \
        lint lint-fix format format-check \
        test test-cov test-integration \
        migrate migrate-down revision \
        psql shell \
        infra-up infra-down infra-ps \
        docker-build docker-run docker-up docker-down docker-logs docker-migrate up \
        clean check

help:  ## Print available targets.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------

install:  ## Install runtime deps only (uv sync --frozen).
	$(UV) sync --frozen

dev:  ## Install runtime + dev deps.
	$(UV) sync --frozen --extra dev

sync:  ## Re-sync the venv against the lockfile (after pulling).
	$(UV) sync --frozen --extra dev

lock:  ## Regenerate uv.lock (commit the result).
	$(UV) lock

# ---------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------

run:  ## Run the worker locally (python -m provisioning_worker).
	$(UV) run python -m provisioning_worker

# ---------------------------------------------------------------------
# Lint / format
# ---------------------------------------------------------------------

lint:  ## ruff check (no fix).
	$(UV) run ruff check .

lint-fix:  ## ruff check --fix.
	$(UV) run ruff check --fix .

format:  ## ruff format in place.
	$(UV) run ruff format .

format-check:  ## ruff format --check (CI uses this).
	$(UV) run ruff format --check .

check: lint format-check  ## All static checks (CI gate).

# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

test:  ## Run unit tests (no integration marker).
	$(UV) run pytest -m "not integration"

test-cov:  ## Run unit tests with coverage report.
	$(UV) run pytest --cov=provisioning_worker --cov-report=term-missing --cov-report=xml -m "not integration"

test-integration:  ## Run integration tests (requires Docker for testcontainers).
	$(UV) run pytest -m integration

# ---------------------------------------------------------------------
# Migrations — single provisioning tree
# ---------------------------------------------------------------------

migrate:  ## Apply the provisioning Alembic tree.
	$(UV) run alembic -n provisioning upgrade head

migrate-down:  ## Downgrade the provisioning tree one step.
	$(UV) run alembic -n provisioning downgrade -1

# Pass `name="..."` to set the message.
# Example: make revision name="add instance table"
revision:  ## New revision under migrations/provisioning (use name=...).
	$(UV) run alembic -n provisioning revision --autogenerate -m "$(name)"

# ---------------------------------------------------------------------
# DB shells
# ---------------------------------------------------------------------

psql:  ## Open a psql shell on the platform DB (via the host Postgres, not Docker).
	psql "$${DATABASE_URL_SYNC/+psycopg/}"

shell:  ## Open a Python REPL with the package importable.
	$(UV) run python

# ---------------------------------------------------------------------
# platform-infra (sibling repo)
# ---------------------------------------------------------------------
# Targets delegate to ../platform-infra so we don't have to `cd` between
# repos during a dev loop. Postgres and Valkey must be up before the
# worker container has anything to talk to.

infra-up:  ## Bring up Postgres/Valkey via platform-infra.
	$(MAKE) -C ../platform-infra up

infra-down:  ## Stop platform-infra services (data preserved).
	$(MAKE) -C ../platform-infra down

infra-ps:  ## Show platform-infra service status.
	$(MAKE) -C ../platform-infra ps

# ---------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------
# Driven by docker-compose.yml. The compose file attaches the worker
# service to the external `platform-net` bridge created by
# ../platform-infra and overrides DATABASE_URL / VALKEY_URL env vars so
# the container reaches infra by service name (platform-postgres, etc.)
# instead of `localhost`. The infra stack must be up first (`make
# infra-up`) so the network exists.

COMPOSE := docker compose

docker-build:  ## Build the worker image via docker compose.
	$(COMPOSE) build worker

docker-run:  ## Run the worker service in the foreground (Ctrl-C to stop).
	$(COMPOSE) up worker

docker-up:  ## Run the worker service detached.
	$(COMPOSE) up -d worker

docker-down:  ## Stop and remove the worker service container.
	$(COMPOSE) down

docker-logs:  ## Tail logs for the worker service.
	$(COMPOSE) logs -f worker

docker-migrate:  ## Apply the provisioning Alembic tree from a one-shot container.
	$(COMPOSE) run --rm --entrypoint alembic worker -n provisioning upgrade head

up: infra-up docker-build docker-migrate docker-run  ## First-time dev loop: infra -> build -> migrate -> run.

# ---------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------

clean:  ## Remove caches.
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
