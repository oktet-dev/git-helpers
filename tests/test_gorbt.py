"""Tests for gg rbt -- ReviewBoard posting via the Python CLI."""

import subprocess

from tests.conftest import GitRepo, RbtMock


class TestPostOneDryRun:
    def test_first_post(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-42: fix crash")
        r = git_repo.run_gg("rbt", "-d")
        assert "--tracking-branch=master" in r.stdout
        assert "--bugs-closed=" in r.stdout
        assert "BUG-42" in r.stdout

    def test_update_mode(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-42: fix crash")
        r = git_repo.run_gg("rbt", "-d", "-u")
        assert "--update" in r.stdout
        assert "--guess-description" in r.stdout

    def test_dry_run_quotes_summary(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        """Summary with spaces must be shell-quoted in dry-run output."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-42: fix crash")
        git_repo.commit("BUG-43: add tests")
        r = git_repo.run_gg("rbt", "-d")
        # --key= prefix stays unquoted, value is quoted
        assert "--summary='[1/2]: BUG-42: fix crash'" in r.stdout
        assert "--summary='[2/2]: BUG-43: add tests'" in r.stdout

    def test_depends_on_passed(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        r = git_repo.run_gg("rbt", "-d", "--depends-on", "9999")
        assert "--depends-on=9999" in r.stdout


class TestGgRbt:
    def test_single_commit_no_numbering(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-10: single fix")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 1
        call = rbt_mock.call(0)
        summary_args = [a for a in call if a.startswith("--summary=")]
        assert len(summary_args) == 1
        assert "[1/1]" not in summary_args[0]

    def test_multiple_commits_numbered(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        c1 = rbt_mock.call(1)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        s1 = [a for a in c1 if a.startswith("--summary=")][0]
        assert "[1/2]" in s0
        assert "[2/2]" in s1

    def test_no_numbers_flag(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt", "--no-numbers")
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        assert "[1/" not in s0

    def test_publish_flag(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: publish me")
        git_repo.run_gg("rbt", "--publish")
        call = rbt_mock.call(0)
        assert "-p" in call

    def test_continue_and_depends_on(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: continued")
        git_repo.commit("BUG-2: continued too")
        git_repo.run_gg("rbt", "--continue-from", "5", "--depends-on", "1234")
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        assert "[6/7]" in s0
        assert "--depends-on=1234" in c0

    def test_users_and_groups_passed(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: review me")
        git_repo.run_gg("rbt", "--users", "alice", "--groups", "backend")
        call = rbt_mock.call(0)
        assert "--target-people" in call
        assert "alice" in call
        assert "--target-groups" in call
        assert "backend" in call

    def test_help_flag(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gg("rbt", "-h")
        assert r.returncode == 0
        assert "rbt" in r.stdout

    def test_dependency_chaining(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        """Second commit's --depends-on should be the review ID from first."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 2
        c1 = rbt_mock.call(1)
        # rbt mock returns "Review request #1000 posted." for first call
        assert "--depends-on=1000" in c1


class TestSmartUpdate:
    """Tests for `gg rbt -u` smart update with diff hash cache."""

    def test_first_post_creates_cache(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.run_gg("rbt")
        assert (git_repo.work_dir / ".gg" / "posted-diffs").exists()

    def test_update_skips_unchanged(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 2

        r = git_repo.run_gg("rbt", "-u")
        # No new rbt calls -- both patches unchanged
        assert rbt_mock.call_count() == 2
        assert "skip (unchanged): BUG-1: first" in r.stdout
        assert "skip (unchanged): BUG-2: second" in r.stdout

    def test_update_posts_changed(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 2

        # Amend the second commit to change its diff
        (git_repo.work_dir / "extra_file").write_text("changed\n")
        git_repo.git("add", "extra_file")
        git_repo.git("commit", "--amend", "--no-edit")

        r = git_repo.run_gg("rbt", "-u")
        # Only the amended commit should be posted
        assert rbt_mock.call_count() == 3
        assert "skip (unchanged): BUG-1: first" in r.stdout

    def test_mixed_skip_and_post(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.commit("BUG-3: third")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 3

        # Amend the second commit via rebase
        revs = git_repo.git(
            "log", "--reverse", "--format=%H", "master..HEAD"
        ).stdout.strip().splitlines()
        second_rev = revs[1]

        # Use git replace trick: amend second commit
        git_repo.git("checkout", second_rev)
        (git_repo.work_dir / "changed").write_text("new content\n")
        git_repo.git("add", "changed")
        git_repo.git("commit", "--amend", "--no-edit")
        new_second = git_repo.git("rev-parse", "HEAD").stdout.strip()
        git_repo.git("checkout", "feature")
        # Rebase onto amended second
        subprocess.run(
            ["git", "rebase", "--onto", new_second, second_rev, "feature"],
            cwd=git_repo.work_dir,
            env=git_repo._env,
            capture_output=True,
            text=True,
        )

        r = git_repo.run_gg("rbt", "-u")
        # First and third unchanged, second changed
        assert rbt_mock.call_count() == 4
        assert "skip (unchanged): BUG-1: first" in r.stdout
        assert "skip (unchanged): BUG-3: third" in r.stdout

    def test_update_without_cache_posts_everything(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        """Without a prior cache, -u posts everything (no skip possible)."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        r = git_repo.run_gg("rbt", "-u")
        assert rbt_mock.call_count() == 2
        assert "skip" not in r.stdout

    def test_no_prompt_needed(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        """Update completes without stdin -- no interactive prompt."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.run_gg("rbt")
        r = git_repo.run_gg("rbt", "-u")
        assert r.returncode == 0

    def test_dry_run_does_not_write_cache(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt", "-d")
        assert not (git_repo.work_dir / ".gg" / "posted-diffs").exists()

    def test_single_commit_update_skips(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        """Single commit path also uses cache."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: only one")
        git_repo.run_gg("rbt")
        assert rbt_mock.call_count() == 1

        r = git_repo.run_gg("rbt", "-u")
        assert rbt_mock.call_count() == 1
        assert "skip (unchanged): BUG-1: only one" in r.stdout
