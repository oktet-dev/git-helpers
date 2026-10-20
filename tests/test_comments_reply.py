"""Tests for `gg comments reply`, in process against the in-memory FakeRB."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from gg import cli, comments
from gg.rb_model import Comment, RBError, Reply, ReplyComment, Review
from gg.reply_input import Block, parse_blocks
from tests.fake_rb import NOW, FakeRB

T1 = "2026-10-20T10:00:00Z"
NOT_POSTED = "Not posted (send again as is):\n"

TWO = """\
=== r/19954 #37441
Fewer than 3 traffic classes is legal.
=== r/19954 #37442 fixed
Fixed in this diff.
"""


def comment(cid: int, *, issue: bool = True, kind: str = "diff") -> Comment:
    return Comment(kind=kind, id=cid, author="ark-slav", timestamp=T1,
                   text=f"comment {cid}", issue_opened=issue,
                   issue_status="open" if issue else "")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeRB:
    """Three requests: r/19954 (review 36005), r/19917 (36100), r/19920 (36200)."""
    monkeypatch.chdir(tmp_path)
    f = FakeRB()
    f.add_request("19954", "dcb_ets")
    f.add_review("19954", Review(
        36005, "ark-slav", T1, True, True, "Looks good, one question.",
        [comment(37441), comment(37442), comment(37443, issue=False),
         comment(812, issue=False, kind="general")], []))
    f.add_request("19917", "other")
    f.add_review("19917", Review(36100, "ark-slav", T1, True, False, "",
                                 [comment(37502), comment(37503)], []))
    f.add_request("19920", "third")
    f.add_review("19920", Review(36200, "ark-slav", T1, True, False, "",
                                 [comment(37510)], []))
    monkeypatch.setattr(comments, "connect", lambda cwd: f)
    return f


def reply(monkeypatch: pytest.MonkeyPatch, text: str, *args: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return cli.main(["comments", "reply", *args])


def unsent(out: str) -> tuple[str, list[Block]]:
    """What was printed before the "Not posted" line, and the blocks after it."""
    head, sep, rest = out.partition(NOT_POSTED)
    assert sep, out
    return head, parse_blocks(rest)


def test_one_reply_per_review_then_status(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    assert reply(monkeypatch, TWO) == 0
    assert fake.log == [
        ("create_reply", "19954", 36005),
        ("add_reply_comment", "19954", 36005, 1001, 37441,
         "Fewer than 3 traffic classes is legal."),
        ("add_reply_comment", "19954", 36005, 1001, 37442, "Fixed in this diff."),
        ("publish_reply", "19954", 36005, 1001),
        ("set_issue_status", "19954", 36005, 37442, "resolved"),
    ]
    assert capsys.readouterr().out == (
        "r/19954 #37441  replied\n"
        "r/19954 #37442  replied, resolved\n")


def test_reviews_posted_in_order_of_first_appearance(
        monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
        capsys: pytest.CaptureFixture[str]) -> None:
    text = ("=== r/19917 #37502\nB.\n=== 19954 #37441\nA.\n"
            "=== r/19917 #37503 dropped\n")
    assert reply(monkeypatch, text) == 0
    assert [entry[:3] for entry in fake.log] == [
        ("create_reply", "19917", 36100),
        ("add_reply_comment", "19917", 36100),
        ("publish_reply", "19917", 36100),
        ("set_issue_status", "19917", 36100),
        ("create_reply", "19954", 36005),
        ("add_reply_comment", "19954", 36005),
        ("publish_reply", "19954", 36005),
    ]
    assert capsys.readouterr().out == (
        "r/19917 #37502  replied\n"
        "r/19917 #37503  dropped\n"
        "r/19954 #37441  replied\n")


def test_verifying_result_is_reported(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    fake.status_result[37442] = "verifying-resolved"
    assert reply(monkeypatch, TWO) == 0
    assert "r/19954 #37442  replied, verifying-resolved\n" in capsys.readouterr().out


def test_review_top_reply(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                          capsys: pytest.CaptureFixture[str]) -> None:
    assert reply(monkeypatch, "=== r/19954 review 36005\n\nThanks!\n\n") == 0
    assert fake.log == [
        ("create_reply", "19954", 36005),
        ("set_reply_top", "19954", 36005, 1001, "Thanks!"),
        ("publish_reply", "19954", 36005, 1001),
    ]
    assert capsys.readouterr().out == "r/19954 review 36005  replied\n"


def test_general_comment_reply(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                               capsys: pytest.CaptureFixture[str]) -> None:
    assert reply(monkeypatch, "=== r/19954 #g812\nNoted.\n") == 0
    assert ("add_reply_comment", "19954", 36005, 1001, 812, "Noted.") in fake.log
    assert capsys.readouterr().out == "r/19954 #g812  replied\n"


def test_status_only_block_posts_no_reply(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    assert reply(monkeypatch, "=== r/19954 #37442 fixed\n") == 0
    assert fake.log == [("set_issue_status", "19954", 36005, 37442, "resolved")]
    assert capsys.readouterr().out == "r/19954 #37442  resolved\n"


def test_errors_post_nothing(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                             capsys: pytest.CaptureFixture[str]) -> None:
    text = ("=== r/19954 #99999\nA.\n"
            "=== r/19954 #37502\nB.\n"        # a comment of r/19917
            "=== r/19954 #37443 fixed\nC.\n"  # no issue
            "=== r/19954 #37441\nFine.\n")
    assert reply(monkeypatch, text) == 1
    captured = capsys.readouterr()
    assert "r/19954 #99999" in captured.err
    assert "r/19954 #37502" in captured.err
    assert "r/19954 #37443 fixed" in captured.err
    assert "#37441" not in captured.err
    assert "nothing was posted" in captured.err
    assert captured.out == ""
    assert fake.log == []


def test_unknown_request_and_review_post_nothing(
        monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
        capsys: pytest.CaptureFixture[str]) -> None:
    text = ("=== r/404 #1\nA.\n=== r/404 #2\nB.\n"
            "=== r/19954 review 99\nC.\n=== r/19954 #37441\nFine.\n")
    assert reply(monkeypatch, text) == 1
    err = capsys.readouterr().err
    assert err.count("r/404") == 1
    assert "r/19954 review 99" in err
    assert fake.log == []


def test_comment_of_unpublished_review_is_unknown(
        monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
        capsys: pytest.CaptureFixture[str]) -> None:
    fake.add_review("19920", Review(36201, "kostik", T1, False, False, "draft",
                                    [comment(37600)], []))
    assert reply(monkeypatch, "=== r/19920 #37600\nA.\n=== r/19920 review 36201\nB.\n") == 1
    err = capsys.readouterr().err
    assert "r/19920 #37600" in err and "r/19920 review 36201" in err
    assert fake.log == []


def test_parse_error_posts_nothing(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    assert reply(monkeypatch, "=== r/19954 #37441\nFine.\n=== r/19954 #x1\nA.\n") == 1
    err = capsys.readouterr().err
    assert "line 3" in err
    assert "nothing was posted" in err
    assert fake.log == []


def test_draft_with_content_stops_whole_batch(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    fake.set_draft("19954", 36005, Reply(
        900, "kostik", NOW, False, "",
        [ReplyComment("diff", 37441, "kostik", NOW, "My half-written answer.\nSecond line.")]))
    text = "=== r/19954 #37442\nA.\n=== r/19917 #37502\nB.\n"
    assert reply(monkeypatch, text) == 1
    captured = capsys.readouterr()
    assert ("[gg] r/19954 review 36005: you have an unpublished reply draft there; "
            "nothing was posted.") in captured.err
    assert "#37441" in captured.err
    assert "    My half-written answer.\n    Second line.\n" in captured.err
    assert "19917" not in captured.err
    assert captured.out == ""
    assert fake.log == []


def test_draft_with_top_text_stops_dry_run(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    fake.set_draft("19917", 36100, Reply(901, "kostik", NOW, False, "Thanks, will do.", []))
    assert reply(monkeypatch, "=== r/19917 #37502\nB.\n", "-d") == 1
    captured = capsys.readouterr()
    assert "[gg] r/19917 review 36100: you have an unpublished reply draft there" in captured.err
    assert "Thanks, will do." in captured.err
    assert captured.out == ""
    assert fake.log == []


def test_draft_with_only_footer_reply_stops_whole_batch(
        monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
        capsys: pytest.CaptureFixture[str]) -> None:
    fake.set_draft("19954", 36005, Reply(902, "kostik", NOW, False, "", [],
                                         body_bottom="About the footer: not yet."))
    text = "=== r/19954 #37442\nA.\n=== r/19917 #37502\nB.\n"
    assert reply(monkeypatch, text) == 1
    captured = capsys.readouterr()
    assert ("[gg] r/19954 review 36005: you have an unpublished reply draft there; "
            "nothing was posted.") in captured.err
    assert "  to review 36005's footer:\n    About the footer: not yet.\n" in captured.err
    assert captured.out == ""
    assert fake.log == []


def test_draft_read_error_posts_nothing(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    fake.fail["draft_reply"] = (1, RBError("reading your reply draft failed"))
    assert reply(monkeypatch, "=== r/19954 #37441\nA.\n") == 1
    err = capsys.readouterr().err
    assert "[gg] r/19954 review 36005: reading your reply draft failed" in err
    assert "[gg] nothing was posted." in err
    assert fake.log == []


def test_empty_draft_is_used(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                             capsys: pytest.CaptureFixture[str]) -> None:
    fake.set_draft("19954", 36005, Reply(555, "kostik", NOW, False, "", []))
    assert reply(monkeypatch, "=== r/19954 #37441\nA.\n") == 0
    assert fake.log == [
        ("create_reply", "19954", 36005),
        ("add_reply_comment", "19954", 36005, 555, 37441, "A."),
        ("publish_reply", "19954", 36005, 555),
    ]


def test_dry_run_posts_nothing(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                               capsys: pytest.CaptureFixture[str]) -> None:
    text = TWO + "=== r/19917 #37502 open\n=== r/19954 review 36005\nThanks!\n"
    assert reply(monkeypatch, text, "-d") == 0
    out = capsys.readouterr().out
    assert out.startswith(
        "r/19954 #37441  reply\n"
        "r/19954 #37442  reply, set fixed\n"
        "r/19917 #37502  set open\n"
        "r/19954 review 36005  reply\n")
    assert "nothing was posted" in out
    assert fake.log == []


def test_dry_run_still_checks(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                              capsys: pytest.CaptureFixture[str]) -> None:
    assert reply(monkeypatch, "=== r/19954 #99999\nA.\n", "--dry") == 1
    captured = capsys.readouterr()
    assert "r/19954 #99999" in captured.err
    assert captured.out == ""


def test_failure_deletes_draft_and_prints_unsent(monkeypatch: pytest.MonkeyPatch,
                                                 fake: FakeRB,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    text = ("=== r/19954 #37441\nA.\n"
            "=== r/19917 #37502\nB1.\n"
            "=== r/19917 #37503 fixed\nB2.\n"
            "=== r/19920 #37510 dropped\nC.\n")
    fake.fail["add_reply_comment"] = (2, RBError("boom"))
    assert reply(monkeypatch, text) == 1
    assert fake.log == [
        ("create_reply", "19954", 36005),
        ("add_reply_comment", "19954", 36005, 1001, 37441, "A."),
        ("publish_reply", "19954", 36005, 1001),
        ("create_reply", "19917", 36100),
        ("add_reply_comment", "19917", 36100, 1002, 37502, "B1."),
        ("delete_reply", "19917", 36100, 1002),
    ]
    captured = capsys.readouterr()
    assert "[gg] r/19917 review 36100: boom" in captured.err
    head, blocks = unsent(captured.out)
    assert head == "r/19954 #37441  replied\n"
    assert blocks == parse_blocks(text)[1:]
    assert fake.draft_reply("19917", 36100) is None


def test_unexpected_error_deletes_draft_and_prints_unsent(
        monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
        capsys: pytest.CaptureFixture[str]) -> None:
    text = ("=== r/19954 #37441\nA.\n"
            "=== r/19917 #37502\nB1.\n"
            "=== r/19920 #37510 dropped\nC.\n")
    fake.fail["add_reply_comment"] = (2, ValueError("Expecting value: line 1 column 1"))
    assert reply(monkeypatch, text) == 1
    assert fake.log == [
        ("create_reply", "19954", 36005),
        ("add_reply_comment", "19954", 36005, 1001, 37441, "A."),
        ("publish_reply", "19954", 36005, 1001),
        ("create_reply", "19917", 36100),
        ("add_reply_comment", "19917", 36100, 1002, 37502, "B1."),
        ("delete_reply", "19917", 36100, 1002),
    ]
    captured = capsys.readouterr()
    assert ("[gg] r/19917 review 36100: unexpected error: "
            "ValueError('Expecting value: line 1 column 1')\n") in captured.err
    head, blocks = unsent(captured.out)
    assert head == "r/19954 #37441  replied\n"
    assert blocks == parse_blocks(text)[1:]
    assert fake.draft_reply("19917", 36100) is None


def test_unexpected_delete_error_is_reported(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    fake.fail["publish_reply"] = (1, RBError("boom"))
    fake.fail["delete_reply"] = (1, ValueError("bad page"))
    assert reply(monkeypatch, TWO) == 1
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert "could not delete the unpublished reply 1001 (unexpected error: " \
           "ValueError('bad page'))" in captured.err
    head, blocks = unsent(captured.out)
    assert head == ""
    assert blocks == parse_blocks(TWO)


def test_interrupt_is_not_swallowed(monkeypatch: pytest.MonkeyPatch, fake: FakeRB) -> None:
    fake.fail["publish_reply"] = (1, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        reply(monkeypatch, TWO)


def test_delete_failure_is_reported(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    fake.fail["publish_reply"] = (1, RBError("boom"))
    fake.fail["delete_reply"] = (1, RBError("gone wrong"))
    assert reply(monkeypatch, TWO) == 1
    captured = capsys.readouterr()
    assert "boom" in captured.err and "gone wrong" in captured.err
    head, blocks = unsent(captured.out)
    assert head == ""
    assert blocks == parse_blocks(TWO)


def test_status_failure_prints_status_only_block(monkeypatch: pytest.MonkeyPatch,
                                                 fake: FakeRB,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    fake.fail["set_issue_status"] = (1, RBError("boom"))
    assert reply(monkeypatch, TWO) == 1
    assert ("publish_reply", "19954", 36005, 1001) in fake.log
    assert not any(entry[0] == "delete_reply" for entry in fake.log)
    captured = capsys.readouterr()
    assert "boom" in captured.err
    head, blocks = unsent(captured.out)
    assert head == "r/19954 #37441  replied\nr/19954 #37442  replied\n"
    assert captured.out.endswith(NOT_POSTED + "=== r/19954 #37442 fixed\n")
    assert blocks == [Block("19954", "#37442", "fixed", "")]


def test_later_status_failure_keeps_set_ones_out(monkeypatch: pytest.MonkeyPatch,
                                                 fake: FakeRB,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    text = ("=== r/19917 #37502 fixed\n=== r/19917 #37503 dropped\nOut of scope.\n"
            "=== r/19920 #37510 open\n")
    fake.fail["set_issue_status"] = (2, RBError("boom"))
    assert reply(monkeypatch, text) == 1
    head, blocks = unsent(capsys.readouterr().out)
    assert head == "r/19917 #37502  resolved\nr/19917 #37503  replied\n"
    assert blocks == [Block("19917", "#37503", "dropped", ""),
                      Block("19920", "#37510", "open", "")]


def test_reads_file_argument(monkeypatch: pytest.MonkeyPatch, fake: FakeRB, tmp_path: Path,
                             capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "replies.txt"
    path.write_text("=== r/19954 #37442 fixed\n")
    assert reply(monkeypatch, "not reply input", str(path)) == 0
    assert fake.log == [("set_issue_status", "19954", 36005, 37442, "resolved")]


def test_missing_file_is_an_error(monkeypatch: pytest.MonkeyPatch, fake: FakeRB,
                                  tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert reply(monkeypatch, "", str(tmp_path / "nope.txt")) == 1
    assert "nope.txt" in capsys.readouterr().err
    assert fake.log == []


def test_connect_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                       capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)

    def refuse(cwd: Path) -> FakeRB:
        raise RBError("no REVIEWBOARD_URL in .reviewboardrc")

    monkeypatch.setattr(comments, "connect", refuse)
    assert reply(monkeypatch, "=== r/19954 #37442 fixed\n") == 1
    assert "[gg] no REVIEWBOARD_URL in .reviewboardrc" in capsys.readouterr().err


def test_help_documents_format(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["comments", "reply", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "usage: gg comments reply" in out
    for text in ("=== r/<request> #<comment> [fixed|dropped|open]",
                 "=== r/<request> review <review>", "#g", "#a", "<<'EOF'", "-d",
                 "fixed     resolved", "Not posted (send again as is):",
                 "gg rbt-sync"):
        assert text in out, text
