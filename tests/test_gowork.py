"""Tests for git_gowork -- branch creation with tracking."""

from tests.conftest import GitRepo


class TestGowork:
    def test_creates_branch(self, git_repo: GitRepo) -> None:
        git_repo.run_gitgo("git_gowork", "my-feature")
        r = git_repo.run_gitgo("git_branchname")
        assert r.stdout.strip() == "my-feature"

    def test_branch_tracks_parent(self, git_repo: GitRepo) -> None:
        git_repo.run_gitgo("git_gowork", "my-feature")
        r = git_repo.run_gitgo("git_branchname", "@{u}")
        assert r.stdout.strip() == "master"

    def test_nested_tracking(self, git_repo: GitRepo) -> None:
        git_repo.run_gitgo("git_gowork", "parent-branch")
        git_repo.run_gitgo("git_gowork", "child-branch")
        r = git_repo.run_gitgo("git_branchname", "@{u}")
        assert r.stdout.strip() == "parent-branch"

    def test_help_flag(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gitgo("git_gowork", "-h")
        assert r.returncode == 0
        assert "gowork" in r.stdout
