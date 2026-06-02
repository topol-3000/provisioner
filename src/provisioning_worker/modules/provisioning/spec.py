"""InstanceSpec builder: converts subscription entitlements to desired deployment state.

This module contains only the `build_instance_spec` builder function.
`InstanceSpec` and `ResourceRequests` are defined in
`ports/deployment_adapter.py` (the dependency arrow always points inward:
modules → ports, never the reverse).

The M1 slug derivation uses the first 8 characters of the subscription UUID
as the hostname label — deterministic and collision-free at M1 scale (D-03).
`seat_cap` comes from `entitlement.seat_cap` (resolved by `EntitlementResolver`
from `Settings` defaults); it is NEVER derived from `payload.line_count` (D-03).
"""

from typing import TYPE_CHECKING

from provisioning_worker.ports.deployment_adapter import InstanceSpec, ResourceRequests

if TYPE_CHECKING:
    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.ports.entitlement_resolver import EntitlementPicture
    from provisioning_worker.settings import Settings

__all__ = ["build_instance_spec"]


def build_instance_spec(
    payload: SubscriptionActivatedPayload,
    settings: Settings,
    entitlement: EntitlementPicture,
) -> InstanceSpec:
    """Build an orchestrator-agnostic InstanceSpec from entitlement and settings.

    This is a pure transform — no I/O, no side effects. The spec is
    deterministic for the same inputs, which satisfies the idempotency
    requirement for retried convergence tasks.

    Slug derivation (M1): ``{subscription_id[:8]}.{instance_domain_suffix}``.
    The 8-char prefix of the subscription UUID is unique enough for M1
    scale; M2 may use a collision-resistant approach if needed.

    Args:
        payload: The ``subscription.activated`` payload from the event bus.
        settings: Application settings — supplies ``instance_domain_suffix``,
            ``odoo_base_image``, and provisioning defaults.
        entitlement: Resolved entitlement picture from ``EntitlementResolver``.
            Provides ``seat_cap``, ``module_set``, and ``resource_caps``.

    Returns:
        A frozen ``InstanceSpec`` ready for the deployment adapter.
    """
    slug = f"{str(payload.subscription_id)[:8]}.{settings.instance_domain_suffix}"
    return InstanceSpec(
        subscription_id=payload.subscription_id,
        customer_id=payload.customer_id,
        slug=slug,
        admin_email=str(payload.subscription_id),  # placeholder; real email in M2
        odoo_image=settings.odoo_base_image,
        module_set=tuple(entitlement.module_set),
        seat_cap=entitlement.seat_cap,
        resource_caps=dict(entitlement.resource_caps),
        env={},
        resources=ResourceRequests(),
    )
