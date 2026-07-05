"""staleness flag on fact_versions + fact_sources table

Revision ID: c3a9f1d2e4b5
Revises: b1f5a7c8d9e0
Create Date: 2026-06-30 12:00:00.000000

Adds the two pieces behind Change Cost / Staleness and Dynamic (sourced) facts:

- `fact_versions.stale_since` — mutable governance annotation flipped by the
  cascade when an upstream derivation is superseded/retracted (NULL = fresh).
- `fact_sources` — optional 1:1 sidecar on a fact recording where its value is
  fetched from and the policy governing when a fresh snapshot is due.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c3a9f1d2e4b5"
down_revision: Union[str, Sequence[str], None] = "b1f5a7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Staleness flag on fact-versions (nullable; NULL means fresh).
    with op.batch_alter_table("fact_versions") as batch_op:
        batch_op.add_column(sa.Column("stale_since", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_fact_versions_stale_since", ["stale_since"], unique=False)

    # 2. fact_sources: optional 1:1 sidecar on facts.
    op.create_table(
        "fact_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fact_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column(
            "params",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "refresh_policy",
            sa.String(length=32),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column("schedule_cron", sa.String(length=128), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('inline', 'python', 'sql', 'http', 'mcp_tool')",
            name="ck_fact_sources_kind",
        ),
        sa.CheckConstraint(
            "refresh_policy IN ('manual', 'on_read', 'ttl', 'scheduled')",
            name="ck_fact_sources_refresh_policy",
        ),
        sa.CheckConstraint(
            "ttl_seconds IS NULL OR ttl_seconds >= 0",
            name="ck_fact_sources_ttl_nonneg",
        ),
        sa.ForeignKeyConstraint(["fact_id"], ["facts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fact_id", name="uq_fact_sources_fact_id"),
    )


def downgrade() -> None:
    op.drop_table("fact_sources")
    with op.batch_alter_table("fact_versions") as batch_op:
        batch_op.drop_index("ix_fact_versions_stale_since")
        batch_op.drop_column("stale_since")
