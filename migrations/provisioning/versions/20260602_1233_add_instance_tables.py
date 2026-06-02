"""add instance tables

Revision ID: 1096465f70af
Revises: 0e3f3be0f9ad
Create Date: 2026-06-02 12:33:49.953654
"""

from collections.abc import Sequence  # noqa: TC003 — used in module-level type annotation

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1096465f70af"
down_revision: str | None = "0e3f3be0f9ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create Postgres ENUM types explicitly in the provisioning schema.
    # autogenerate does not reliably handle schema-qualified ENUM types;
    # the ORM models use create_type=False so the CREATE TYPE must come
    # from here, before the tables that reference them.
    op.execute(
        "CREATE TYPE provisioning.instance_status AS ENUM ("
        "'pending', 'deploying', 'configuring', 'ready', "
        "'suspended', 'failed', 'deprovisioning', 'deprovisioned')"
    )
    op.execute(
        "CREATE TYPE provisioning.task_type AS ENUM ("
        "'create', 'update', 'suspend', 'reinstate', 'delete')"
    )
    op.execute(
        "CREATE TYPE provisioning.task_status AS ENUM ('pending', 'running', 'succeeded', 'failed')"
    )

    op.create_table(
        "instance",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("admin_email", sa.Text(), nullable=True),
        sa.Column("desired_seat_cap", sa.Integer(), nullable=True),
        sa.Column("desired_resource_caps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("deployment_handle", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failed_step", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("ready_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_status_check_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("snapshot_version", sa.Integer(), nullable=True),
        # WR-07: server_default mirrors the model-side Python default (version=1)
        # so non-ORM INSERTs (backfills, raw SQL) do not fail the NOT NULL.
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # WR-07: CHECK constraints for the numeric invariants the models imply.
        sa.CheckConstraint("version >= 1", name="ck_instance_version_positive"),
        sa.CheckConstraint(
            "desired_seat_cap IS NULL OR desired_seat_cap >= 1",
            name="ck_instance_seat_cap_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subscription_id"),
        schema="provisioning",
    )
    # Cast the status column to the named ENUM type created above.
    op.execute(
        "ALTER TABLE provisioning.instance "
        "ALTER COLUMN status TYPE provisioning.instance_status "
        "USING status::provisioning.instance_status"
    )

    op.create_table(
        "enforcement_snapshot",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        # WR-07: server_default mirrors the model default (version=1).
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "computed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("module_set", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seat_cap", sa.Integer(), nullable=False),
        sa.Column("resource_caps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        # WR-07: numeric invariants.
        sa.CheckConstraint("version >= 1", name="ck_snapshot_version_positive"),
        sa.CheckConstraint("seat_cap >= 1", name="ck_snapshot_seat_cap_positive"),
        sa.ForeignKeyConstraint(["instance_id"], ["provisioning.instance.id"]),
        sa.PrimaryKeyConstraint("instance_id"),
        schema="provisioning",
    )

    op.create_table(
        "provisioning_task",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.String(length=26), nullable=False),
        sa.Column("change_set_id", sa.UUID(), nullable=True),
        # WR-07: server_default mirrors the model default (attempt_count=0).
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # WR-07: numeric invariants matching the model intent.
        sa.CheckConstraint("attempt_count >= 0", name="ck_task_attempt_count_nonneg"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_task_max_attempts_positive"),
        sa.ForeignKeyConstraint(["instance_id"], ["provisioning.instance.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id", "change_set_id"),
        schema="provisioning",
    )
    # Cast enum columns to the named ENUM types created above.
    op.execute(
        "ALTER TABLE provisioning.provisioning_task "
        "ALTER COLUMN task_type TYPE provisioning.task_type "
        "USING task_type::provisioning.task_type"
    )
    op.execute(
        "ALTER TABLE provisioning.provisioning_task "
        "ALTER COLUMN status TYPE provisioning.task_status "
        "USING status::provisioning.task_status"
    )
    # WR-07: set the status server_default AFTER the enum cast — a default on
    # the pre-cast Text column would be dropped by the ALTER COLUMN ... TYPE.
    # Mirrors the model default (status=pending).
    op.execute(
        "ALTER TABLE provisioning.provisioning_task "
        "ALTER COLUMN status SET DEFAULT 'pending'::provisioning.task_status"
    )


def downgrade() -> None:
    # Drop tables in FK-safe order (child tables first).
    op.drop_table("provisioning_task", schema="provisioning")
    op.drop_table("enforcement_snapshot", schema="provisioning")
    op.drop_table("instance", schema="provisioning")
    # Drop ENUM types in reverse creation order.
    op.execute("DROP TYPE provisioning.task_status")
    op.execute("DROP TYPE provisioning.task_type")
    op.execute("DROP TYPE provisioning.instance_status")
