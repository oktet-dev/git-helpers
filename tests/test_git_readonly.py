"""Tests for git_branchname, git_summary, gitgo_range -- read-only git queries."""

from tests.conftest import GitRepo


class TestGitBranchname:
    def test_returns_current_branch(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gitgo("git_branchname")
        assert r.stdout.strip() == "master"

    def test_returns_tracking_branch(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        r = git_repo.run_gitgo("git_branchname", "@{u}")
        assert r.stdout.strip() == "master"


class TestGitSummary:
    def test_returns_subject_line(self, git_repo: GitRepo) -> None:
        git_repo.commit("BUG-1: fix the thing")
        r = git_repo.run_gitgo("git_summary")
        assert r.stdout.strip() == "BUG-1: fix the thing"

    def test_multiline_returns_first_line(self, git_repo: GitRepo) -> None:
        # Commit with multi-line message
        (git_repo.work_dir / "multi").write_text("multi\n")
        git_repo.git("add", "multi")
        git_repo.git("commit", "-m", "first line\n\nsecond paragraph")
        r = git_repo.run_gitgo("git_summary")
        assert r.stdout.strip() == "first line"


class TestGitgoRange:
    def test_returns_tracking_to_head(self, git_repo: GitRepo) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("work on feature")
        r = git_repo.run_gitgo("gitgo_range")
        # Tracks local master, not origin/master
        assert r.stdout.strip() == "master..HEAD"
