"""gg commands report a missing upstream instead of crashing with a traceback."""

import pytest

from tests.conftest import GitRepo, RbtMock


@pytest.mark.parametrize("cmd", [["rbt-sync", "-d"], ["rbt", "-d"]])
def test_no_upstream_reports_clear_error(
    git_repo: GitRepo, rbt_mock: RbtMock, cmd: list[str],
) -> None:
    git_repo.git("checkout", "--no-track", "-b", "feature")
    git_repo.commit("work on feature")

    r = git_repo.run_gg(*cmd)

    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert "branch 'feature' has no upstream" in r.stderr
    assert "git branch --set-upstream-to=" in r.stderr
    assert rbt_mock.call_count() == 0
