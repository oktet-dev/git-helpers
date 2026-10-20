"""The `gg rbt-sync` subcommand -- reconcile commit series with ReviewBoard."""

from __future__ import annotations

import argparse
import functools
import sys
from collections import Counter
from dataclasses import dataclass, replace
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
                   help="forget this branch's own record and post its commits fresh; "
                        "series recorded under other branches are still picked up")
    p.add_argument("--close", action="store_true",
                   help="close all reviews as submitted and clear DB")
    p.add_argument("-s", "--series", action="append", default=[], type=_series_spec,
                   metavar="N[,N...]|N:P[-Q][,...]",
                   help="act only on these series, or on patches P..Q of series N "
                        "(numbers as shown by -d; $ is the last series or patch); "
                        "repeatable; not with a range")
    p.add_argument("range", nargs="?", default=None, help="revision range (default: tracking..HEAD)")
    p.set_defaults(func=run)


# One -s selection: a series number and its patch ranges (None: all patches).
SeriesSpec = tuple[int, list[tuple[int, int]] | None]


# "$" in a -s value: the last series of the plan, or the last patch of a series.
_LAST = 0


def _positive(token: str, what: str) -> int:
    if token.strip() == "$":
        return _LAST
    if not token.strip().isdigit() or int(token) < 1:
        raise argparse.ArgumentTypeError(f"invalid series {what}: {token!r}")
    return int(token)


def _series_spec(text: str) -> list[SeriesSpec]:
    """Parse a -s value: "3", "3,4" (series) or "4:1-3,5" (patches of series 4).

    "$" stands for the last series ("$:1-3") or the last patch ("4:2-$").
    """
    if ":" not in text:
        return [(_positive(token, "number"), None) for token in text.split(",")]
    number, patches = text.split(":", 1)
    ranges = []
    for token in patches.split(","):
        first, _, last = token.partition("-")
        a = _positive(first, "patch")
        b = _positive(last, "patch") if last else a
        if (a == _LAST and b != _LAST) or (_LAST not in (a, b) and b < a):
            raise argparse.ArgumentTypeError(f"invalid series patch range: {token!r}")
        ranges.append((a, b))
    return [(_positive(number, "number"), ranges)]


def _selected_revs(
    plan: SeriesPlan, specs: list[SeriesSpec], *, branch_name: str,
) -> set[str] | str:
    """Commits picked by -s specs in a whole-branch plan, or an error message."""
    revs: set[str] = set()
    for number, ranges in specs:
        name = str(number)
        if number == _LAST:
            number = len(plan.series)
            name = f"{number} ($)"
        if number > len(plan.series):
            return f"no series {number} on '{branch_name}' (it has {len(plan.series)})."
        series = plan.series[number - 1]
        actions = series.actions
        last = len(actions)
        spans = [(1, last)] if ranges is None else [
            (last if a == _LAST else a, last if b == _LAST else b) for a, b in ranges
        ]
        top = max(max(span) for span in spans)
        if top > last:
            return f"series {name} has {last} patches; no patch {top}."
        picked = [k for a, b in spans for k in range(a, b + 1)]
        if series.new and min(picked) != 1:
            return (
                f"series {name} is new: the selection must start at patch 1 "
                "(patches left before it would later join the series below)."
            )
        revs.update(actions[k - 1].new_commit.rev for k in picked)
    return revs


def _number_matches(action: SyncAction, num_str: str, old_total: int) -> bool:
    """True when the review already has the correct number prefix.

    It was posted with its rank as recorded, not the one it is shown with
    now (which no longer counts reviews that left its series).
    """
    assert action.old_entry is not None
    rank = action.old_rank if action.old_rank is not None else action.old_entry.position
    return f"[{rank}/{old_total}]" == num_str


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


def _entry(
    branch: str, review_id: str, subject: str, diff_hash: str, series: int,
) -> review_store.ReviewEntry:
    """A record entry; its position is set when the record is saved."""
    return review_store.ReviewEntry(
        branch=branch, position=0, review_id=review_id, subject=subject,
        diff_hash=diff_hash, series=series,
    )


