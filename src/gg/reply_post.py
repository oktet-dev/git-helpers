"""Post the blocks of `gg comments reply` to ReviewBoard.

Nothing is written until the whole input is checked against ReviewBoard
and none of the reviews it touches holds an unpublished reply draft of the
user's with content. Then each review gets one reply, published, followed
by the issue status changes of its blocks. If a write fails, gg stops,
deletes the reply it could not publish and prints the blocks that were not
posted, in the input format, so they can be sent again as they are.

A status word becomes the status RB's own UI would set: a fix or drop of
an issue whose author asked to verify it waits for them (verifying-...),
which is all RB 8 lets anyone else set. A status the comment already has
is not sent: RB takes such a "change" for an edit of the reviewer's
comment and refuses it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar

from gg.discussions import comment_key
from gg.rb_links import Links
from gg.rb_model import Comment, RBApi, RBError, Reply, Review
from gg.reply_input import STATUS_WORDS, Block, format_blocks

_WORKERS = 8

T = TypeVar("T")
K = TypeVar("K")


@dataclass(frozen=True)
class _Target:
    """A checked block: its review, the comment it answers (None: the top
    text) and the issue status its status word asks for."""

    block: Block
    review: Review
    comment: Comment | None
    status: str | None = None

    @property
    def already(self) -> bool:
        """Whether the comment already has the status asked for."""
        return self.comment is not None and self.comment.issue_status == self.status


@dataclass
class _Done:
    """What has been done for one block."""

    replied: bool = False
    status: str | None = None       # the status ReviewBoard reported
    already: bool = False           # the comment had it: nothing was sent


def _fetch_all(keys: list[K], fetch: Callable[[K], T]) -> list[T | RBError]:
    """fetch(key) for every key, in a pool; an RBError is returned, not raised."""
    def one(key: K) -> T | RBError:
        try:
            return fetch(key)
        except RBError as e:
            return e

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        return list(pool.map(one, keys))


def _status_to_set(word: str, comment: Comment, user: Callable[[], str]) -> str:
    """The issue status a status word asks for on `comment`; `user()` is
    called only for an issue that asks to verify."""
    status = STATUS_WORDS[word]
    if (status in ("resolved", "dropped") and comment.require_verification
            and comment.author != user()):
        return f"verifying-{status}"
    return status


def _check(api: RBApi, blocks: list[Block]) -> tuple[list[_Target], list[str]]:
    """Resolve every block against its request's public reviews; problems as messages."""
    requests = list(dict.fromkeys(b.request for b in blocks))
    fetched = dict(zip(requests, _fetch_all(requests, api.reviews)))

    targets: list[_Target] = []
    errors: list[str] = []
    for b in blocks:
        reviews = fetched[b.request]
        if isinstance(reviews, RBError):
            message = f"r/{b.request}: {reviews}"
            if message not in errors:
                errors.append(message)
            continue
        where = f"r/{b.request} {b.target}" + (f" {b.status}" if b.status else "")
        public = [r for r in reviews if r.public]
        if b.target.startswith("review "):
            review_id = int(b.target.removeprefix("review "))
            review = next((r for r in public if r.id == review_id), None)
            if review is None:
                errors.append(f"{where}: no such review on this review request")
            else:
                targets.append(_Target(b, review, None))
            continue
        found = next(((r, c) for r in public for c in r.comments
                      if comment_key(c) == b.target), None)
        if found is None:
            errors.append(f"{where}: no such comment on this review request")
        elif b.status is None:
            targets.append(_Target(b, *found))
        elif not found[1].issue_opened:
            errors.append(f"{where}: the comment has no issue; drop the status word")
        else:
            try:
                status = _status_to_set(b.status, found[1], api.user)
                targets.append(_Target(b, *found, status))
            except RBError as e:
                errors.append(f"{where}: {e}")
    return targets, errors


def _by_review(targets: list[_Target]) -> list[list[int]]:
    """Indexes of the targets grouped by review, reviews in order of first appearance."""
    groups: dict[tuple[str, int], list[int]] = {}
    for i, t in enumerate(targets):
        groups.setdefault((t.block.request, t.review.id), []).append(i)
    return list(groups.values())


def _has_content(draft: Reply) -> bool:
    return bool(draft.comments or draft.body_top.strip() or draft.body_bottom.strip())


def _indented(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())


def _draft_report(rid: str, review: Review, draft: Reply) -> str:
    """The message for a draft that stops the batch, with the draft's texts."""
    lines = [f"[gg] r/{rid} review {review.id}: you have an unpublished reply draft "
             f"there; nothing was posted."]
    if draft.body_top.strip():
        lines += [f"  to review {review.id}'s top text:", _indented(draft.body_top.strip())]
    for rc in draft.comments:
        answered = next((c for c in review.comments
                         if c.id == rc.reply_to and c.kind == rc.kind), None)
        key = comment_key(answered) if answered else f"{rc.kind} comment {rc.reply_to}"
        lines += [f"  to {key}:", _indented(rc.text.strip())]
    if draft.body_bottom.strip():
        lines += [f"  to review {review.id}'s footer:", _indented(draft.body_bottom.strip())]
    return "\n".join(lines)


