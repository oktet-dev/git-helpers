"""Thin wrapper around `rbt api-get` for querying ReviewBoard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def fetch_review(review_id: str, *, cwd: Path | None = None) -> dict:
    """Fetch a review request and return {id, summary, blocks}."""
    r = subprocess.run(
        ["rbt", "api-get", f"/api/review-requests/{review_id}/"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(r.stdout)
    rr = data["review_request"]
    return {
        "id": str(rr["id"]),
        "summary": rr["summary"],
        "blocks": [str(b["id"]) for b in rr.get("blocks", [])],
    }


def follow_chain(first_id: str, *, cwd: Path | None = None) -> list[tuple[str, str]]:
    """Walk the blocks chain starting at first_id.

    Returns [(review_id, summary), ...] in chain order.
    Raises if a review blocks more than one other review (ambiguous chain).
    """
    chain: list[tuple[str, str]] = []
    current = first_id

    while True:
        review = fetch_review(current, cwd=cwd)
        chain.append((review["id"], review["summary"]))
        blocks = review["blocks"]

        if not blocks:
            break
        if len(blocks) > 1:
            ids = ", ".join(blocks)
            raise SystemExit(
                f"Ambiguous chain: review {current} blocks multiple reviews: {ids}"
            )
        current = blocks[0]

    return chain
