"""The `gg rbt-sync` subcommand -- reconcile commit series with ReviewBoard."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from gg import diff_cache, git, rb_api, review_store
from gg.matcher import ActionKind, NewCommit, SyncAction
from gg.numbering import number_series
from gg.rbt_close import close_discarded, close_submitted
from gg.rbt_post import post_one
from gg.rbt_publish import publish_one
from gg.series import SeriesPlan, build_plan
from gg.sync_edit import edit_plan
from gg.sync_plan import format_series_plan, format_summary

_BOLD = "\033[1m"
_RESET = "\033[0m"

# Diff hash recorded for a review whose planned post did not happen: it
# matches no commit, so the next run plans an update (which also re-applies
# the review's dependency).
_POST_FAILED = "post-failed"


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the rbt-sync subcommand."""
    p = subparsers.add_parser("rbt-sync", help="reconcile commit series with ReviewBoard")
    p.add_argument("-d", "--dry", action="store_true", help="plan only, don't execute")
    p.add_argument("-i", "--interactive", action="store_true", help="edit plan before executing")
    p.add_argument("-f", "--force", action="store_true",
                   help="re-post every matched commit, ignoring diff hash")
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
    p.add_argument("--close", action="store_true",
                   help="close all reviews as submitted and clear DB")
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


def _all_actions(plan: SeriesPlan) -> list[SyncAction]:
    """Every action of the plan: live series in order, then gone groups."""
    actions = [a for s in plan.series for a in s.actions]
    actions += [a for g in plan.gone for a in g.actions]
    return actions


def _old_series_sizes(plan: SeriesPlan) -> Counter[tuple[str, int]]:
    """Number of recorded entries the plan holds per old series.

    Keyed by (recording branch, old series number). Every entry of this
    branch's record is in the plan, matched or gone, so for its own series
    this is the size the series was posted with.
    """
    return Counter(
        (a.old_entry.branch, a.old_entry.series)
        for a in _all_actions(plan) if a.old_entry is not None
    )


def _execute(
    plan: SeriesPlan,
    *,
    branch_name: str,
    tracking: str,
    renumber: bool,
    publish: bool,
    verbose: bool,
    explicit_branch: str | None,
    initial_depends: str | None,
    reviewers: list[str] | None = None,
    groups: list[str] | None = None,
    no_numbers: bool = False,
    cwd: Path,
) -> tuple[list[review_store.ReviewEntry], bool]:
    """Execute a series plan.

    Returns the live series' review entries, in branch order with
    positions 1..M and their series number, and whether a post failed.
    Posting stops at the first failed post. Every review that was due a
    post which did not happen is still returned, with the diff hash
    `_POST_FAILED` so that the next run re-posts it; creates that did not
    happen are left out.
    """
    # Phase 1: close reviews whose commits left the branch. Forgotten
    # (still recorded on another branch) and skipped ones stay open.
    for group in plan.gone:
        for action in group.actions:
            if action.old_entry is None:
                continue
            if action.kind == ActionKind.DISCARD:
                close_discarded(action.old_entry.review_id, verbose=verbose, cwd=cwd)
            elif action.kind == ActionKind.SUBMIT:
                close_submitted(action.old_entry.review_id, verbose=verbose, cwd=cwd)

    # Phase 2: post the live series in order; the dependency chain runs
    # across series.
    old_sizes = _old_series_sizes(plan)
    entries: list[review_store.ReviewEntry] = []
    prev_review_id = initial_depends
    failed = False

    def record(review_id: str, subject: str, diff_hash: str, series: int) -> None:
        entries.append(review_store.ReviewEntry(
            branch=branch_name,
            position=len(entries) + 1,
            review_id=review_id,
            subject=subject,
            diff_hash=diff_hash,
            series=series,
        ))

    for series in plan.series:
        for action, num_str in number_series(series, renumber=renumber):
            if action.kind in (ActionKind.DISCARD, ActionKind.SKIP):
                continue

            assert action.new_commit is not None
            old = action.old_entry
            subject = review_store.strip_prefix(action.new_commit.subject)

            if action.kind == ActionKind.KEEP and action.outside_range:
                # Outside the range: left untouched, recorded as it was so a
                # later run still sees what changed.
                assert old is not None
                record(old.review_id, old.subject, old.diff_hash, series.number)
                prev_review_id = old.review_id
                continue

            if no_numbers:
                num_prefix = ""
            else:
                num_prefix = f"{num_str}: " if num_str != "--" else ""

            if (action.kind == ActionKind.KEEP
                    and not action.needs_dep_update
                    and (not renumber
                         or _number_matches(
                             old, num_str, old_sizes[(old.branch, old.series)]))):
                # Nothing about the commit changed. If --publish was requested,
                # still publish the (possibly draft) review request -- unless
                # posting already stopped.
                assert old is not None
                if publish and not failed:
                    publish_one(old.review_id, verbose=verbose, cwd=cwd)
                record(old.review_id, subject, action.new_commit.diff_hash, series.number)
                prev_review_id = old.review_id
                continue

            if failed:
                # Posting stopped: keep the review, due for a re-post.
                if old is not None:
                    record(old.review_id, subject, _POST_FAILED, series.number)
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
                # UPDATE, KEEP_DEP or a renumbered KEEP: re-post with -r ID
                assert old is not None
                result = post_one(
                    action.new_commit.rev, tracking,
                    review_id=old.review_id,
                    publish=publish,
                    verbose=verbose,
                    explicit_branch=explicit_branch,
                    num_string=num_prefix,
                    depends_on=prev_review_id,
                    cwd=cwd,
                )
                rid = result.review_id or old.review_id

            if result.returncode != 0 or not rid:
                # Stop here: posting on would chain later patches to the
                # wrong review.
                failed = True
                if old is not None:
                    record(old.review_id, subject, _POST_FAILED, series.number)
                continue

            record(rid, subject, action.new_commit.diff_hash, series.number)
            prev_review_id = rid

    return entries, failed


