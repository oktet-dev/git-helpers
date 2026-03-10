"""SQLite-backed storage for review metadata (.gg/reviews.db)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from gg import git

_DB_NAME = "reviews.db"
_PREFIX_RE = re.compile(r"^\[\d+(?:\.\d+)?/\d+\]:\s*")


def strip_prefix(subject: str) -> str:
    """Remove [idx/total]: prefix from a review subject."""
    return _PREFIX_RE.sub("", subject)


@dataclass
class ReviewEntry:
    """A single review in a posted series."""

    branch: str
    position: int
    review_id: str
    subject: str
    diff_hash: str


def _db_path(*, cwd: Path | None = None) -> Path:
    return git.repo_root(cwd=cwd) / ".gg" / _DB_NAME


def _connect(*, cwd: Path | None = None) -> sqlite3.Connection:
    path = _db_path(cwd=cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reviews (
            branch TEXT NOT NULL,
            position INTEGER NOT NULL,
            review_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            diff_hash TEXT NOT NULL,
            PRIMARY KEY (branch, position)
        );
        CREATE TABLE IF NOT EXISTS diff_hashes (
            branch TEXT NOT NULL,
            diff_hash TEXT NOT NULL,
            PRIMARY KEY (branch, diff_hash)
        );
    """)
    return conn


def load_reviews(branch: str, *, cwd: Path | None = None) -> list[ReviewEntry]:
    """Load all reviews for a branch, ordered by position."""
    conn = _connect(cwd=cwd)
    try:
        rows = conn.execute(
            "SELECT branch, position, review_id, subject, diff_hash "
            "FROM reviews WHERE branch = ? ORDER BY position",
            (branch,),
        ).fetchall()
        return [ReviewEntry(*row) for row in rows]
    finally:
        conn.close()


def save_reviews(entries: list[ReviewEntry], *, cwd: Path | None = None) -> None:
    """Replace all reviews for a branch with new entries."""
    if not entries:
        return
    branch = entries[0].branch
    conn = _connect(cwd=cwd)
    try:
        conn.execute("DELETE FROM reviews WHERE branch = ?", (branch,))
        conn.executemany(
            "INSERT INTO reviews (branch, position, review_id, subject, diff_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            [(e.branch, e.position, e.review_id, e.subject, e.diff_hash) for e in entries],
        )
        conn.commit()
    finally:
        conn.close()


def load_diff_hashes(branch: str, *, cwd: Path | None = None) -> set[str]:
    """Load posted diff hashes for a branch."""
    conn = _connect(cwd=cwd)
    try:
        rows = conn.execute(
            "SELECT diff_hash FROM diff_hashes WHERE branch = ?",
            (branch,),
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def save_diff_hashes(
    branch: str, hashes: set[str], *, cwd: Path | None = None,
) -> None:
    """Replace all diff hashes for a branch."""
    conn = _connect(cwd=cwd)
    try:
        conn.execute("DELETE FROM diff_hashes WHERE branch = ?", (branch,))
        conn.executemany(
            "INSERT INTO diff_hashes (branch, diff_hash) VALUES (?, ?)",
            [(branch, h) for h in sorted(hashes)],
        )
        conn.commit()
    finally:
        conn.close()
