"""Close ReviewBoard review requests (discard or submit)."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from gg.rbt_retry import run_with_retry

# rbt close's message for a review that already has the requested status.
_ALREADY_CLOSED = "is already closed as"


def _close(
    review_id: str,
    close_type: str,
    *,
    dry_run: bool,
    verbose: bool,
    cwd: Path | None,
) -> int:
    cmd = ["rbt", "close", f"--close-type={close_type}", review_id]

    if dry_run:
        print(shlex.join(cmd))
        return 0

    r = run_with_retry(cmd, cwd=cwd)
    output = r.stdout + r.stderr
    if r.returncode != 0 and _ALREADY_CLOSED in output:
        # rbt refuses only when the review already has this status:
        # the close the caller wants has happened.
        if verbose:
            print(output, end="")
        return 0
    if r.returncode != 0:
        sys.stderr.write(output if output.endswith("\n") else output + "\n")
    elif verbose and output:
        print(output, end="")
    return r.returncode


def close_discarded(
    review_id: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    cwd: Path | None = None,
) -> int:
    """Close a review request as discarded via rbt close. Returns rbt's exit code."""
    return _close(review_id, "discarded", dry_run=dry_run, verbose=verbose, cwd=cwd)


def close_submitted(
    review_id: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    cwd: Path | None = None,
) -> int:
    """Close a review request as submitted via rbt close. Returns rbt's exit code."""
    return _close(review_id, "submitted", dry_run=dry_run, verbose=verbose, cwd=cwd)
