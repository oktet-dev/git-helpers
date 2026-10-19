"""Tests for gg.series -- planning a branch as a list of series."""

from gg.matcher import NewCommit
from gg.review_store import ReviewEntry
from gg.series import build_plan


def entry(branch, pos, rid, subject, series=1, h=None):
    return ReviewEntry(branch, pos, rid, subject, h or f"h-{subject}", series=series)


def commit(subject, h=None, rev=None):
    return NewCommit(rev=rev or f"r-{subject}", subject=subject, diff_hash=h or f"h-{subject}")


def plan_of(own, commits, *, foreign=None, in_range=None, landed=(), live=(), deps=None, initial=None):
    return build_plan(
        branch="stack", own=own, foreign=foreign or {}, commits=commits,
        in_range=in_range if in_range is not None else {c.rev for c in commits},
        landed_subjects=lambda: set(landed),
        branch_exists=lambda b: b in live,
        depends_on_of=lambda rid: (deps or {}).get(rid, []),
        initial_depends=initial,
    )


def members(plan):
    return [(s.number, [a.new_commit.subject for a in s.actions]) for s in plan.series]


def action(plan, subject):
    return next(a for s in plan.series for a in s.actions if a.new_commit.subject == subject)


def test_adopts_foreign_series_and_starts_new_one():
    foreign = {"base": [entry("base", 1, "1000", "s1"), entry("base", 2, "1001", "s2")]}
    plan = plan_of([], [commit("s1"), commit("s2"), commit("c1")], foreign=foreign)
    assert members(plan) == [(1, ["s1", "s2"]), (2, ["c1"])]
    assert plan.series[0].origin == "base" and plan.series[1].new
    assert action(plan, "s2").old_entry.review_id == "1001"
    assert action(plan, "c1").kind.value == "create"
    assert plan.adopted_from == {"base"}


def test_recorded_series_stay_apart():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b", series=2)]
    plan = plan_of(own, [commit("a"), commit("b"), commit("d")])
    assert members(plan) == [(1, ["a"]), (2, ["b"]), (3, ["d"])]


def test_insert_inside_series_joins_it():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b")]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")])
    assert members(plan) == [(1, ["a", "x", "b"])]


def test_insert_at_boundary_joins_series_below():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b", series=2)]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")])
    assert members(plan) == [(1, ["a", "x"]), (2, ["b"])]


def test_moved_commit_joins_series_it_sits_in():
    own = [entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b"), entry("stack", 3, "3", "c"),
           entry("stack", 4, "4", "d", series=2), entry("stack", 5, "5", "e", series=2)]
    plan = plan_of(own, [commit(s) for s in ("a", "b", "d", "c", "e")])
    assert members(plan) == [(1, ["a", "b"]), (2, ["d", "c", "e"])]
    assert action(plan, "c").kind.value == "update" and action(plan, "c").moved


def test_foreign_overlap_joins_existing_series():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b")]
    foreign = {"base": [entry("base", 1, "1000", "a"), entry("base", 2, "1001", "b"),
                        entry("base", 3, "1002", "c")]}
    plan = plan_of(own, [commit("a"), commit("b"), commit("c")], foreign=foreign)
    assert members(plan) == [(1, ["a", "b", "c"])]
    assert action(plan, "c").old_entry.review_id == "1002"


def test_noncontiguous_foreign_series_skipped():
    own = [entry("stack", 1, "900", "x")]
    foreign = {"base": [entry("base", 1, "1000", "s1"), entry("base", 2, "1001", "s2")]}
    plan = plan_of(own, [commit("s1"), commit("x"), commit("s2")], foreign=foreign)
    assert members(plan) == [(1, ["x"])]
    assert any("'base'" in w for w in plan.warnings)


def test_conflicting_foreign_ids_skipped():
    foreign = {"one": [entry("one", 1, "1000", "s")], "two": [entry("two", 1, "2000", "s")]}
    plan = plan_of([], [commit("s"), commit("x")], foreign=foreign)
    assert members(plan) == [(1, ["x"])]
    assert any("r/1000" in w and "r/2000" in w for w in plan.warnings)


def test_duplicate_subject_adopted_once():
    foreign = {"base": [entry("base", 1, "1000", "s")]}
    plan = plan_of([], [commit("s", rev="r1"), commit("s", h="h2", rev="r2")], foreign=foreign)
    ids = [a.old_entry.review_id for s in plan.series for a in s.actions if a.old_entry]
    assert ids == ["1000"]
    assert members(plan) == [(1, ["s"]), (2, ["s"])]


def test_out_of_range_commit_kept_even_if_changed():
    own = [entry("stack", 1, "1000", "a", h="old")]
    plan = plan_of(own, [commit("a", h="new"), commit("n")], in_range={"r-n"})
    a = action(plan, "a")
    assert a.kind.value == "keep" and a.outside_range
    assert members(plan) == [(1, ["a"]), (2, ["n"])]


def test_range_end_leaves_later_commits_out():
    plan = plan_of([], [commit("n1"), commit("n2")], in_range={"r-n1"})
    assert members(plan) == [(1, ["n1"])]
