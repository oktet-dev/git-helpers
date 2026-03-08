"""Thin wrappers around git subprocess calls."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def branchname(rev: str = "HEAD", *, cwd: Path | None = None) -> str:
    """Return branch name for a revision (e.g. HEAD, @{u})."""
    return _run("rev-parse", "--abbrev-ref", "--symbolic-full-name", rev, cwd=cwd)


def summary(rev: str = "HEAD", *, cwd: Path | None = None) -> str:
    """Return the subject line of a commit."""
    return _run("show", "--quiet", "--format=%s", rev, cwd=cwd)


def tracking_branch(*, cwd: Path | None = None) -> str:
    """Return the upstream tracking branch name."""
    return branchname("@{u}", cwd=cwd)


def rev_range(*, cwd: Path | None = None) -> str:
    """Return 'tracking..HEAD' range string."""
    return f"{tracking_branch(cwd=cwd)}..HEAD"


def diff_tree(rev: str, *, cwd: Path | None = None) -> str:
    """Raw diff for a single commit (no commit metadata)."""
    return _run("diff-tree", "--no-commit-id", "-p", rev, cwd=cwd)


def list_revs(range_spec: str, *, cwd: Path | None = None) -> list[str]:
    """Return list of short commit hashes in chronological order."""
    output = _run("log", "--reverse", "--format=%h", range_spec, cwd=cwd)
    if not output:
        return []
    return output.splitlines()
