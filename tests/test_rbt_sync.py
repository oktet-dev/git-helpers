"""Tests for gg rbt-sync -- series reconciliation with ReviewBoard."""

import os
import re
import subprocess
import sys
import textwrap

import pytest

from gg import review_store
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

    def test_no_existing_reviews_auto_new(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Empty reviews.db: `gg rbt-sync -d` auto-falls into --new."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        # Plan shows the commit as a CREATE
        assert "create" in r.stdout
        # Stderr notice on the auto path
        assert "No existing reviews; posting as a fresh series" in r.stderr

    def test_auto_new_executes(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Empty reviews.db: non-dry `gg rbt-sync` posts every commit."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0

        post_calls = [c for c in rbt_mock.calls() if c and c[0] == "post"]
        assert len(post_calls) == 2

        # reviews.db now has two entries
        from gg import review_store
        entries = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert len(entries) == 2

    def test_explicit_new_no_auto_notice(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """`--new` with populated DB does not emit the auto-new notice."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        # Add one more commit so the explicit --new has something to replace
        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync", "--new", "-d")
        assert r.returncode == 0
        assert "No existing reviews; posting as a fresh series" not in r.stderr

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

        # Insert a commit inside the series (one added on top would start a
        # new series) — old series was [1/2],[2/2], new is [1/3]..[3/3]
        add_tests = git_repo.git("rev-parse", "HEAD").stdout.strip()
        git_repo.git("reset", "--hard", "HEAD~1")
        git_repo.commit("new feature")
        git_repo.git("cherry-pick", add_tests)

        r = git_repo.run_gg("rbt-sync", "--renumber")
        assert r.returncode == 0
        all_calls = rbt_mock.calls()[initial_calls:]
        post_calls = [c for c in all_calls if c and c[0] == "post"]
        # 2 re-posts (stale prefix; the second also re-chains) + 1 create
        assert len(post_calls) == 3
        assert any("--summary=[1/3]: fix crash" in c for c in post_calls)

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

    def test_create_inherits_every_reviewer(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        r = git_repo.run_gg("rbt", "-U", "alice", "-U", "bob")
        assert r.returncode == 0

        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0

        post_calls = [c for c in rbt_mock.calls() if c and c[0] == "post"]
        last_post = post_calls[-1]
        assert last_post[last_post.index("--target-people") + 1] == "alice,bob"

    def test_failed_create_is_not_recorded(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """A post that yields no review id must not be saved as a review."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        rbt_mock.queue_failure("ERROR: boom\n")
        git_repo.run_gg("rbt-sync")

        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        assert "create" in r.stdout
        assert "keep" not in r.stdout


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
    def test_interactive_parse_error_exits_cleanly(
        self, git_repo: GitRepo, rbt_mock: RbtMock, tmp_path,
    ) -> None:
        """A stray separator is reported; nothing is posted."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()
        git_repo.commit("another")

        git_repo._env["EDITOR"] = _make_editor_script(tmp_path, "1i\\\n---")
        git_repo._env.pop("VISUAL", None)

        r = git_repo.run_gg("rbt-sync", "-i")
        assert r.returncode == 1
        assert "[gg]" in r.stderr
        assert "Traceback" not in r.stderr
        assert rbt_mock.call_count() == initial_calls

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


class TestExplicitReviewers:
    def test_users_override_inheritance(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """-U overrides reviewer inheritance from depends-on."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.run_gg("rbt", "-U", "alice")

        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync", "-U", "bob")
        assert r.returncode == 0

        calls = rbt_mock.calls()
        post_calls = [c for c in calls if c and c[0] == "post"]
        last_post = post_calls[-1]
        assert "--target-people" in last_post
        assert "bob" in last_post
        # Should NOT have alice (inherited) — bob overrides
        assert "alice" not in last_post

    def test_groups_override(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """-G overrides group inheritance."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.run_gg("rbt", "-G", "team-a")

        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync", "-G", "team-b")
        assert r.returncode == 0

        calls = rbt_mock.calls()
        post_calls = [c for c in calls if c and c[0] == "post"]
        last_post = post_calls[-1]
        assert "--target-groups" in last_post
        assert "team-b" in last_post
        assert "team-a" not in last_post

    def test_reviewers_shown_in_plan(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Plan output includes reviewer/group header."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        r = git_repo.run_gg("rbt-sync", "-d", "-U", "alice", "-G", "devteam")
        assert r.returncode == 0
        assert "Reviewers: alice" in r.stdout
        assert "Groups: devteam" in r.stdout


class TestNoNumbers:
    def test_no_numbers_suppresses_prefix(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--no-numbers prevents [i/N] prefix on posted reviews."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        # Amend to trigger update
        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")

        r = git_repo.run_gg("rbt-sync", "--no-numbers")
        assert r.returncode == 0

        calls = rbt_mock.calls()
        post_calls = [c for c in calls if c and c[0] == "post"]
        last_post = post_calls[-1]
        # The summary should not start with [N/M]:
        summary_args = [a for a in last_post if a.startswith("--summary=")]
        assert summary_args
        summary = summary_args[0].split("=", 1)[1]
        assert not summary.startswith("[")

    def test_no_numbers_on_create(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--no-numbers also works for newly created reviews."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync", "--no-numbers")
        assert r.returncode == 0

        calls = rbt_mock.calls()
        post_calls = [c for c in calls if c and c[0] == "post"]
        last_post = post_calls[-1]
        summary_args = [a for a in last_post if a.startswith("--summary=")]
        assert summary_args
        summary = summary_args[0].split("=", 1)[1]
        assert not summary.startswith("[")


class TestCloseFlag:
    def test_close_no_reviews_errors(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--close with no DB entries returns 1."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")

        r = git_repo.run_gg("rbt-sync", "--close")
        assert r.returncode == 1
        assert "No reviews to close" in r.stdout

    def test_close_dry_run_shows_plan(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """-d --close prints reviews but makes no rbt calls."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Simulate pushed branch: reset to master so tracking..HEAD is empty
        git_repo.git("reset", "--hard", "master")

        r = git_repo.run_gg("rbt-sync", "-d", "--close")
        assert r.returncode == 0
        assert "close r/" in r.stdout
        # No new rbt calls in dry mode
        assert rbt_mock.call_count() == initial_calls

    def test_close_calls_rbt_close_submitted(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--close calls rbt close --close-type=submitted for each review."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        git_repo.git("reset", "--hard", "master")

        r = git_repo.run_gg("rbt-sync", "--close")
        assert r.returncode == 0

        all_calls = rbt_mock.calls()
        new_calls = all_calls[initial_calls:]
        close_calls = [c for c in new_calls if c and c[0] == "close"]
        assert len(close_calls) == 2
        for c in close_calls:
            assert "--close-type=submitted" in c

    def test_close_clears_db(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """After --close, load_reviews() returns empty."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        git_repo.git("reset", "--hard", "master")

        r = git_repo.run_gg("rbt-sync", "--close")
        assert r.returncode == 0

        # Try --close again: should error with "no reviews"
        r2 = git_repo.run_gg("rbt-sync", "--close")
        assert r2.returncode == 1
        assert "No reviews to close" in r2.stdout

    def test_close_empty_range_ok(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--close works even when tracking..HEAD is empty (main use case)."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        # Push to origin so tracking..HEAD becomes empty
        git_repo.git("reset", "--hard", "master")

        r = git_repo.run_gg("rbt-sync", "--close")
        assert r.returncode == 0
        assert "Closed 1 review(s) as submitted" in r.stderr


class TestNewFlag:
    def test_new_bypasses_no_reviews_guard(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--new allows sync even when no existing reviews are stored."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")

        r = git_repo.run_gg("rbt-sync", "--new")
        assert r.returncode == 0
        assert "2 created" in r.stderr

    def test_new_creates_all_from_scratch(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--new with existing reviews produces only CREATE actions (no discard)."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        git_repo.commit("temporary hack")
        _post_series(git_repo)

        # Drop last commit — normally would show discard
        git_repo.git("reset", "--hard", "HEAD~1")

        r = git_repo.run_gg("rbt-sync", "--new", "-d")
        assert r.returncode == 0
        out = r.stdout
        assert "create" in out
        assert "discard" not in out

    def test_new_no_close_calls(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """--new does not close any old reviews on ReviewBoard."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        git_repo.commit("temporary hack")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        # Drop last commit and sync with --new
        git_repo.git("reset", "--hard", "HEAD~1")

        r = git_repo.run_gg("rbt-sync", "--new")
        assert r.returncode == 0

        all_calls = rbt_mock.calls()
        new_calls = all_calls[initial_calls:]
        has_close = any("close" in c for c in new_calls)
        assert not has_close

    def test_new_replaces_db(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """After --new, re-running sync sees the fresh series (all keep)."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)

        # Add a third commit, sync with --new
        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync", "--new")
        assert r.returncode == 0

        # Re-running plain sync should show all keep
        r2 = git_repo.run_gg("rbt-sync", "-d")
        assert r2.returncode == 0
        lines = [l for l in r2.stdout.splitlines() if "keep" in l]
        assert len(lines) == 3


class TestPublishUnchangedOnSync:
    """rbt-sync -p must publish KEEP drafts, not just no-op them."""

    def test_publish_publishes_kept_reviews(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()
        # Initial _post_series uses `gg rbt` without -p; reviews are drafts.

        r = git_repo.run_gg("rbt-sync", "-p")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial_calls:]
        publish_calls = [c for c in new_calls if c and c[0] == "publish"]
        # Both KEEP entries get an explicit rbt publish call
        assert len(publish_calls) == 2
        published_ids = sorted(c[1] for c in publish_calls)
        assert published_ids == ["1000", "1001"]

    def test_no_publish_when_flag_absent(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)
        initial_calls = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial_calls:]
        assert [c for c in new_calls if c and c[0] == "publish"] == []


class TestSyncRetry:
    def test_keep_publish_retries_after_207(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo._env["GG_RBT_MISSING_BASE_DELAY"] = "0"
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        rbt_mock.queue_failure(
            output="Error Message: The file was not found in the repository.\n"
                   "API Code: code: 207\n",
            returncode=1,
            count=1,
        )
        r = git_repo.run_gg("rbt-sync", "-p")
        assert r.returncode == 0
        publish_calls = [c for c in rbt_mock.calls() if c and c[0] == "publish"]
        assert len(publish_calls) == 2

    def test_keep_publish_gives_up_after_4_attempts(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo._env["GG_RBT_MISSING_BASE_DELAY"] = "0"
        git_repo._env["GG_RBT_MISSING_BASE_RETRIES"] = "3"

        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        rbt_mock.queue_failure(
            output="Error Message: The file was not found in the repository.\n"
                   "API Code: code: 207\n",
            returncode=1,
            count=10,
        )
        r = git_repo.run_gg("rbt-sync", "-p")
        # rbt-sync's KEEP branch calls publish_one without propagating its
        # exit code (current behavior), so the overall command may still
        # succeed even when publish fails. But the 4 attempts must have
        # been made.
        publish_calls = [c for c in rbt_mock.calls() if c and c[0] == "publish"]
        # 4 attempts: 1 initial + 3 retries
        assert len(publish_calls) == 4


class TestForceFlag:
    def test_force_converts_keep_to_update(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync", "-f")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial:]
        post_calls = [c for c in new_calls if c and c[0] == "post"]
        # Both kept commits get re-posted with -r <id>
        assert len(post_calls) == 2
        for c in post_calls:
            assert any(arg == "-r" for arg in c), c

    def test_force_keeps_create_and_discard(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("to drop")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        # Drop the second commit; add a new one
        git_repo.git("reset", "--hard", "HEAD~1")
        git_repo.commit("new feature")

        r = git_repo.run_gg("rbt-sync", "-f")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial:]
        post_calls = [c for c in new_calls if c and c[0] == "post"]
        close_calls = [c for c in new_calls if c and c[0] == "close"]
        # 1 forced update (the kept "fix crash") + 1 create (the new commit)
        assert len(post_calls) == 2
        # 1 discard of the dropped review
        assert len(close_calls) == 1

    def test_force_with_publish_publishes_each(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync", "-f", "-p")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial:]
        post_calls = [c for c in new_calls if c and c[0] == "post"]
        publish_calls = [c for c in new_calls if c and c[0] == "publish"]
        # 2 forced re-posts, each with -p
        assert len(post_calls) == 2
        for c in post_calls:
            assert "-p" in c, c
        # No separate rbt publish calls (UPDATE+-p covers it)
        assert publish_calls == []

    def test_force_header_appears_in_plan(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)
        r = git_repo.run_gg("rbt-sync", "-f", "-d")
        assert r.returncode == 0
        assert "Force: yes" in r.stdout


class TestSeries:
    def _stack(self, git_repo: GitRepo) -> None:
        """base: s1,s2 posted; stack = base + c1,c2 + n1."""
        git_repo.create_branch("base", "master")
        git_repo.commit("s1")
        git_repo.commit("s2")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        git_repo.git("checkout", "-b", "stack", "--track", "master")
        git_repo.git("merge", "--ff-only", "base")
        git_repo.commit("c1")
        git_repo.commit("c2")
        git_repo.commit("n1")

    def _ids(self, git_repo: GitRepo, branch: str = "stack") -> list[str]:
        return [e.review_id for e in review_store.load_reviews(branch, cwd=git_repo.work_dir)]

    def test_stacked_branch_shows_series(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        base_ids = self._ids(git_repo, "base")
        assert git_repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
        ids = self._ids(git_repo)  # s1, s2 (adopted), c1, c2
        assert ids[:2] == base_ids
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert "Series 1: 2 patches" in out
        assert f"Series 2: 2 patches, depends on r/{ids[1]}" in out
        assert f"Series 3: 1 patch, new, depends on r/{ids[3]}" in out
        assert re.search(r"\[1/1\]\s+create", out)

    def test_range_without_saved_series_keeps_them(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        r = git_repo.run_gg("rbt-sync", "HEAD~1..HEAD")
        assert r.returncode == 0, r.stderr
        assert not [c for c in rbt_mock.calls() if c and c[0] == "close"]
        posts = [c for c in rbt_mock.calls() if c and c[0] == "post"]
        assert "--summary=[1/1]: n1" in posts[-1]

    def test_landed_series_submitted(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        ids = self._ids(git_repo)
        git_repo.git("checkout", "master")
        git_repo.git("merge", "--ff-only", "base")
        git_repo.git("push", "origin", "master")
        git_repo.git("branch", "-D", "base")
        git_repo.git("checkout", "stack")
        r = git_repo.run_gg("rbt-sync", "-d")
        assert "landed in" in r.stdout and "submit" in r.stdout
        git_repo.run_gg("rbt-sync")
        closes = [c for c in rbt_mock.calls() if c and c[0] == "close"]
        assert ["close", "--close-type=submitted", ids[0]] in closes

    def test_dropped_shared_series_forgotten(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        git_repo.git("rebase", "--onto", "master", "base", "stack")
        r = git_repo.run_gg("rbt-sync")
        assert "forget" in r.stdout
        assert not [c for c in rbt_mock.calls() if c and c[0] == "close"]

    def test_record_of_deleted_source_branch_cleared(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.git("branch", "-D", "base")
        assert git_repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
        assert "base" not in review_store.list_branches(cwd=git_repo.work_dir)

    def test_range_off_branch_refused(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.git("checkout", "-b", "side", "master")
        git_repo.commit("elsewhere")
        git_repo.git("checkout", "stack")
        calls = rbt_mock.call_count()
        r = git_repo.run_gg("rbt-sync", "master..side")
        assert r.returncode == 1 and "not on 'stack'" in r.stderr
        assert rbt_mock.call_count() == calls

    def test_out_of_range_change_detected_later(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        c1_id = self._ids(git_repo)[2]
        c1 = git_repo.git("rev-parse", "HEAD~2").stdout.strip()
        (git_repo.work_dir / "c1-extra").write_text("x\n")
        git_repo.git("add", "c1-extra")
        git_repo.git("commit", "--fixup", c1)
        git_repo.git("-c", "sequence.editor=:", "rebase", "-i", "--autosquash", "master")
        assert git_repo.run_gg("rbt-sync", "HEAD~1..HEAD").returncode == 0
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert re.search(rf"\[1/2\]\s+update\s+\S+\s+r/{c1_id}\s+c1", out)

    def test_force_respects_range(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        calls = rbt_mock.call_count()
        git_repo.run_gg("rbt-sync", "-f", "HEAD~1..HEAD")
        posts = [c for c in rbt_mock.calls()[calls:] if c and c[0] == "post"]
        assert len(posts) == 1

    def test_deleted_source_keeps_reviews_not_taken(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """base: s1,s2 (series 1), t1,t2 (series 2); stack: s1 s2 t1 o1 t2."""
        git_repo.create_branch("base", "master")
        git_repo.commit("s1")
        git_repo.commit("s2")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        git_repo.commit("t1")
        git_repo.commit("t2")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        git_repo.create_branch("other", "master")
        git_repo.commit("o1")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        git_repo.create_branch("stack", "master")
        for rev in ("base~3", "base~2", "base~1", "other", "base"):
            assert git_repo.git("cherry-pick", rev).returncode == 0
        git_repo.git("branch", "-D", "base")
        t_ids = self._ids(git_repo, "base")[2:]
        calls = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0, r.stderr
        assert "not contiguous" in r.stderr
        assert self._ids(git_repo, "base") == t_ids
        assert git_repo.run_gg("rbt-sync").returncode == 0
        posts = [c for c in rbt_mock.calls()[calls:] if c and c[0] == "post"]
        assert not [
            c for c in posts
            if any(a.startswith("--summary=") and a.endswith((": t1", ": t2")) for a in c)
        ]

    def test_changed_commit_outside_range_waits(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        c2_id = self._ids(git_repo)[3]
        c2 = git_repo.git("rev-parse", "HEAD~1").stdout.strip()
        n1 = git_repo.git("rev-parse", "HEAD").stdout.strip()
        # Drop c1, amend c2.
        git_repo.git("reset", "--hard", "HEAD~3")
        git_repo.git("cherry-pick", c2)
        (git_repo.work_dir / "c2-extra").write_text("x\n")
        git_repo.git("add", "c2-extra")
        git_repo.git("commit", "--amend", "--no-edit")
        git_repo.git("cherry-pick", n1)
        calls = rbt_mock.call_count()

        assert git_repo.run_gg("rbt-sync", "HEAD~1..HEAD").returncode == 0
        posts = [c for c in rbt_mock.calls()[calls:] if c and c[0] == "post"]
        assert len(posts) == 1 and "--summary=[1/1]: n1" in posts[0]
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert re.search(rf"update\s+\S+\s+r/{c2_id}\s+c2", out)

    def test_publish_respects_range(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        calls = rbt_mock.call_count()
        assert git_repo.run_gg("rbt-sync", "-p", "HEAD~1..HEAD").returncode == 0
        new_calls = rbt_mock.calls()[calls:]
        assert not [c for c in new_calls if c and c[0] == "publish"]
        posts = [c for c in new_calls if c and c[0] == "post"]
        assert len(posts) == 1 and "-p" in posts[0]

    def test_dry_run_leaves_db_alone(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        before = git_repo.run_gg("db", "--all").stdout
        git_repo.run_gg("rbt-sync", "-d")
        assert git_repo.run_gg("db", "--all").stdout == before


class TestAcceptance:
    """The spec's acceptance case, scaled down: series recorded under other
    branches both below and above the branch's own series, plus new commits.
    """

    def _ids(self, git_repo: GitRepo, branch: str) -> list[str]:
        return [e.review_id for e in review_store.load_reviews(branch, cwd=git_repo.work_dir)]

    def test_stacked_branch_syncs_as_four_series(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        for name, prefix in (("scenario", "s"), ("dcb", "d")):
            git_repo.git("checkout", "master")
            git_repo.create_branch(name, "master")
            for i in (1, 2, 3):
                git_repo.commit(f"{name}: {prefix}{i}")
            assert git_repo.run_gg("rbt-sync").returncode == 0
        s_ids = self._ids(git_repo, "scenario")
        d_ids = self._ids(git_repo, "dcb")
        # mq's own series, posted on top of scenario's last review.
        git_repo.git("checkout", "master")
        git_repo.create_branch("mq", "master")
        git_repo.commit("conf: c1")
        git_repo.commit("conf: c2")
        assert git_repo.run_gg("rbt-sync", "-D", s_ids[-1]).returncode == 0
        c_ids = self._ids(git_repo, "mq")
        # Stack: scenario below mq's series, dcb and new commits above it.
        assert git_repo.git("rebase", "--onto", "scenario", "master", "mq").returncode == 0
        assert git_repo.git("cherry-pick", "master..dcb").returncode == 0
        for i in (1, 2, 3):
            git_repo.commit(f"mq: n{i}")

        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert "Series 1: 3 patches, from 'scenario'\n" in out
        assert f"Series 2: 2 patches (was series 1), depends on r/{s_ids[-1]}\n" in out
        assert f"Series 3: 3 patches, from 'dcb', depends on r/{c_ids[-1]}\n" in out
        assert f"Series 4: 3 patches, new, depends on r/{d_ids[-1]}\n" in out
        assert re.search(rf"\[1/3\]\s+keep\+dep\s+\S+\s+r/{d_ids[0]}\s+dcb: d1", out)
        assert len(re.findall(r"\]\s+keep\s", out)) == 7
        assert len(re.findall(r"\]\s+create\s", out)) == 3

        calls = rbt_mock.call_count()
        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0, r.stderr
        ids = self._ids(git_repo, "mq")
        assert ids == s_ids + c_ids + d_ids + ids[8:]
        assert [e.series for e in review_store.load_reviews("mq", cwd=git_repo.work_dir)] == [
            1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 4]
        n_ids = ids[8:]
        posts = [c for c in rbt_mock.calls()[calls:] if c and c[0] in ("post", "close", "publish")]
        assert [(c[c.index("-r") + 1] if "-r" in c else None,
                 next(a for a in c if a.startswith("--summary=")),
                 next(a for a in c if a.startswith("--depends-on="))) for c in posts] == [
            (d_ids[0], "--summary=[1/3]: dcb: d1", f"--depends-on={c_ids[-1]}"),
            (None, "--summary=[1/3]: mq: n1", f"--depends-on={d_ids[-1]}"),
            (None, "--summary=[2/3]: mq: n2", f"--depends-on={n_ids[0]}"),
            (None, "--summary=[3/3]: mq: n3", f"--depends-on={n_ids[1]}"),
        ]

        calls = rbt_mock.call_count()
        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0, r.stderr
        assert not [c for c in rbt_mock.calls()[calls:] if c and c[0] in ("post", "close", "publish")]


class TestFailedPost:
    def test_failed_create_stops_the_run(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("s1")
        git_repo.commit("s2")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        before = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        git_repo.commit("n1")
        git_repo.commit("n2")
        calls = rbt_mock.call_count()
        rbt_mock.queue_failure("ERROR: boom\n")

        # -U: no reviewer lookup, so n1's post is the first rbt call
        r = git_repo.run_gg("rbt-sync", "-U", "alice")
        assert r.returncode == 1
        assert "[gg] stopped after a failed post; run gg rbt-sync again to retry." in r.stderr
        posts = [c for c in rbt_mock.calls()[calls:] if c and c[0] == "post"]
        assert len(posts) == 1  # n1's failed create; n2 not posted
        assert review_store.load_reviews("feature", cwd=git_repo.work_dir) == before
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert "Series 2: 2 patches, new" in out
        assert len(re.findall(r"\]\s+create", out)) == 2

    def test_exception_mid_run_keeps_what_was_done(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("n1")
        git_repo.commit("n2")
        git_repo.commit("n3")
        # n1 is created with no reviewer lookup; the lookup for n2 (n1's
        # reviewers) fails with SystemExit.
        rbt_mock.queue_failure("ERROR: boom\n", command="api-get")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 1
        assert "rbt api-get failed" in r.stderr
        posts = [c for c in rbt_mock.calls() if c and c[0] == "post"]
        assert len(posts) == 1
        [n1] = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert n1.subject == "n1"
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert re.search(rf"\[1/1\]\s+keep\s+--\s+r/{n1.review_id}\s+n1", out)
        assert len(re.findall(r"\]\s+create", out)) == 2

    def test_interrupt_mid_run_saves_and_reraises(
        self, git_repo: GitRepo, rbt_mock: RbtMock, monkeypatch,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("a1")
        git_repo.commit("a2")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        a1, a2 = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        git_repo.commit("n1")

        from gg import cli, sync
        real_post = sync.post_one
        posts: list[str] = []

        def post_one(*args, **kwargs):
            posts.append(args[0])
            if len(posts) == 2:
                raise KeyboardInterrupt
            return real_post(*args, **kwargs)

        monkeypatch.setattr(sync, "post_one", post_one)
        monkeypatch.chdir(git_repo.work_dir)
        # -f: a1 and a2 are re-posted; the interrupt hits a2's post.
        with pytest.raises(KeyboardInterrupt):
            cli.main(["rbt-sync", "-f"])

        saved = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert [(e.review_id, e.diff_hash) for e in saved] == [
            (a1.review_id, a1.diff_hash), (a2.review_id, "post-failed")]
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert re.search(rf"\[1/2\]\s+keep\s+--\s+r/{a1.review_id}\s+a1", out)
        assert re.search(rf"\[2/2\]\s+update\s+\S+\s+r/{a2.review_id}\s+a2", out)
        assert re.search(r"\[1/1\]\s+create\s+\S+\s+--\s+n1", out)

    def test_stop_with_no_live_review_clears_closed_ones(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("a1")
        git_repo.commit("a2")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        git_repo.git("reset", "--hard", "master")
        git_repo.commit("n1")
        rbt_mock.queue_failure("ERROR: boom\n", command="post")
        calls = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync", "-U", "alice")
        assert r.returncode == 1
        closes = [c for c in rbt_mock.calls()[calls:] if c and c[0] == "close"]
        assert len(closes) == 2
        assert review_store.load_reviews("feature", cwd=git_repo.work_dir) == []
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert "discard" not in out
        assert re.search(r"\[1/1\]\s+create\s+\S+\s+--\s+n1", out)

    def test_failed_update_is_retried(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        (git_repo.work_dir / "extra").write_text("changed\n")
        git_repo.git("add", "extra")
        git_repo.git("commit", "--amend", "--no-edit")
        rbt_mock.queue_failure("ERROR: boom\n")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 1
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert re.search(r"\[2/2\]\s+update\s+\S+\s+r/\d+\s+add tests", out)
