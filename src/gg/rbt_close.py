"""Close (discard) a ReviewBoard review request."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


def close_discarded(
    review_id: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    cwd: Path | None = None,
) -> None:
    """Close a review request as discarded via rbt close."""
    cmd = ["rbt", "close", "--close-type=discarded", review_id]

    if dry_run:
        print(shlex.join(cmd))
        return

    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if verbose:
        output = r.stdout + r.stderr
        if output:
            print(output, end="")
