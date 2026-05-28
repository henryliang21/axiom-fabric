from __future__ import annotations

import json
import uuid

import typer
from rich.console import Console
from rich.table import Table

from axiom_fabric.db import session_scope
from axiom_fabric.facts import (
    RETRACTION_NOTE,
    ForwardReferenceError,
    append_fact,
    append_fact_version,
    edges_for,
    get_fact,
    list_facts,
    retract_fact,
)
from axiom_fabric.layers import (
    get_layer_by_name,
    get_layer_version,
    list_layer_versions,
    list_layers,
    seed_default_layers,
)
from axiom_fabric.migrate import current_revision, upgrade_to_head
from axiom_fabric.models import FactVersion

app = typer.Typer(
    name="af",
    help="axiom-fabric: the versioned truth layer for agentic AI.",
    no_args_is_help=True,
)

layer_app = typer.Typer(help="Manage truth layers.", no_args_is_help=True)
app.add_typer(layer_app, name="layer")

fact_app = typer.Typer(help="Inspect facts and fact-versions.", no_args_is_help=True)
app.add_typer(fact_app, name="fact")

console = Console()
err_console = Console(stderr=True)


@app.command()
def init(
    skip_seed: bool = typer.Option(
        False, "--skip-seed", help="Run migrations but do not seed default layers."
    ),
) -> None:
    """Run migrations and seed the three default layers (Canonical / Episodic / Living).

    Each default layer also gets a v1 layer-version row on first seeding.
    """
    console.print("[bold]Running migrations...[/bold]")
    upgrade_to_head()
    rev = current_revision()
    console.print(f"  schema at revision: [cyan]{rev}[/cyan]")

    if skip_seed:
        console.print("[yellow]Skipped layer seeding (--skip-seed).[/yellow]")
        return

    with session_scope() as session:
        created = seed_default_layers(session)

    if created:
        names = ", ".join(layer.name for layer in created)
        console.print(f"[green]Seeded layers (with v1 snapshots):[/green] {names}")
    else:
        console.print("[dim]All default layers already present; nothing to seed.[/dim]")


@layer_app.command("list")
def layer_list() -> None:
    """List all layers, ordered by ordinal (foundational first)."""
    with session_scope() as session:
        layers = list_layers(session)

    if not layers:
        err_console.print(
            "[red]No layers defined.[/red] Run [bold]af init[/bold] to seed defaults."
        )
        raise typer.Exit(code=1)

    table = Table(title="Layers")
    table.add_column("Ordinal", justify="right")
    table.add_column("Name")
    table.add_column("Display")
    table.add_column("Weight", justify="right")
    for layer in layers:
        table.add_row(
            str(layer.ordinal),
            layer.name,
            layer.display_name or "",
            str(layer.weight),
        )
    console.print(table)


