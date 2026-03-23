"""The `gg rbt-sync` subcommand -- reconcile commit series with ReviewBoard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gg import diff_cache, git, rb_api, review_store
from gg.matcher import ActionKind, NewCommit, SyncAction, reconcile
from gg.numbering import assign_numbers
from gg.rbt_close import close_discarded
from gg.rbt_post import post_one
from gg.sync_edit import edit_plan
from gg.sync_plan import format_plan

_BOLD = "\033[1m"
_RESET = "\033[0m"


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the rbt-sync subcommand."""
    p = subparsers.add_parser("rbt-sync", help="reconcile commit series with ReviewBoard")
    p.add_argument("-d", "--dry", action="store_true", help="plan only, don't execute")
    p.add_argument("-i", "--interactive", action="store_true", help="edit plan before executing")
    p.add_argument("--renumber", action="store_true", help="full renumber instead of fractional")
    p.add_argument("-p", "--publish", action="store_true", help="publish new/updated requests")
    p.add_argument("-v", "--verbose", action="store_true", help="show rbt output")
    p.add_argument(
        "-D", "--depends-on", default=None, metavar="ID",
        help="first patch depends on this review request ID",
    )
    p.add_argument("-U", "--users", action="append", default=[], help="reviewer (--target-people)")
    p.add_argument("-G", "--groups", action="append", default=[], help="review group (--target-groups)")
    p.add_argument("-n", "--no-numbers", action="store_true", help="don't number the patches")
    p.add_argument("-b", "--branch", default=None, help="explicit --branch for new reviews")
    p.add_argument("--new", action="store_true",
                   help="forget old reviews, post current commits as a fresh series")
    p.add_argument("range", nargs="?", default=None, help="revision range (default: tracking..HEAD)")
    p.set_defaults(func=run)


def _number_matches(old_entry: review_store.ReviewEntry, num_str: str, old_total: int) -> bool:
    """True when the old review already has the correct number prefix."""
    old_num_str = f"[{old_entry.position}/{old_total}]"
    return old_num_str == num_str


def _build_new_commits(revs: list[str], *, cwd: Path) -> list[NewCommit]:
    """Build NewCommit list from revision hashes."""
    commits = []
    for rev in revs:
        subject = git.summary(rev, cwd=cwd)
        h = diff_cache.diff_hash(rev, cwd=cwd)
        commits.append(NewCommit(rev=rev, subject=subject, diff_hash=h))
    return commits


def _execute(
    actions: list[SyncAction],
    *,
    branch_name: str,
    tracking: str,
    renumber: bool,
    publish: bool,
    verbose: bool,
    dry_run: bool,
    explicit_branch: str | None,
    initial_depends: str | None,
    reviewers: list[str] | None = None,
    groups: list[str] | None = None,
    no_numbers: bool = False,
    cwd: Path,
) -> list[review_store.ReviewEntry]:
    """Execute sync actions and return updated review entries.

    On partial failure, returns entries for completed actions.
    """
    numbered = assign_numbers(actions, renumber=renumber)
    old_total = sum(1 for a, _ in numbered if a.old_entry is not None)

    # Phase 1: discard removed reviews
    for action, _ in numbered:
        if action.kind == ActionKind.DISCARD and action.old_entry:
            close_discarded(
                action.old_entry.review_id,
                dry_run=dry_run, verbose=verbose, cwd=cwd,
            )

    # Phase 2: process non-discard actions in order
    entries: list[review_store.ReviewEntry] = []
    prev_review_id = initial_depends

    for action, num_str in numbered:
        if action.kind in (ActionKind.DISCARD, ActionKind.SKIP):
            continue

        assert action.new_commit is not None
        if no_numbers:
            num_prefix = ""
        else:
            num_prefix = f"{num_str}: " if num_str != "--" else ""

        if (action.kind == ActionKind.KEEP
                and not action.needs_dep_update
                and (not renumber
                     or _number_matches(action.old_entry, num_str, old_total))):
            # Nothing changed, preserve existing entry
            assert action.old_entry is not None
            entries.append(review_store.ReviewEntry(
                branch=branch_name,
                position=len(entries) + 1,
                review_id=action.old_entry.review_id,
                subject=review_store.strip_prefix(action.new_commit.subject),
                diff_hash=action.new_commit.diff_hash,
            ))
            prev_review_id = action.old_entry.review_id
            continue

        if action.kind == ActionKind.CREATE:
            if reviewers is not None or groups is not None:
                create_reviewers = reviewers or []
                create_groups = groups or []
            elif prev_review_id:
                create_reviewers, create_groups = rb_api.fetch_reviewers(
                    prev_review_id, cwd=cwd,
                )
            else:
                create_reviewers, create_groups = [], []
            result = post_one(
                action.new_commit.rev, tracking,
                first_post=True,
                publish=publish,
                dry_run=dry_run,
                verbose=verbose,
                reviewers=create_reviewers,
                groups=create_groups,
                explicit_branch=explicit_branch,
                num_string=num_prefix,
                depends_on=prev_review_id,
                cwd=cwd,
            )
            rid = result.review_id
        else:
            # UPDATE or KEEP_DEP: re-post with -r ID
            assert action.old_entry is not None
            result = post_one(
                action.new_commit.rev, tracking,
                review_id=action.old_entry.review_id,
                publish=publish,
                dry_run=dry_run,
                verbose=verbose,
                explicit_branch=explicit_branch,
                num_string=num_prefix,
                depends_on=prev_review_id,
                cwd=cwd,
            )
            rid = result.review_id or action.old_entry.review_id

        entries.append(review_store.ReviewEntry(
            branch=branch_name,
            position=len(entries) + 1,
            review_id=rid or "",
            subject=review_store.strip_prefix(action.new_commit.subject),
            diff_hash=action.new_commit.diff_hash,
        ))
        prev_review_id = rid

    return entries