def _close_gone(plan: SeriesPlan, *, verbose: bool, cwd: Path) -> None:
    """Phase 1: close reviews whose commits left the branch.

    Forgotten (still recorded on another branch) and skipped ones stay
    open. A review whose close fails becomes skipped: it stays in the
    record, so the next run tries again.
    """
    for group in plan.gone:
        for action in group.actions:
            if action.old_entry is None:
                continue
            review_id = action.old_entry.review_id
            if action.kind == ActionKind.DISCARD:
                rc = close_discarded(review_id, verbose=verbose, cwd=cwd)
            elif action.kind == ActionKind.SUBMIT:
                rc = close_submitted(review_id, verbose=verbose, cwd=cwd)
            else:
                continue
            if rc != 0:
                print(f"[gg] failed to close r/{review_id}", file=sys.stderr)
                action.kind = ActionKind.SKIP


@dataclass
class _Row:
    """A live row of the plan, in posting order."""

    action: SyncAction
    num_str: str                          # its [i/N] label
    series: int                           # its live series number
    post: bool                            # whether the plan posts it
    # What the record keeps for it unless a post is done: a row not to be
    # posted keeps its entry; a review due a post is kept with the diff
    # hash `_POST_FAILED`, so that the next run re-posts it; a create has
    # nothing to keep.
    held: review_store.ReviewEntry | None


def _live_rows(plan: SeriesPlan, *, branch_name: str, renumber: bool) -> list[_Row]:
    """The live series' rows to post or keep, in branch order."""
    old_sizes = _old_series_sizes(plan)
    rows: list[_Row] = []
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
                post = False
                held = _entry(branch_name, old.review_id, old.subject, old.diff_hash,
                              series.number)
            elif (action.kind == ActionKind.KEEP
                    and not action.needs_dep_update
                    and (not renumber
                         or _number_matches(
                             action, num_str, old_sizes[(old.branch, old.series)]))):
                # Nothing about the commit changed.
                assert old is not None
                post = False
                held = _entry(branch_name, old.review_id, subject,
                              action.new_commit.diff_hash, series.number)
            else:
                post = True
                held = None if old is None else _entry(
                    branch_name, old.review_id, subject, _POST_FAILED, series.number)
            rows.append(_Row(action, num_str, series.number, post, held))
    return rows


def _execute(
    rows: list[_Row],
    record: list[review_store.ReviewEntry | None],
    *,
    branch_name: str,
    tracking: str,
    publish: bool,
    verbose: bool,
    explicit_branch: str | None,
    initial_depends: str | None,
    reviewers: list[str] | None = None,
    groups: list[str] | None = None,
    no_numbers: bool = False,
    cwd: Path,
) -> bool:
    """Phase 2: post the live rows in order; the chain runs across series.

    `record[i]` is what the branch's record keeps for `rows[i]`: its held
    entry until its post is done, then the posted review. Posting stops at
    the first failed post; returns whether one failed. On an exception the
    rows not done keep their held entries.
    """
    prev_review_id = initial_depends
    for i, row in enumerate(rows):
        action = row.action
        assert action.new_commit is not None
        old = action.old_entry

        if not row.post:
            # Nothing to post. If --publish was requested, still publish
            # the (possibly draft) review request, unless it is outside
            # the range.
            assert old is not None
            if publish and not action.outside_range:
                publish_one(old.review_id, verbose=verbose, cwd=cwd)
            prev_review_id = old.review_id
            continue

        if no_numbers or row.num_str == "--":
            num_prefix = ""
        else:
            num_prefix = f"{row.num_str}: "

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

        subject = review_store.strip_prefix(action.new_commit.subject)
        if result.returncode != 0 or not rid:
            if rid and old is None:
                # rbt failed after creating the review: it exists on
                # ReviewBoard, so keep it, due a re-post.
                record[i] = _entry(branch_name, rid, subject, _POST_FAILED, row.series)
            # Stop here: posting on would chain later patches to the
            # wrong review.
            return True

        record[i] = _entry(
            branch_name, rid, subject, action.new_commit.diff_hash, row.series,
        )
        prev_review_id = rid

    return False


