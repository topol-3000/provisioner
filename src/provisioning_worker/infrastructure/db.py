"""Async SQLAlchemy 2.0 engine and session factory.

Usage in service/handler code:

    from provisioning_worker.infrastructure.db import session_scope

    async with session_scope() as session:
        ...

The engine is created lazily on first call to `get_engine()` and reused
for the lifetime of the process. Call `dispose_engine()` on shutdown.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from provisioning_worker.settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine(settings: Settings) -> AsyncEngine:
    """Build a new async SQLAlchemy engine from settings.

    Args:
        settings: Application settings providing the database URL.

    Returns:
        A configured async engine instance.
    """
    return create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        echo=False,
        future=True,
    )


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the shared engine, building it on first call.

    Args:
        settings: Optional settings; uses `get_settings()` if omitted.

    Returns:
        The process-wide shared async engine.
    """
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = _build_engine(settings or get_settings())
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return the shared session factory, building it on first call.

    Args:
        settings: Optional settings; uses `get_settings()` if omitted.

    Returns:
        The process-wide async session factory.
    """
    global _session_factory  # noqa: PLW0603
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(settings),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager for non-HTTP code paths (workers, scripts).

    Yields:
        An async SQLAlchemy session. Rolls back automatically on exception.

    Example:
        async with session_scope() as session:
            result = await session.execute(select(Instance))
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Tear down the engine on shutdown.

    Disposes the engine connection pool and resets the module-level
    singletons so a subsequent call to `get_engine()` rebuilds fresh.
    """
    global _engine, _session_factory  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
