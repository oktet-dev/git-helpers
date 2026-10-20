"""Plan the current branch as a list of patch series, for `gg rbt-sync` and others."""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gg import diff_cache, git, rb_api, review_store
from gg.matcher import NewCommit
from gg.series import SeriesPlan, build_plan


@dataclass
class BranchPlanner:
    """The branch's commits and a planner over them."""

    branch: str
    base: str                                   # git.range_base()
    full: list[str]                             # base..HEAD, oldest first
    plan_for: Callable[[set[str]], SeriesPlan]  # plan, acting on these commits


def _build_new_commits(revs: list[str], *, cwd: Path) -> list[NewCommit]:
    """Build NewCommit list from revision hashes."""
    commits = []
    for rev in revs:
        subject = git.summary(rev, cwd=cwd)
        h = diff_cache.diff_hash(rev, cwd=cwd)
        commits.append(NewCommit(rev=rev, subject=subject, diff_hash=h))
    return commits


def branch_planner(
    *, cwd: Path, new: bool = False, depends_on: str | None = None,
) -> BranchPlanner:
    """A planner for the whole branch (`new`: ignore its own record).

    Only the branch name, base and commit list are read now. The record,
    the commits' diff hashes and the ReviewBoard lookups are loaded on the
    first `plan_for` call and kept for the following ones.
    """
    branch = git.branchname(cwd=cwd)
    base = git.range_base(cwd=cwd)
    full = git.list_revs(f"{base}..HEAD", cwd=cwd)
    # -s plans the branch twice; cache the lookups so RB is read once.
    landed = functools.cache(lambda: git.upstream_subjects(base, cwd=cwd))
    depends_on_of = functools.cache(
        lambda review_id: rb_api.fetch_depends_on(review_id, cwd=cwd),
    )

    @functools.cache
    def load() -> tuple[
        list[review_store.ReviewEntry],
        dict[str, list[review_store.ReviewEntry]],
        list[NewCommit],
    ]:
        own = [] if new else review_store.load_reviews(branch, cwd=cwd)
        foreign = {
            b: review_store.load_reviews(b, cwd=cwd)
            for b in review_store.list_branches(cwd=cwd)
            if b != branch
        }
        return own, foreign, _build_new_commits(full, cwd=cwd)

    def plan_for(in_range: set[str]) -> SeriesPlan:
        own, foreign, commits = load()
        return build_plan(
            branch=branch,
            own=own,
            foreign=foreign,
            commits=commits,
            in_range=in_range,
            landed_subjects=landed,
            branch_exists=lambda name: git.branch_exists(name, cwd=cwd),
            depends_on_of=depends_on_of,
            initial_depends=depends_on,
        )

    return BranchPlanner(branch=branch, base=base, full=full, plan_for=plan_for)