@layer_app.command("history")
def layer_history(name: str = typer.Argument(..., help="Layer name.")) -> None:
    """Show the version history of a layer (every snapshot, oldest first)."""
    with session_scope() as session:
        layer = get_layer_by_name(session, name)
        if layer is None:
            err_console.print(f"[red]No such layer:[/red] {name}")
            raise typer.Exit(code=1)
        versions = list_layer_versions(session, layer)

    if not versions:
        err_console.print(
            f"[yellow]Layer '{name}' exists but has no versions.[/yellow] "
            "Run [bold]af init[/bold] to seed v1."
        )
        raise typer.Exit(code=1)

    table = Table(title=f"Layer history: {name}")
    table.add_column("Version", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Ordinal", justify="right")
    table.add_column("Created")
    table.add_column("Notes")
    for lv in versions:
        table.add_row(
            str(lv.version),
            str(lv.weight),
            str(lv.ordinal),
            lv.created_at.isoformat(timespec="seconds"),
            lv.notes or "",
        )
    console.print(table)


@layer_app.command("version")
def layer_version(
    name: str = typer.Argument(..., help="Layer name."),
    n: int = typer.Argument(..., help="Version number (1-based)."),
) -> None:
    """Inspect a specific layer-version: its metadata and the fact-versions pinned to it."""
    with session_scope() as session:
        layer = get_layer_by_name(session, name)
        if layer is None:
            err_console.print(f"[red]No such layer:[/red] {name}")
            raise typer.Exit(code=1)
        lv = get_layer_version(session, layer, n)
        if lv is None:
            err_console.print(f"[red]No version {n} of layer '{name}'.[/red]")
            raise typer.Exit(code=1)

        console.print(
            f"[bold]{name} v{lv.version}[/bold]  "
            f"weight={lv.weight}  ordinal={lv.ordinal}  "
            f"created={lv.created_at.isoformat(timespec='seconds')}"
        )
        if lv.notes:
            console.print(f"  [dim]notes:[/dim] {lv.notes}")

        fact_versions = list(lv.fact_versions)
        if not fact_versions:
            console.print("[dim]No fact-versions pinned to this layer-version.[/dim]")
            return

        table = Table(title=f"Fact-versions in {name} v{lv.version}")
        table.add_column("Fact ID")
        table.add_column("FV ID")
        table.add_column("V", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Temp", justify="right")
        for fv in fact_versions:
            table.add_row(
                str(fv.fact_id),
                str(fv.id),
                str(fv.version),
                str(fv.weight),
                "" if fv.temperature is None else f"{fv.temperature:.3f}",
            )
        console.print(table)


def _parse_content(raw: str) -> dict:
    """Parse the --content flag as a JSON object. Exits with code 1 on bad input."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        err_console.print(f"[red]--content is not valid JSON:[/red] {e}")
        raise typer.Exit(code=1) from e
    if not isinstance(parsed, dict):
        err_console.print(
            f"[red]--content must be a JSON object (got {type(parsed).__name__}).[/red]"
        )
        raise typer.Exit(code=1)
    return parsed


def _parse_edge_uuids(raw_ids: list[str]) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    for value in raw_ids:
        try:
            parsed.append(uuid.UUID(value))
        except ValueError as e:
            err_console.print(f"[red]--edges-to is not a valid UUID:[/red] {value} ({e})")
            raise typer.Exit(code=1) from e
    return parsed


@fact_app.command("create")
def fact_create(
    layer: str = typer.Option(..., "--layer", help="Name of the layer to create the fact in."),
    content: str = typer.Option(
        ..., "--content", help="Fact body as a JSON object, e.g. '{\"claim\": \"sky is blue\"}'."
    ),
    weight: int | None = typer.Option(
        None, "--weight", min=0, max=100, help="Per-version weight 0-100; defaults to the layer's weight."
    ),
    edges_to: list[str] = typer.Option(
        [],
        "--edges-to",
        help="UUID of an upstream fact-version this one derives from. Repeatable.",
    ),
    note: str | None = typer.Option(None, "--note", help="Free-text annotation on this version."),
    schema_ref: str | None = typer.Option(
        None, "--schema-ref", help="Optional schema identifier for the fact's content."
    ),
) -> None:
    """Create a new fact (its v1 fact-version) in the given layer.

    Each create wraps the new fact-version in a fresh layer-version snapshot of
    its layer, so the truth ledger stays append-only and fully auditable.
    """
    parsed_content = _parse_content(content)
    edge_ids = _parse_edge_uuids(edges_to)

    with session_scope() as session:
        target_layer = get_layer_by_name(session, layer)
        if target_layer is None:
            err_console.print(f"[red]No such layer:[/red] {layer}")
            raise typer.Exit(code=1)
        try:
            fv = append_fact(
                session,
                target_layer,
                content=parsed_content,
                weight=weight if weight is not None else target_layer.weight,
                edges_to=edge_ids,
                note=note,
                schema_ref=schema_ref,
            )
        except ForwardReferenceError as e:
            err_console.print(f"[red]Edge target does not exist:[/red] {e}")
            raise typer.Exit(code=1) from e

        console.print(
            f"[green]Created fact[/green] {fv.fact_id} "
            f"[dim](fv {fv.id}, v{fv.version}, weight={fv.weight})[/dim]"
        )


@fact_app.command("update")
def fact_update(
    fact_id: str = typer.Option(..., "--fact-id", help="UUID of the existing fact to append a new version to."),
    content: str = typer.Option(
        ..., "--content", help="New fact body as a JSON object."
    ),
    weight: int | None = typer.Option(
        None, "--weight", min=0, max=100, help="Per-version weight 0-100; defaults to the prior version's weight."
    ),
    edges_to: list[str] = typer.Option(
        [],
        "--edges-to",
        help="UUID of an upstream fact-version this version derives from. Repeatable.",
    ),
    note: str | None = typer.Option(None, "--note", help="Free-text annotation on this version."),
) -> None:
    """Append a new version to an existing fact (the truth ledger is append-only)."""
    try:
        target_id = uuid.UUID(fact_id)
    except ValueError as e:
        err_console.print(f"[red]--fact-id is not a valid UUID:[/red] {fact_id} ({e})")
        raise typer.Exit(code=1) from e

    parsed_content = _parse_content(content)
    edge_ids = _parse_edge_uuids(edges_to)

    with session_scope() as session:
        fact = get_fact(session, target_id)
        if fact is None:
            err_console.print(f"[red]No fact with id[/red] {fact_id}")
            raise typer.Exit(code=1)
        # Carry the prior version's weight forward when --weight isn't given.
        if weight is None:
            prior = fact.versions[-1] if fact.versions else None
            resolved_weight = prior.weight if prior is not None else fact.layer.weight
        else:
            resolved_weight = weight
        try:
            fv = append_fact_version(
                session,
                fact,
                content=parsed_content,
                weight=resolved_weight,
                edges_to=edge_ids,
                note=note,
            )
        except ForwardReferenceError as e:
            err_console.print(f"[red]Edge target does not exist:[/red] {e}")
            raise typer.Exit(code=1) from e

        console.print(
            f"[green]Appended version[/green] v{fv.version} "
            f"[dim](fv {fv.id}, weight={fv.weight})[/dim] to fact {fv.fact_id}"
        )


@fact_app.command("retract")
def fact_retract(
    fact_id: str = typer.Option(..., "--fact-id", help="UUID of the fact to retract."),
    note: str | None = typer.Option(
        None, "--note", help=f"Retraction reason (default: '{RETRACTION_NOTE}')."
    ),
) -> None:
    """Tombstone a fact: appends a new version with weight=0 and a retraction note.

    Append-only — prior versions and all their edges remain intact for audit.
    """
    try:
        target_id = uuid.UUID(fact_id)
    except ValueError as e:
        err_console.print(f"[red]--fact-id is not a valid UUID:[/red] {fact_id} ({e})")
        raise typer.Exit(code=1) from e

    with session_scope() as session:
        fact = get_fact(session, target_id)
        if fact is None:
            err_console.print(f"[red]No fact with id[/red] {fact_id}")
            raise typer.Exit(code=1)
        fv = retract_fact(session, fact, note=note)
        console.print(
            f"[yellow]Retracted fact[/yellow] {fv.fact_id} "
            f"[dim](tombstone fv {fv.id}, v{fv.version})[/dim]"
        )


@fact_app.command("list")
def fact_list(
    layer: str | None = typer.Option(
        None, "--layer", help="Restrict to facts in this layer."
    ),
    latest_only: bool = typer.Option(
        True,
        "--latest-only/--all-versions",
        help="Show one row per fact (latest version) or one row per fact-version.",
    ),
) -> None:
    """List facts (and optionally every version)."""
    with session_scope() as session:
        target_layer = None
        if layer is not None:
            target_layer = get_layer_by_name(session, layer)
            if target_layer is None:
                err_console.print(f"[red]No such layer:[/red] {layer}")
                raise typer.Exit(code=1)
        facts = list_facts(session, target_layer)

        if not facts:
            scope = f" in layer '{layer}'" if layer else ""
            console.print(f"[dim]No facts{scope}.[/dim]")
            return

        # Build a name lookup so we can render layer names without re-querying.
        layer_names = {
            layer_row.id: layer_row.name for layer_row in list_layers(session)
        }

        title = "Facts" + (f" in '{layer}'" if layer else "")
        table = Table(title=title)
        table.add_column("Fact ID")
        table.add_column("Layer")
        if not latest_only:
            table.add_column("V", justify="right")
        table.add_column("Latest V" if latest_only else "FV ID")
        table.add_column("Weight", justify="right")
        table.add_column("Content")
        table.add_column("Note")

        for fact in facts:
            layer_name = layer_names.get(fact.layer_id, str(fact.layer_id))
            versions = fact.versions if not latest_only else (fact.versions[-1:] if fact.versions else [])
            if not versions:
                # Fact with no versions is anomalous, but show it rather than hide it.
                placeholders = ["-"] * (5 if latest_only else 6)
                table.add_row(str(fact.id), layer_name, *placeholders)
                continue
            for fv in versions:
                preview = json.dumps(fv.content, sort_keys=True)
                if len(preview) > 60:
                    preview = preview[:57] + "..."
                if latest_only:
                    table.add_row(
                        str(fact.id),
                        layer_name,
                        str(fv.version),
                        str(fv.weight),
                        preview,
                        fv.note or "",
                    )
                else:
                    table.add_row(
                        str(fact.id),
                        layer_name,
                        str(fv.version),
                        str(fv.id),
                        str(fv.weight),
                        preview,
                        fv.note or "",
                    )
        console.print(table)


@fact_app.command("edges")
def fact_edges(
    fv_id: str = typer.Argument(..., help="Fact-version UUID."),
) -> None:
    """Show the incoming and outgoing edges of a fact-version."""
    try:
        target = uuid.UUID(fv_id)
    except ValueError as e:
        err_console.print(f"[red]Not a valid UUID:[/red] {fv_id} ({e})")
        raise typer.Exit(code=1) from e

    with session_scope() as session:
        fv = session.get(FactVersion, target)
        if fv is None:
            err_console.print(f"[red]No fact-version with id {fv_id}.[/red]")
            raise typer.Exit(code=1)
        out_edges, in_edges = edges_for(session, target)

        console.print(
            f"[bold]Fact-version[/bold] {fv.id}  "
            f"(fact={fv.fact_id}, v={fv.version}, weight={fv.weight})"
        )

        if out_edges:
            table = Table(title="Outgoing (this fact-version was derived from...)")
            table.add_column("Target FV ID")
            table.add_column("Edge kind")
            for e in out_edges:
                table.add_row(str(e.target_fv_id), e.edge_kind)
            console.print(table)
        else:
            console.print("[dim]No outgoing edges.[/dim]")

        if in_edges:
            table = Table(title="Incoming (these fact-versions were derived from this one)")
            table.add_column("Source FV ID")
            table.add_column("Edge kind")
            for e in in_edges:
                table.add_row(str(e.source_fv_id), e.edge_kind)
            console.print(table)
        else:
            console.print("[dim]No incoming edges.[/dim]")


if __name__ == "__main__":
    app()
