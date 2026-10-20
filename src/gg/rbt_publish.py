"""Publish ReviewBoard review-request drafts via `rbt publish`."""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

from gg.rbt_retry import run_with_retry

# rbt publish's message when the review request has no draft (a missing
# review request reads "Error getting review request ...").
_NO_DRAFT = re.compile(r"it may already be published\): .*\(API Error 100:")


def publish_one(
    review_id: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    cwd: Path | None = None,
) -> int:
    """Publish a single review request draft. Returns rbt's exit code.

    A review request without a draft has nothing to publish: that is not
    a failure.
    """
    cmd = ["rbt", "publish", review_id]

    if dry_run:
        print(shlex.join(cmd))
        return 0

    r = run_with_retry(cmd, cwd=cwd)
    if r.returncode != 0 and _NO_DRAFT.search(r.stdout + r.stderr):
        if verbose:
            print(f"r/{review_id}: no draft to publish")
        return 0
    if r.returncode != 0:
        sys.stderr.write(f"\n[gg] rbt publish r/{review_id} failed (exit {r.returncode})\n")
        sys.stderr.write(r.stdout + r.stderr)
        return r.returncode
    if verbose:
        out = r.stdout + r.stderr
        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
    return 0
