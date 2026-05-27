"""Entry point for the `af-dashboard` command.

Placeholder until the FastAPI app lands. It already imports from the core
package to prove the dependency wiring works end to end.
"""

from __future__ import annotations

import sys

from axiom_fabric_dashboard import DEFAULT_PORT


def main() -> int:
    # Importing the core here (not at module load) keeps the failure mode clear
    # if `axiom-fabric` somehow isn't installed alongside the dashboard.
    try:
        import axiom_fabric  # noqa: F401
    except ImportError:
        print(
            "axiom-fabric (the core package) is not installed; "
            "the dashboard cannot run without it.",
            file=sys.stderr,
        )
        return 1

    print(
        "axiom-fabric-dashboard is scaffolded but not implemented yet.\n"
        f"When built, this will serve the UI at http://localhost:{DEFAULT_PORT}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
