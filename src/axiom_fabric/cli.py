from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from axiom_fabric.db import session_scope
from axiom_fabric.layers import list_layers, seed_default_layers
from axiom_fabric.migrate import current_revision, upgrade_to_head

app = typer.Typer(
    name="af",
    help="axiom-fabric: the versioned truth layer for agentic AI.",
    no_args_is_help=True,
)

layer_app = typer.Typer(help="Manage truth layers.", no_args_is_help=True)
app.add_typer(layer_app, name="layer")

console = Console()
err_console = Console(stderr=True)


@app.command()
def init(
    skip_seed: bool = typer.Option(
        False, "--skip-seed", help="Run migrations but do not seed default layers."
    ),
) -> None:
    """Run migrations and seed the three default layers (Canonical / Episodic / Living)."""
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
        console.print(f"[green]Seeded layers:[/green] {names}")
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


if __name__ == "__main__":
    app()
