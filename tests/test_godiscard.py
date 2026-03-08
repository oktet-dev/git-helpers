"""Tests for git_godiscard -- force delete with confirmation."""

from tests.conftest import GitRepo


class TestGodiscard:
    def test_confirm_yes_deletes_branch(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("doomed", "master")
        git_repo.commit("some work")
        git_repo.run_gitgo("git_godiscard", stdin="y\n")
        r = git_repo.run_gitgo("git_branchname")
        assert r.stdout.strip() == "master"
        r2 = git_repo.git("branch", "--list", "doomed")
        assert "doomed" not in r2.stdout

    def test_confirm_no_keeps_branch(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("keep-me", "master")
        git_repo.commit("some work")
        git_repo.run_gitgo("git_godiscard", stdin="n\n")
        r = git_repo.run_gitgo("git_branchname")
        assert r.stdout.strip() == "keep-me"

    def test_help_flag(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gitgo("git_godiscard", "-h")
        assert r.returncode == 0
        assert "godiscard" in r.stdout
