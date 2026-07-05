from __future__ import annotations

import json
import uuid
from enum import Enum

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table
from sqlalchemy import func, select, text

from axiom_fabric.config import get_settings
from axiom_fabric.cost import change_cost, list_stale_fact_versions
from axiom_fabric.db import get_engine, session_scope
from axiom_fabric.facts import (
    RETRACTION_NOTE,
    ForwardReferenceError,
    append_fact,
    append_fact_version,
    edges_for,
    get_fact,
    get_fact_version,
    list_facts,
    retract_fact,
)
from axiom_fabric.layers import (
    LayerAlreadyExistsError,
    create_layer,
    get_layer_by_name,
    get_layer_version,
    list_layer_versions,
    list_layers,
    seed_default_layers,
)
from axiom_fabric.migrate import current_revision, upgrade_to_head
from axiom_fabric.models import Fact, FactVersion, FactVersionEdge, Layer, LayerVersion
from axiom_fabric.sources import (
    FactSourceSpec,
    SourceError,
    attach_source,
    detach_source,
    get_source,
    refresh_fact,
)


class EdgeKind(str, Enum):
    """Mirrors models.EDGE_KINDS as a Typer-friendly choice."""

    derived_from = "derived_from"
    evidence_of = "evidence_of"
    refutes = "refutes"
    supersedes = "supersedes"


class SourceKind(str, Enum):
    """Mirrors models.SOURCE_KINDS as a Typer-friendly choice."""

    inline = "inline"
    python = "python"
    sql = "sql"
    http = "http"
    mcp_tool = "mcp_tool"


class RefreshPolicy(str, Enum):
    """Mirrors models.REFRESH_POLICIES as a Typer-friendly choice."""

    manual = "manual"
    on_read = "on_read"
    ttl = "ttl"
    scheduled = "scheduled"

app = typer.Typer(
    name="af",
    help="axiom-fabric: the versioned truth layer for agentic AI.",
    no_args_is_help=True,
)

layer_app = typer.Typer(help="Manage truth layers.", no_args_is_help=True)
app.add_typer(layer_app, name="layer")

fact_app = typer.Typer(help="Inspect facts and fact-versions.", no_args_is_help=True)
app.add_typer(fact_app, name="fact")

source_app = typer.Typer(help="Attach and refresh dynamic (sourced) facts.", no_args_is_help=True)
app.add_typer(source_app, name="source")

console = Console()
err_console = Console(stderr=True)


@app.command()
def init(
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Also seed three example layers (Canonical / Episodic / Living) for a quick tour.",
    ),
    skip_seed: bool = typer.Option(
        False,
        "--skip-seed",
        hidden=True,
        help="Deprecated no-op: a clean store is now the default (use --demo to seed examples).",
    ),
) -> None:
    """Run migrations, producing a clean (empty) truth store.

    By default no layers are created — you (or an agent via MCP) define your own
    layers and facts. Pass --demo to seed the three example layers, each with a
    v1 layer-version snapshot, for a quick tour.
    """
    console.print("[bold]Running migrations...[/bold]")
    upgrade_to_head()
    rev = current_revision()
    console.print(f"  schema at revision: [cyan]{rev}[/cyan]")

    if not demo:
        console.print(
            "[green]Clean store ready.[/green] No layers yet — create one with "
            "[cyan]af layer create[/cyan] (or let an agent create them via MCP). "
            "Run [cyan]af init --demo[/cyan] for example layers."
        )
        return

    with session_scope() as session:
        created = seed_default_layers(session)

    if created:
        names = ", ".join(layer.name for layer in created)
        console.print(f"[green]Seeded demo layers (with v1 snapshots):[/green] {names}")
    else:
        console.print("[dim]All demo layers already present; nothing to seed.[/dim]")


