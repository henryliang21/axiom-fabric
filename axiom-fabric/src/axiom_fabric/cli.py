from __future__ import annotations

import uuid

import typer
from rich.console import Console
from rich.table import Table

from axiom_fabric.db import session_scope
from axiom_fabric.facts import edges_for
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
