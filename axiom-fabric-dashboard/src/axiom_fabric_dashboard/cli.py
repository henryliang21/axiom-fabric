"""Entry point for the `af-dashboard` command.

Resolves the database exactly like the `af` CLI (it imports the core), then
serves the read-only dashboard. If the database isn't reachable or initialized,
the server still starts — the UI surfaces the connection error via /api/health.
"""

from __future__ import annotations

import argparse
import os
import sys

from axiom_fabric_dashboard import DEFAULT_PORT


def _default_port() -> int:
    raw = os.environ.get("AF_DASHBOARD_PORT")
    if raw is None:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError:
        print(
            f"Ignoring invalid AF_DASHBOARD_PORT={raw!r}; using {DEFAULT_PORT}.",
            file=sys.stderr,
        )
        return DEFAULT_PORT


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="af-dashboard",
        description="Serve the Axiom Fabric web dashboard for the database in this directory.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    parser.add_argument(
        "--port",
        type=int,
        default=_default_port(),
        help=f"Port to bind (default: {DEFAULT_PORT}, or $AF_DASHBOARD_PORT). "
        "Auto-increments if busy.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser window on start.",
    )
    args = parser.parse_args()

    try:
        import axiom_fabric  # noqa: F401
    except ImportError:
        print(
            "axiom-fabric (the core package) is not installed; "
            "the dashboard cannot run without it.",
            file=sys.stderr,
        )
        return 1

    from axiom_fabric_dashboard.server import run

    run(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
