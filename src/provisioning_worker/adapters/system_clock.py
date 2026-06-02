"""System clock adapter — re-exports from ports/clock.py.

``SystemClock`` and ``FakeClock`` are defined in ``ports/clock.py``
(Plan 01) alongside the ``Clock`` Protocol so that tests can import the
test double from the ports module without an adapters import. This
adapter module re-exports both classes for callers that prefer the
adapters namespace (``main.py`` wiring, for example).

Usage::

    from provisioning_worker.adapters.system_clock import SystemClock, FakeClock
"""

from provisioning_worker.ports.clock import FakeClock, SystemClock

__all__ = ["FakeClock", "SystemClock"]
