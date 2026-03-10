"""Tests for gg.diff_cache -- diff hash caching for smart updates."""

from tests.conftest import GitRepo, RbtMock

from gg import diff_cache


class TestDiffHash:
    def test_returns_64_char_hex(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        rev = git_repo.commit("BUG-1: first")
        h = diff_cache.diff_hash(rev, cwd=git_repo.work_dir)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_diff_same_hash(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        rev = git_repo.commit("BUG-1: first")
        h1 = diff_cache.diff_hash(rev, cwd=git_repo.work_dir)
        h2 = diff_cache.diff_hash(rev, cwd=git_repo.work_dir)
        assert h1 == h2

    def test_different_diff_different_hash(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        r1 = git_repo.commit("BUG-1: first")
        r2 = git_repo.commit("BUG-2: second")
        h1 = diff_cache.diff_hash(r1, cwd=git_repo.work_dir)
        h2 = diff_cache.diff_hash(r2, cwd=git_repo.work_dir)
        assert h1 != h2


class TestLoadSaveHashes:
    def test_load_empty_when_no_file(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        result = diff_cache.load_hashes(cwd=git_repo.work_dir)
        assert result == set()

    def test_round_trip(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        hashes = {"aaa", "bbb", "ccc"}
        diff_cache.save_hashes(hashes, cwd=git_repo.work_dir)
        loaded = diff_cache.load_hashes(cwd=git_repo.work_dir)
        assert loaded == hashes

    def test_save_creates_gg_dir(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        gg_dir = git_repo.work_dir / ".gg"
        assert not gg_dir.exists()
        diff_cache.save_hashes({"abc"}, cwd=git_repo.work_dir)
        assert gg_dir.is_dir()
        assert (gg_dir / "reviews.db").exists()
