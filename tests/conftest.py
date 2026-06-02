"""Test configuration and shared fixtures.

Unit tests (the default ``make test`` run, ``-m "not integration"``) need no
fixtures from here — they use mocks. The fixtures below back the
``@pytest.mark.integration`` suite: a session-scoped Postgres 18 container, an
async engine bound to it (with the ``provisioning`` schema and the mapped
tables created), and a function-scoped session that rolls back after each test
for isolation.

Phase 3 additions:
- ENUM type creation before ``Base.metadata.create_all`` (required because ORM
  columns use ``create_type=False``).
- ``fake_clock`` fixture for ``FakeClock`` (deterministic time in unit tests).
- ``in_memory_broker`` fixture stub (full wiring in Plan 02 once adapters are
  available).
"""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from provisioning_worker.modules.provisioning.models import Base
from provisioning_worker.ports.clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Start a real Postgres 18 container for the integration suite."""
    with PostgresContainer("postgres:18") as container:
        yield container


@pytest.fixture(scope="session")
async def pg_engine(postgres_container: PostgresContainer) -> AsyncIterator[AsyncEngine]:
    """Yield an async engine bound to the container, schema + tables created.

    The testcontainers connection URL names the ``psycopg2`` driver; this repo
    pins ``psycopg`` (v3), so the driver token is rewritten. The
    ``provisioning`` schema is created first (it exists empty in real infra via
    platform-infra's init SQL), then ENUM types are created explicitly (because
    ORM columns use ``create_type=False``), and finally ``Base.metadata`` builds
    the mapped tables.
    """
    url = postgres_container.get_connection_url().replace("psycopg2", "psycopg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS provisioning"))
        # ENUM types must be created before create_all because ORM columns use
        # create_type=False (T-3-01 mitigation — avoids "type already exists" errors).
        await conn.execute(
            text(
                "CREATE TYPE IF NOT EXISTS provisioning.instance_status AS ENUM ("
                "'pending', 'deploying', 'configuring', 'ready', "
                "'suspended', 'failed', 'deprovisioning', 'deprovisioned')"
            )
        )
        await conn.execute(
            text(
                "CREATE TYPE IF NOT EXISTS provisioning.task_type AS ENUM ("
                "'create', 'update', 'suspend', 'reinstate', 'delete')"
            )
        )
        await conn.execute(
            text(
                "CREATE TYPE IF NOT EXISTS provisioning.task_status AS ENUM ("
                "'pending', 'running', 'succeeded', 'failed')"
            )
        )
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a function-scoped async session; truncate tables after the test.

    Tests that commit (the idempotency guard commits its ``processed_event``
    row) make rows durable, so a plain rollback is not enough to isolate them.
    After each test the session rolls back any open transaction and truncates
    the mapped tables so the next test starts from an empty schema.
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
            for table in reversed(Base.metadata.sorted_tables):
                await session.execute(
                    text(f'TRUNCATE TABLE "{table.schema}"."{table.name}" CASCADE')
                )
            await session.commit()


@pytest.fixture
def fake_clock() -> FakeClock:
    """FakeClock with a fixed time; sleep() is a no-op.

    Injects deterministic time into convergence tasks so retry/backoff
    paths execute without real delays in unit tests.
    """
    return FakeClock()
