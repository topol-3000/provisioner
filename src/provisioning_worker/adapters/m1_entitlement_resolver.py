"""M1 placeholder entitlement resolver.

``DefaultEntitlementResolver`` is the milestone-1 implementation of the
``EntitlementResolver`` Protocol. It builds an ``EntitlementPicture``
entirely from ``Settings`` defaults — it never reads ``payload.line_count``
or any other subscription payload field to derive the seat cap (D-03).

Milestone 2 will swap this adapter for a cross-schema read-back resolver
that fetches the actual entitled module set, seat cap, and resource caps
from the platform's entitlement schema. The swap is a one-line wiring
change in ``main.py`` — ``spec.py`` and the convergence service are
unchanged (D-02 design invariant).

M1 entitlement picture:
- ``module_set``: empty tuple (base Odoo modules only).
- ``seat_cap``: ``settings.provisioning_default_seat_cap`` (D-03).
- ``resource_caps``: ``settings.default_resource_caps`` (parsed from the
  ``provisioning_default_resource_caps`` JSON-string setting; empty by
  default). WR-02 fix — the setting is no longer dead configuration.
"""

from typing import TYPE_CHECKING

from provisioning_worker.ports.entitlement_resolver import EntitlementPicture

if TYPE_CHECKING:
    from provisioning_worker.events.subscription import SubscriptionActivatedPayload
    from provisioning_worker.settings import Settings

__all__ = ["DefaultEntitlementResolver"]


class DefaultEntitlementResolver:
    """M1 placeholder: builds an ``EntitlementPicture`` from ``Settings`` only.

    Intentionally ignores ``payload.line_count`` and all other payload fields
    when computing the entitlement picture (D-03). This is a deliberate design
    choice: the M1 placeholder spec avoids cross-schema coupling by reading
    only the ``Settings`` defaults.

    Comment: M1 placeholder — uses settings default, ignores payload.line_count.
    """

    def resolve(
        self,
        payload: SubscriptionActivatedPayload,
        settings: Settings,
    ) -> EntitlementPicture:
        """Resolve the entitlement picture from settings defaults.

        M1 placeholder: uses settings default, ignores payload.line_count.
        The returned ``seat_cap`` is always
        ``settings.provisioning_default_seat_cap``, never derived from
        ``payload.line_count`` (D-03).

        Args:
            payload: The ``subscription.activated`` payload. Its fields are
                intentionally ignored in M1; the resolver reads only settings.
            settings: Application settings providing the M1 defaults.

        Returns:
            An ``EntitlementPicture`` with empty module set, settings-sourced
            seat cap, and the settings-sourced default resource caps.
        """
        # M1 placeholder: uses settings default, ignores payload.line_count (D-03)
        # WR-02: resource_caps now come from settings.default_resource_caps
        # (parsed from the provisioning_default_resource_caps JSON setting)
        # instead of being hard-coded to {}.
        return EntitlementPicture(
            module_set=(),
            seat_cap=settings.provisioning_default_seat_cap,
            resource_caps=settings.default_resource_caps,
        )
