"""Deployment-adapter port for the provisioning worker.

This Protocol is the load-bearing seam between domain code and the concrete
orchestrator adapter. Only adapters import orchestrator SDKs (Coolify client,
aiohttp, etc.); domain code in `modules/` and `tasks.py` types against this
Protocol so the adapter is swappable and the convergence logic is testable
with the in-memory fake (CLAUDE.md §6.7).

`InstanceSpec` and `ResourceRequests` are defined here alongside the Protocol
so that the dependency arrow always points inward — ``modules/provisioning/spec.py``
imports ``InstanceSpec`` FROM this port, never the reverse.

Milestone 1 uses `FakeDeploymentAdapter`; milestone 2 wires `CoolifyAdapter`.
"""

import enum
from collections.abc import Mapping  # noqa: TC003 — runtime type in frozen dataclass fields
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "BackupRef",
    "CreateResult",
    "DeploymentAdapter",
    "DeploymentStatus",
    "InstanceHandle",
    "InstanceSpec",
    "ResourceRequests",
]


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceRequests:
    """CPU and memory resource requests for the Odoo container.

    Attributes:
        cpu_request: CPU request in fractional cores (e.g. ``"0.5"``).
        memory_request: Memory request in Mi notation (e.g. ``"512Mi"``).
    """

    cpu_request: str = "0.5"
    memory_request: str = "512Mi"


