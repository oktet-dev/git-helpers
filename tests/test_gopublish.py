"""Tests for git_gopublish -- publish branch to personal namespace."""

import os

from tests.conftest import GitRepo


class TestGopublish:
    def test_dry_run_shows_user_prefix(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("my-feature", "master")
        r = git_repo.run_gitgo("git_gopublish", "-d")
        user = os.environ.get("USER", os.getlogin())
        assert f"user/{user}/my-feature" in r.stdout

    def test_initial_includes_upstream_flag(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("my-feature", "master")
        r = git_repo.run_gitgo("git_gopublish", "-d", "--initial")
        assert "-u" in r.stdout

    def test_already_prefixed_branch_not_doubled(self, git_repo: GitRepo) -> None:
        user = os.environ.get("USER", os.getlogin())
        prefixed = f"user/{user}/feature"
        git_repo.create_branch(prefixed, "master")
        r = git_repo.run_gitgo("git_gopublish", "-d")
        # Destination should be user/X/feature, not user/X/user/X/feature
        assert f"user/{user}/user/{user}" not in r.stdout

    def test_help_flag(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("my-feature", "master")
        r = git_repo.run_gitgo("git_gopublish", "-h")
        assert r.returncode == 0
        assert "gopublish" in r.stdout or "publish" in r.stdout.lower()
