"""Cache of posted diff hashes for smart update detection.

Uses branch-scoped sqlite storage via review_store.
Falls back to the legacy .gg/posted-diffs flat file on first read,
migrating its contents into the DB.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from gg import git, review_store


def diff_hash(rev: str, *, cwd: Path | None = None) -> str:
    """SHA256 of a commit's diff content."""
    raw = git.diff_tree(rev, cwd=cwd)
    return hashlib.sha256(raw.encode()).hexdigest()


def _legacy_path(cwd: Path | None = None) -> Path:
    return git.repo_root(cwd=cwd) / ".gg" / "posted-diffs"


def _migrate_legacy(branch: str, *, cwd: Path | None = None) -> set[str]:
    """Read and migrate the legacy flat file, then delete it."""
    legacy = _legacy_path(cwd)
    if not legacy.exists():
        return set()
    hashes = set(legacy.read_text().strip().splitlines())
    if hashes:
        review_store.save_diff_hashes(branch, hashes, cwd=cwd)
    legacy.unlink()
    return hashes


def load_hashes(*, cwd: Path | None = None, branch: str | None = None) -> set[str]:
    """Load posted diff hashes for current branch. Migrates legacy file if present."""
    br = branch or git.branchname(cwd=cwd)
    legacy = _legacy_path(cwd)
    if legacy.exists():
        return _migrate_legacy(br, cwd=cwd)
    return review_store.load_diff_hashes(br, cwd=cwd)


def save_hashes(
    hashes: set[str], *, cwd: Path | None = None, branch: str | None = None,
) -> None:
    """Write diff hashes for current branch to sqlite."""
    br = branch or git.branchname(cwd=cwd)
    review_store.save_diff_hashes(br, hashes, cwd=cwd)
