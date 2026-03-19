"""Tests for gg rbt-sync -- series reconciliation with ReviewBoard."""

import os
import re
import subprocess
import sys
import textwrap

from tests.conftest import GitRepo, RbtMock

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _post_series(git_repo: GitRepo) -> None:
    """Post the current series with gg rbt to seed reviews.db."""
    r = git_repo.run_gg("rbt")
    assert r.returncode == 0, f"gg rbt failed: {r.stderr}"


class TestSyncDryRun:
    def test_unchanged_series_all_keep(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        out = r.stdout
        assert "keep" in out
        # No new rbt calls in dry mode
        assert rbt_mock.call_count() == initial_calls

    def test_amended_commit_shows_update(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        # Amend last commit to change its diff
        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")

        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        out = r.stdout
        assert "keep" in out
        assert "update" in out

    def test_dropped_commit_shows_discard(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        git_repo.commit("temporary hack")
        _post_series(git_repo)

        # Drop last commit
        git_repo.git("reset", "--hard", "HEAD~1")

        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        out = r.stdout
        assert "discard" in out

    def test_inserted_commit_shows_create(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        rev1 = git_repo.commit("fix crash")
        rev2 = git_repo.commit("add tests")
        _post_series(git_repo)

        # Insert a commit between the two via rebase
        full_revs = git_repo.git(
            "log", "--reverse", "--format=%H", "master..HEAD"
        ).stdout.strip().splitlines()

        git_repo.git("checkout", full_revs[0])
        git_repo.commit("inserted helper")
        new_insert = git_repo.git("rev-parse", "HEAD").stdout.strip()
        # Cherry-pick the second commit on top
        git_repo.git("cherry-pick", full_revs[1])
        new_head = git_repo.git("rev-parse", "HEAD").stdout.strip()

        # Point the branch to the new series
        git_repo.git("checkout", "feature")
        git_repo.git("reset", "--hard", new_head)

        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        out = r.stdout
        assert "create" in out

    def test_renumber_flag(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        r = git_repo.run_gg("rbt-sync", "-d", "--renumber")
        assert r.returncode == 0
        out = r.stdout
        assert "[1/" in out
        assert "[2/" in out


class TestSyncExecution:
    def test_update_posts_changed_only(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Amend last commit
        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0
        # Should have posted only the changed review (update) + close none
        new_calls = rbt_mock.call_count() - initial_calls
        assert new_calls == 1  # one rbt post -r call

    def test_discard_calls_rbt_close(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("to be dropped")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Drop last commit
        git_repo.git("reset", "--hard", "HEAD~1")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0

        # Check that rbt close was called
        all_calls = rbt_mock.calls()
        close_calls = [c for c in all_calls[initial_calls:] if c[0:2] == ["post", "close"] or (len(c) > 1 and c[1] == "close")]
        # The mock logs all rbt calls; close would be ["close", "--close-type=discarded", "ID"]
        new_calls = all_calls[initial_calls:]
        has_close = any("close" in c for c in new_calls)
        assert has_close

    def test_no_existing_reviews_errors(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 1
        assert "No existing reviews" in r.stdout

    def test_create_posts_new_review(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Add a new commit
        git_repo.commit("new feature")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0
        new_calls = rbt_mock.call_count() - initial_calls
        # One new post for the created review
        assert new_calls >= 1


    def test_renumber_reposts_stale_prefix(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--renumber re-posts kept reviews whose [i/N] prefix changed."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Add a third commit — old series was [1/2],[2/2], new is [1/3]..[3/3]
        git_repo.commit("new feature")

        r = git_repo.run_gg("rbt-sync", "--renumber")
        assert r.returncode == 0
        all_calls = rbt_mock.calls()[initial_calls:]
        post_calls = [c for c in all_calls if c and c[0] == "post"]
        # 2 re-posts (stale prefix) + 1 create = 3 rbt post calls
        assert len(post_calls) == 3

    def test_renumber_skips_matching_prefix(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--renumber does not re-post when [i/N] already matches."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # No changes — positions [1/2],[2/2] still correct
        r = git_repo.run_gg("rbt-sync", "--renumber")
        assert r.returncode == 0
        assert rbt_mock.call_count() == initial_calls


class TestPlanPublishColumn:
    def test_publish_flag_shows_yes(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """With -p -d, update/create rows show 'yes', keep rows show '--'."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        # Amend last commit to trigger update
        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")

        r = git_repo.run_gg("rbt-sync", "-p", "-d")
        assert r.returncode == 0
        out = r.stdout
        assert "Pub" in out
        # keep row has '--', update row has 'yes'
        for line in out.splitlines():
            if "keep" in line and "keep+dep" not in line:
                assert "--" in line
            if "update" in line:
                assert "yes" in line

    def test_no_publish_flag_shows_draft(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Without -p, update/create rows show 'draft'."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")

        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        out = r.stdout
        assert "Pub" in out
        for line in out.splitlines():
            if "update" in line:
                assert "draft" in line

    def test_all_keep_no_pub_column(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """When all actions are keep, Pub column is omitted."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        assert "Pub" not in r.stdout


class TestExecutionSummary:
    def test_summary_after_sync(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """After sync execution, stderr has correct counts."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        # Amend last commit (1 keep + 1 update)
        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0
        assert "Synced:" in r.stderr
        assert "1 kept" in r.stderr
        assert "1 updated" in r.stderr

    def test_summary_with_create_and_discard(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Summary includes created and discarded counts."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("to be dropped")
        _post_series(git_repo)

        # Drop last commit and add a new one
        git_repo.git("reset", "--hard", "HEAD~1")
        git_repo.commit("new feature")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0
        assert "Synced:" in r.stderr
        assert "1 created" in r.stderr
        assert "1 discarded" in r.stderr


class TestReviewerInheritance:
    def test_create_inherits_reviewers(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """New reviews inherit target-people and target-groups from depends-on."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        r = git_repo.run_gg("rbt", "-U", "alice", "-G", "devteam")
        assert r.returncode == 0

        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0

        # Find the post call for the new review (the last post call)
        calls = rbt_mock.calls()
        post_calls = [c for c in calls if c and c[0] == "post"]
        last_post = post_calls[-1]
        assert "--target-people" in last_post
        assert "alice" in last_post
        assert "--target-groups" in last_post
        assert "devteam" in last_post


class TestSyncState:
    def test_reviews_db_updated_after_sync(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        # Add a new commit and sync
        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0

        # Re-running sync should show the updated state
        r2 = git_repo.run_gg("rbt-sync", "-d")
        assert r2.returncode == 0
        out = r2.stdout
        # All three should now be keep (no changes since last sync)
        lines = [l for l in out.splitlines() if "keep" in l]
        assert len(lines) == 3


def _make_editor_script(tmp_path, sed_expr: str) -> str:
    """Create a script that applies a sed expression to the file argument."""
    script = tmp_path / "fake_editor.sh"
    script.write_text(f"#!/bin/sh\nsed -i '{sed_expr}' \"$1\"\n")
    script.chmod(0o755)
    return str(script)


def _make_clear_editor(tmp_path) -> str:
    """Create a script that empties the file (abort)."""
    script = tmp_path / "clear_editor.sh"
    script.write_text("#!/bin/sh\n: > \"$1\"\n")
    script.chmod(0o755)
    return str(script)


class TestInteractiveMode:
    def test_interactive_skip_discard(
        self, git_repo: GitRepo, rbt_mock: RbtMock, tmp_path,
    ) -> None:
        """Editor changes 'discard' to 'skip', review is not closed."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("to be dropped")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Drop last commit so it shows as discard
        git_repo.git("reset", "--hard", "HEAD~1")

        editor = _make_editor_script(tmp_path, "s/^discard/skip   /")
        git_repo._env["EDITOR"] = editor
        # Unset VISUAL so EDITOR is used
        git_repo._env.pop("VISUAL", None)

        r = git_repo.run_gg("rbt-sync", "-i")
        assert r.returncode == 0

        # No close calls should have happened
        all_calls = rbt_mock.calls()
        new_calls = all_calls[initial_calls:]
        has_close = any("close" in c for c in new_calls)
        assert not has_close

        # The skipped entry should be preserved -- next dry-run should
        # still show it as discard
        r2 = git_repo.run_gg("rbt-sync", "-d")
        assert r2.returncode == 0
        assert "discard" in r2.stdout

    def test_interactive_abort(
        self, git_repo: GitRepo, rbt_mock: RbtMock, tmp_path,
    ) -> None:
        """Editor empties file, sync aborts with no execution."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Amend so there's something to sync
        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")

        editor = _make_clear_editor(tmp_path)
        git_repo._env["EDITOR"] = editor
        git_repo._env.pop("VISUAL", None)

        r = git_repo.run_gg("rbt-sync", "-i")
        assert r.returncode == 0
        assert "Aborted" in r.stdout

        # No new rbt calls
        assert rbt_mock.call_count() == initial_calls
