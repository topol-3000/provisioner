"""Unit and integration tests for provisioning Taskiq tasks.

Wave 2 — unit-only assertions (backoff formula, enforcement snapshot delegation).
Full integration assertions (create path, fault injection, credential delivery)
arrive in Plan 03-04 (Wave 3).
"""

import pytest

from provisioning_worker.modules.provisioning.tasks import _compute_backoff_seconds
from provisioning_worker.settings import Settings


def _test_settings() -> Settings:
    """Return a minimal Settings instance for unit tests."""
    return Settings(
        database_url="postgresql+psycopg://user:pass@localhost/test",
        database_url_sync="postgresql+psycopg://user:pass@localhost/test",
        valkey_url="redis://localhost:6379/0",
    )


def test_backoff_formula_attempt_0() -> None:
    """Backoff at attempt 0 equals base delay (2.0 seconds)."""
    settings = _test_settings()
    result = _compute_backoff_seconds(attempt_count=0, settings=settings)
    assert result == 2.0


def test_backoff_formula_attempt_1() -> None:
    """Backoff at attempt 1 is base * multiplier^1 = 4.0 seconds."""
    settings = _test_settings()
    result = _compute_backoff_seconds(attempt_count=1, settings=settings)
    assert result == 4.0


def test_backoff_formula_capped() -> None:
    """Backoff at attempt 10 is capped at provisioning_cap_s (60.0 seconds)."""
    settings = _test_settings()
    result = _compute_backoff_seconds(attempt_count=10, settings=settings)
    assert result == 60.0


def test_backoff_formula_respects_cap() -> None:
    """Backoff never exceeds cap regardless of attempt count."""
    settings = _test_settings()
    for attempt in range(20):
        result = _compute_backoff_seconds(attempt_count=attempt, settings=settings)
        assert result <= settings.provisioning_cap_s


def test_create_instance_task_is_registered() -> None:
    """create_instance_task is a registered Taskiq decorated task with kiq method."""
    from provisioning_worker.modules.provisioning.tasks import create_instance_task

    assert hasattr(create_instance_task, "kiq"), "create_instance_task must have .kiq() method"
    assert hasattr(create_instance_task, "broker"), "create_instance_task must have .broker"


def test_placeholder() -> None:
    """Placeholder test — full integration assertions added in Wave 3 (Plan 03-04)."""
    pytest.skip("full integration assertions in Wave 3 — Plan 03-04")
