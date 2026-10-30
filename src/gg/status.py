"""`gg status`: how each review request of the branch stands with its reviewers.

One line per patch, laid out as `gg rbt-sync -d` lays out the branch, with
the number of reviewers who gave a Ship It and the number of issues still
open. Always asks ReviewBoard; nothing is cached.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from gg import comments
from gg.branch_plan import branch_planner
from gg.discussions import ship_its
from gg.numbering import number_series
from gg.rb_links import Links, links_for
from gg.rb_model import RBApi, RBError, Review
from gg.series_select import selected_revs, series_spec
from gg.sync_plan import series_header, visible

_WORKERS = 8

# Issue states that still block landing: open, or a fix / drop the
# reviewer has yet to confirm.
_PENDING = ("open", "verifying-resolved", "verifying-dropped")

_EPILOG = """\
Which requests:
  gg status                    every patch of the current branch
  gg status -s 3               series 3, numbered as in `gg rbt-sync -d`
  gg status -s 4:1-3,5         patches 1-3 and 5 of series 4 (-s forms as
                               in `gg comments`)
  gg status r/19954 19917      given review requests (r/ optional)

Columns:
  Ship It   reviewers with a published Ship It on the request
  Issues    issues not closed yet: open, or a fix or drop the reviewer
            still has to verify
A patch that was never posted shows "--"; a request ReviewBoard could not
serve shows "?" and is reported on stderr (exit status 1).

For the discussions themselves: gg comments.
"""


@dataclass(frozen=True)
class Counts:
    """What the reviewers left on one request."""

    ship_its: int
    issues: int


@dataclass(frozen=True)
class Line:
    """One row of the table: a patch, posted or not."""

    number: str                 # "[1/2]" on the branch, "--" for a given id
    review_id: str | None       # None: never posted
    subject: str


@dataclass(frozen=True)
class Section:
    """A series of the branch, or the given requests (no title)."""

    title: str | None
    lines: list[Line]


def counts(reviews: list[Review]) -> Counts:
    """Ship It reviewers and pending issues over the public reviews."""
    issues = sum(
        1 for r in reviews if r.public for c in r.comments if c.issue_status in _PENDING
    )
    return Counts(len(ship_its(reviews)), issues)


def _review_id(text: str) -> str:
    """`r/123` or `123` as `123`."""
    digits = text[2:] if text.startswith("r/") else text
    if not digits.isdigit():
        raise argparse.ArgumentTypeError(f"invalid review request id: {text!r}")
    return digits


def add_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the status subcommand."""
    p = subparsers.add_parser(
        "status", help="show Ship Its and open issues on the branch's review requests",
        description="Show, per patch, how many reviewers gave a Ship It and how many "
                    "issues are still open on its review request.",
        epilog=_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("ids", nargs="*", type=_review_id, metavar="ID",
                   help="review requests (r/123 or 123); default: the whole branch")
    p.add_argument("-s", "--series", action="append", default=[], type=series_spec,
                   metavar="N[,N...]|N:P[-Q][,...]",
                   help="only these series, or patches P..Q of series N ($ is the last)")
    p.add_argument("-l", "--links", action="store_true",
                   help="print full review URLs instead of r/N")
    p.set_defaults(func=run)


def _branch_sections(specs: list, *, cwd: Path, links: Links) -> list[Section] | str:
    """The branch as sections, narrowed by `specs`; or an error message."""
    planner = branch_planner(cwd=cwd)
    plan = planner.plan_for(set(planner.full))
    keep = None
    if specs:
        keep = selected_revs(plan, specs, branch_name=planner.branch)
        if isinstance(keep, str):
            return keep
    sections = []
    for series in plan.series:
        lines = []
        for action, number in number_series(series):
            if not visible(action):
                continue
            if keep is not None and (action.new_commit is None or action.new_commit.rev not in keep):
                continue
            review_id = action.old_entry.review_id if action.old_entry else None
            subject = action.new_commit.subject if action.new_commit else ""
            lines.append(Line(number, review_id, subject))
        if lines:
            sections.append(Section(series_header(series, links), lines))
    return sections


def _fetch(api: RBApi, rid: str, *, summary: bool) -> tuple[Counts, str] | RBError:
    """The counts and, if `summary`, the request's summary; or the failure."""
    try:
        subject = api.request(rid).summary if summary else ""
        return counts(api.reviews(rid)), subject
    except RBError as e:
        return e


def format_table(sections: list[Section], fetched: dict[str, Counts | None],
                 *, links: Links) -> str:
    """The sections as one table; a None in `fetched` shows as "?"."""
    ids = [line.review_id for s in sections for line in s.lines if line.review_id]
    width = max([10] + [len(links.text(rid)) + 1 for rid in ids])
    header = f"{'#':<10} {'Ship It':<8} {'Issues':<7} {'Review':<{width}} Subject"
    out = [header, "-" * len(header)]
    for section in sections:
        if section.title is not None:
            out.append(section.title)
        for line in section.lines:
            if line.review_id is None:
                ship, issues, review = "--", "--", f"{'--':<{width}}"
            else:
                got = fetched.get(line.review_id)
                ship = "?" if got is None else str(got.ship_its)
                issues = "?" if got is None else str(got.issues)
                review = links.show(line.review_id, width)
            out.append(f"{line.number:<10} {ship:<8} {issues:<7} {review} {line.subject}".rstrip())
        out.append("")
    return "\n".join(out).rstrip("\n")


def run(args: argparse.Namespace) -> int:
    """Execute the status subcommand."""
    cwd = Path.cwd()
    specs = [spec for one in args.series for spec in one]
    links = links_for(full=args.links, cwd=cwd)
    if args.links and not links.server:
        print("[gg] no ReviewBoard URL (REVIEWBOARD_URL in .reviewboardrc "
              "or git config reviewboard.url); showing r/N.", file=sys.stderr)

    if args.ids:
        if specs:
            print("[gg] -s and review request ids cannot be combined.", file=sys.stderr)
            return 1
        sections: list[Section] | str = [
            Section(None, [Line("--", rid, "") for rid in args.ids])
        ]
    else:
        sections = _branch_sections(specs, cwd=cwd, links=links)
    if isinstance(sections, str):
        print(f"[gg] {sections}", file=sys.stderr)
        return 1
    ids = [line.review_id for s in sections for line in s.lines if line.review_id]
    if not ids:
        print("[gg] nothing posted to ReviewBoard in this scope.", file=sys.stderr)
        return 1

    try:
        api = comments.connect(cwd)
    except RBError as e:
        print(f"[gg] {e}", file=sys.stderr)
        return 1
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(lambda rid: _fetch(api, rid, summary=bool(args.ids)), ids))

    fetched: dict[str, Counts | None] = {}
    subjects: dict[str, str] = {}
    failed = False
    for rid, result in zip(ids, results):
        if isinstance(result, RBError):
            print(f"[gg] r/{rid}: {result}", file=sys.stderr)
            fetched[rid] = None
            failed = True
        else:
            fetched[rid], subjects[rid] = result
    if args.ids:
        sections = [Section(None, [
            Line(line.number, line.review_id, subjects.get(line.review_id or "", ""))
            for line in sections[0].lines
        ])]
    print(format_table(sections, fetched, links=links))
    return 1 if failed else 0