@dataclass(frozen=True, slots=True)
class InstanceSpec:
    """Orchestrator-agnostic desired state for one Odoo instance.

    Built by ``spec.build_instance_spec()`` from the entitlement picture and
    settings defaults. The adapter consumes this to create or update an Odoo
    deployment; it never inspects orchestrator-specific fields.

    Attributes:
        subscription_id: Platform subscription UUID (opaque to the adapter).
        customer_id: Platform customer UUID.
        slug: Derived hostname label: ``{subscription_id[:8]}.{domain_suffix}``.
        admin_email: Email address for the Odoo admin account.
        odoo_image: Docker image reference for the Odoo application.
        module_set: Tuple of Odoo module technical names to install.
        seat_cap: Maximum number of concurrent Odoo users.
        resource_caps: Resource quotas keyed by resource name.
        env: Extra environment variables to inject into the container.
        resources: Container resource requests (CPU / memory).
    """

    subscription_id: UUID
    customer_id: UUID
    slug: str
    admin_email: str
    odoo_image: str
    module_set: tuple[str, ...]
    seat_cap: int
    resource_caps: Mapping[str, int]
    env: Mapping[str, str]
    resources: ResourceRequests

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict for storage in JSONB columns.

        All fields are converted to primitive types so the result can be
        round-tripped through ``json.dumps`` / ``json.loads`` without loss.
        UUID fields become strings; ``tuple`` becomes ``list``;
        ``Mapping`` fields become plain ``dict``.

        Returns:
            A JSON-safe dictionary representation of this spec.
        """
        return {
            "subscription_id": str(self.subscription_id),
            "customer_id": str(self.customer_id),
            "slug": self.slug,
            "admin_email": self.admin_email,
            "odoo_image": self.odoo_image,
            "module_set": list(self.module_set),
            "seat_cap": self.seat_cap,
            "resource_caps": dict(self.resource_caps),
            "env": dict(self.env),
            "resources": {
                "cpu_request": self.resources.cpu_request,
                "memory_request": self.resources.memory_request,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> InstanceSpec:
        """Reconstruct an InstanceSpec from a dict produced by ``to_dict()``.

        Converts JSON-native types back to the expected Python types:
        ``list`` → ``tuple`` for ``module_set``, ``str`` → ``UUID`` for UUID
        fields, nested dict → ``ResourceRequests`` for resources.

        Args:
            data: Dictionary as returned by ``to_dict()``.

        Returns:
            A fully reconstructed, frozen ``InstanceSpec``.
        """
        return cls(
            subscription_id=UUID(data["subscription_id"]),
            customer_id=UUID(data["customer_id"]),
            slug=data["slug"],
            admin_email=data["admin_email"],
            odoo_image=data["odoo_image"],
            module_set=tuple(data["module_set"]),
            seat_cap=data["seat_cap"],
            resource_caps=dict(data["resource_caps"]),
            env=dict(data["env"]),
            resources=ResourceRequests(
                cpu_request=data["resources"]["cpu_request"],
                memory_request=data["resources"]["memory_request"],
            ),
        )


@dataclass(frozen=True, slots=True)
class InstanceHandle:
    """Opaque reference to a deployed instance in the orchestrator.

    Stored as JSONB in ``instance.deployment_handle``; the domain never
    inspects its contents — only the adapter reads and constructs it.

    Attributes:
        id: Adapter-specific identifier (e.g. Coolify project id).
    """

    id: str


@dataclass(frozen=True, slots=True)
class CreateResult:
    """Return value of ``create_instance``: handle plus generated secrets.

    Attributes:
        handle: Opaque adapter handle for subsequent operations.
        admin_password: Plaintext Odoo admin password — in-memory only (D-12).
            Never written to any DB column, log, or event.
        db_password: Plaintext database password — in-memory only (D-12).
            Never written to any DB column, log, or event.

    Note:
        Sensitive: never log, never serialize to DB. Pass admin_password and
        db_password in-memory to NotificationTransport then discard.
    """

    handle: InstanceHandle
    admin_password: str  # sensitive: never log, never serialize to DB
    db_password: str  # sensitive: never log, never serialize to DB


@dataclass(frozen=True, slots=True)
class BackupRef:
    """Reference to a backup artifact created before instance deletion.

    Attributes:
        ref: Adapter-specific backup reference (e.g. S3 key or snapshot id).
    """

    ref: str


class DeploymentStatus(enum.Enum):
    """Status of a deployed instance as reported by the orchestrator."""

    DEPLOYING = "deploying"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DeploymentAdapter(Protocol):
    """Port for the deployment orchestrator adapter.

    All methods are async and idempotent: re-running an already-applied
    operation must converge (not duplicate or fail). Adapter-level exceptions
    are translated to ``ProvisioningError`` subclasses at the adapter boundary
    and must never propagate into domain code.

    Milestone 1: ``FakeDeploymentAdapter`` (in-memory).
    Milestone 2: ``CoolifyAdapter`` (aiohttp → Coolify API).
    """

    async def create_instance(self, spec: InstanceSpec) -> CreateResult:
        """Deploy a new Odoo instance from the given spec.

        Args:
            spec: The desired state for the new instance.

        Returns:
            A ``CreateResult`` containing the adapter handle and generated secrets.

        Raises:
            DeploymentFailed: The adapter could not create the instance.
            AdapterTimeout: The operation exceeded the configured timeout.
        """
        ...

    async def update_instance(self, handle: InstanceHandle, spec: InstanceSpec) -> InstanceHandle:
        """Apply a new desired state to an existing instance.

        Args:
            handle: The adapter handle for the target instance.
            spec: The updated desired state.

        Returns:
            The (possibly updated) adapter handle.

        Raises:
            DeploymentFailed: The adapter could not apply the update.
            AdapterTimeout: The operation exceeded the configured timeout.
        """
        ...

    async def suspend_instance(self, handle: InstanceHandle) -> None:
        """Soft-suspend an instance (stop containers; keep data).

        Args:
            handle: The adapter handle for the target instance.

        Raises:
            DeploymentFailed: The adapter could not suspend the instance.
        """
        ...

    async def reinstate_instance(self, handle: InstanceHandle) -> None:
        """Resume a previously suspended instance.

        Args:
            handle: The adapter handle for the target instance.

        Raises:
            DeploymentFailed: The adapter could not reinstate the instance.
        """
        ...

    async def delete_instance(self, handle: InstanceHandle) -> BackupRef | None:
        """Permanently delete an instance, optionally producing a backup.

        Args:
            handle: The adapter handle for the target instance.

        Returns:
            A ``BackupRef`` if a backup was created, or ``None``.

        Raises:
            DeploymentFailed: The adapter could not delete the instance.
        """
        ...

    async def get_instance_status(self, handle: InstanceHandle) -> DeploymentStatus:
        """Poll the current deployment status of an instance.

        Args:
            handle: The adapter handle for the target instance.

        Returns:
            The current ``DeploymentStatus``.

        Raises:
            AdapterTimeout: The status poll exceeded the configured timeout.
        """
        ...