def _save(
    plan: SeriesPlan,
    live: list[review_store.ReviewEntry],
    *,
    branch_name: str,
    cwd: Path,
) -> None:
    """Save the branch's record: the live entries, then skipped gone ones."""
    entries = list(live)
    # Preserve skipped gone entries so they reappear next sync
    for group in plan.gone:
        for a in group.actions:
            if a.kind == ActionKind.SKIP and a.old_entry:
                entries.append(_entry(
                    branch_name, a.old_entry.review_id, a.old_entry.subject,
                    a.old_entry.diff_hash, a.old_entry.series,
                ))
    entries = [replace(e, position=n) for n, e in enumerate(entries, 1)]

    if not entries:
        # No review is left on the branch (say every live row is a create
        # that did not happen): the record must still drop the reviews
        # closed or forgotten above.
        review_store.clear_branch(branch_name, cwd=cwd)
        return

    review_store.save_reviews(entries, cwd=cwd)
    new_hashes = {e.diff_hash for e in entries if e.diff_hash != _POST_FAILED}
    diff_cache.save_hashes(new_hashes, cwd=cwd, branch=branch_name)
    # Reviews adopted from a branch that is gone (renamed or deleted) now
    # live on this branch only: drop them from its record. Its other
    # reviews stay there (still open on ReviewBoard).
    recorded = {e.review_id for e in entries if e.review_id}
    for b in sorted(plan.adopted_from):
        if not git.branch_exists(b, cwd=cwd):
            review_store.remove_reviews(b, recorded, cwd=cwd)


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

    specs = [spec for specs_of_one in args.series for spec in specs_of_one]
    if specs and args.range:
        print("[gg] -s and a range cannot be combined.", file=sys.stderr)
        return 1

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
    commits = _build_new_commits(full, cwd=cwd)
    # -s plans the branch twice; cache the lookups so RB is read once.
    landed = functools.cache(lambda: git.upstream_subjects(base, cwd=cwd))
    depends_on_of = functools.cache(
        lambda review_id: rb_api.fetch_depends_on(review_id, cwd=cwd),
    )

    def plan_for(in_range: set[str]) -> SeriesPlan:
        return build_plan(
            branch=branch_name,
            own=own,
            foreign=foreign,
            commits=commits,
            in_range=in_range,
            landed_subjects=landed,
            branch_exists=lambda name: git.branch_exists(name, cwd=cwd),
            depends_on_of=depends_on_of,
            initial_depends=args.depends_on,
        )

    if specs:
        revs = _selected_revs(plan_for(set(full)), specs, branch_name=branch_name)
        if isinstance(revs, str):
            print(f"[gg] {revs}", file=sys.stderr)
            return 1
        selected = [rev for rev in full if rev in revs]

    plan = plan_for(set(selected))
    for warning in dict.fromkeys(plan.warnings):
        print(f"[gg] {warning}", file=sys.stderr)
    if not own and not plan.adopted_from and not args.new:
        print(
            "[gg] No existing reviews; posting as a fresh series.",
            file=sys.stderr,
        )

    if args.interactive:
        try:
            edited = edit_plan(
                plan, renumber=args.renumber,
                separators=args.range is None and not specs,
            )
        except ValueError as e:
            print(f"[gg] {e}", file=sys.stderr)
            return 1
        if edited is None:
            print("Aborted.")
            return 0
        plan = edited

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
    _close_gone(plan, verbose=args.verbose, cwd=cwd)
    rows = _live_rows(plan, branch_name=branch_name, renumber=args.renumber)
    record = [row.held for row in rows]
    try:
        failed = _execute(
            rows,
            record,
            branch_name=branch_name,
            tracking=tracking,
            publish=args.publish,
            verbose=args.verbose,
            explicit_branch=args.branch,
            initial_depends=args.depends_on,
            reviewers=args.users or None,
            groups=args.groups or None,
            no_numbers=args.no_numbers,
            cwd=cwd,
        )
    finally:
        # Save state, also after a failed post or an exception (Ctrl-C, a
        # failed ReviewBoard lookup): what was done stays recorded.
        _save(plan, [e for e in record if e], branch_name=branch_name, cwd=cwd)

    if failed:
        print(
            "[gg] stopped after a failed post; run gg rbt-sync again to retry "
            "(if a new series was cut short, use gg rbt-sync -i and delete "
            'the "---" before the rest of it).',
            file=sys.stderr,
        )
        return 1
    print(format_summary(plan), file=sys.stderr)
    return 0
