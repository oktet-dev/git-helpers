"""Publish ReviewBoard review-request drafts via `rbt publish`."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


def publish_one(
    review_id: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    cwd: Path | None = None,
) -> int:
    """Publish a single review request draft. Returns rbt's exit code."""
    cmd = ["rbt", "publish", review_id]

    if dry_run:
        print(shlex.join(cmd))
        return 0

    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(f"\n[gg] rbt publish r/{review_id} failed (exit {r.returncode})\n")
        sys.stderr.write(r.stdout + r.stderr)
        return r.returncode
    if verbose:
        out = r.stdout + r.stderr
        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
    return 0
