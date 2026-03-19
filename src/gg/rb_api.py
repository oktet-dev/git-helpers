"""Thin wrapper around `rbt api-get` for querying ReviewBoard."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Extract review ID from an API href like .../review-requests/123/
_HREF_ID_RE = re.compile(r"/review-requests/(\d+)/?$")


def _parse_block_id(block: int | dict) -> str:
    """Extract review ID from a blocks entry.

    RB API returns blocks as link objects ({"href": "...", "method": "GET"}).
    Test mocks may return plain ints.
    """
    if isinstance(block, (int, str)):
        return str(block)
    href = block.get("href", "")
    m = _HREF_ID_RE.search(href)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot parse review ID from block: {block}")


def fetch_review(review_id: str, *, cwd: Path | None = None) -> dict:
    """Fetch a review request and return {id, summary, blocks}."""
    r = subprocess.run(
        ["rbt", "api-get", f"/review-requests/{review_id}/"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        raise SystemExit(f"rbt api-get failed for review {review_id}: {msg}")

    data = json.loads(r.stdout)
    rr = data["review_request"]
    return {
        "id": str(rr["id"]),
        "summary": rr["summary"],
        "blocks": [_parse_block_id(b) for b in rr.get("blocks", [])],
        "target_people": [p["title"] for p in rr.get("target_people", [])],
        "target_groups": [g["title"] for g in rr.get("target_groups", [])],
    }


def fetch_reviewers(
    review_id: str, *, cwd: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Fetch target people and groups for a review request."""
    review = fetch_review(review_id, cwd=cwd)
    return review["target_people"], review["target_groups"]


@dataclass
class ChainResult:
    """Result of walking a ReviewBoard dependency chain."""

    chain: list[tuple[str, str]]
    reviewers: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)


def follow_chain(first_id: str, *, cwd: Path | None = None) -> ChainResult:
    """Walk the blocks chain starting at first_id.

    Returns a ChainResult with the chain list and reviewers/groups from the
    first review (all reviews in a series share the same reviewers).
    Raises if a review blocks more than one other review (ambiguous chain).
    """
    chain: list[tuple[str, str]] = []
    reviewers: list[str] = []
    groups: list[str] = []
    current = first_id

    while True:
        review = fetch_review(current, cwd=cwd)
        chain.append((review["id"], review["summary"]))
        if not chain[1:]:  # first review
            reviewers = review["target_people"]
            groups = review["target_groups"]
        blocks = review["blocks"]

        if not blocks:
            break
        if len(blocks) > 1:
            ids = ", ".join(blocks)
            raise SystemExit(
                f"Ambiguous chain: review {current} blocks multiple reviews: {ids}"
            )
        current = blocks[0]

    return ChainResult(chain=chain, reviewers=reviewers, groups=groups)
