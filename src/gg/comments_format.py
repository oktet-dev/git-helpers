"""Text output of `gg comments`: review requests with their discussions."""

from __future__ import annotations

from dataclasses import dataclass

from gg.discussions import DONE, NEEDS_YOU, WAITING, Discussion, ExcerptLine, line_span
from gg.rb_links import Links
from gg.rb_model import RequestInfo


@dataclass(frozen=True)
class Place:
    """Where a request sits on the branch."""

    series: int
    patch: int
    commit: str                     # first 8 characters


@dataclass
class RequestView:
    """A review request as shown: its discussions and the code they refer to."""

    info: RequestInfo
    place: Place | None
    ship_its: list[str]
    discussions: list[Discussion]
    code: dict[str, list[ExcerptLine] | None]   # diff discussions by key; None: not available


def shown(view: RequestView, *, show_all: bool) -> list[Discussion]:
    """The discussions to print: those waiting for someone, or all."""
    return [d for d in view.discussions if show_all or d.state != DONE]


def _header(view: RequestView, links: Links) -> list[str]:
    lines = [f"{links.show(view.info.id)}  {view.info.summary}"]
    parts = []
    if view.place:
        p = view.place
        parts.append(f"series {p.series}, patch {p.patch}, commit {p.commit}")
    if view.ship_its:
        parts.append(f"Ship It: {', '.join(view.ship_its)}")
    if parts:
        lines.append(" " * 9 + "   ".join(parts))
    return lines


def _head_line(view: RequestView, d: Discussion) -> str:
    author = d.messages[0].author if d.messages else d.review.author
    c = d.comment
    if c is None:
        return f"  {d.key}  {d.review.author}  {d.state}"
    fields = [d.key, author]
    if c.issue_opened:
        fields.append(f"issue {c.issue_status}" + (f" ({c.severity})" if c.severity else ""))
    else:
        fields.append("comment")
    fields.append(d.state)
    if c.kind == "diff" and c.diff_revision is not None:
        latest = view.info.latest_diff
        of = f" of {latest}" if latest is not None else ""
        if c.interdiff_revision is not None:
            fields.append(f"interdiff {c.diff_revision}-{c.interdiff_revision}{of}")
        else:
            fields.append(f"diff {c.diff_revision}{of}")
    return "  " + "  ".join(fields)


def _code_lines(view: RequestView, d: Discussion) -> list[str]:
    c = d.comment
    if c is None:
        return []
    if c.kind == "attachment":
        return [f"    attachment: {c.attachment}"]
    if c.kind != "diff":
        return []
    excerpt = view.code.get(d.key)
    if excerpt is None or not any(e.marked for e in excerpt):
        first = c.first_row or 0
        last = first + (c.num_rows or 1) - 1
        return [f"    {c.path} (rows {first}-{last}): code not available"]
    lines = [f"    {c.path}:{line_span(excerpt)}{_old_note(excerpt)}"]
    # Old-side (removed) line numbers end in "-"; when there are any, the
    # new-side ones end in a space so that the digits line up.
    any_old = any(e.old for e in excerpt)
    numbers = [
        "" if e.line is None else f"{e.line}{('-' if e.old else ' ') if any_old else ''}"
        for e in excerpt
    ]
    width = max(map(len, numbers), default=0)
    for e, number in zip(excerpt, numbers):
        text = f"{number:>{width}}  {e.text}".rstrip()
        lines.append(("      > " if e.marked else "        ") + text)
    return lines


def _old_note(excerpt: list[ExcerptLine]) -> str:
    """What the line numbers are when commented lines were removed."""
    marked = [e for e in excerpt if e.marked]
    if all(e.old for e in marked):
        return " (removed lines; old line numbers)"
    if any(e.old for e in marked):
        return " (with removed lines; old line numbers end in -)"
    return ""


def _message_lines(d: Discussion) -> list[str]:
    lines = []
    for m in d.messages:
        first, *rest = m.text.strip().splitlines() or [""]
        lines.append(f"    {m.author}, {m.timestamp[:10]}: {first}")
        lines += [("      " + r).rstrip() for r in rest]
    return lines


def format_view(view: RequestView, *, links: Links, show_all: bool) -> str:
    """One request with its shown discussions, without a trailing newline."""
    lines = _header(view, links)
    discussions = shown(view, show_all=show_all)
    if not discussions and show_all:
        lines.append("  no discussions")
    blocks = []
    for d in discussions:
        blocks.append("\n".join(
            [_head_line(view, d), *_code_lines(view, d), *_message_lines(d)]))
    return _join(lines, blocks)


def _join(header: list[str], blocks: list[str]) -> str:
    """Header, then the blocks; the first block follows directly, the others after a blank line."""
    text = "\n".join(header)
    for i, block in enumerate(blocks):
        text += ("\n\n" if i else "\n") + block
    return text


def format_totals(views: list[RequestView], *, show_all: bool) -> str:
    """`2 discussions need you, 1 waits on reviewers.` (plus `, n done` with show_all)."""
    states = [d.state for v in views for d in v.discussions]
    needs, waiting = states.count(NEEDS_YOU), states.count(WAITING)
    text = (f"{needs} {'discussion needs' if needs == 1 else 'discussions need'} you, "
            f"{waiting} {'waits' if waiting == 1 else 'wait'} on reviewers")
    if show_all:
        text += f", {states.count(DONE)} done"
    return text + "."
