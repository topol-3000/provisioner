"""Unit tests for the provisioning ORM models.

Pins the `ProcessedEvent` idempotency-ledger mapping: table name, schema,
column set, and the composite primary key on `(event_id, consumer_group)`.
"""

from provisioning_worker.modules.provisioning.models import Base, ProcessedEvent


def test_processed_event_tablename() -> None:
    """ProcessedEvent maps to the `processed_event` table."""
    assert ProcessedEvent.__tablename__ == "processed_event"


def test_processed_event_schema() -> None:
    """ProcessedEvent lives in the `provisioning` schema."""
    assert ProcessedEvent.__table_args__ == {"schema": "provisioning"}


def test_processed_event_columns() -> None:
    """The mapped column set is exactly the three ledger columns."""
    cols = {c.name for c in ProcessedEvent.__table__.columns}
    assert cols == {"event_id", "consumer_group", "processed_at"}


def test_processed_event_composite_pk() -> None:
    """Both event_id and consumer_group form the composite primary key (D-07)."""
    pk = {c.name for c in ProcessedEvent.__table__.primary_key}
    assert pk == {"event_id", "consumer_group"}


def test_base_metadata_registers_processed_event() -> None:
    """Base.metadata includes the schema-qualified processed_event table.

    This is what `migrations/provisioning/env.py` imports for autogenerate.
    """
    assert "provisioning.processed_event" in Base.metadata.tables