def _format_summary(actions: list[SyncAction]) -> str:
    """One-line summary of sync action counts."""
    counts: dict[str, int] = {}
    for a in actions:
        if a.kind in (ActionKind.KEEP, ActionKind.KEEP_DEP):
            counts["kept"] = counts.get("kept", 0) + 1
        elif a.kind == ActionKind.UPDATE:
            counts["updated"] = counts.get("updated", 0) + 1
        elif a.kind == ActionKind.CREATE:
            counts["created"] = counts.get("created", 0) + 1
        elif a.kind == ActionKind.DISCARD:
            counts["discarded"] = counts.get("discarded", 0) + 1
        elif a.kind == ActionKind.SKIP:
            counts["skipped"] = counts.get("skipped", 0) + 1
    parts = []
    for key in ("kept", "updated", "created", "discarded", "skipped"):
        if key in counts:
            parts.append(f"{counts[key]} {key}")
    return "Synced: " + ", ".join(parts)


def run(args: argparse.Namespace) -> int:
    """Execute the rbt-sync subcommand."""
    cwd = Path.cwd()
    branch_name = git.branchname(cwd=cwd)
    tracking = git.tracking_branch(cwd=cwd)

    range_spec = args.range or git.rev_range(cwd=cwd)
    revs = git.list_revs(range_spec, cwd=cwd)

    if not revs:
        print("No commits in range.")
        return 1

    old = review_store.load_reviews(branch_name, cwd=cwd)
    if not old and not args.new:
        print("No existing reviews found. Use `gg rbt` to post the initial series.")
        return 1

    new = _build_new_commits(revs, cwd=cwd)
    actions = reconcile([] if args.new else old, new)

    if args.interactive:
        edited = edit_plan(actions, renumber=args.renumber)
        if edited is None:
            print("Aborted.")
            return 0
        actions = edited

    # Show plan
    plan = format_plan(
        actions, renumber=args.renumber, publish=args.publish,
        reviewers=args.users, groups=args.groups,
    )
    print(plan)

    if args.dry:
        return 0

    print()
    entries = _execute(
        actions,
        branch_name=branch_name,
        tracking=tracking,
        renumber=args.renumber,
        publish=args.publish,
        verbose=args.verbose,
        dry_run=False,
        explicit_branch=args.branch,
        initial_depends=args.depends_on,
        reviewers=args.users or None,
        groups=args.groups or None,
        no_numbers=args.no_numbers,
        cwd=cwd,
    )

    # Preserve skipped-discard entries so they reappear next sync
    for a in actions:
        if a.kind == ActionKind.SKIP and a.old_entry and not a.new_commit:
            entries.append(review_store.ReviewEntry(
                branch=branch_name,
                position=len(entries) + 1,
                review_id=a.old_entry.review_id,
                subject=a.old_entry.subject,
                diff_hash=a.old_entry.diff_hash,
            ))

    print(_format_summary(actions), file=sys.stderr)

    # Save state
    if entries:
        review_store.save_reviews(entries, cwd=cwd)
        new_hashes = {e.diff_hash for e in entries}
        diff_cache.save_hashes(new_hashes, cwd=cwd, branch=branch_name)

    return 0
