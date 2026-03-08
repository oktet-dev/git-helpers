"""Tests for gg rbt -- ReviewBoard posting via the Python CLI."""

from tests.conftest import GitRepo, RbtMock


class TestPostOneDryRun:
    def test_first_post(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-42: fix crash")
        r = git_repo.run_gg("rbt", "-d")
        assert "--tracking-branch=master" in r.stdout
        assert "--bugs-closed=" in r.stdout
        assert "BUG-42" in r.stdout

    def test_update_mode(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-42: fix crash")
        r = git_repo.run_gg("rbt", "-d", "-u")
        assert "--update" in r.stdout
        assert "--guess-description" in r.stdout

    def test_depends_on_passed(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        r = git_repo.run_gg("rbt", "-d", "--depends-on", "9999")
        assert "--depends-on=9999" in r.stdout


class TestGgRbt:
    def test_single_commit_no_numbering(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-10: single fix")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 1
        call = rbt_mock.call(0)
        summary_args = [a for a in call if a.startswith("--summary=")]
        assert len(summary_args) == 1
        assert "[1/1]" not in summary_args[0]

    def test_multiple_commits_numbered(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        c1 = rbt_mock.call(1)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        s1 = [a for a in c1 if a.startswith("--summary=")][0]
        assert "[1/2]" in s0
        assert "[2/2]" in s1

    def test_no_numbers_flag(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt", "--no-numbers")
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        assert "[1/" not in s0

    def test_publish_flag(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: publish me")
        git_repo.run_gg("rbt", "--publish")
        call = rbt_mock.call(0)
        assert "-p" in call

    def test_continue_and_depends_on(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: continued")
        git_repo.commit("BUG-2: continued too")
        git_repo.run_gg("rbt", "--continue-from", "5", "--depends-on", "1234")
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        assert "[6/7]" in s0
        assert "--depends-on=1234" in c0

    def test_users_and_groups_passed(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: review me")
        git_repo.run_gg("rbt", "--users", "alice", "--groups", "backend")
        call = rbt_mock.call(0)
        assert "--target-people" in call
        assert "alice" in call
        assert "--target-groups" in call
        assert "backend" in call

    def test_help_flag(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gg("rbt", "-h")
        assert r.returncode == 0
        assert "rbt" in r.stdout

    def test_dependency_chaining(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        """Second commit's --depends-on should be the review ID from first."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 2
        c1 = rbt_mock.call(1)
        # rbt mock returns "Review request #1000 posted." for first call
        assert "--depends-on=1000" in c1
