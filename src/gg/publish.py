"""The `gg publish` subcommand -- publish drafts of all reviews on a branch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gg import git, review_store
from gg.rbt_publish import publish_one


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the publish subcommand."""
    p = subparsers.add_parser(
        "publish",
        help="publish drafts of every review request in the current branch",
    )
    p.add_argument("-d", "--dry", action="store_true", help="print rbt commands without executing")
    p.add_argument("-v", "--verbose", action="store_true", help="show rbt output")
    p.add_argument("-b", "--branch", default=None, help="target branch (default: current)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the publish subcommand."""
    cwd = Path.cwd()
    branch = args.branch or git.branchname(cwd=cwd)

    entries = review_store.load_reviews(branch, cwd=cwd)
    if not entries:
        print(f"No reviews recorded for branch {branch}.", file=sys.stderr)
        return 1

    for e in entries:
        print(f"  publish r/{e.review_id}  {e.subject}")

    if args.dry:
        for e in entries:
            publish_one(e.review_id, dry_run=True, verbose=args.verbose, cwd=cwd)
        return 0

    failures = 0
    for e in entries:
        rc = publish_one(e.review_id, dry_run=False, verbose=args.verbose, cwd=cwd)
        if rc != 0:
            failures += 1

    if failures:
        print(f"[gg] {failures} of {len(entries)} publish call(s) failed.", file=sys.stderr)
        return 1

    print(f"Published {len(entries)} review(s).", file=sys.stderr)
    return 0
