"""SQLAlchemy mapped classes for the `provisioning` schema.

The first domain table is `processed_event` — the idempotency ledger that
backs at-least-once dedupe on `(event_id, consumer_group)`. Later phases add
`instance`, `provisioning_task`, `enforcement_snapshot`, `instance_credential`,
and `event_outbox` to this same `Base.metadata`.

`Base.metadata` is imported by `migrations/provisioning/env.py` so Alembic
autogenerate can diff future tables against the mapped models.
"""

from datetime import (
    datetime,  # noqa: TC003 — runtime import: SQLAlchemy resolves Mapped[datetime] at mapping time
)
from typing import ClassVar, Final

from sqlalchemy import TIMESTAMP, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = ["Base", "ProcessedEvent"]

_SCHEMA: Final[str] = "provisioning"


class Base(DeclarativeBase):
    """Declarative base for every mapped class in the `provisioning` schema."""


class ProcessedEvent(Base):
    """Idempotency ledger row — one row per `(event_id, consumer_group)` pair.

    Inserted in the same transaction as any handler side-effects, and before
    the inbound message is `XACK`-ed. The composite primary key makes
    duplicate delivery a safe no-op: a re-delivered event conflicts on INSERT,
    the transaction rolls back, and the dedupe guard short-circuits on the
    re-query. A crash after commit but before `XACK` re-delivers the message,
    and the existing row causes the handler to skip the side-effects.
    """

    __tablename__ = "processed_event"
    __table_args__: ClassVar[dict[str, str]] = {"schema": _SCHEMA}

    event_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    consumer_group: Mapped[str] = mapped_column(Text, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
