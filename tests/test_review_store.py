"""Tests for gg.review_store -- sqlite review metadata storage."""

import sqlite3

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

    def test_series_round_trip(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        entries = [
            ReviewEntry("feature", 1, "1000", "a", "aaa", series=1),
            ReviewEntry("feature", 2, "1001", "b", "bbb", series=2),
        ]
        review_store.save_reviews(entries, cwd=git_repo.work_dir)
        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert [e.series for e in loaded] == [1, 2]

    def test_old_schema_gets_series_column(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        db = git_repo.work_dir / ".gg" / "reviews.db"
        db.parent.mkdir()
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE reviews (branch TEXT NOT NULL, position INTEGER NOT NULL, "
            "review_id TEXT NOT NULL, subject TEXT NOT NULL, diff_hash TEXT NOT NULL, "
            "PRIMARY KEY (branch, position))"
        )
        conn.execute("INSERT INTO reviews VALUES ('feature', 1, '1000', 'a', 'aaa')")
        conn.commit()
        conn.close()
        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert [(e.review_id, e.series) for e in loaded] == [("1000", 1)]

    def test_save_replaces(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        entries1 = [ReviewEntry("feature", 1, "1000", "old", "aaa")]
        review_store.save_reviews(entries1, cwd=git_repo.work_dir)

        entries2 = [ReviewEntry("feature", 1, "2000", "new", "bbb")]
        review_store.save_reviews(entries2, cwd=git_repo.work_dir)

        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert len(loaded) == 1
        assert loaded[0].review_id == "2000"

    def test_save_skips_entries_without_review_id(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        entries = [
            ReviewEntry("feature", 1, "1000", "fix crash", "aaa"),
            ReviewEntry("feature", 2, "", "post failed", "bbb"),
        ]
        review_store.save_reviews(entries, cwd=git_repo.work_dir)
        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert [e.review_id for e in loaded] == ["1000"]

    def test_load_skips_rows_without_review_id(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Rows with an empty id, left by older gg versions, are not reviews."""
        review_store.load_reviews("feature", cwd=git_repo.work_dir)  # create DB
        conn = sqlite3.connect(git_repo.work_dir / ".gg" / "reviews.db")
        conn.executemany(
            "INSERT INTO reviews (branch, position, review_id, subject, diff_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            [("feature", 1, "", "lost id", "aaa"),
             ("feature", 2, "1001", "kept", "bbb")],
        )
        conn.commit()
        conn.close()
        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert [e.review_id for e in loaded] == ["1001"]

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


class TestRemoveReviews:
    def _seed(self, git_repo: GitRepo) -> None:
        review_store.save_reviews(
            [ReviewEntry("base", 1, "1000", "s1", "aaa"),
             ReviewEntry("base", 2, "1001", "t1", "bbb", 2)],
            cwd=git_repo.work_dir,
        )
        review_store.save_diff_hashes("base", {"aaa", "bbb"}, cwd=git_repo.work_dir)

    def test_removes_only_given_ids(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._seed(git_repo)
        review_store.remove_reviews("base", {"1000", "9999"}, cwd=git_repo.work_dir)
        left = review_store.load_reviews("base", cwd=git_repo.work_dir)
        assert [(e.review_id, e.series) for e in left] == [("1001", 2)]
        assert review_store.load_diff_hashes("base", cwd=git_repo.work_dir) == {"aaa", "bbb"}

    def test_clears_branch_when_nothing_left(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._seed(git_repo)
        review_store.remove_reviews("base", {"1000", "1001"}, cwd=git_repo.work_dir)
        assert "base" not in review_store.list_branches(cwd=git_repo.work_dir)


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
