"""`gg comments`: list the ReviewBoard discussions of a branch, with their code."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gg import git, rb_client
from gg.branch_plan import BranchPlanner, branch_planner
from gg.comments_format import Place, RequestView, format_totals, format_view, shown
from gg.discussions import build_discussions, code_excerpt, ship_its
from gg.rb_links import links_for
from gg.rb_model import Comment, DiffRow, RBApi, RBError
from gg.series_select import selected_revs, series_spec

_WORKERS = 8

_EPILOG = """\
Which requests:
  gg comments                  every review request of the current branch
  gg comments -s 3             series 3, numbered as in `gg rbt-sync -d`
  gg comments -s 3,4           several series
  gg comments -s $             the last series ($ = last)
  gg comments -s 4:1-3,5       patches 1-3 and 5 of series 4
  gg comments -s 2:2-$         patch 2 to the last patch of series 2
  gg comments r/19954 19917    given review requests (r/ optional)
  gg comments --all            also done discussions and quiet requests

-s and review request ids cannot be combined. Series that were never
posted have no review requests and show nothing.
Without --all only discussions that wait for someone are shown.

States:
  needs you             the reviewer spoke last, or a new issue is open
  waiting on reviewer   you answered an open issue
  done                  issue resolved or dropped, or a remark you answered

Discussions are named by a key: #<id> (diff comment), #g<id> (general
comment), #a<id> (file attachment comment), review <id> (a review's top text).

To reply: gg comments reply --help
"""


def _review_id(text: str) -> str:
    """`r/123` or `123` as `123`."""
    digits = text[2:] if text.startswith("r/") else text
    if not digits.isdigit():
        raise argparse.ArgumentTypeError(f"invalid review request id: {text!r}")
    return digits


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the comments subcommand."""
    p = subparsers.add_parser(
        "comments", help="show ReviewBoard discussions of the branch",
        description="Show the reviewers' discussions on the branch's review requests, "
                    "with the commented code.",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("ids", nargs="*", type=_review_id, metavar="ID",
                   help="review requests (r/123 or 123); default: the whole branch")
    p.add_argument("-s", "--series", action="append", default=[], type=series_spec,
                   metavar="N[,N...]|N:P[-Q][,...]",
                   help="only these series, or patches P..Q of series N ($ is the last)")
    p.add_argument("--all", action="store_true", help="also show discussions that are done")
    p.set_defaults(func=run)


def connect(cwd: Path) -> RBApi:
    """The ReviewBoard client for `cwd`; tests replace this name."""
    return rb_client.connect(cwd=cwd)


def run_reply(argv: list[str]) -> int:
    """`gg comments reply` (not implemented yet)."""
    print("not implemented")
    return 1


def _posted(planner: BranchPlanner, specs: list) -> tuple[list[tuple[str, Place]], str | None]:
    """(review id, place) of the posted patches of the branch in plan order.

    With `specs` only the selected commits; the second value is an error message.
    """
    plan = planner.plan_for(set(planner.full))
    keep = None
    if specs:
        keep = selected_revs(plan, specs, branch_name=planner.branch)
        if isinstance(keep, str):
            return [], keep
    return [
        (a.old_entry.review_id, Place(s.number, k, a.new_commit.rev[:8]))
        for s in plan.series
        for k, a in enumerate(s.actions, 1)
        if a.old_entry is not None and a.new_commit is not None
        and (keep is None or a.new_commit.rev in keep)
    ], None


def _scope(args: argparse.Namespace, cwd: Path) -> list[tuple[str, Place | None]] | str:
    """The requests to show with their places, or an error message."""
    specs = [spec for one in args.series for spec in one]
    if args.ids:
        if specs:
            return "-s and review request ids cannot be combined."
        places: dict[str, Place] = {}
        if git.has_upstream(cwd=cwd):
            places = dict(_posted(branch_planner(cwd=cwd), [])[0])
        return [(rid, places.get(rid)) for rid in args.ids]
    posted, error = _posted(branch_planner(cwd=cwd), specs)
    return error if error is not None else list(posted)


def _fetch(api: RBApi, user: str, rid: str, place: Place | None,
           show_all: bool) -> RequestView:
    """Everything shown for one request; raises RBError."""
    info = api.request(rid)
    reviews = api.reviews(rid)
    view = RequestView(info, place, ship_its(reviews),
                       build_discussions(reviews, user=user), {})
    rows: dict[tuple[int | None, int | None], list[DiffRow] | None] = {}

    def rows_of(comment: Comment) -> list[DiffRow] | None:
        key = (comment.filediff_id, comment.interfilediff_id)
        if key not in rows:
            try:
                rows[key] = api.diff_rows(rid, comment)
            except RBError:
                rows[key] = None
        return rows[key]

    for d in shown(view, show_all=show_all):
        c = d.comment
        if c is None or c.kind != "diff":
            continue
        diff_rows = rows_of(c)
        view.code[d.key] = (
            None if diff_rows is None or c.first_row is None
            else code_excerpt(diff_rows, c.first_row, c.num_rows or 1)
        )
    return view


def run(args: argparse.Namespace) -> int:
    """Execute the comments subcommand."""
    cwd = Path.cwd()
    scope = _scope(args, cwd)
    if isinstance(scope, str):
        print(f"[gg] {scope}", file=sys.stderr)
        return 1
    if not scope:
        print("[gg] nothing posted to ReviewBoard in this scope.", file=sys.stderr)
        return 1

    try:
        api = connect(cwd)      # once, here: it changes the working directory briefly
        user = api.user()
    except RBError as e:
        print(f"[gg] {e}", file=sys.stderr)
        return 1

    def fetch(item: tuple[str, Place | None]) -> RequestView | RBError:
        try:
            return _fetch(api, user, item[0], item[1], args.all)
        except RBError as e:
            return e

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(fetch, scope))

    links = links_for(cwd=cwd)
    views: list[RequestView] = []
    failed = False
    for (rid, _), result in zip(scope, results):
        if isinstance(result, RBError):
            print(f"[gg] r/{rid}: {result}", file=sys.stderr)
            failed = True
        else:
            views.append(result)

    printed = [v for v in views if args.all or shown(v, show_all=False)]
    if printed:
        print("\n\n".join(format_view(v, links=links, show_all=args.all) for v in printed))
        print()
    print(format_totals(views, show_all=args.all))
    return 1 if failed else 0
