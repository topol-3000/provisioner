"""In-memory fake deployment adapter for milestone-1 testing.

``FakeDeploymentAdapter`` implements the ``DeploymentAdapter`` Protocol
without any real orchestrator calls. It is the test double for the entire
create-path convergence pipeline (PROV-02, PROV-04).

Fault-injection mode (used in PROV-04 tests)::

    adapter = FakeDeploymentAdapter(fail_on={"create"}, fail_count=1)
    # First call raises DeploymentFailed; second call succeeds.

Secrets are stable per-spec (D-11): for the same spec, ``create_instance``
always returns the same ``admin_password`` and ``db_password`` so that an
idempotent re-run sees consistent credentials.

Instances are tracked by ``handle.id`` in an internal dict so
``get_instance_status`` reflects the adapter state after creation.
"""

from dataclasses import dataclass, field

import structlog

from provisioning_worker.ports.deployment_adapter import (
    BackupRef,
    CreateResult,
    DeploymentStatus,
    InstanceHandle,
    InstanceSpec,
)
from provisioning_worker.shared.errors import DeploymentFailed

__all__ = ["FakeDeploymentAdapter"]

log = structlog.get_logger(__name__)


@dataclass
class FakeDeploymentAdapter:
    """In-memory adapter implementing the ``DeploymentAdapter`` Protocol.

    All methods are async, idempotent, and orchestrator-agnostic —
    satisfying the same contract as the milestone-2 ``CoolifyAdapter``
    so convergence logic is exercised identically against either.

    Args:
        fail_on: Set of operation names that should fail (e.g. ``{"create"}``).
        fail_count: Number of times each operation in ``fail_on`` fails
            before succeeding. Defaults to 1.
    """

    fail_on: set[str] = field(default_factory=set)
    fail_count: int = 1
    _call_counts: dict[str, int] = field(default_factory=dict, init=False)
    _instances: dict[str, DeploymentStatus] = field(default_factory=dict, init=False)

    async def create_instance(self, spec: InstanceSpec) -> CreateResult:
        """Deploy a new Odoo instance from the given spec.

        On success, registers the instance as HEALTHY and returns stable
        credentials (D-11: same spec → same credentials, idempotent re-run).

        Args:
            spec: The desired state for the new instance.

        Returns:
            A ``CreateResult`` with a stable handle and stable secrets.

        Raises:
            DeploymentFailed: If ``"create"`` is in ``fail_on`` and the
                call count is below ``fail_count``.
        """
        count = self._call_counts.get("create", 0)
        if "create" in self.fail_on and count < self.fail_count:
            self._call_counts["create"] = count + 1
            log.warning("fake adapter: injecting create failure", attempt=count + 1)
            raise DeploymentFailed(
                "injected failure",
                step="create",
                reason="fault injection",
            )
        self._call_counts["create"] = count + 1
        handle = InstanceHandle(id=str(spec.subscription_id))
        self._instances[handle.id] = DeploymentStatus.HEALTHY
        log.info("fake adapter: create_instance succeeded", handle_id=handle.id)
        return CreateResult(
            handle=handle,
            admin_password="test-password-stable",  # noqa: S106 — stable fake secret (D-11)
            db_password="test-db-password-stable",  # noqa: S106 — stable fake secret (D-11)
        )

    async def update_instance(self, handle: InstanceHandle, spec: InstanceSpec) -> InstanceHandle:
        """Apply a new desired state to an existing instance (stub).

        Args:
            handle: The adapter handle for the target instance.
            spec: The updated desired state.

        Returns:
            The same handle (fake adapter does not reassign handles).
        """
        log.info("fake adapter: update_instance (no-op)", handle_id=handle.id)
        return handle

    async def suspend_instance(self, handle: InstanceHandle) -> None:
        """Soft-suspend an instance (stub — no-op in the fake).

        Args:
            handle: The adapter handle for the target instance.
        """
        log.info("fake adapter: suspend_instance (no-op)", handle_id=handle.id)
        if handle.id in self._instances:
            self._instances[handle.id] = DeploymentStatus.UNKNOWN

    async def reinstate_instance(self, handle: InstanceHandle) -> None:
        """Resume a previously suspended instance (stub — no-op in the fake).

        Args:
            handle: The adapter handle for the target instance.
        """
        log.info("fake adapter: reinstate_instance (no-op)", handle_id=handle.id)
        if handle.id in self._instances:
            self._instances[handle.id] = DeploymentStatus.HEALTHY

    async def delete_instance(self, handle: InstanceHandle) -> BackupRef | None:
        """Permanently delete an instance (stub — no backup in the fake).

        Args:
            handle: The adapter handle for the target instance.

        Returns:
            ``None`` — the fake adapter never produces a backup.
        """
        log.info("fake adapter: delete_instance (no-op)", handle_id=handle.id)
        self._instances.pop(handle.id, None)
        return None

    async def get_instance_status(self, handle: InstanceHandle) -> DeploymentStatus:
        """Poll the current deployment status.

        Returns ``HEALTHY`` for known handles (created successfully),
        ``DEPLOYING`` as the safe default for unknown handles.

        Args:
            handle: The adapter handle for the target instance.

        Returns:
            The current ``DeploymentStatus``.
        """
        return self._instances.get(handle.id, DeploymentStatus.DEPLOYING)
