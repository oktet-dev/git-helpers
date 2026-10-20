"""ReviewBoard data model and the API protocol used by `gg comments`.

Request ids are strings (as in reviews.db); ids of RB objects inside a
request are ints; timestamps are RB's ISO 8601 strings; all text is plain
(already HTML-unescaped by the client). The protocol is implemented by an
RBTools-backed client and by an in-memory fake for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RBError(Exception):
    """A ReviewBoard call failed; the message says which and why."""


@dataclass(frozen=True)
class Comment:
    """A reviewer's comment: on a diff, general, or on a file attachment."""

    kind: str                       # "diff" | "general" | "attachment"
    id: int
    author: str
    timestamp: str
    text: str
    issue_opened: bool
    issue_status: str               # "" | "open" | "resolved" | "dropped" | "verifying-resolved" | "verifying-dropped"
    severity: str | None = None
    path: str | None = None
    filediff_id: int | None = None
    interfilediff_id: int | None = None
    diff_revision: int | None = None
    interdiff_revision: int | None = None
    first_row: int | None = None
    num_rows: int | None = None
    attachment: str | None = None


@dataclass(frozen=True)
class ReplyComment:
    """A comment inside a reply, answering the comment `reply_to`."""

    kind: str
    reply_to: int
    author: str
    timestamp: str
    text: str


@dataclass(frozen=True)
class Reply:
    """A reply to a review.

    `body_top` answers the review's top text and `body_bottom` its footer;
    each is "" if the reply has none.
    """

    id: int
    author: str
    timestamp: str
    public: bool
    body_top: str
    comments: list[ReplyComment]
    body_bottom: str = ""


@dataclass(frozen=True)
class Review:
    """A review with its comments and the replies to it."""

    id: int
    author: str
    timestamp: str
    public: bool
    ship_it: bool
    body_top: str
    comments: list[Comment]
    replies: list[Reply]


@dataclass(frozen=True)
class RequestInfo:
    """The parts of a review request that `gg comments` needs."""

    id: str
    summary: str
    latest_diff: int | None


@dataclass(frozen=True)
class DiffRow:
    """One row of a side-by-side diff; a side is absent (line None) when added/removed."""

    row: int
    old_line: int | None
    old_text: str
    new_line: int | None
    new_text: str


class RBApi(Protocol):
    """Everything `gg comments` does against ReviewBoard. Every method raises RBError on failure."""

    def user(self) -> str: ...

    def request(self, rid: str) -> RequestInfo: ...

    def reviews(self, rid: str) -> list[Review]:
        """All reviews, including the user's unpublished replies (public=False)."""
        ...

    def diff_rows(self, rid: str, comment: Comment) -> list[DiffRow]: ...

    def draft_reply(self, rid: str, review_id: int) -> Reply | None: ...

    def create_reply(self, rid: str, review_id: int) -> int:
        """The user's draft reply: the existing one or a new one."""
        ...

    def add_reply_comment(self, rid: str, review_id: int, reply_id: int,
                          comment: Comment, text: str) -> None: ...

    def set_reply_top(self, rid: str, review_id: int, reply_id: int, text: str) -> None: ...

    def publish_reply(self, rid: str, review_id: int, reply_id: int) -> None: ...

    def delete_reply(self, rid: str, review_id: int, reply_id: int) -> None: ...

    def set_issue_status(self, rid: str, review_id: int, comment: Comment,
                         status: str) -> str:
        """Returns the status RB reports."""
        ...
