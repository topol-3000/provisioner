"""Clock port for deterministic time and sleep in the provisioning worker.

The `Clock` Protocol abstracts the system clock and `asyncio.sleep` so that
convergence tasks can be unit-tested without real time delays. `FakeClock`
is the test double: `now()` returns a fixed datetime and `sleep()` is a
no-op async def (D-06).

Both `SystemClock` and `FakeClock` are defined here to keep test setup simple
— tests import `FakeClock` from this module without needing a separate
adapters import.
"""

import asyncio
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

__all__ = [
    "Clock",
    "FakeClock",
    "SystemClock",
]


@runtime_checkable
class Clock(Protocol):
    """Port for wall-clock time and async sleep.

    Implementations must be injected (not imported at module scope) so
    the convergence task's `poll_until_healthy` loop is deterministic in
    tests. The `FakeDeploymentAdapter` transitions instances to HEALTHY
    instantly, so `FakeClock.sleep()` being a no-op produces correct test
    behaviour without actual delays.
    """

    def now(self) -> datetime:
        """Return the current UTC datetime.

        Returns:
            Current datetime with UTC timezone attached.
        """
        ...

    async def sleep(self, seconds: float) -> None:
        """Pause for the given number of seconds.

        Args:
            seconds: Number of seconds to sleep; must be non-negative.
        """
        ...


class SystemClock:
    """Production clock: delegates to the real system clock and asyncio.sleep."""

    def now(self) -> datetime:
        """Return the current UTC datetime from the system clock."""
        return datetime.now(tz=UTC)

    async def sleep(self, seconds: float) -> None:
        """Pause for `seconds` using asyncio.sleep (non-blocking).

        Args:
            seconds: Number of seconds to sleep.
        """
        await asyncio.sleep(seconds)


class FakeClock:
    """Deterministic test double for Clock.

    `now()` returns a fixed datetime; `sleep()` is a no-op so tests that
    exercise the retry/backoff path run without real delays.

    Args:
        fixed_time: The datetime to return from `now()`. Defaults to
            2026-06-01 00:00:00 UTC.
    """

    def __init__(self, fixed_time: datetime | None = None) -> None:
        self._now = fixed_time or datetime(2026, 6, 1, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the fixed datetime set at construction time."""
        return self._now

    async def sleep(self, seconds: float) -> None:
        """No-op: backoff waits return instantly in tests."""
