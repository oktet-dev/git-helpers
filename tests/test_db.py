"""Tests for gg db subcommand."""

from gg import review_store
from gg.review_store import ReviewEntry
from tests.conftest import GitRepo, RbtMock


def _seed(repo: GitRepo, branch: str = "feature") -> None:
    """Insert sample reviews and diff hashes for a branch."""
    entries = [
        ReviewEntry(branch, 1, "1000", "fix crash", "aaa111"),
        ReviewEntry(branch, 2, "1001", "add tests", "bbb222"),
    ]
    review_store.save_reviews(entries, cwd=repo.work_dir)
    review_store.save_diff_hashes(branch, {"hash1", "hash2"}, cwd=repo.work_dir)


class TestList:
    def test_shows_reviews_and_hashes(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature")
        _seed(git_repo)
        result = git_repo.run_gg("db", "--list")
        assert result.returncode == 0
        assert "Branch: feature" in result.stdout
        assert "Reviews (2):" in result.stdout
        assert "r/1000" in result.stdout
        assert "r/1001" in result.stdout
        assert "Diff hashes: 2 cached" in result.stdout

    def test_no_data(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("empty-branch")
        result = git_repo.run_gg("db")
        assert result.returncode == 0
        assert "No cached state for branch empty-branch." in result.stdout

    def test_scoped_to_branch(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        _seed(git_repo, branch="other")
        git_repo.create_branch("feature")
        result = git_repo.run_gg("db", "--list", "-b", "other")
        assert "Branch: other" in result.stdout
        assert "r/1000" in result.stdout


    def test_all_shows_multiple_branches(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature")
        _seed(git_repo, branch="alpha")
        _seed(git_repo, branch="beta")
        result = git_repo.run_gg("db", "--list", "--all")
        assert result.returncode == 0
        assert "Branch: alpha" in result.stdout
        assert "Branch: beta" in result.stdout
        assert "---" in result.stdout

    def test_all_no_data(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature")
        result = git_repo.run_gg("db", "--list", "--all")
        assert result.returncode == 0
        assert "No cached state." in result.stdout


class TestClear:
    def test_removes_branch_data(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature")
        _seed(git_repo)
        result = git_repo.run_gg("db", "--clear")
        assert result.returncode == 0
        assert "Cleared state for branch feature." in result.stdout

        result = git_repo.run_gg("db", "--list")
        assert "No cached state" in result.stdout

    def test_scoped_to_branch(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        _seed(git_repo, branch="keep")
        _seed(git_repo, branch="remove")
        git_repo.create_branch("feature")

        git_repo.run_gg("db", "--clear", "-b", "remove")

        result = git_repo.run_gg("db", "-b", "remove")
        assert "No cached state" in result.stdout

        result = git_repo.run_gg("db", "-b", "keep")
        assert "Reviews (2):" in result.stdout


class TestInit:
    def test_wipes_all_branches(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature")
        _seed(git_repo, branch="branch-a")
        _seed(git_repo, branch="branch-b")

        result = git_repo.run_gg("db", "--init")
        assert result.returncode == 0
        assert "Reinitialized .gg/reviews.db" in result.stdout

        result = git_repo.run_gg("db", "-b", "branch-a")
        assert "No cached state" in result.stdout
        result = git_repo.run_gg("db", "-b", "branch-b")
        assert "No cached state" in result.stdout