def _check_drafts(api: RBApi, targets: list[_Target],
                  groups: list[list[int]]) -> tuple[list[str], list[str]]:
    """Reports of the drafts in the way, and errors reading drafts; both empty: go ahead."""
    reviews = [targets[g[0]] for g in groups]
    drafts = _fetch_all(reviews,
                        lambda t: api.draft_reply(t.block.request, t.review.id))
    blocked: list[str] = []
    errors: list[str] = []
    for t, draft in zip(reviews, drafts):
        if isinstance(draft, RBError):
            errors.append(f"r/{t.block.request} review {t.review.id}: {draft}")
        elif draft is not None and _has_content(draft):
            blocked.append(_draft_report(t.block.request, t.review, draft))
    return blocked, errors


def _plan_line(t: _Target, links: Links) -> str:
    """What a dry run says about a block."""
    b = t.block
    parts = ["reply"] if b.text else []
    if b.status and t.already:
        parts.append(f"already {t.status}")
    elif b.status:
        plain = t.status == STATUS_WORDS[b.status]
        parts.append(f"set {b.status}" + ("" if plain else f" ({t.status})"))
    return f"{links.show(b.request)} {b.target}  {', '.join(parts)}"


def _done_line(b: Block, done: _Done, links: Links) -> str | None:
    """What was done for a block, or None when nothing was."""
    parts = ["replied"] if done.replied else []
    if done.status:
        parts.append(f"already {done.status}" if done.already else done.status)
    return f"{links.show(b.request)} {b.target}  {', '.join(parts)}" if parts else None


def _not_done(b: Block, done: _Done) -> Block | None:
    """The part of a block still to be sent: all of it, its status only, or None."""
    if b.text and not done.replied:
        return b
    if b.status and done.status is None:
        return Block(b.request, b.target, b.status, "")
    return None


def _post_review(api: RBApi, rid: str, review: Review,
                 items: list[tuple[_Target, _Done]]) -> list[str]:
    """One review's reply, published, then its status changes; marks what is done.

    Returns the error messages when a call fails (empty: all done). A reply
    created but not published by then is deleted. Any exception counts as a
    failure, not only RBError, so that the caller can still say what was not
    posted.
    """
    texts = [(t, d) for t, d in items if t.block.text]
    reply_id: int | None = None
    try:
        if texts:
            reply_id = api.create_reply(rid, review.id)
            for t, _ in texts:
                if t.comment is None:
                    api.set_reply_top(rid, review.id, reply_id, t.block.text)
                else:
                    api.add_reply_comment(rid, review.id, reply_id, t.comment, t.block.text)
            api.publish_reply(rid, review.id, reply_id)
            reply_id = None                     # published: nothing to delete
            for _, d in texts:
                d.replied = True
        for t, d in items:
            if t.status is None:
                continue
            assert t.comment is not None        # a review header has no status word
            if t.already:
                d.status, d.already = t.status, True
            else:
                d.status = api.set_issue_status(rid, review.id, t.comment, t.status)
    except Exception as e:      # any failure: report what went out and what did not
        errors = [_describe(e)]
        if reply_id is not None:
            try:
                api.delete_reply(rid, review.id, reply_id)
            except Exception as delete_error:
                errors.append(f"could not delete the unpublished reply {reply_id} "
                              f"({_describe(delete_error)}); discard it on ReviewBoard "
                              f"before sending again")
        return errors
    return []


def _describe(e: Exception) -> str:
    """An RBError's message; any other error (a bug, an odd server answer) with its type."""
    return str(e) if isinstance(e, RBError) else f"unexpected error: {e!r}"


def post_blocks(api: RBApi, blocks: list[Block], *, dry_run: bool, links: Links) -> int:
    """Check, then post the blocks (or show them with dry_run); the exit code."""
    targets, errors = _check(api, blocks)
    if errors:
        for message in errors:
            print(f"[gg] {message}", file=sys.stderr)
        print("[gg] nothing was posted.", file=sys.stderr)
        return 1

    groups = _by_review(targets)
    blocked, errors = _check_drafts(api, targets, groups)
    if blocked or errors:
        for message in errors:
            print(f"[gg] {message}", file=sys.stderr)
        for report in blocked:
            print(report, file=sys.stderr)
        print("[gg] publish or discard the draft on ReviewBoard first, then send the "
              "replies again." if blocked else "[gg] nothing was posted.", file=sys.stderr)
        return 1

    if dry_run:
        for t in targets:
            print(_plan_line(t, links))
        print("Dry run: nothing was posted.")
        return 0

    done = [_Done() for _ in targets]
    for group in groups:
        first = targets[group[0]]
        rid, review = first.block.request, first.review
        errors = _post_review(api, rid, review, [(targets[i], done[i]) for i in group])
        for i in group:
            line = _done_line(targets[i].block, done[i], links)
            if line:
                print(line)
        if errors:
            sys.stdout.flush()
            for message in errors:
                print(f"[gg] r/{rid} review {review.id}: {message}", file=sys.stderr)
            rest = [part for t, d in zip(targets, done)
                    if (part := _not_done(t.block, d)) is not None]
            print("Not posted (send again as is):")
            print(format_blocks(rest), end="")
            return 1
    return 0
