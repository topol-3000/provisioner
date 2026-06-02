"""Entitlement-resolver port for building the provisioning picture.

This Protocol is the seam between the `spec.py` builder and the concrete
resolver implementation. Milestone 1 uses a placeholder resolver that reads
defaults from `Settings` (D-01, D-02, D-03); milestone 2 swaps in a
cross-schema read-back adapter without touching `spec.py`.

The resolve method is intentionally synchronous — it is a pure transform
with no I/O in M1. Milestone 2's real implementation may need to be async;
the Protocol signature will evolve at that point.
"""

from collections.abc import Mapping  # noqa: TC003 — runtime type in frozen dataclass field
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.settings import Settings

__all__ = [
    "EntitlementPicture",
    "EntitlementResolver",
]


@dataclass(frozen=True, slots=True)
class EntitlementPicture:
    """The resolved entitlement picture used to build an InstanceSpec.

    Represents the authoritative desired state for an instance's entitlements.
    In milestone 1 this is populated from Settings defaults; in milestone 2
    it will be read from the platform's entitlement schema.

    Attributes:
        module_set: Tuple of Odoo module technical names the customer is
            entitled to. Empty tuple in M1 (base modules only).
        seat_cap: Maximum number of concurrent Odoo users the subscription
            entitles. Populated from ``Settings.provisioning_default_seat_cap``
            in M1; never derived from ``line_count`` (D-03).
        resource_caps: Resource quotas keyed by resource name. Empty dict in M1.
    """

    module_set: tuple[str, ...]
    seat_cap: int
    resource_caps: Mapping[str, int]


@runtime_checkable
class EntitlementResolver(Protocol):
    """Port for resolving the entitlement picture from a subscription event.

    The resolver is called by the handler to produce an ``EntitlementPicture``
    before opening the ``instance`` and ``provisioning_task`` rows. In M1 the
    implementation is a pure function of ``settings``; the Protocol signature
    keeps the dependency injected and the adapter swappable.
    """

    def resolve(
        self,
        payload: SubscriptionActivatedPayload,
        settings: Settings,
    ) -> EntitlementPicture:
        """Resolve the entitlement picture for a newly activated subscription.

        Args:
            payload: The ``subscription.activated`` payload carrying
                subscription metadata (line_count, amounts, etc.).
            settings: Application settings — the M1 implementation reads
                ``provisioning_default_seat_cap`` and
                ``provisioning_default_resource_caps`` from here.

        Returns:
            The resolved ``EntitlementPicture`` for this subscription.
        """
        ...
