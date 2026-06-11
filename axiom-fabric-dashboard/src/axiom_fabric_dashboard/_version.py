"""Build-time version source for Hatchling.

The major.minor line is hardcoded; the patch is the total commit count from
`git rev-list --count HEAD`. Each build of a git checkout gets a unique,
deterministic version like `0.3.142`. Mirrors `axiom_fabric._version`; both
packages live in the same repo and intentionally share a patch number.

If git is not available (no `.git` directory, e.g. a source tarball without
history) we raise — by design. See axiom_fabric._version for rationale.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_MAJOR_MINOR = "0.3"


def _commit_count() -> str:
    """Return the total commit count on HEAD as a string, or raise."""
    try:
        return subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise RuntimeError(
            "Cannot determine version: this build is not a git checkout "
            "(no .git directory or `git` not on PATH). Install from a published "
            "wheel, or build from a `git clone` of the repository."
        ) from e


__version__ = f"{_MAJOR_MINOR}.{_commit_count()}"
