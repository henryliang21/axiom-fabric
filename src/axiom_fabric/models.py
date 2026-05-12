from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
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
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONType, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("facts.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("fact_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    justification: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    temperature: Mapped[float | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    fact: Mapped[Fact] = relationship(back_populates="versions", foreign_keys=[fact_id])
