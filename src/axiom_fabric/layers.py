from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from axiom_fabric.models import Layer


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
    return created


def list_layers(session: Session) -> list[Layer]:
    return list(session.execute(select(Layer).order_by(Layer.ordinal)).scalars().all())
