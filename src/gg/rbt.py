"""The `gg rbt` subcommand -- post commit series to ReviewBoard."""

from __future__ import annotations

import argparse
from pathlib import Path

from gg import diff_cache, git
from gg.rbt_post import post_one


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the rbt subcommand."""
    p = subparsers.add_parser("rbt", help="post commits to ReviewBoard")
    p.add_argument("-d", "--dry", action="store_true", help="print rbt commands without executing")
    p.add_argument("-n", "--no-numbers", action="store_true", help="don't number the patches")
    p.add_argument("-p", "--publish", action="store_true", help="publish review requests")
    p.add_argument("-U", "--users", action="append", default=[], help="reviewer (--target-people)")
    p.add_argument("-G", "--groups", action="append", default=[], help="review group (--target-groups)")
    p.add_argument("-b", "--branch", default=None, help="explicit branch for --branch arg")
    p.add_argument("-u", "--update", action="store_true", help="update existing review requests")
    p.add_argument(
        "-C", "--continue-from", type=int, default=0, metavar="N",
        help="continue numbering from patch N",
    )
    p.add_argument(
        "-D", "--depends-on", default=None, metavar="ID",
        help="first patch depends on review request ID",
    )
    p.add_argument("range", nargs="?", default=None, help="revision range (default: tracking..HEAD)")
    p.set_defaults(func=run)


def _is_unchanged(rev: str, cached: set[str], cwd: Path) -> tuple[bool, str]:
    """Check if a commit's diff matches the cache. Returns (unchanged, hash)."""
    h = diff_cache.diff_hash(rev, cwd=cwd)
    return h in cached, h


def run(args: argparse.Namespace) -> int:
    """Execute the rbt subcommand."""
    cwd = Path.cwd()
    first_post = not args.update

    # rbt is not happy with reviewer options passed during update
    reviewers = args.users if first_post else []
    groups = args.groups if first_post else []

    range_spec = args.range or git.rev_range(cwd=cwd)
    revs = git.list_revs(range_spec, cwd=cwd)

    if not revs:
        print("No commits to post.")
        return 1

    tracking = git.tracking_branch(cwd=cwd)
    continue_from = args.continue_from
    total = len(revs) + continue_from
    depends = args.depends_on

    cached = diff_cache.load_hashes(cwd=cwd) if args.update else set()
    new_hashes: set[str] = set()

    # Single commit without --continue: no numbering
    if len(revs) == 1 and continue_from == 0:
        rev = revs[0]
        unchanged, h = _is_unchanged(rev, cached, cwd)
        new_hashes.add(h)

        if args.update and unchanged:
            summary_text = git.summary(rev, cwd=cwd)
            print(f"skip (unchanged): {summary_text}")
        else:
            post_one(
                rev, tracking,
                first_post=first_post,
                publish=args.publish,
                dry_run=args.dry,
                reviewers=reviewers,
                groups=groups,
                explicit_branch=args.branch,
                depends_on=depends,
                cwd=cwd,
            )

        if not args.dry:
            diff_cache.save_hashes(new_hashes, cwd=cwd)
        return 0

    # Multiple commits: loop with numbering and dependency chaining
    for idx, rev in enumerate(revs, start=continue_from + 1):
        unchanged, h = _is_unchanged(rev, cached, cwd)
        new_hashes.add(h)

        if args.update and unchanged:
            summary_text = git.summary(rev, cwd=cwd)
            print(f"skip (unchanged): {summary_text}")
            continue

        if args.no_numbers:
            num_string = ""
        else:
            num_string = f"[{idx}/{total}]: "

        result = post_one(
            rev, tracking,
            first_post=first_post,
            publish=args.publish,
            dry_run=args.dry,
            reviewers=reviewers,
            groups=groups,
            explicit_branch=args.branch,
            num_string=num_string,
            depends_on=depends,
            cwd=cwd,
        )

        if result.review_id:
            depends = result.review_id

    if not args.dry:
        diff_cache.save_hashes(new_hashes, cwd=cwd)
    return 0
