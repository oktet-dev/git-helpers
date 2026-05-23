"""Close ReviewBoard review requests (discard or submit)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from gg.rbt_retry import run_with_retry


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

    r = run_with_retry(cmd, cwd=cwd)
    if verbose:
        output = r.stdout + r.stderr
        if output:
            print(output, end="")


def close_submitted(
    review_id: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    cwd: Path | None = None,
) -> None:
    """Close a review request as submitted via rbt close."""
    cmd = ["rbt", "close", "--close-type=submitted", review_id]

    if dry_run:
        print(shlex.join(cmd))
        return

    r = run_with_retry(cmd, cwd=cwd)
    if verbose:
        output = r.stdout + r.stderr
        if output:
            print(output, end="")