@app.command()
def status() -> None:
    """Show database URL, schema revision, and row counts. Useful when juggling DBs."""
    settings = get_settings()
    url = settings.database_url
    backend = (url.split("://", 1)[0].split("+", 1)[0]) or "unknown"

    # Reachability first — a bad URL or missing file should not fall through to count queries.
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        console.print(f"[bold]Database:[/bold]      [cyan]{backend}[/cyan] -> {url}")
        err_console.print(f"[red]Cannot connect:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    revision = current_revision()
    if revision is None:
        console.print(f"[bold]Database:[/bold]      [cyan]{backend}[/cyan] -> {url}")
        console.print("[bold]Schema:[/bold]        [yellow](not migrated)[/yellow]")
        console.print("[bold]Initialized:[/bold]  [yellow]no[/yellow] — run [bold]af init[/bold].")
        return

    with session_scope() as session:
        layer_count = session.scalar(select(func.count()).select_from(Layer)) or 0
        layer_version_count = (
            session.scalar(select(func.count()).select_from(LayerVersion)) or 0
        )
        fact_count = session.scalar(select(func.count()).select_from(Fact)) or 0
        fv_count = session.scalar(select(func.count()).select_from(FactVersion)) or 0
        edge_count = (
            session.scalar(select(func.count()).select_from(FactVersionEdge)) or 0
        )

    initialized = layer_count >= 1
    init_label = "[green]yes[/green]" if initialized else "[yellow]no[/yellow]  (run `af init` to seed defaults)"

    console.print(f"[bold]Database:[/bold]         [cyan]{backend}[/cyan] -> {url}")
    console.print(f"[bold]Schema:[/bold]           [cyan]{revision}[/cyan]")
    console.print(f"[bold]Initialized:[/bold]      {init_label}")
    console.print()
    console.print(f"  Layers:           {layer_count}")
    console.print(f"  Layer-versions:   {layer_version_count}")
    console.print(f"  Fact identities:  {fact_count}")
    console.print(f"  Fact-versions:    {fv_count}")
    console.print(f"  Edges:            {edge_count}")


@layer_app.command("create")
def layer_create(
    name: str = typer.Option(..., "--name", help="Short slug, e.g. 'policy' or 'staging'."),
    weight: int = typer.Option(..., "--weight", min=0, max=100, help="Default gravity 0-100 for facts in this layer."),
    ordinal: int = typer.Option(
        ...,
        "--ordinal",
        help="Position in the layer hierarchy. Lower = more foundational. Must be unique.",
    ),
    display_name: str | None = typer.Option(
        None, "--display", help="Human-readable label (defaults to None)."
    ),
) -> None:
    """Create a new layer (and its v1 layer-version) in the current database."""
    with session_scope() as session:
        try:
            layer = create_layer(
                session,
                name=name,
                weight=weight,
                ordinal=ordinal,
                display_name=display_name,
            )
        except LayerAlreadyExistsError as e:
            err_console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1) from e

        console.print(
            f"[green]Created layer[/green] {layer.name} "
            f"[dim](id {layer.id}, weight={layer.weight}, ordinal={layer.ordinal})[/dim]"
        )


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
    edge_kind: EdgeKind = typer.Option(
        EdgeKind.derived_from,
        "--edge-kind",
        case_sensitive=False,
        help="Relationship of every --edges-to: derived_from | evidence_of | refutes | supersedes.",
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
                edge_kind=edge_kind.value,
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
    edge_kind: EdgeKind = typer.Option(
        EdgeKind.derived_from,
        "--edge-kind",
        case_sensitive=False,
        help="Relationship of every --edges-to: derived_from | evidence_of | refutes | supersedes.",
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
                edge_kind=edge_kind.value,
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


def _print_json_block(payload: dict | None, *, label: str) -> None:
    """Pretty-print a JSON dict via Rich syntax highlighting, or a dim placeholder if None."""
    if payload is None:
        console.print(f"[dim]{label}: (none)[/dim]")
        return
    console.print(f"[bold]{label}:[/bold]")
    console.print(Syntax(json.dumps(payload, indent=2, sort_keys=True), "json", theme="ansi_dark"))


@fact_app.command("show")
def fact_show(
    fact_id: str = typer.Argument(..., help="Fact identity UUID."),
) -> None:
    """Show a fact's metadata, every version (full content), and version-level lineage hints."""
    try:
        target = uuid.UUID(fact_id)
    except ValueError as e:
        err_console.print(f"[red]Not a valid UUID:[/red] {fact_id} ({e})")
        raise typer.Exit(code=1) from e

    with session_scope() as session:
        fact = get_fact(session, target)
        if fact is None:
            err_console.print(f"[red]No fact with id[/red] {fact_id}")
            raise typer.Exit(code=1)
        layer = session.get(Layer, fact.layer_id)
        layer_name = layer.name if layer else str(fact.layer_id)

        console.print(f"[bold]Fact[/bold] {fact.id}")
        console.print(f"  Layer:        [cyan]{layer_name}[/cyan]")
        console.print(f"  Schema ref:   {fact.schema_ref or '[dim](none)[/dim]'}")
        console.print(f"  Created:      {fact.created_at.isoformat(timespec='seconds')}")
        console.print(f"  Versions:     {len(fact.versions)}")

        if not fact.versions:
            console.print("[dim]No versions yet.[/dim]")
            return

        table = Table(title="Versions")
        table.add_column("V", justify="right")
        table.add_column("FV ID")
        table.add_column("Weight", justify="right")
        table.add_column("Temp", justify="right")
        table.add_column("Note")
        table.add_column("Created")
        for fv in fact.versions:
            table.add_row(
                str(fv.version),
                str(fv.id),
                str(fv.weight),
                "" if fv.temperature is None else f"{fv.temperature:.3f}",
                fv.note or "",
                fv.created_at.isoformat(timespec="seconds"),
            )
        console.print(table)

        latest = fact.versions[-1]
        console.print()
        _print_json_block(latest.content, label=f"Latest content (v{latest.version})")
        console.print(
            "[dim]Use `af fact version <fv-uuid>` for full per-version detail "
            "(content, justification, edges).[/dim]"
        )


@fact_app.command("version")
def fact_version(
    fv_id: str = typer.Argument(..., help="Fact-version UUID."),
) -> None:
    """Full inspection of one fact-version: content, justification, edges in/out."""
    try:
        target = uuid.UUID(fv_id)
    except ValueError as e:
        err_console.print(f"[red]Not a valid UUID:[/red] {fv_id} ({e})")
        raise typer.Exit(code=1) from e

    with session_scope() as session:
        fv = get_fact_version(session, target)
        if fv is None:
            err_console.print(f"[red]No fact-version with id[/red] {fv_id}")
            raise typer.Exit(code=1)
        fact = session.get(Fact, fv.fact_id)
        layer = session.get(Layer, fact.layer_id) if fact else None
        out_edges, in_edges = edges_for(session, target)

        console.print(f"[bold]Fact-version[/bold] {fv.id}")
        console.print(f"  Fact:           {fv.fact_id}")
        console.print(f"  Layer:          [cyan]{layer.name if layer else '?'}[/cyan]")
        console.print(f"  Version:        {fv.version}")
        console.print(f"  Weight:         {fv.weight}")
        console.print(
            "  Temperature:    "
            + ("[dim](none)[/dim]" if fv.temperature is None else f"{fv.temperature:.3f}")
        )
        console.print(f"  Note:           {fv.note or '[dim](none)[/dim]'}")
        console.print(f"  Layer-version:  {fv.layer_version_id}")
        console.print(f"  Created:        {fv.created_at.isoformat(timespec='seconds')}")
        console.print()
        _print_json_block(fv.content, label="Content")
        console.print()
        _print_json_block(fv.justification, label="Justification")
        console.print()

        if out_edges:
            table = Table(title="Outgoing (this version was derived from...)")
            table.add_column("Target FV ID")
            table.add_column("Edge kind")
            for e in out_edges:
                table.add_row(str(e.target_fv_id), e.edge_kind)
            console.print(table)
        else:
            console.print("[dim]No outgoing edges.[/dim]")

        if in_edges:
            table = Table(title="Incoming (these versions were derived from this one)")
            table.add_column("Source FV ID")
            table.add_column("Edge kind")
            for e in in_edges:
                table.add_row(str(e.source_fv_id), e.edge_kind)
            console.print(table)
        else:
            console.print("[dim]No incoming edges.[/dim]")


@fact_app.command("edges")
def fact_edges(
    fv_id: str = typer.Argument(..., help="Fact-version UUID."),
) -> None:
    """Show only the incoming and outgoing edges of a fact-version (subset of `fact version`)."""
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


@fact_app.command("cost")
def fact_cost(
    fv_id: str = typer.Argument(..., help="Fact-version UUID to price a change to."),
) -> None:
    """Change cost of altering a fact-version: sum(weight x depth x temperature) over its descendant subtree."""
    try:
        target = uuid.UUID(fv_id)
    except ValueError as e:
        err_console.print(f"[red]Not a valid UUID:[/red] {fv_id} ({e})")
        raise typer.Exit(code=1) from e

    with session_scope() as session:
        fv = get_fact_version(session, target)
        if fv is None:
            err_console.print(f"[red]No fact-version with id[/red] {fv_id}")
            raise typer.Exit(code=1)
        report = change_cost(session, target)

        console.print(
            f"[bold]Change cost[/bold] for fact-version {fv_id}\n"
            f"  Total:            [cyan]{report.total:g}[/cyan]\n"
            f"  Descendants:      {report.descendant_count}"
        )
        if not report.nodes:
            console.print(
                "[dim]No descendants — nothing derives from this version, so the "
                "change is free.[/dim]"
            )
            return
        table = Table(title="Descendant subtree (the blast radius)")
        table.add_column("FV ID")
        table.add_column("Depth", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Temp", justify="right")
        table.add_column("Penalty", justify="right")
        table.add_column("Contribution", justify="right")
        for n in report.nodes:
            table.add_row(
                str(n.fact_version_id),
                str(n.depth),
                str(n.weight),
                "" if n.temperature is None else f"{n.temperature:.3f}",
                f"{n.penalty:.3f}",
                f"{n.contribution:g}",
            )
        console.print(table)


@fact_app.command("stale")
def fact_stale() -> None:
    """List every fact-version currently flagged stale (an upstream derivation was superseded)."""
    with session_scope() as session:
        stale = list_stale_fact_versions(session)
        if not stale:
            console.print("[green]No stale fact-versions.[/green]")
            return
        table = Table(title="Stale fact-versions")
        table.add_column("FV ID")
        table.add_column("Fact ID")
        table.add_column("V", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Stale since")
        for fv in stale:
            table.add_row(
                str(fv.id),
                str(fv.fact_id),
                str(fv.version),
                str(fv.weight),
                fv.stale_since.isoformat(timespec="seconds") if fv.stale_since else "",
            )
        console.print(table)


def _parse_fact_uuid(fact_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(fact_id)
    except ValueError as e:
        err_console.print(f"[red]--fact-id is not a valid UUID:[/red] {fact_id} ({e})")
        raise typer.Exit(code=1) from e


@source_app.command("attach")
def source_attach(
    fact_id: str = typer.Option(..., "--fact-id", help="UUID of the fact to make sourced."),
    kind: SourceKind = typer.Option(
        ..., "--kind", case_sensitive=False, help="inline | python | sql | http | mcp_tool."
    ),
    uri: str | None = typer.Option(
        None, "--uri", help="Kind-specific target, e.g. 'module:callable' for python."
    ),
    params: str | None = typer.Option(
        None, "--params", help="JSON object of fetch params (e.g. inline: '{\"value\": {...}}')."
    ),
    refresh_policy: RefreshPolicy = typer.Option(
        RefreshPolicy.manual,
        "--refresh-policy",
        case_sensitive=False,
        help="manual | on_read | ttl | scheduled.",
    ),
    ttl_seconds: int | None = typer.Option(
        None, "--ttl-seconds", min=0, help="Required for the 'ttl' policy."
    ),
    schedule_cron: str | None = typer.Option(
        None, "--schedule-cron", help="Cron expr for the 'scheduled' policy (external scheduler)."
    ),
) -> None:
    """Attach a source to a fact so it can be refreshed into new snapshot versions."""
    target = _parse_fact_uuid(fact_id)
    parsed_params = _parse_content(params) if params is not None else None

    with session_scope() as session:
        fact = get_fact(session, target)
        if fact is None:
            err_console.print(f"[red]No fact with id[/red] {fact_id}")
            raise typer.Exit(code=1)
        try:
            source = attach_source(
                session,
                fact,
                FactSourceSpec(
                    kind=kind.value,
                    uri=uri,
                    params=parsed_params,
                    refresh_policy=refresh_policy.value,
                    ttl_seconds=ttl_seconds,
                    schedule_cron=schedule_cron,
                ),
            )
        except (SourceError, ValueError) as e:
            err_console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1) from e
        console.print(
            f"[green]Attached {source.kind} source[/green] to fact {source.fact_id} "
            f"[dim](policy={source.refresh_policy})[/dim]"
        )


@source_app.command("show")
def source_show(
    fact_id: str = typer.Argument(..., help="Fact identity UUID."),
) -> None:
    """Show the source configured on a fact (if any)."""
    target = _parse_fact_uuid(fact_id)
    with session_scope() as session:
        fact = get_fact(session, target)
        if fact is None:
            err_console.print(f"[red]No fact with id[/red] {fact_id}")
            raise typer.Exit(code=1)
        source = get_source(session, fact)
        if source is None:
            console.print(f"[dim]Fact {fact_id} has no source (it is a static fact).[/dim]")
            return
        console.print(f"[bold]Source[/bold] for fact {fact_id}")
        console.print(f"  Kind:            [cyan]{source.kind}[/cyan]")
        console.print(f"  URI:             {source.uri or '[dim](none)[/dim]'}")
        console.print(f"  Refresh policy:  {source.refresh_policy}")
        console.print(
            "  TTL seconds:     "
            + ("[dim](none)[/dim]" if source.ttl_seconds is None else str(source.ttl_seconds))
        )
        console.print(f"  Schedule cron:   {source.schedule_cron or '[dim](none)[/dim]'}")
        console.print(
            "  Last refreshed:  "
            + (
                "[dim](never)[/dim]"
                if source.last_refreshed_at is None
                else source.last_refreshed_at.isoformat(timespec="seconds")
            )
        )
        _print_json_block(source.params, label="Params")


@source_app.command("refresh")
def source_refresh(
    fact_id: str = typer.Option(..., "--fact-id", help="UUID of the sourced fact to refresh."),
) -> None:
    """Fetch the source's current value and append it as a new snapshot version."""
    target = _parse_fact_uuid(fact_id)
    with session_scope() as session:
        fact = get_fact(session, target)
        if fact is None:
            err_console.print(f"[red]No fact with id[/red] {fact_id}")
            raise typer.Exit(code=1)
        try:
            fv = refresh_fact(session, fact)
        except SourceError as e:
            err_console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1) from e
        console.print(
            f"[green]Refreshed[/green] fact {fv.fact_id} "
            f"[dim](new fv {fv.id}, v{fv.version})[/dim]"
        )


@source_app.command("detach")
def source_detach(
    fact_id: str = typer.Option(..., "--fact-id", help="UUID of the fact to make static again."),
) -> None:
    """Remove a fact's source. Its existing versions and history are untouched."""
    target = _parse_fact_uuid(fact_id)
    with session_scope() as session:
        fact = get_fact(session, target)
        if fact is None:
            err_console.print(f"[red]No fact with id[/red] {fact_id}")
            raise typer.Exit(code=1)
        removed = detach_source(session, fact)
        if removed:
            console.print(f"[yellow]Detached source[/yellow] from fact {fact_id}.")
        else:
            console.print(f"[dim]Fact {fact_id} had no source.[/dim]")


if __name__ == "__main__":
    app()
