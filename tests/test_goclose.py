"""Tests for git_goclose -- branch deletion after merge."""

from tests.conftest import GitRepo


class TestGoclose:
    def test_switches_to_tracking_and_deletes(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        # Branch is fully merged (no new commits), so -d should succeed
        git_repo.run_gitgo("git_goclose")
        r = git_repo.run_gitgo("git_branchname")
        assert r.stdout.strip() == "master"
        # Verify feature branch is gone
        r2 = git_repo.git("branch", "--list", "feature")
        assert "feature" not in r2.stdout

    def test_fails_with_unmerged_commits(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("unmerged work")
        r = git_repo.run_gitgo("git_goclose")
        # Should fail to delete and switch back to feature
        branch = git_repo.run_gitgo("git_branchname")
        assert branch.stdout.strip() == "feature"

    def test_help_flag(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gitgo("git_goclose", "-h")
        assert r.returncode == 0
        assert "goclose" in r.stdout
