"""Produced instance.* event payload models.

Re-implemented per repo against ``docs/events.md §Events this service PRODUCES``
(no shared contracts package — CLAUDE.md §6.2). Platform-api re-implements
the consumer side against the same doc; drift is caught by review and the
schema-evolution discipline in ``docs/events.md``.

Phase 4 scope: ``instance.provisioned`` only. Remaining catalog
(``updated``, ``suspended``, ``reinstated``, ``failed``, ``deprovisioned``)
is added in Phase 5.

Security: no credentials in any produced payload (D-12 Phase 3, D-09 Phase 4).
Credentials flow only through ``NotificationTransport`` and are discarded
in-memory after delivery.
"""

from datetime import datetime  # noqa: TC003 — runtime-typed Pydantic field
from uuid import UUID  # noqa: TC003 — runtime-typed Pydantic field

from pydantic import BaseModel, ConfigDict

__all__ = ["InstanceProvisionedPayload"]


class InstanceProvisionedPayload(BaseModel):
    """Payload for ``instance.provisioned`` (v1).

    Emitted when an Odoo instance first reaches ``ready`` status.
    Copied verbatim from ``docs/events.md §Events this service PRODUCES``
    (CLAUDE.md §6.2 — no shared contracts package). No credentials
    (D-12 Phase 3, D-09 Phase 4): admin_password is delivered via
    ``NotificationTransport`` only and never enters any event or log.

    Attributes:
        instance_id: UUID of the provisioned instance.
        subscription_id: UUID of the triggering subscription (opaque, from platform-api).
        customer_id: UUID of the customer who owns the subscription.
        hostname: Fully-qualified hostname, e.g. ``{slug}.{instance_domain_suffix}``.
        url: Full HTTPS URL, e.g. ``https://{hostname}``.
        admin_email: Email address of the Odoo admin user.
        snapshot_version: Version of the enforcement snapshot written at ``configuring``
            (always 1 for a freshly provisioned instance).
        provisioned_at: UTC wall-clock when the instance reached ``ready`` (``ready_at``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: UUID
    subscription_id: UUID
    customer_id: UUID
    hostname: str
    url: str
    admin_email: str
    snapshot_version: int
    provisioned_at: datetime
