"""Turn ReviewBoard reviews into discussions with states and code excerpts.

A discussion is one reviewer comment (or a review's top text and footer)
with the public replies to it. Its state says who is expected to act next.
Pure logic: no ReviewBoard access.
"""

from __future__ import annotations

from dataclasses import dataclass

from gg.rb_model import Comment, DiffRow, Review

NEEDS_YOU = "needs you"
WAITING = "waiting on reviewer"
DONE = "done"

_KEY_PREFIX = {"diff": "#", "general": "#g", "attachment": "#a"}


@dataclass(frozen=True)
class Message:
    """One message of a discussion."""

    author: str
    timestamp: str
    text: str


@dataclass
class Discussion:
    """A comment (None: the review's top text and footer) with its messages and state."""

    key: str
    review: Review
    comment: Comment | None
    messages: list[Message]
    state: str


@dataclass(frozen=True)
class ExcerptLine:
    """One line of a code excerpt; `marked` lines are the commented ones."""

    marked: bool
    line: int | None
    text: str


def comment_key(comment: Comment) -> str:
    """`#<id>` (diff), `#g<id>` (general) or `#a<id>` (attachment)."""
    return f"{_KEY_PREFIX[comment.kind]}{comment.id}"


def _state(messages: list[Message], user: str, issue_status: str, has_issue: bool) -> str:
    if issue_status in ("resolved", "dropped"):
        return DONE
    if messages[-1].author == user:
        return WAITING if has_issue else DONE
    return NEEDS_YOU


def build_discussions(reviews: list[Review], *, user: str) -> list[Discussion]:
    """Discussions of the public reviews, in review order, comments in given order."""
    result: list[Discussion] = []
    for review in reviews:
        if not review.public:
            continue
        replies = [r for r in review.replies if r.public]

        top = review.body_top.strip()
        messages = [Message(review.author, review.timestamp, text)
                    for text in (review.body_top, review.body_bottom) if text.strip()]
        answers = [Message(r.author, r.timestamp, text)
                   for r in sorted(replies, key=lambda r: r.timestamp)
                   for text in (r.body_top, r.body_bottom) if text.strip()]
        if (top and top != "Ship It!") or review.body_bottom.strip() or answers:
            messages += answers
            result.append(Discussion(f"review {review.id}", review, None, messages,
                                     _state(messages, user, "", False)))

        for c in review.comments:
            answers = sorted((rc for r in replies for rc in r.comments
                              if rc.reply_to == c.id and rc.kind == c.kind),
                             key=lambda rc: rc.timestamp)
            messages = [Message(c.author, c.timestamp, c.text)]
            messages += [Message(rc.author, rc.timestamp, rc.text) for rc in answers]
            has_issue = c.issue_status in ("open", "verifying-resolved", "verifying-dropped")
            result.append(Discussion(comment_key(c), review, c, messages,
                                     _state(messages, user, c.issue_status, has_issue)))
    return result


def ship_its(reviews: list[Review]) -> list[str]:
    """Authors of public Ship It reviews, unique, in first-seen order."""
    seen: dict[str, None] = {}
    for r in reviews:
        if r.public and r.ship_it:
            seen.setdefault(r.author)
    return list(seen)


def code_excerpt(rows: list[DiffRow], first_row: int, num_rows: int,
                 *, context: int = 2) -> list[ExcerptLine]:
    """The commented rows, marked, with `context` rows on each side that exist."""
    last_row = first_row + num_rows - 1
    lines = []
    for r in rows:
        if first_row - context <= r.row <= last_row + context:
            marked = first_row <= r.row <= last_row
            if r.new_line is not None:
                lines.append(ExcerptLine(marked, r.new_line, r.new_text))
            else:
                lines.append(ExcerptLine(marked, r.old_line, r.old_text))
    return lines


def line_span(excerpt: list[ExcerptLine]) -> str:
    """`141-143` for the marked lines of an excerpt, or `141` for one line."""
    marked = [e.line for e in excerpt if e.marked and e.line is not None]
    if not marked:
        return ""
    lo, hi = min(marked), max(marked)
    return str(lo) if lo == hi else f"{lo}-{hi}"
