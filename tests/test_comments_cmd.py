"""Tests for `gg comments`, in process against the in-memory FakeRB."""

from __future__ import annotations

import pytest

from gg import cli, comments, review_store
from gg.rb_model import Comment, DiffRow, Review
from tests import test_rbt_sync
from tests.conftest import GitRepo, RbtMock
from tests.fake_rb import FakeRB

T1 = "2026-10-20T10:00:00Z"


def diff_comment(cid=37441, status="open", **kw) -> Comment:
    args = dict(kind="diff", id=cid, author="ark-slav", timestamp=T1,
                text="Why does it matter?", issue_opened=True, issue_status=status,
                severity="info", path="net-drv-ts/dcb/dcb_ets.c", filediff_id=5,
                diff_revision=1, first_row=3, num_rows=1)
    args.update(kw)
    return Comment(**args)


def add_discussion(fake: FakeRB, rid: str, status="open", cid=37441) -> None:
    fake.add_request(rid, f"summary of {rid}")
    fake.add_review(rid, Review(36005, "ark-slav", T1, True, False, "",
                                [diff_comment(cid, status)], []))
    fake.set_rows(5, [DiffRow(i, i, f"old {i}", i, f"line {i}") for i in range(1, 7)])


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRB:
    f = FakeRB()
    monkeypatch.setattr(comments, "connect", lambda cwd: f)
    return f


@pytest.fixture
def repo(git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    monkeypatch.chdir(git_repo.work_dir)
    return git_repo


def test_explicit_id(repo: GitRepo, fake: FakeRB, capsys: pytest.CaptureFixture[str]) -> None:
    add_discussion(fake, "19954")
    assert cli.main(["comments", "r/19954"]) == 0
    out = capsys.readouterr().out
    assert "#37441" in out and "needs you" in out
    assert "net-drv-ts/dcb/dcb_ets.c:3" in out
    assert "      > 3  line 3" in out
    assert out.rstrip().endswith("1 discussion needs you, 0 wait on reviewers.")


def test_done_hidden_unless_all(repo: GitRepo, fake: FakeRB,
                                capsys: pytest.CaptureFixture[str]) -> None:
    add_discussion(fake, "7", status="resolved")
    assert cli.main(["comments", "7"]) == 0
    out = capsys.readouterr().out
    assert "#37441" not in out
    assert "0 discussions need you" in out
    assert cli.main(["comments", "7", "--all"]) == 0
    out = capsys.readouterr().out
    assert "#37441" in out and "1 done." in out


def test_default_scope_is_the_branch(repo: GitRepo, rbt_mock: RbtMock, fake: FakeRB,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    test_rbt_sync.TestSeries._stack(None, repo)
    assert repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
    entries = review_store.load_reviews("stack", cwd=repo.work_dir)
    c2_id = entries[3].review_id
    c2_rev = repo.git("rev-parse", "--short", "HEAD~1").stdout.strip()
    for e in entries:
        fake.add_request(e.review_id, e.subject)
    add_discussion(fake, c2_id)

    assert cli.main(["comments"]) == 0
    out = capsys.readouterr().out
    assert f"r/{c2_id}" in out
    assert f"series 2, patch 2, commit {c2_rev[:8]}" in out

    assert cli.main(["comments", "-s", "1"]) == 0
    out = capsys.readouterr().out
    assert f"r/{c2_id}" not in out


def test_new_series_only_scope_is_empty(repo: GitRepo, rbt_mock: RbtMock, fake: FakeRB,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    test_rbt_sync.TestSeries._stack(None, repo)
    assert repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
    assert cli.main(["comments", "-s", "3"]) == 1
    assert "nothing posted to ReviewBoard in this scope" in capsys.readouterr().err


def test_ids_and_series_refused(repo: GitRepo, fake: FakeRB,
                                capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["comments", "5", "-s", "1"]) == 1
    assert "[gg] -s and review request ids cannot be combined." in capsys.readouterr().err


def test_bad_id_is_an_argparse_error(repo: GitRepo, fake: FakeRB) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["comments", "x/5"])
    assert e.value.code == 2


def test_request_error_reported_others_shown(repo: GitRepo, fake: FakeRB,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    add_discussion(fake, "19954")
    assert cli.main(["comments", "404", "19954"]) == 1
    captured = capsys.readouterr()
    assert "r/404" in captured.err
    assert "#37441" in captured.out


def test_code_not_available_when_rows_fail(repo: GitRepo, fake: FakeRB,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    add_discussion(fake, "19954")
    del fake._rows[5]
    assert cli.main(["comments", "19954"]) == 0
    assert "(rows 3-3): code not available" in capsys.readouterr().out


def test_help_lists_forms(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(["comments", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "gg comments -s" in out
    for form in ("-s 3,4", "-s $", "-s 4:1-3,5", "2:2-$"):
        assert form in out
    assert "review <id>" in out
    assert "gg comments reply --help" in out
