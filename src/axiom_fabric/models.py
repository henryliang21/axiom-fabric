from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")

EDGE_KINDS = ("derived_from", "evidence_of", "refutes", "supersedes")


class Base(DeclarativeBase):
    pass


class Layer(Base):
    __tablename__ = "layers"
    __table_args__ = (
        UniqueConstraint("name", name="uq_layers_name"),
        UniqueConstraint("ordinal", name="uq_layers_ordinal"),
        CheckConstraint("weight >= 0 AND weight <= 100", name="ck_layers_weight_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    facts: Mapped[list[Fact]] = relationship(back_populates="layer")
    versions: Mapped[list[LayerVersion]] = relationship(
        back_populates="layer", order_by="LayerVersion.version"
    )


class LayerVersion(Base):
    __tablename__ = "layer_versions"
    __table_args__ = (
        UniqueConstraint("layer_id", "version", name="uq_layer_versions_layer_version"),
        CheckConstraint("version >= 1", name="ck_layer_versions_version_positive"),
        CheckConstraint("weight >= 0 AND weight <= 100", name="ck_layer_versions_weight_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    layer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("layers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    layer: Mapped[Layer] = relationship(back_populates="versions")
    fact_versions: Mapped[list[FactVersion]] = relationship(back_populates="layer_version")


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    layer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("layers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    schema_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    layer: Mapped[Layer] = relationship(back_populates="facts")
    versions: Mapped[list[FactVersion]] = relationship(
        back_populates="fact",
        foreign_keys="FactVersion.fact_id",
        order_by="FactVersion.version",
    )


class FactVersion(Base):
    __tablename__ = "fact_versions"
    __table_args__ = (
        UniqueConstraint("fact_id", "version", name="uq_fact_versions_fact_version"),
        CheckConstraint("version >= 1", name="ck_fact_versions_version_positive"),
        CheckConstraint("weight >= 0 AND weight <= 100", name="ck_fact_versions_weight_range"),
        CheckConstraint(
            "temperature IS NULL OR (temperature >= 0 AND temperature <= 1)",
            name="ck_fact_versions_temperature_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("facts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    layer_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("layer_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONType, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    justification: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    temperature: Mapped[float | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    fact: Mapped[Fact] = relationship(back_populates="versions", foreign_keys=[fact_id])
    layer_version: Mapped[LayerVersion] = relationship(back_populates="fact_versions")
    edges_out: Mapped[list[FactVersionEdge]] = relationship(
        back_populates="source",
        foreign_keys="FactVersionEdge.source_fv_id",
        cascade="all, delete-orphan",
    )
    edges_in: Mapped[list[FactVersionEdge]] = relationship(
        back_populates="target",
        foreign_keys="FactVersionEdge.target_fv_id",
        cascade="all, delete-orphan",
    )


class FactVersionEdge(Base):
    __tablename__ = "fact_version_edges"
    __table_args__ = (
        CheckConstraint(
            "edge_kind IN ('derived_from', 'evidence_of', 'refutes', 'supersedes')",
            name="ck_fact_version_edges_kind",
        ),
        CheckConstraint(
            "source_fv_id != target_fv_id",
            name="ck_fact_version_edges_no_self_loop",
        ),
        Index("ix_fact_version_edges_target", "target_fv_id"),
    )

    source_fv_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("fact_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_fv_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("fact_versions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    edge_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="derived_from", server_default="derived_from"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source: Mapped[FactVersion] = relationship(
        back_populates="edges_out", foreign_keys=[source_fv_id]
    )
    target: Mapped[FactVersion] = relationship(
        back_populates="edges_in", foreign_keys=[target_fv_id]
    )
