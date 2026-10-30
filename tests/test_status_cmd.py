"""Tests for `gg status`, in process against the in-memory FakeRB."""

from __future__ import annotations

import re

import pytest

from gg import cli, comments, review_store
from gg.rb_model import Comment, Review
from tests import test_rbt_sync
from tests.conftest import GitRepo, RbtMock
from tests.fake_rb import FakeRB

T1 = "2026-10-20T10:00:00Z"


def issue(cid: int, status: str) -> Comment:
    return Comment(kind="diff", id=cid, author="ark-slav", timestamp=T1, text="why?",
                   issue_opened=True, issue_status=status)


def ship_it(review_id: int, author: str, *, public: bool = True) -> Review:
    return Review(review_id, author, T1, public, True, "Ship It!", [], [])


def remarks(review_id: int, comments: list[Comment], *, public: bool = True) -> Review:
    return Review(review_id, "ark-slav", T1, public, False, "", comments, [])


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRB:
    f = FakeRB()
    monkeypatch.setattr(comments, "connect", lambda cwd: f)
    return f


@pytest.fixture
def repo(git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    monkeypatch.chdir(git_repo.work_dir)
    return git_repo


def stack(repo: GitRepo, fake: FakeRB) -> list[str]:
    """s1, s2 (series 1), c1, c2 (series 2) posted; n1 (series 3) not. Their ids."""
    test_rbt_sync.TestSeries._stack(None, repo)
    assert repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
    entries = review_store.load_reviews("stack", cwd=repo.work_dir)
    for e in entries:
        fake.add_request(e.review_id, e.subject)
    return [e.review_id for e in entries]


def row(out: str, number: str, ship: str, issues: str, review: str, subject: str) -> bool:
    tail = rf"\s+{re.escape(subject)}" if subject else r"\s*"
    pattern = rf"^{re.escape(number)}\s+{ship}\s+{issues}\s+{re.escape(review)}{tail}$"
    return re.search(pattern, out, re.M) is not None


def test_branch_table(repo: GitRepo, rbt_mock: RbtMock, fake: FakeRB,
                      capsys: pytest.CaptureFixture[str]) -> None:
    s1, s2, c1, c2 = stack(repo, fake)
    fake.add_review(s1, ship_it(1, "alice"))
    fake.add_review(s1, ship_it(2, "bob"))
    fake.add_review(s1, ship_it(3, "alice"))                  # the same reviewer again
    fake.add_review(s2, ship_it(4, "carol", public=False))    # an unpublished review
    fake.add_review(c1, remarks(5, [issue(1, "open"), issue(2, "verifying-resolved"),
                                    issue(3, "verifying-dropped"), issue(4, "resolved"),
                                    issue(5, "dropped"), issue(6, "")]))
    fake.add_review(c1, remarks(6, [issue(7, "open")], public=False))

    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^#\s+Ship It\s+Issues\s+Review\s+Subject$", out, re.M)
    assert "Series 1: 2 patches" in out
    assert f"Series 2: 2 patches, depends on r/{s2}" in out
    assert f"Series 3: 1 patch, new, depends on r/{c2}" in out
    assert row(out, "[1/2]", "2", "0", f"r/{s1}", "s1")
    assert row(out, "[2/2]", "0", "0", f"r/{s2}", "s2")
    assert row(out, "[1/2]", "0", "3", f"r/{c1}", "c1")
    assert row(out, "[2/2]", "0", "0", f"r/{c2}", "c2")
    assert row(out, "[1/1]", "--", "--", "--", "n1")


def test_series_selection(repo: GitRepo, rbt_mock: RbtMock, fake: FakeRB,
                          capsys: pytest.CaptureFixture[str]) -> None:
    s1, s2, c1, c2 = stack(repo, fake)
    assert cli.main(["status", "-s", "2"]) == 0
    out = capsys.readouterr().out
    assert "Series 1" not in out and f"r/{s1}" not in out
    assert "Series 2: 2 patches" in out
    assert row(out, "[1/2]", "0", "0", f"r/{c1}", "c1")


def test_nothing_posted_in_scope(repo: GitRepo, rbt_mock: RbtMock, fake: FakeRB,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    stack(repo, fake)
    assert cli.main(["status", "-s", "3"]) == 1
    assert "nothing posted to ReviewBoard in this scope" in capsys.readouterr().err


def test_explicit_ids(repo: GitRepo, fake: FakeRB, capsys: pytest.CaptureFixture[str]) -> None:
    fake.add_request("19954", "[1/2]: net: add the thing")
    fake.add_request("19955", "[2/2]: net: use the thing")
    fake.add_review("19954", ship_it(1, "alice"))
    fake.add_review("19955", remarks(2, [issue(1, "open")]))
    assert cli.main(["status", "r/19954", "19955"]) == 0
    out = capsys.readouterr().out
    assert "Series" not in out
    assert row(out, "--", "1", "0", "r/19954", "[1/2]: net: add the thing")
    assert row(out, "--", "0", "1", "r/19955", "[2/2]: net: use the thing")


def test_ids_and_series_refused(repo: GitRepo, fake: FakeRB,
                                capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status", "5", "-s", "1"]) == 1
    assert "[gg] -s and review request ids cannot be combined." in capsys.readouterr().err


def test_request_error_reported_others_shown(repo: GitRepo, fake: FakeRB,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    fake.add_request("19954", "ok")
    assert cli.main(["status", "404", "19954"]) == 1
    captured = capsys.readouterr()
    assert "[gg] r/404: review request 404 not found" in captured.err
    assert row(captured.out, "--", r"\?", r"\?", "r/404", "")
    assert row(captured.out, "--", "0", "0", "r/19954", "ok")


def test_full_links(repo: GitRepo, fake: FakeRB, capsys: pytest.CaptureFixture[str]) -> None:
    (repo.work_dir / ".reviewboardrc").write_text('REVIEWBOARD_URL = "https://rb.example.com/"\n')
    fake.add_request("19954", "ok")
    assert cli.main(["status", "19954", "-l"]) == 0
    assert row(capsys.readouterr().out, "--", "0", "0", "https://rb.example.com/r/19954/", "ok")


def test_help_lists_status(repo: GitRepo) -> None:
    r = repo.run_gg("--help")
    assert r.returncode == 0
    assert "status" in r.stdout
