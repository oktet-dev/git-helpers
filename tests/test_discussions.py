"""Tests for discussions.py: discussion states and code excerpts."""

from __future__ import annotations

from gg.discussions import (
    DONE,
    NEEDS_YOU,
    WAITING,
    Message,
    build_discussions,
    code_excerpt,
    comment_key,
    line_span,
    ship_its,
)
from gg.rb_model import Comment, DiffRow, Reply, ReplyComment, Review

T1, T2, T3 = "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z", "2026-01-01T12:00:00Z"


def comment(id=37441, author="ark-slav", text="Why does it matter?", kind="diff",
            issue_status="open", issue_opened=True, ts=T1, **kw) -> Comment:
    return Comment(kind=kind, id=id, author=author, timestamp=ts, text=text,
                   issue_opened=issue_opened, issue_status=issue_status, **kw)


def rc(reply_to=37441, author="kostik", text="Because.", kind="diff", ts=T2) -> ReplyComment:
    return ReplyComment(kind=kind, reply_to=reply_to, author=author, timestamp=ts, text=text)


def reply(comments=(), author="kostik", public=True, body_top="", ts=T2, id=900) -> Reply:
    return Reply(id=id, author=author, timestamp=ts, public=public, body_top=body_top,
                 comments=list(comments))


def review(comments=(), replies=(), id=36005, author="ark-slav", public=True,
           ship_it=False, body_top="") -> Review:
    return Review(id=id, author=author, timestamp=T1, public=public, ship_it=ship_it,
                  body_top=body_top, comments=list(comments), replies=list(replies))


def states(reviews):
    return [(d.key, d.state) for d in build_discussions(reviews, user="kostik")]


def test_keys():
    assert comment_key(comment(id=37441, kind="diff")) == "#37441"
    assert comment_key(comment(id=812, kind="general")) == "#g812"
    assert comment_key(comment(id=95, kind="attachment")) == "#a95"


def test_open_issue_from_reviewer_needs_you():
    ds = build_discussions([review([comment()])], user="kostik")
    assert len(ds) == 1
    assert ds[0].key == "#37441"
    assert ds[0].state == NEEDS_YOU
    assert ds[0].messages == [Message("ark-slav", T1, "Why does it matter?")]


def test_user_reply_waits_on_reviewer():
    ds = build_discussions([review([comment()], [reply([rc()])])], user="kostik")
    assert ds[0].state == WAITING
    assert len(ds[0].messages) == 2


def test_reviewer_answer_after_user_needs_you_again():
    rep1 = reply([rc()], id=900)
    rep2 = reply([rc(author="ark-slav", text="Still unclear", ts=T3)],
                 author="ark-slav", ts=T3, id=901)
    ds = build_discussions([review([comment()], [rep2, rep1])], user="kostik")
    assert ds[0].state == NEEDS_YOU
    assert [m.timestamp for m in ds[0].messages] == [T1, T2, T3]


def test_unpublished_reply_does_not_count():
    ds = build_discussions([review([comment()], [reply([rc()], public=False)])], user="kostik")
    assert ds[0].state == NEEDS_YOU
    assert len(ds[0].messages) == 1


def test_closed_issues_are_done():
    for status in ("resolved", "dropped"):
        assert states([review([comment(issue_status=status)])]) == [("#37441", DONE)]


def test_verifying_waits_on_reviewer():
    r = review([comment(issue_status="verifying-resolved")], [reply([rc()])])
    assert states([r]) == [("#37441", WAITING)]


def test_comment_without_issue():
    c = comment(issue_opened=False, issue_status="")
    assert states([review([c])]) == [("#37441", NEEDS_YOU)]
    assert states([review([c], [reply([rc()])])]) == [("#37441", DONE)]


def test_review_top_text():
    ds = build_discussions([review(body_top="Looks fine, see below")], user="kostik")
    assert [(d.key, d.comment) for d in ds] == [("review 36005", None)]
    assert ds[0].messages[0].text == "Looks fine, see below"
    assert ds[0].state == NEEDS_YOU
    assert build_discussions([review(body_top="Ship It!")], user="kostik") == []


def test_ship_its():
    rs = [review(id=1, author="ark-slav", ship_it=True),
          review(id=2, author="ark-roma", ship_it=True),
          review(id=3, author="ark-slav", ship_it=True),
          review(id=4, author="other", ship_it=False)]
    assert ship_its(rs) == ["ark-slav", "ark-roma"]


def rows(lo=137, hi=147):
    return [DiffRow(row=n, old_line=n, old_text=f"old{n}", new_line=n, new_text=f"new{n}")
            for n in range(lo, hi + 1)]


def test_excerpt_marks_commented_rows():
    ex = code_excerpt(rows(), 141, 3, context=2)
    assert [e.line for e in ex] == list(range(139, 146))
    assert [e.line for e in ex if e.marked] == [141, 142, 143]
    assert ex[0].text == "new139"
    assert line_span(ex) == "141-143"
    assert line_span(code_excerpt(rows(), 141, 1)) == "141"


def test_excerpt_of_removed_lines():
    rs = [DiffRow(row=n, old_line=n + 10, old_text=f"gone{n + 10}", new_line=None, new_text="")
          for n in range(50, 52)]
    ex = code_excerpt(rs, 50, 2, context=2)
    assert [(e.line, e.text) for e in ex] == [(60, "gone60"), (61, "gone61")]
    assert line_span(ex) == "60-61"


def test_excerpt_at_file_start():
    ex = code_excerpt(rows(1, 10), 1, 1, context=2)
    assert [e.line for e in ex] == [1, 2, 3]
    assert ex[0].marked
