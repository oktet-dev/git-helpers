"""Tests for gorbt_defaults, gorbt_one, gorbt -- ReviewBoard posting."""

from tests.conftest import GitRepo, RbtMock


class TestGorbtDefaults:
    def test_initializes_state_vars(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gitgo(
            "git_gorbt_defaults; "
            "echo rbt_action=$rbt_action; "
            "echo rbt_publish=$rbt_publish; "
            "echo rbt_first_post=$rbt_first_post"
        )
        assert "rbt_action=" in r.stdout  # empty
        assert "rbt_publish=false" in r.stdout
        assert "rbt_first_post=true" in r.stdout


class TestGorbtOne:
    def test_dry_run_first_post(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        rev = git_repo.commit("BUG-42: fix crash")
        r = git_repo.run_gitgo(
            "git_gorbt_defaults; rbt_action=echo; "
            f"git_gorbt_one {rev}"
        )
        assert "--tracking-branch=master" in r.stdout
        assert "--bugs-closed=" in r.stdout
        assert "BUG-42" in r.stdout

    def test_dry_run_update_mode(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        rev = git_repo.commit("BUG-42: fix crash")
        r = git_repo.run_gitgo(
            "git_gorbt_defaults; rbt_action=echo; rbt_first_post=false; "
            f"git_gorbt_one {rev}"
        )
        assert "--update" in r.stdout
        assert "--guess-description" in r.stdout

    def test_depends_on_passed(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        git_repo.create_branch("feature", "master")
        rev = git_repo.commit("BUG-1: first")
        # gorbt_one <rev> <branch> <num_string> <dep_id>
        r = git_repo.run_gitgo(
            "git_gorbt_defaults; rbt_action=echo; "
            f"git_gorbt_one {rev} master '#' 9999"
        )
        assert "--depends-on=9999" in r.stdout


class TestGorbt:
    def test_single_commit_no_numbering(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-10: single fix")
        git_repo.run_gitgo("git_gorbt")
        assert rbt_mock.call_count() == 1
        call = rbt_mock.call(0)
        # Single commit should not have [1/1] numbering
        summary_args = [a for a in call if a.startswith("--summary=")]
        assert len(summary_args) == 1
        assert "[1/1]" not in summary_args[0]

    def test_multiple_commits_numbered(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gitgo("git_gorbt")
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
        git_repo.run_gitgo("git_gorbt", "--no-numbers")
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        assert "[1/" not in s0

    def test_publish_flag(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: publish me")
        git_repo.run_gitgo("git_gorbt", "--publish")
        call = rbt_mock.call(0)
        assert "-p" in call

    def test_continue_and_depends_on(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: continued")
        git_repo.commit("BUG-2: continued too")
        git_repo.run_gitgo(
            "git_gorbt", "--continue", "5", "--depends-on", "1234"
        )
        assert rbt_mock.call_count() == 2
        c0 = rbt_mock.call(0)
        s0 = [a for a in c0 if a.startswith("--summary=")][0]
        # continue_from=5, so first is [6/7], second is [7/7]
        assert "[6/7]" in s0
        # First call should have --depends-on=1234
        assert "--depends-on=1234" in c0

    def test_users_and_groups_passed(
        self, git_repo: GitRepo, rbt_mock: RbtMock
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: review me")
        git_repo.run_gitgo(
            "git_gorbt", "--users", "alice", "--groups", "backend"
        )
        call = rbt_mock.call(0)
        assert "--target-people" in call
        assert "alice" in call
        assert "--target-groups" in call
        assert "backend" in call

    def test_help_flag(self, git_repo: GitRepo) -> None:
        r = git_repo.run_gitgo("git_gorbt", "-h")
        assert r.returncode == 0
        assert "gorbt" in r.stdout
