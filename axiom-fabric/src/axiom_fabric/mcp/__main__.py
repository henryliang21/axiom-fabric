"""Allow `python -m axiom_fabric.mcp` to run the af-mcp CLI."""

from __future__ import annotations

from axiom_fabric.mcp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
