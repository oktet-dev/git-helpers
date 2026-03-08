"""Parametrized test: every go* function responds to -h with help text and exit 0."""

import pytest

from tests.conftest import GitRepo

# Functions that need a git repo context (they call git_branchname etc.)
REPO_FUNCTIONS = [
    "git_gowork",
    "git_gopull",
    "git_goshow",
    "git_golog",
    "git_gopush",
    "git_goclose",
    "git_godiscard",
    "git_gopublish",
    "git_gorbt",
    "git_gopr",
]


@pytest.mark.parametrize("func", REPO_FUNCTIONS)
def test_help_flag(func: str, git_repo: GitRepo) -> None:
    r = git_repo.run_gitgo(func, "-h")
    assert r.returncode == 0, f"{func} -h returned {r.returncode}: {r.stderr}"
    # All help output should contain at least some text
    assert len(r.stdout.strip()) > 0, f"{func} -h produced no output"
