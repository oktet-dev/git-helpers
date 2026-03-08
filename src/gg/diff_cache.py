"""Cache of posted diff hashes for smart update detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

from gg import git

_CACHE_DIR = ".gg"
_CACHE_FILE = "posted-diffs"


def _repo_root(cwd: Path | None = None) -> Path:
    """Return the working tree root (parent of .git/)."""
    import subprocess

    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(r.stdout.strip())


def diff_hash(rev: str, *, cwd: Path | None = None) -> str:
    """SHA256 of a commit's diff content."""
    raw = git.diff_tree(rev, cwd=cwd)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_path(cwd: Path | None = None) -> Path:
    return _repo_root(cwd) / _CACHE_DIR / _CACHE_FILE


def load_hashes(*, cwd: Path | None = None) -> set[str]:
    """Load posted diff hashes. Empty set if no cache file."""
    path = _cache_path(cwd)
    if not path.exists():
        return set()
    return set(path.read_text().strip().splitlines())


def save_hashes(hashes: set[str], *, cwd: Path | None = None) -> None:
    """Write sorted hashes to .gg/posted-diffs, creating dir if needed."""
    path = _cache_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(hashes)) + "\n")
