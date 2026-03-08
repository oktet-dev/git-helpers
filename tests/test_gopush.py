"""Tests for git_gopush -- push to tracking branch."""

from tests.conftest import GitRepo


class TestGopush:
    def test_dry_run_prints_command(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        r = git_repo.run_gitgo("git_gopush", "-d")
        assert "git push origin HEAD:master" in r.stdout

    def test_to_overrides_destination(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        r = git_repo.run_gitgo("git_gopush", "-d", "-t", "other-branch")
        assert "HEAD:other-branch" in r.stdout

    def test_rev_argument(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        rev = git_repo.commit("test commit")
        r = git_repo.run_gitgo("git_gopush", "-d", rev)
        assert f"{rev}:master" in r.stdout

    def test_help_flag(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        r = git_repo.run_gitgo("git_gopush", "-h")
        assert r.returncode == 0
        assert "gopush" in r.stdout
