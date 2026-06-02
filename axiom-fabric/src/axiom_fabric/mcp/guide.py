"""Loader for the canonical agent-usage guidance.

`agent_guide.md` is the single source of truth for how an agent should use the
truth store. It is shipped as package data, served live by the MCP server as the
`axiom_fabric_usage` prompt, and rendered into per-agent skill files by
`af-mcp install --with-skill`. This module has no third-party dependencies, so
`af-mcp install` works even when the `mcp` SDK extra is absent.
"""

from __future__ import annotations

from importlib import resources


def read_agent_guide() -> str:
    """Return the packaged agent guidance markdown."""
    return (
        resources.files("axiom_fabric.mcp")
        .joinpath("agent_guide.md")
        .read_text(encoding="utf-8")
    )
