"""The `gg db` subcommand -- inspect and manage the .gg/reviews.db state."""

from __future__ import annotations

import argparse

from gg import git, review_store


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the db subcommand."""
    p = subparsers.add_parser("db", help="inspect/manage .gg/reviews.db")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", default=True, help="list state (default)")
    group.add_argument("--clear", action="store_true", help="remove state for a branch")
    group.add_argument("--init", action="store_true", help="drop and recreate entire DB")
    p.add_argument("-b", "--branch", default=None, help="target branch (default: current)")
    p.set_defaults(func=run)


def _run_list(branch: str) -> int:
    reviews = review_store.load_reviews(branch)
    hashes = review_store.load_diff_hashes(branch)

    if not reviews and not hashes:
        print(f"No cached state for branch {branch}.")
        return 0

    print(f"Branch: {branch}")
    print()

    if reviews:
        print(f"Reviews ({len(reviews)}):")
        for r in reviews:
            print(f"  #{r.position}  r/{r.review_id}  {r.subject}  {r.diff_hash[:8]}")
    else:
        print("Reviews: none")

    print()
    print(f"Diff hashes: {len(hashes)} cached")
    return 0


def run(args: argparse.Namespace) -> int:
    """Execute the db subcommand."""
    if args.init:
        review_store.reinit_db()
        print("Reinitialized .gg/reviews.db")
        return 0

    branch = args.branch or git.branchname()

    if args.clear:
        review_store.clear_branch(branch)
        print(f"Cleared state for branch {branch}.")
        return 0

    return _run_list(branch)
