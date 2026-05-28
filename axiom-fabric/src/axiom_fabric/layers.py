from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from axiom_fabric.models import Layer, LayerVersion


@dataclass(frozen=True)
class LayerSpec:
    name: str
    display_name: str
    weight: int
    ordinal: int


DEFAULT_LAYERS: tuple[LayerSpec, ...] = (
    LayerSpec(name="canonical", display_name="Canonical", weight=90, ordinal=0),
    LayerSpec(name="episodic", display_name="Episodic", weight=30, ordinal=50),
    LayerSpec(name="living", display_name="Living", weight=10, ordinal=100),
)


class NoLayersDefinedError(RuntimeError):
    pass


def count_layers(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Layer)) or 0


def ensure_at_least_one_layer(session: Session) -> None:
    if count_layers(session) == 0:
        raise NoLayersDefinedError(
            "axiom-fabric requires at least one layer to be defined; "
            "run `af init` or `af layer add` first."
        )


def ensure_v1_layer_version(session: Session, layer: Layer) -> LayerVersion:
    """Return the v1 LayerVersion for `layer`, creating it if missing.

    Idempotent — safe to call repeatedly during seeding or re-init.
    """
    existing = session.scalar(
        select(LayerVersion).where(
            LayerVersion.layer_id == layer.id, LayerVersion.version == 1
        )
    )
    if existing is not None:
        return existing
    lv = LayerVersion(
        layer_id=layer.id,
        version=1,
        weight=layer.weight,
        ordinal=layer.ordinal,
        notes="Initial snapshot",
    )
    session.add(lv)
    session.flush()
    return lv


def seed_default_layers(session: Session) -> list[Layer]:
    existing_names = {row[0] for row in session.execute(select(Layer.name)).all()}
    created: list[Layer] = []
    for spec in DEFAULT_LAYERS:
        if spec.name in existing_names:
            continue
        layer = Layer(
            name=spec.name,
            display_name=spec.display_name,
            weight=spec.weight,
            ordinal=spec.ordinal,
        )
        session.add(layer)
        created.append(layer)
    session.flush()
    # Ensure every layer (new or pre-existing) has a v1 snapshot.
    for layer in session.execute(select(Layer)).scalars().all():
        ensure_v1_layer_version(session, layer)
    return created


class LayerAlreadyExistsError(ValueError):
    """Raised when creating a layer would collide with an existing name or ordinal."""


def create_layer(
    session: Session,
    *,
    name: str,
    weight: int,
    ordinal: int,
    display_name: str | None = None,
) -> Layer:
    """Create a new Layer and its v1 LayerVersion. Raises if name or ordinal collide.

    The DB has uniqueness constraints on both (layers.name, layers.ordinal); we
    pre-check here so callers get a typed error instead of a raw IntegrityError.
    """
    if not 0 <= weight <= 100:
        raise ValueError(f"weight must be 0..100, got {weight}")

    if session.scalar(select(Layer).where(Layer.name == name)) is not None:
        raise LayerAlreadyExistsError(f"layer with name {name!r} already exists")
    if session.scalar(select(Layer).where(Layer.ordinal == ordinal)) is not None:
        raise LayerAlreadyExistsError(
            f"layer with ordinal {ordinal} already exists "
            "(ordinals must be unique; pick a different position)"
        )

    layer = Layer(
        name=name,
        display_name=display_name,
        weight=weight,
        ordinal=ordinal,
    )
    session.add(layer)
    session.flush()
    ensure_v1_layer_version(session, layer)
    return layer


def list_layers(session: Session) -> list[Layer]:
    return list(session.execute(select(Layer).order_by(Layer.ordinal)).scalars().all())


def get_layer_by_name(session: Session, name: str) -> Layer | None:
    return session.scalar(select(Layer).where(Layer.name == name))


def list_layer_versions(session: Session, layer: Layer) -> list[LayerVersion]:
    return list(
        session.execute(
            select(LayerVersion)
            .where(LayerVersion.layer_id == layer.id)
            .order_by(LayerVersion.version)
        )
        .scalars()
        .all()
    )


def get_layer_version(session: Session, layer: Layer, version: int) -> LayerVersion | None:
    return session.scalar(
        select(LayerVersion).where(
            LayerVersion.layer_id == layer.id, LayerVersion.version == version
        )
    )
