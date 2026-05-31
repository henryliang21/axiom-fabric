"""phase 1.5: layer_versions table + fact_version_edges adjacency table

Revision ID: b1f5a7c8d9e0
Revises: 2b53e2588f3e
Create Date: 2026-05-25 12:00:00.000000

"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1f5a7c8d9e0"
down_revision: Union[str, Sequence[str], None] = "2b53e2588f3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. layer_versions: one row per snapshot of a layer.
    op.create_table(
        "layer_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("layer_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_layer_versions_version_positive"),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 100", name="ck_layer_versions_weight_range"
        ),
        sa.ForeignKeyConstraint(["layer_id"], ["layers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("layer_id", "version", name="uq_layer_versions_layer_version"),
    )
    op.create_index(
        op.f("ix_layer_versions_layer_id"), "layer_versions", ["layer_id"], unique=False
    )

    # 2. fact_version_edges: adjacency table for the derivation DAG between fact-versions.
    op.create_table(
        "fact_version_edges",
        sa.Column("source_fv_id", sa.Uuid(), nullable=False),
        sa.Column("target_fv_id", sa.Uuid(), nullable=False),
        sa.Column(
            "edge_kind",
            sa.String(length=32),
            server_default="derived_from",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "edge_kind IN ('derived_from', 'evidence_of', 'refutes', 'supersedes')",
            name="ck_fact_version_edges_kind",
        ),
        sa.CheckConstraint(
            "source_fv_id != target_fv_id", name="ck_fact_version_edges_no_self_loop"
        ),
        sa.ForeignKeyConstraint(
            ["source_fv_id"], ["fact_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_fv_id"], ["fact_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_fv_id", "target_fv_id"),
    )
    op.create_index(
        "ix_fact_version_edges_target", "fact_version_edges", ["target_fv_id"], unique=False
    )

    # 3. Add fact_versions.layer_version_id (nullable for now, backfilled below).
    with op.batch_alter_table("fact_versions") as batch_op:
        batch_op.add_column(sa.Column("layer_version_id", sa.Uuid(), nullable=True))

    # 4. Backfill: create a v1 layer_version for each existing layer (typed via sa.table
    #    so the Uuid column processes values correctly on both dialects).
    bind = op.get_bind()
    layers_ref = sa.table(
        "layers",
        sa.column("id", sa.Uuid()),
        sa.column("weight", sa.Integer()),
        sa.column("ordinal", sa.Integer()),
    )
    layer_versions_ref = sa.table(
        "layer_versions",
        sa.column("id", sa.Uuid()),
        sa.column("layer_id", sa.Uuid()),
        sa.column("version", sa.Integer()),
        sa.column("weight", sa.Integer()),
        sa.column("ordinal", sa.Integer()),
        sa.column("notes", sa.Text()),
    )
    existing_layers = bind.execute(
        sa.select(layers_ref.c.id, layers_ref.c.weight, layers_ref.c.ordinal)
    ).all()
    if existing_layers:
        op.bulk_insert(
            layer_versions_ref,
            [
                {
                    "id": uuid.uuid4(),
                    "layer_id": row.id,
                    "version": 1,
                    "weight": row.weight,
                    "ordinal": row.ordinal,
                    "notes": "Backfilled by Phase 1.5 migration",
                }
                for row in existing_layers
            ],
        )

    # 5. Backfill fact_versions.layer_version_id from each fact's home layer's v1 snapshot.
    #    Correlated subquery — works identically on SQLite and Postgres.
    op.execute(
        sa.text(
            """
            UPDATE fact_versions
               SET layer_version_id = (
                   SELECT lv.id
                     FROM layer_versions lv
                     JOIN facts f ON f.layer_id = lv.layer_id
                    WHERE f.id = fact_versions.fact_id
                      AND lv.version = 1
               )
            """
        )
    )

    # 6. Backfill fact_version_edges from any existing single-link parents.
    op.execute(
        sa.text(
            """
            INSERT INTO fact_version_edges (source_fv_id, target_fv_id, edge_kind)
            SELECT id, parent_version_id, 'derived_from'
              FROM fact_versions
             WHERE parent_version_id IS NOT NULL
               AND parent_version_id != id
            """
        )
    )

    # 7. Lock down layer_version_id, add FK + index, drop the old parent columns.
    with op.batch_alter_table("fact_versions") as batch_op:
        batch_op.alter_column("layer_version_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.create_foreign_key(
            "fk_fact_versions_layer_version_id",
            "layer_versions",
            ["layer_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_fact_versions_layer_version_id", ["layer_version_id"], unique=False
        )
        batch_op.drop_column("parent_version_id")
        batch_op.drop_column("parent_fact_id")


def downgrade() -> None:
    # Restore single-link parent columns from edges where possible (lossy if a
    # fact-version has multiple edges).
    with op.batch_alter_table("fact_versions") as batch_op:
        batch_op.add_column(sa.Column("parent_fact_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("parent_version_id", sa.Uuid(), nullable=True))
        batch_op.drop_index("ix_fact_versions_layer_version_id")
        batch_op.drop_constraint("fk_fact_versions_layer_version_id", type_="foreignkey")
        batch_op.drop_column("layer_version_id")
        batch_op.create_foreign_key(
            "fact_versions_parent_fact_id_fkey",
            "facts",
            ["parent_fact_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fact_versions_parent_version_id_fkey",
            "fact_versions",
            ["parent_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.drop_index("ix_fact_version_edges_target", table_name="fact_version_edges")
    op.drop_table("fact_version_edges")
    op.drop_index(op.f("ix_layer_versions_layer_id"), table_name="layer_versions")
    op.drop_table("layer_versions")
