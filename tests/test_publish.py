"""Tests for `gg publish` -- publish all drafts in the current branch."""

from __future__ import annotations

import re

from tests.conftest import GitRepo, RbtMock

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _post_series(git_repo: GitRepo) -> None:
    r = git_repo.run_gg("rbt")
    assert r.returncode == 0, f"gg rbt failed: {r.stderr}"


class TestGgPublish:
    def test_publishes_each_review(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("publish")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial:]
        publish_calls = [c for c in new_calls if c and c[0] == "publish"]
        assert len(publish_calls) == 2
        assert sorted(c[1] for c in publish_calls) == ["1000", "1001"]

    def test_dry_run_prints_commands_without_executing(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("publish", "-d")
        assert r.returncode == 0
        assert "rbt publish 1000" in r.stdout
        # No new rbt invocations
        assert rbt_mock.call_count() == initial

    def test_errors_when_no_reviews(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        # No `gg rbt` call -> nothing in reviews.db

        r = git_repo.run_gg("publish")
        assert r.returncode == 1
        assert "No reviews" in (r.stdout + r.stderr)

    def test_explicit_branch_flag(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        # Move to master so the implicit branch lookup would find nothing
        git_repo.git("checkout", "master")
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("publish", "-b", "feature")
        assert r.returncode == 0
        new_calls = rbt_mock.calls()[initial:]
        publish_calls = [c for c in new_calls if c and c[0] == "publish"]
        assert len(publish_calls) == 1
        assert publish_calls[0][1] == "1000"

    def test_lists_reviews_being_published(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        r = git_repo.run_gg("publish", "-d")
        out = _plain(r.stdout)
        # Each line should mention the review id
        assert "r/1000" in out
        assert "r/1001" in out
