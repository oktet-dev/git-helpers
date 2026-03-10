"""Tests for gg.review_store -- sqlite review metadata storage."""

from gg import review_store
from gg.review_store import ReviewEntry, strip_prefix
from tests.conftest import GitRepo, RbtMock


class TestStripPrefix:
    def test_strips_integer_prefix(self) -> None:
        assert strip_prefix("[1/3]: fix crash") == "fix crash"

    def test_strips_fractional_prefix(self) -> None:
        assert strip_prefix("[2.1/5]: new helper") == "new helper"

    def test_no_prefix(self) -> None:
        assert strip_prefix("fix crash") == "fix crash"

    def test_empty(self) -> None:
        assert strip_prefix("") == ""

    def test_bracket_in_middle(self) -> None:
        assert strip_prefix("fix [1/3] thing") == "fix [1/3] thing"


class TestReviewCRUD:
    def test_load_empty(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        result = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert result == []

    def test_save_and_load(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        entries = [
            ReviewEntry("feature", 1, "1000", "fix crash", "aaa"),
            ReviewEntry("feature", 2, "1001", "add tests", "bbb"),
        ]
        review_store.save_reviews(entries, cwd=git_repo.work_dir)
        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert len(loaded) == 2
        assert loaded[0].review_id == "1000"
        assert loaded[1].subject == "add tests"

    def test_save_replaces(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        entries1 = [ReviewEntry("feature", 1, "1000", "old", "aaa")]
        review_store.save_reviews(entries1, cwd=git_repo.work_dir)

        entries2 = [ReviewEntry("feature", 1, "2000", "new", "bbb")]
        review_store.save_reviews(entries2, cwd=git_repo.work_dir)

        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert len(loaded) == 1
        assert loaded[0].review_id == "2000"

    def test_branches_isolated(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        review_store.save_reviews(
            [ReviewEntry("feat-a", 1, "1000", "a stuff", "aaa")],
            cwd=git_repo.work_dir,
        )
        review_store.save_reviews(
            [ReviewEntry("feat-b", 1, "2000", "b stuff", "bbb")],
            cwd=git_repo.work_dir,
        )
        a = review_store.load_reviews("feat-a", cwd=git_repo.work_dir)
        b = review_store.load_reviews("feat-b", cwd=git_repo.work_dir)
        assert len(a) == 1
        assert a[0].review_id == "1000"
        assert len(b) == 1
        assert b[0].review_id == "2000"


class TestDiffHashCRUD:
    def test_load_empty(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        result = review_store.load_diff_hashes("feature", cwd=git_repo.work_dir)
        assert result == set()

    def test_round_trip(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        hashes = {"aaa", "bbb", "ccc"}
        review_store.save_diff_hashes("feature", hashes, cwd=git_repo.work_dir)
        loaded = review_store.load_diff_hashes("feature", cwd=git_repo.work_dir)
        assert loaded == hashes

    def test_branches_isolated(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        review_store.save_diff_hashes("feat-a", {"aaa"}, cwd=git_repo.work_dir)
        review_store.save_diff_hashes("feat-b", {"bbb"}, cwd=git_repo.work_dir)
        assert review_store.load_diff_hashes("feat-a", cwd=git_repo.work_dir) == {"aaa"}
        assert review_store.load_diff_hashes("feat-b", cwd=git_repo.work_dir) == {"bbb"}
