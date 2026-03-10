"""The `gg rbt-import` subcommand -- populate reviews.db from existing RB series."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gg import diff_cache, git, rb_api, review_store


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


def _format_table(
    entries: list[review_store.ReviewEntry],
    skipped: list[tuple[str, str]],
) -> str:
    """Format review entries and skipped commits as a table."""
    header = f"{'#':<6} {'Review':<10} Subject"
    lines = [header, "-" * len(header)]
    for e in entries:
        lines.append(f"{e.position:<6} r/{e.review_id:<7} {e.subject}")
    if skipped:
        lines.append("")
        lines.append("Skipped commits (no matching review):")
        for rev, subject in skipped:
            lines.append(f"  {rev}  {subject}")
    return "\n".join(lines)


def _match_by_subject(
    revs: list[str],
    chain: list[tuple[str, str]],
    *,
    cwd: Path,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Align reviews to commits by matching stripped subjects.

    Returns (matched, skipped) where:
      matched: [(rev, review_id, review_summary), ...]
      skipped: [(rev, subject), ...] for unmatched commits
    """
    # Build commit subject list
    commit_subjects = [
        (rev, review_store.strip_prefix(git.summary(rev, cwd=cwd)))
        for rev in revs
    ]

    matched: list[tuple[str, str, str]] = []
    skipped: list[tuple[str, str]] = []
    chain_idx = 0

    for rev, subject in commit_subjects:
        if chain_idx >= len(chain):
            skipped.append((rev, subject))
            continue

        review_id, review_summary = chain[chain_idx]
        stripped_review = review_store.strip_prefix(review_summary)

        if subject == stripped_review:
            matched.append((rev, review_id, stripped_review))
            chain_idx += 1
        else:
            skipped.append((rev, subject))

    # Any remaining reviews had no matching commit
    if chain_idx < len(chain):
        unmatched = [rid for rid, _ in chain[chain_idx:]]
        raise SystemExit(
            f"Could not match reviews to commits. "
            f"Unmatched reviews: {', '.join(unmatched)}"
        )

    return matched, skipped


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
    matched, skipped = _match_by_subject(revs, chain, cwd=cwd)

    # Check for existing entries and warn
    existing = review_store.load_reviews(branch_name, cwd=cwd)
    if existing:
        print("Warning: overwriting existing review entries.", file=sys.stderr)

    entries: list[review_store.ReviewEntry] = []
    hashes: set[str] = set()

    for idx, (rev, review_id, _summary) in enumerate(matched, start=1):
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

    print(_format_table(entries, skipped))

    if args.dry:
        return 0

    review_store.save_reviews(entries, cwd=cwd)
    diff_cache.save_hashes(hashes, cwd=cwd, branch=branch_name)
    return 0
