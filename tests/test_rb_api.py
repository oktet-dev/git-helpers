from __future__ import annotations

from gg import rb_api
from tests.conftest import GitRepo, RbtMock


def test_fetch_depends_on(git_repo: GitRepo, rbt_mock: RbtMock) -> None:
    rbt_mock.set_review("19917", depends_on=["19997"])
    assert rb_api.fetch_depends_on("19917", cwd=git_repo.work_dir) == ["19997"]


def test_fetch_depends_on_none(git_repo: GitRepo, rbt_mock: RbtMock) -> None:
    rbt_mock.set_review("19917")
    assert rb_api.fetch_depends_on("19917", cwd=git_repo.work_dir) == []
