"""Domain error hierarchy for the provisioning worker.

All domain-layer failures raise a subclass of `ProvisioningError`.
Adapter-level exceptions (e.g. `redis.RedisError`, `aiohttp.ClientError`)
are translated into these at the adapter boundary and must never leak into
`service.py` or `tasks.py` (CLAUDE.md §6.2, dependency rule).

Hierarchy:

    ProvisioningError
    ├── DeploymentFailed     — adapter could not provision/update/delete
    ├── AdapterTimeout       — adapter call exceeded the configured timeout
    ├── InvalidTransition    — illegal state-machine transition requested
    ├── InstanceNotFound     — no instance row for the given identifier
    └── NonRetryableError    — permanent failure; the retry loop must not retry
"""

__all__ = [
    "AdapterTimeout",
    "DeploymentFailed",
    "InstanceNotFound",
    "InvalidTransition",
    "NonRetryableError",
    "ProvisioningError",
]


class ProvisioningError(Exception):
    """Base for all domain-layer provisioning failures."""


class DeploymentFailed(ProvisioningError):
    """Adapter could not provision, update, or delete the instance.

    Optionally carries `step` (the name of the convergence step that failed)
    and `reason` (the adapter-level error message) for populating
    `instance.failed_step` / `instance.failure_reason` in the task ledger.

    Args:
        message: Human-readable description of the failure.
        step: Name of the convergence step (e.g. ``"create_instance"``).
        reason: Adapter-level detail (e.g. ``"connection refused"``).
    """

    def __init__(self, message: str, *, step: str = "", reason: str = "") -> None:
        super().__init__(message)
        self.step = step
        self.reason = reason


class AdapterTimeout(ProvisioningError):
    """Adapter call exceeded the configured timeout."""


class InvalidTransition(ProvisioningError):
    """Attempted state transition that the state machine does not allow."""


class InstanceNotFound(ProvisioningError):
    """No instance row exists for the given identifier."""


class NonRetryableError(ProvisioningError):
    """A permanent failure that must NOT be retried.

    Raised when the convergence step fails for a reason that re-running the
    task cannot fix (e.g. a missing or malformed ``task.payload`` that can
    never be parsed into an ``InstanceSpec``). The retry loop in
    ``tasks._handle_failure`` treats this as immediately terminal — it marks
    the task ``failed`` and the instance ``failed`` without burning the
    remaining attempt budget on a doomed re-run (WR-03).

    Optionally carries ``step`` and ``reason`` for populating
    ``instance.failed_step`` / ``instance.failure_reason``.

    Args:
        message: Human-readable description of the permanent failure.
        step: Name of the convergence step that failed.
        reason: Lower-level detail of why the failure is permanent.
    """

    def __init__(self, message: str, *, step: str = "", reason: str = "") -> None:
        super().__init__(message)
        self.step = step
        self.reason = reason
