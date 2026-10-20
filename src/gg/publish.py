"""The `gg publish` subcommand -- publish drafts of all reviews on a branch."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from gg import git, review_store
from gg.rb_links import links_for
from gg.rbt_publish import publish_one


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the publish subcommand."""
    p = subparsers.add_parser(
        "publish",
        help="publish the drafts gg left on the branch's review requests",
    )
    p.add_argument("-d", "--dry", action="store_true", help="print rbt commands without executing")
    p.add_argument("-v", "--verbose", action="store_true", help="show rbt output")
    p.add_argument("-b", "--branch", default=None, help="target branch (default: current)")
    p.add_argument("-a", "--all", action="store_true",
                   help="try every review of the branch, also those gg knows no draft "
                        "on (e.g. drafts made in the browser)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the publish subcommand."""
    cwd = Path.cwd()
    branch = args.branch or git.branchname(cwd=cwd)

    recorded = review_store.load_reviews(branch, cwd=cwd)
    if not recorded:
        print(f"No reviews recorded for branch {branch}.", file=sys.stderr)
        return 1

    entries = recorded if args.all else [e for e in recorded if e.draft]
    if not entries:
        print(
            f"No unpublished drafts recorded for branch {branch} "
            "(gg publish --all tries every review)."
        )
        return 0

    links = links_for(cwd=cwd)
    for e in entries:
        print(f"  publish {links.show(e.review_id)}  {e.subject}")

    if args.dry:
        for e in entries:
            publish_one(e.review_id, dry_run=True, verbose=args.verbose, cwd=cwd)
        return 0

    published: set[str] = set()
    for e in entries:
        if publish_one(e.review_id, dry_run=False, verbose=args.verbose, cwd=cwd) == 0:
            published.add(e.review_id)

    if published:
        review_store.save_reviews(
            [replace(e, draft=False) if e.review_id in published else e
             for e in recorded],
            cwd=cwd,
        )

    failures = len(entries) - len(published)
    if failures:
        print(f"[gg] {failures} of {len(entries)} publish call(s) failed.", file=sys.stderr)
        return 1

    print(f"Published {len(entries)} review(s).", file=sys.stderr)
    return 0
