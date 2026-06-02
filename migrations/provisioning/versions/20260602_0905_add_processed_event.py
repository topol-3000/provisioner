"""add processed_event

Revision ID: 0e3f3be0f9ad
Revises: 
Create Date: 2026-06-02 09:05:50.007480
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '0e3f3be0f9ad'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "processed_event",
        sa.Column("event_id", sa.String(26), nullable=False),
        sa.Column("consumer_group", sa.Text(), nullable=False),
        sa.Column(
            "processed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id", "consumer_group"),
        schema="provisioning",
    )


def downgrade() -> None:
    op.drop_table("processed_event", schema="provisioning")
