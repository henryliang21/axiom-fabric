"""Model Context Protocol server for Axiom Fabric.

Exposes the truth ledger to MCP-capable agents (Claude, Gemini, Codex, ...) as a
set of read tools (always) and write tools (gated behind --allow-writes). See
`build_server` and the `af-mcp` CLI.

`build_server` is imported lazily so that importing this package (e.g. for
`af-mcp install`) does not require the optional `mcp` SDK to be installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from axiom_fabric.mcp.server import build_server

__all__ = ["build_server"]


def __getattr__(name: str):  # PEP 562 lazy attribute access
    if name == "build_server":
        from axiom_fabric.mcp.server import build_server

        return build_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
