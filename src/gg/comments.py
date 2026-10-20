"""`gg comments`: list the ReviewBoard discussions of a branch, with their code."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gg import git
from gg.branch_plan import BranchPlanner, branch_planner
from gg.comments_format import Place, RequestView, format_totals, format_view, shown
from gg.discussions import build_discussions, code_excerpt, ship_its
from gg.rb_links import links_for
from gg.rb_model import Comment, DiffRow, RBApi, RBError
from gg.reply_input import ReplyInputError, parse_blocks
from gg.reply_post import post_blocks
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
  waiting on reviewer   you answered an open issue, or an issue in a
                        verifying state (a fix or drop the reviewer is to
                        confirm)
  done                  issue resolved or dropped, or a remark you answered

Code: a diff comment shows the code it is on as it was in the diff
revision it was made on ("diff N of M": the code is from diff N, what the
reviewer saw), with two lines of context; ">" marks the commented lines.
Line numbers are new-side; removed lines show their old-side numbers with
a "-" after them ("  > 60-  ..."). A comment on an interdiff has no code
("code not available": ReviewBoard's API does not serve interdiff rows).
The current code is in the local commit: read it there.

Discussions are named by a key: #<id> (diff comment), #g<id> (general
comment), #a<id> (file attachment comment), review <id> (a review's top
text and footer, as one discussion).

To reply: gg comments reply --help
"""

_REPLY_EPILOG = """\
Input: blocks. Each block is one header line and the reply text under it,
running up to the next header line (or the end):

  === r/<request> #<comment> [fixed|dropped|open]
  === r/<request> review <review>

  <request>        the review request id; the r/ is optional
  #<comment>       the discussion key as `gg comments` prints it:
                   #<id> diff comment, #g<id> general comment,
                   #a<id> file attachment comment
  review <review>  the top text of review <review> (takes no status word)

Status words change the comment's issue status after the reply is
published (only for a comment that has an issue):
  fixed     resolved; verifying-resolved when the reviewer asked to
            verify the fix (it then waits on the reviewer; as in RB's
            own UI, and unless the comment is yours)
  dropped   dropped; likewise verifying-dropped
  open      open again (reopen), from any status
A status the comment already has is not set again ("already resolved").

Text: Markdown, posted as is. Blank lines at its start and end are
trimmed, inner ones kept. A block with a status word and no text only
changes the status.

Rules:
  - Every line starting with "=== " is a header, so reply text cannot
    contain such a line; a header that does not parse is an error.
  - One block per comment or review top text: a second one is an error,
    and so is a block with neither text nor a status word.
  - Everything is checked before anything is posted: every request,
    review and comment exists, each comment belongs to its request, a
    status word is given only for a comment with an issue. On any error
    nothing is posted.
  - If you have an unpublished reply draft with content on any review the
    input touches, nothing is posted and gg prints the draft: publish or
    discard it on ReviewBoard first. An empty draft is simply used.
  - gg posts one reply per review and publishes it, then sets that
    review's issue statuses; reviews in order of their first block.
  - -d (--dry) checks everything and prints what would be posted, one line
    per block; it posts nothing.

Output: one line per block, "r/<request> <target>  " and what was done:
"replied", the status RB reports ("already <status>" when it was there),
or both, e.g. "r/19954 #37441  replied", "r/19917 #37502  replied,
resolved", "r/19917 #37503  dropped".
If posting fails midway, gg stops, deletes the reply it could not
publish, prints the error and then, after the line
"Not posted (send again as is):", the blocks that were not posted, in this
format (a block whose reply went out but whose status change failed comes
back as a status-only block). Exit status 0: everything was posted (with
-d: everything checks out); 1 otherwise.

Example:
  gg comments reply <<'EOF'
  === r/19954 #37441
  Fewer than 3 traffic classes is legal, but then the 3-TC
  configurations below are skipped; the warning says why.
  === r/19917 #37502 fixed
  Fixed in this diff: the error path now restores the old config.
  === r/19920 #37510 dropped
  Agreed with ark-slav: out of scope here, tracked in BUG-1234.
  === r/19954 review 36005
  Thanks!
  EOF

Quote the heredoc word ('EOF') so the shell leaves $, ` and \\ alone.

Working through comments with an agent:
  1. gg comments (or -s N, or ids); read the code around each discussion.
  2. Report a short triage to the user: what to fix, where to reply, and
     why.
  3. Show the replies in the chat; post them with gg comments reply only
     after the user approves them, exactly as approved.
  4. Fixes go into the commit of the review request they belong to; a
     separate commit only when the change spans later commits. The reply
     says where the fix is.
  5. Mark issues fixed only after the new diff is uploaded with
     gg rbt-sync and published: a status-only block, e.g.
     "=== r/19917 #37502 fixed" with no text.
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
    """The ReviewBoard client for `cwd`; tests replace this name.

    RBTools is imported here, not with this module, so that the other gg
    commands run in an install that lacks it.
    """
    try:
        from gg import rb_client
    except ImportError as e:
        raise RBError("gg comments needs RBTools: reinstall gg with ./install.sh --gg") from e
    return rb_client.connect(cwd=cwd)


def _reply_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gg comments reply",
        description="Post replies to ReviewBoard discussions and change their issue\n"
                    "statuses. The blocks to post are read from FILE, or from stdin.",
        epilog=_REPLY_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("file", nargs="?", metavar="FILE",
                   help="read the blocks from FILE instead of stdin")
    p.add_argument("-d", "--dry", action="store_true",
                   help="check everything and show what would be posted; post nothing")
    return p


def run_reply(argv: list[str]) -> int:
    """Execute `gg comments reply`."""
    args = _reply_parser().parse_args(argv)
    try:
        text = Path(args.file).read_text() if args.file else sys.stdin.read()
    except OSError as e:
        print(f"[gg] {e}", file=sys.stderr)
        return 1
    try:
        blocks = parse_blocks(text)
    except ReplyInputError as e:
        for message in str(e).splitlines():
            print(f"[gg] {message}", file=sys.stderr)
        print("[gg] nothing was posted.", file=sys.stderr)
        return 1

    cwd = Path.cwd()
    try:
        api = connect(cwd)
    except RBError as e:
        print(f"[gg] {e}", file=sys.stderr)
        return 1
    return post_blocks(api, blocks, dry_run=args.dry, links=links_for(cwd=cwd))


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


def _places(cwd: Path) -> dict[str, Place]:
    """Where the branch's posted requests sit, by review id; {} if that cannot be had.

    Only decoration for explicitly given ids, so a failure of the branch
    plan (git, `rbt api-get`, which may exit) leaves the requests without
    places instead of stopping `gg comments`.
    """
    try:
        if not git.has_upstream(cwd=cwd):
            return {}
        return dict(_posted(branch_planner(cwd=cwd), [])[0])
    except (Exception, SystemExit):
        return {}


def _scope(args: argparse.Namespace, cwd: Path) -> list[tuple[str, Place | None]] | str:
    """The requests to show with their places, or an error message."""
    specs = [spec for one in args.series for spec in one]
    if args.ids:
        if specs:
            return "-s and review request ids cannot be combined."
        places = _places(cwd)
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