def _edit_interactively(plan: SeriesPlan, *, renumber: bool) -> bool:
    """Let the user edit the plan's action kinds in $EDITOR.

    The editor sees one flat list: live series in order, then gone groups.
    Returns False when the user aborted.
    """
    actions = _all_actions(plan)
    edited = edit_plan(actions, renumber=renumber)
    if edited is None:
        return False
    for action, new in zip(actions, edited):
        action.kind = new.kind
        action.needs_dep_update = new.needs_dep_update
    return True


def run(args: argparse.Namespace) -> int:
    """Execute the rbt-sync subcommand."""
    cwd = Path.cwd()
    branch_name = git.branchname(cwd=cwd)
    tracking = git.tracking_branch(cwd=cwd)

    if args.close:
        old = review_store.load_reviews(branch_name, cwd=cwd)
        if not old:
            print("No reviews to close.")
            return 1
        for entry in old:
            print(f"  close r/{entry.review_id}  {entry.subject}")
        if args.dry:
            return 0
        for entry in old:
            close_submitted(entry.review_id, verbose=args.verbose, cwd=cwd)
        review_store.clear_branch(branch_name, cwd=cwd)
        print(f"Closed {len(old)} review(s) as submitted.", file=sys.stderr)
        return 0

    # The whole branch decides series and what is gone; the range only
    # limits which commits are acted on.
    base = git.range_base(cwd=cwd)
    full = git.list_revs(f"{base}..HEAD", cwd=cwd)
    if args.range:
        selected = git.list_revs(args.range, cwd=cwd)
        on_branch = set(full)
        if any(rev not in on_branch for rev in selected):
            print(
                f"[gg] {args.range} has commits that are not on "
                f"'{branch_name}' ({base}..HEAD).",
                file=sys.stderr,
            )
            return 1
    else:
        selected = full

    if not selected:
        print("No commits in range.")
        return 1

    own = [] if args.new else review_store.load_reviews(branch_name, cwd=cwd)
    foreign = {
        b: review_store.load_reviews(b, cwd=cwd)
        for b in review_store.list_branches(cwd=cwd)
        if b != branch_name
    }
    plan = build_plan(
        branch=branch_name,
        own=own,
        foreign=foreign,
        commits=_build_new_commits(full, cwd=cwd),
        in_range=set(selected),
        landed_subjects=lambda: git.upstream_subjects(base, cwd=cwd),
        branch_exists=lambda name: git.branch_exists(name, cwd=cwd),
        depends_on_of=lambda review_id: rb_api.fetch_depends_on(review_id, cwd=cwd),
        initial_depends=args.depends_on,
    )
    for warning in dict.fromkeys(plan.warnings):
        print(f"[gg] {warning}", file=sys.stderr)
    if not own and not plan.adopted_from and not args.new:
        print(
            "[gg] No existing reviews; posting as a fresh series.",
            file=sys.stderr,
        )

    if args.interactive and not _edit_interactively(plan, renumber=args.renumber):
        print("Aborted.")
        return 0

    # --force: re-post every matched commit in the range, ignoring diff
    # hash. Runs AFTER the interactive editor so the user can still skip
    # individual entries from the plan.
    if args.force:
        for series in plan.series:
            for a in series.actions:
                if a.kind in (ActionKind.KEEP, ActionKind.KEEP_DEP) and not a.outside_range:
                    a.kind = ActionKind.UPDATE
                    a.needs_dep_update = False

    print(format_series_plan(
        plan,
        upstream=base,
        renumber=args.renumber,
        publish=args.publish,
        reviewers=args.users,
        groups=args.groups,
        force=args.force,
    ))

    if args.dry:
        return 0

    print()
    entries, failed = _execute(
        plan,
        branch_name=branch_name,
        tracking=tracking,
        renumber=args.renumber,
        publish=args.publish,
        verbose=args.verbose,
        explicit_branch=args.branch,
        initial_depends=args.depends_on,
        reviewers=args.users or None,
        groups=args.groups or None,
        no_numbers=args.no_numbers,
        cwd=cwd,
    )

    # Preserve skipped gone entries so they reappear next sync
    for group in plan.gone:
        for a in group.actions:
            if a.kind == ActionKind.SKIP and a.old_entry:
                entries.append(review_store.ReviewEntry(
                    branch=branch_name,
                    position=len(entries) + 1,
                    review_id=a.old_entry.review_id,
                    subject=a.old_entry.subject,
                    diff_hash=a.old_entry.diff_hash,
                    series=a.old_entry.series,
                ))

    # Save state, also after a failed post: what was done stays recorded.
    if entries:
        review_store.save_reviews(entries, cwd=cwd)
        new_hashes = {e.diff_hash for e in entries if e.diff_hash != _POST_FAILED}
        diff_cache.save_hashes(new_hashes, cwd=cwd, branch=branch_name)
        # Reviews adopted from a branch that is gone (renamed or deleted)
        # now live on this branch only: drop them from its record. Its
        # other reviews stay there (still open on ReviewBoard).
        recorded = {e.review_id for e in entries if e.review_id}
        for b in sorted(plan.adopted_from):
            if not git.branch_exists(b, cwd=cwd):
                review_store.remove_reviews(b, recorded, cwd=cwd)

    if failed:
        print(
            "[gg] stopped after a failed post; run gg rbt-sync again to retry.",
            file=sys.stderr,
        )
        return 1
    print(format_summary(plan), file=sys.stderr)
    return 0
