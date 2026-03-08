"""Tests for git_gopr -- pull request creation helper."""

from tests.conftest import GitRepo


class TestGopr:
    def test_help_flag(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gitgo("git_gopr", "-h")
        assert r.returncode == 0
        assert "gopr" in r.stdout

    def test_ssh_url_transformed_to_https(self, git_repo: GitRepo) -> None:
        # Set origin to an SSH-style URL
        git_repo.git(
            "remote", "set-url", "origin", "git@github.com:foo/bar.git"
        )
        git_repo.create_branch("feature", "master")
        git_repo.commit("some work")
        # Add a fork remote
        git_repo.git(
            "remote", "add", "myfork", "git@github.com:myuser/bar.git"
        )
        r = git_repo.run_gitgo("git_gopr", "-F", "myfork")
        # The URL should be https, not git@
        assert "https://github.com/foo/bar" in r.stdout

    def test_fork_mode_outputs_pr_url(self, git_repo: GitRepo) -> None:
        git_repo.git(
            "remote", "set-url", "origin", "git@github.com:org/repo.git"
        )
        git_repo.create_branch("feature", "master")
        git_repo.commit("fork work")
        git_repo.git(
            "remote", "add", "myfork", "git@github.com:forkuser/repo.git"
        )
        r = git_repo.run_gitgo("git_gopr", "-F", "myfork")
        assert "forkuser" in r.stdout
        assert "compare" in r.stdout
