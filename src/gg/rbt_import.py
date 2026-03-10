"""The `gg rbt-import` subcommand -- populate reviews.db from existing RB series."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gg import diff_cache, git, rb_api, review_store
from gg.sync_plan import format_plan
from gg.matcher import ActionKind, SyncAction


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the rbt-import subcommand."""
    p = subparsers.add_parser("rbt-import", help="import existing RB series into reviews.db")
    p.add_argument("-d", "--dry", action="store_true", help="show what would be imported")
    p.add_argument(
        "--range", default=None, metavar="RANGE",
        help="revision range (default: tracking..HEAD)",
    )
    p.add_argument("first_review_id", metavar="FIRST_REVIEW_ID", help="first review request ID")
    p.set_defaults(func=run)


def _format_table(entries: list[review_store.ReviewEntry]) -> str:
    """Format review entries as a table."""
    header = f"{'#':<6} {'Review':<10} Subject"
    lines = [header, "-" * len(header)]
    for e in entries:
        lines.append(f"{e.position:<6} r/{e.review_id:<7} {e.subject}")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    """Execute the rbt-import subcommand."""
    cwd = Path.cwd()
    branch_name = git.branchname(cwd=cwd)
    range_spec = args.range or git.rev_range(cwd=cwd)
    revs = git.list_revs(range_spec, cwd=cwd)

    if not revs:
        print("No commits in range.")
        return 1

    chain = rb_api.follow_chain(args.first_review_id, cwd=cwd)

    if len(chain) != len(revs):
        print(
            f"Mismatch: {len(revs)} commits but {len(chain)} reviews in chain.",
            file=sys.stderr,
        )
        return 1

    # Check for existing entries and warn
    existing = review_store.load_reviews(branch_name, cwd=cwd)
    if existing:
        print("Warning: overwriting existing review entries.", file=sys.stderr)

    entries: list[review_store.ReviewEntry] = []
    hashes: set[str] = set()

    for idx, (rev, (review_id, _summary)) in enumerate(zip(revs, chain), start=1):
        subject = review_store.strip_prefix(git.summary(rev, cwd=cwd))
        h = diff_cache.diff_hash(rev, cwd=cwd)
        hashes.add(h)
        entries.append(review_store.ReviewEntry(
            branch=branch_name,
            position=idx,
            review_id=review_id,
            subject=subject,
            diff_hash=h,
        ))

    print(_format_table(entries))

    if args.dry:
        return 0

    review_store.save_reviews(entries, cwd=cwd)
    diff_cache.save_hashes(hashes, cwd=cwd, branch=branch_name)
    return 0
