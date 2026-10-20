"""An in-memory ReviewBoard implementing `gg.rb_model.RBApi`, for tests."""

from __future__ import annotations

import dataclasses
from collections import Counter

from gg.rb_model import (
    Comment, DiffRow, RBError, Reply, ReplyComment, RequestInfo, Review,
)

NOW = "2026-10-20T12:00:00Z"


class FakeRB:
    """Requests, reviews, diff rows and the user's reply drafts, all in memory.

    `log` records every write call as `(method, args...)`. `fail` makes a
    method raise on its n-th call (1-based, reads included). `status_result`
    overrides the status `set_issue_status` reports for a comment id.
    """

    def __init__(self, user: str = "kostik") -> None:
        self._user = user
        self._requests: dict[str, RequestInfo] = {}
        self._reviews: dict[str, list[Review]] = {}
        self._replies: dict[tuple[str, int], list[Reply]] = {}
        self._rows: dict[int, list[DiffRow]] = {}
        self._next_reply = 1000
        self._calls: Counter[str] = Counter()
        self.log: list[tuple] = []
        self.fail: dict[str, tuple[int, BaseException]] = {}
        self.status_result: dict[int, str] = {}

    # -- setup ----------------------------------------------------------

    def add_request(self, rid: str, summary: str, *, latest_diff: int | None = 1) -> None:
        self._requests[rid] = RequestInfo(rid, summary, latest_diff)
        self._reviews.setdefault(rid, [])

    def add_review(self, rid: str, review: Review) -> None:
        self._reviews[rid].append(review)
        self._replies.setdefault((rid, review.id), []).extend(review.replies)

    def set_rows(self, filediff_id: int, rows: list[DiffRow]) -> None:
        self._rows[filediff_id] = rows

    def set_draft(self, rid: str, review_id: int, draft: Reply) -> None:
        replies = self._replies.setdefault((rid, review_id), [])
        replies[:] = [r for r in replies if r.public] + [draft]

    # -- helpers --------------------------------------------------------

    def _enter(self, method: str, rid: str, *args: object, write: bool = False) -> None:
        self._calls[method] += 1
        if write:
            self.log.append((method, rid, *args))
        if method in self.fail and self.fail[method][0] == self._calls[method]:
            raise self.fail[method][1]
        if rid not in self._requests:
            raise RBError(f"review request {rid} not found")

    def _find(self, rid: str, review_id: int) -> Review:
        for review in self._reviews[rid]:
            if review.id == review_id:
                return review
        raise RBError(f"review {review_id} not found on r/{rid}")

    def _draft(self, rid: str, review_id: int) -> Reply | None:
        return next((r for r in self._replies.get((rid, review_id), []) if not r.public), None)

    def _put(self, rid: str, review_id: int, reply: Reply) -> None:
        replies = self._replies[(rid, review_id)]
        replies[:] = [reply if r.id == reply.id else r for r in replies]

    def _reply(self, rid: str, review_id: int, reply_id: int) -> Reply:
        for r in self._replies.get((rid, review_id), []):
            if r.id == reply_id:
                return r
        raise RBError(f"reply {reply_id} not found")

    # -- RBApi ----------------------------------------------------------

    def user(self) -> str:
        self._calls["user"] += 1
        return self._user

    def request(self, rid: str) -> RequestInfo:
        self._enter("request", rid)
        return self._requests[rid]

    def reviews(self, rid: str) -> list[Review]:
        self._enter("reviews", rid)
        return [dataclasses.replace(r, replies=list(self._replies.get((rid, r.id), [])))
                for r in self._reviews[rid]]

    def diff_rows(self, rid: str, comment: Comment) -> list[DiffRow]:
        self._enter("diff_rows", rid)
        if comment.interfilediff_id is not None:
            raise RBError("ReviewBoard has no rows for an interdiff")
        if comment.filediff_id not in self._rows:
            raise RBError(f"no diff rows for file diff {comment.filediff_id}")
        return self._rows[comment.filediff_id]

    def draft_reply(self, rid: str, review_id: int) -> Reply | None:
        self._enter("draft_reply", rid)
        return self._draft(rid, review_id)

    def create_reply(self, rid: str, review_id: int) -> int:
        self._enter("create_reply", rid, review_id, write=True)
        draft = self._draft(rid, review_id)
        if draft:
            return draft.id
        self._next_reply += 1
        self.set_draft(rid, review_id,
                       Reply(self._next_reply, self._user, NOW, False, "", []))
        return self._next_reply

    def add_reply_comment(self, rid: str, review_id: int, reply_id: int,
                          comment: Comment, text: str) -> None:
        self._enter("add_reply_comment", rid, review_id, reply_id, comment.id, text, write=True)
        reply = self._reply(rid, review_id, reply_id)
        added = ReplyComment(comment.kind, comment.id, self._user, NOW, text)
        self._put(rid, review_id, dataclasses.replace(reply, comments=[*reply.comments, added]))

    def set_reply_top(self, rid: str, review_id: int, reply_id: int, text: str) -> None:
        self._enter("set_reply_top", rid, review_id, reply_id, text, write=True)
        reply = self._reply(rid, review_id, reply_id)
        self._put(rid, review_id, dataclasses.replace(reply, body_top=text))

    def publish_reply(self, rid: str, review_id: int, reply_id: int) -> None:
        self._enter("publish_reply", rid, review_id, reply_id, write=True)
        reply = self._reply(rid, review_id, reply_id)
        self._put(rid, review_id, dataclasses.replace(reply, public=True))

    def delete_reply(self, rid: str, review_id: int, reply_id: int) -> None:
        self._enter("delete_reply", rid, review_id, reply_id, write=True)
        self._reply(rid, review_id, reply_id)
        self._replies[(rid, review_id)] = [
            r for r in self._replies[(rid, review_id)] if r.id != reply_id]

    def set_issue_status(self, rid: str, review_id: int, comment: Comment,
                         status: str) -> str:
        self._enter("set_issue_status", rid, review_id, comment.id, status, write=True)
        result = self.status_result.get(comment.id, status)
        review = self._find(rid, review_id)
        review.comments[:] = [
            dataclasses.replace(c, issue_status=result) if c.id == comment.id else c
            for c in review.comments]
        return result
