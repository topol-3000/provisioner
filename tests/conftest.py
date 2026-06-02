"""Test configuration and shared fixtures.

Unit tests (the default ``make test`` run, ``-m "not integration"``) need no
fixtures from here — they use mocks. The fixtures below back the
``@pytest.mark.integration`` suite: a session-scoped Postgres 18 container, an
async engine bound to it (with the ``provisioning`` schema and the mapped
tables created), and a function-scoped session that rolls back after each test
for isolation.
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
    platform-infra's init SQL), then ``Base.metadata`` builds the mapped
    tables.
    """
    url = postgres_container.get_connection_url().replace("psycopg2", "psycopg")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS provisioning"))
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a function-scoped async session; roll back after the test.

    Rolling back after yield keeps tests isolated even though they share the
    session-scoped engine and tables.
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
