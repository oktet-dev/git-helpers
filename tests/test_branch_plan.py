from gg.branch_plan import branch_planner
from tests.conftest import GitRepo, RbtMock
from tests import test_rbt_sync


def test_plans_the_whole_branch(git_repo: GitRepo, rbt_mock: RbtMock) -> None:
    test_rbt_sync.TestSeries._stack(None, git_repo)  # type: ignore[arg-type]
    assert git_repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
    planner = branch_planner(cwd=git_repo.work_dir)
    assert planner.branch == "stack"
    plan = planner.plan_for(set(planner.full))
    assert [len(s.actions) for s in plan.series] == [2, 2, 1]
