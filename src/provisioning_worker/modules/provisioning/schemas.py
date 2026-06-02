"""Pydantic command and result models for the provisioning domain.

Internal schemas used at the handler → task boundary. These are distinct
from wire contracts (which use ``extra="forbid"``) — internal domain
schemas use ``frozen=True`` only and do not restrict extra fields.

`CredentialNotification` is defined in `ports/notification_transport.py`
(co-located with its transport contract); do NOT duplicate it here.
"""

from datetime import datetime  # noqa: TC003 — runtime-typed Pydantic field
from uuid import UUID  # noqa: TC003 — runtime-typed Pydantic field

from pydantic import BaseModel, ConfigDict

__all__ = [
    "CreateInstanceCommand",
]


class CreateInstanceCommand(BaseModel):
    """Command passed from the handler to the Taskiq create task.

    Carries the instance and task identifiers needed for the convergence
    job to load DB state and drive the adapter. The `InstanceSpec` is
    stored in the task's `payload` JSONB column and not repeated here.

    Attributes:
        instance_id: UUID of the `provisioning.instance` row opened by the
            handler.
        task_id: UUID of the `provisioning.provisioning_task` row opened by
            the handler.
        enqueued_at: UTC timestamp when the command was enqueued (informational).
    """

    model_config = ConfigDict(frozen=True)

    instance_id: UUID
    task_id: UUID
    enqueued_at: datetime
