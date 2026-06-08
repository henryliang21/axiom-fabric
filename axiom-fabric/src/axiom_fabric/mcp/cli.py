"""Entry point for the `af-mcp` command.

Subcommands:
  serve    (default)  Run the MCP server over stdio. --allow-writes enables
                      mutation tools (or set AF_MCP_ALLOW_WRITES=1).
  install             Merge the MCP server config into an agent's config file.

`serve` writes nothing to stdout (that channel is the JSON-RPC stream); all
diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys

from axiom_fabric.mcp.install import CLIENTS

_MCP_MISSING_HINT = (
    "The MCP SDK is not installed. Install the optional extra:\n"
    "    pipx install 'axiom-fabric[mcp]'      # or: pip install 'axiom-fabric[mcp]'\n"
    "    uv sync --extra mcp                    # from the workspace"
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _serve(args: argparse.Namespace) -> int:
    allow_writes = args.allow_writes or _env_truthy("AF_MCP_ALLOW_WRITES")
    try:
        from axiom_fabric.mcp.server import build_server
    except ImportError:
        print(_MCP_MISSING_HINT, file=sys.stderr)
        return 1

    server = build_server(allow_writes=allow_writes)
    mode = "read+write" if allow_writes else "read-only"
    print(f"axiom-fabric MCP server starting (stdio, {mode})", file=sys.stderr)
    server.run(transport="stdio")
    return 0


def _install(args: argparse.Namespace) -> int:
    from axiom_fabric.mcp.install import install

    try:
        summary = install(
            args.client,
            allow_writes=args.allow_writes,
            db=args.db,
            scope=args.scope,
            print_only=args.print_only,
            with_skill=args.with_skill,
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(summary)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="af-mcp",
        description="Axiom Fabric MCP server — expose the truth ledger to AI agents.",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the MCP server over stdio (default).")
    serve.add_argument(
        "--allow-writes",
        action="store_true",
        help="Expose write tools (create/update/retract). Default: read-only. "
        "Can also be enabled with AF_MCP_ALLOW_WRITES=1.",
    )
    serve.set_defaults(func=_serve)

    install = sub.add_parser("install", help="Write the MCP config into an agent's config file.")
    install.add_argument(
        "--client",
        required=True,
        choices=sorted(CLIENTS),
        help="Target agent client.",
    )
    install.add_argument(
        "--allow-writes",
        action="store_true",
        help="Configure the server with write tools enabled.",
    )
    install.add_argument(
        "--db",
        default=None,
        help="Path to the af.db to bind (default: ./af.db in the current directory). "
        "Resolved to an absolute path.",
    )
    install.add_argument(
        "--scope",
        choices=["project", "user"],
        default="project",
        help="project (current dir, e.g. ./.mcp.json) or user (global, e.g. ~/.claude.json). "
        "Also selects where --with-skill writes the guidance file.",
    )
    install.add_argument(
        "--with-skill",
        dest="with_skill",
        action="store_true",
        help="Also install the per-agent guidance file (Claude SKILL.md / Gemini GEMINI.md / "
        "Codex AGENTS.md), generated from the packaged guide.",
    )
    install.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print the config block (and skill, with --with-skill) instead of writing any file.",
    )
    install.set_defaults(func=_install)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # Default to `serve` so an MCP client can launch `af-mcp` with no args.
        args = parser.parse_args(["serve", *(argv or [])])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
