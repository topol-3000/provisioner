"""Domain error hierarchy for the provisioning worker.

All domain-layer failures raise a subclass of `ProvisioningError`.
Adapter-level exceptions (e.g. `redis.RedisError`, `aiohttp.ClientError`)
are translated into these at the adapter boundary and must never leak into
`service.py` or `tasks.py` (CLAUDE.md §6.2, dependency rule).

Hierarchy:

    ProvisioningError
    ├── DeploymentFailed   — adapter could not provision/update/delete
    ├── AdapterTimeout     — adapter call exceeded the configured timeout
    ├── InvalidTransition  — illegal state-machine transition requested
    └── InstanceNotFound   — no instance row for the given identifier
"""

__all__ = [
    "AdapterTimeout",
    "DeploymentFailed",
    "InstanceNotFound",
    "InvalidTransition",
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
