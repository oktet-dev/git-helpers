"""Tests for gg.series -- planning a branch as a list of series."""

from gg.matcher import NewCommit
from gg.review_store import ReviewEntry
from gg.series import build_plan


def entry(branch, pos, rid, subject, series=1, h=None):
    return ReviewEntry(branch, pos, rid, subject, h or f"h-{subject}", series=series)


def commit(subject, h=None, rev=None):
    return NewCommit(rev=rev or f"r-{subject}", subject=subject, diff_hash=h or f"h-{subject}")


def plan_of(own, commits, *, foreign=None, in_range=None, landed=(), live=(), deps=None, initial=None,
            reads=None):
    def depends_on_of(rid):
        if reads is not None:
            reads.append(rid)
        return (deps or {}).get(rid, [])

    return build_plan(
        branch="stack", own=own, foreign=foreign or {}, commits=commits,
        in_range=in_range if in_range is not None else {c.rev for c in commits},
        landed_subjects=lambda: set(landed),
        branch_exists=lambda b: b in live,
        depends_on_of=depends_on_of,
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


def test_gone_entries_classified_and_grouped():
    own = [entry("stack", 1, "1", "landed"), entry("stack", 2, "2", "shared"),
           entry("stack", 3, "3", "dropped"), entry("stack", 4, "4", "k", series=2)]
    foreign = {"other": [entry("other", 1, "2", "shared")]}
    plan = plan_of(own, [commit("k")], foreign=foreign, landed={"landed"}, live={"other"})
    [group] = plan.gone
    assert group.old_number == 1
    assert [(a.kind.value, a.old_entry.review_id) for a in group.actions] == [
        ("submit", "1"), ("forget", "2"), ("discard", "3")]
    assert group.actions[1].forget_branch == "other"
    assert plan.series[0].number == 1 and plan.series[0].was == 2


def test_forget_needs_existing_branch():
    own = [entry("stack", 1, "2", "shared"), entry("stack", 2, "4", "k")]
    foreign = {"gone-branch": [entry("gone-branch", 1, "2", "shared")]}
    plan = plan_of(own, [commit("k")], foreign=foreign, live=())
    assert plan.gone[0].actions[0].kind.value == "discard"


def test_keep_dep_after_insert():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b")]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")])
    assert action(plan, "b").kind.value == "keep+dep"


def test_adopted_series_rechained_only_if_rb_differs():
    own = [entry("stack", 1, "1001", "c")]
    foreign = {"dcb": [entry("dcb", 1, "2000", "d1"), entry("dcb", 2, "2001", "d2")]}
    commits = [commit("c"), commit("d1"), commit("d2")]
    stale = plan_of(own, commits, foreign=foreign, deps={"2000": ["999"]})
    assert action(stale, "d1").kind.value == "keep+dep"
    chained = plan_of(own, commits, foreign=foreign, deps={"2000": ["1001"]})
    assert action(chained, "d1").kind.value == "keep"
    assert chained.series[1].depends_on == "1001"


def test_series_one_first_patch_never_rechained():
    own = [entry("stack", 1, "999", "z"), entry("stack", 2, "1000", "a")]
    plan = plan_of(own, [commit("a")])
    assert action(plan, "a").kind.value == "keep"


def test_submitted_predecessor_not_rechained():
    own = [entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b", series=2),
           entry("stack", 3, "3", "c", series=2)]
    plan = plan_of(own, [commit("a"), commit("c")], landed={"b"})
    assert action(plan, "c").kind.value == "keep"


def test_insert_where_predecessor_landed_rechains():
    own = [entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b", series=2),
           entry("stack", 3, "3", "c", series=2)]
    plan = plan_of(own, [commit("a"), commit("x"), commit("c")], landed={"b"})
    assert action(plan, "c").kind.value == "keep+dep"
    assert [a.kind.value for g in plan.gone for a in g.actions] == ["submit"]


def test_reorder_rechained_even_when_old_predecessor_landed():
    own = [entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b"),
           entry("stack", 3, "3", "c"), entry("stack", 4, "4", "d")]
    # b landed, c now sits on d: c still depends on b, which is not d.
    p3 = plan_of(own, [commit("a"), commit("d"), commit("c")], landed={"b"})
    assert action(p3, "c").kind.value == "keep+dep"
    # c landed, order a, d, b: d must go onto a and b onto d (no b->d->c->b cycle).
    p9 = plan_of(own, [commit("a"), commit("d"), commit("b")], landed={"c"})
    assert action(p9, "d").kind.value == "keep+dep"
    assert action(p9, "b").kind.value == "keep+dep"


def test_landed_series_below_not_rechained():
    own = [entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b", series=2),
           entry("stack", 3, "3", "c", series=2), entry("stack", 4, "4", "d", series=3)]
    plan = plan_of(own, [commit("a"), commit("d")], landed={"b", "c"})
    assert action(plan, "d").kind.value == "keep"


def test_insert_at_adopted_boundary_rechains_own_series():
    own = [entry("stack", 1, "19992", "c1"), entry("stack", 2, "19993", "c2")]
    foreign = {"base": [entry("base", 1, "19989", "s1"), entry("base", 2, "19990", "s2")]}
    plan = plan_of(own, [commit(s) for s in ("s1", "s2", "x", "c1", "c2")], foreign=foreign)
    assert members(plan) == [(1, ["s1", "s2", "x"]), (2, ["c1", "c2"])]
    assert action(plan, "x").kind.value == "create"
    assert action(plan, "c1").kind.value == "keep+dep"
    assert plan.series[1].depends_on is None


def test_first_own_entry_above_adopted_series_asks_rb():
    own = [entry("stack", 1, "19992", "c1"), entry("stack", 2, "19993", "c2")]
    foreign = {"base": [entry("base", 1, "19989", "s1"), entry("base", 2, "19990", "s2")]}
    commits = [commit(s) for s in ("s1", "s2", "c1", "c2")]
    reads = []
    chained = plan_of(own, commits, foreign=foreign, deps={"19992": ["19990"]}, reads=reads)
    assert action(chained, "c1").kind.value == "keep"
    assert reads == ["19992"]
    stale = plan_of(own, commits, foreign=foreign, deps={"19992": ["19980"]})
    assert action(stale, "c1").kind.value == "keep+dep"


def test_first_of_merged_foreign_series_asks_rb():
    own = [entry("stack", 1, "500", "p"), entry("stack", 2, "1000", "a", series=2),
           entry("stack", 3, "1001", "b", series=2)]
    foreign = {"base": [entry("base", 1, "999", "x"), entry("base", 2, "1000", "a"),
                        entry("base", 3, "1001", "b")]}
    reads = []
    plan = plan_of(own, [commit(s) for s in ("p", "x", "a", "b")], foreign=foreign,
                   deps={"999": ["500"]}, reads=reads)
    assert members(plan) == [(1, ["p"]), (2, ["x", "a", "b"])]
    assert action(plan, "x").kind.value == "keep"
    assert reads == ["999"]


def test_series_one_depends_on_initial():
    plan = plan_of([entry("stack", 1, "1000", "a")], [commit("a"), commit("n")], initial="900")
    assert [s.depends_on for s in plan.series] == ["900", "1000"]


def test_out_of_range_keep_dep_marked():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b", series=2)]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")], in_range={"r-x"})
    b = action(plan, "b")
    assert b.kind.value == "keep+dep" and b.outside_range


def test_forget_wins_over_landed():
    own = [entry("stack", 1, "2", "[1/2]: shared"), entry("stack", 2, "3", "[2/2]: gone"),
           entry("stack", 3, "4", "k")]
    foreign = {"other": [entry("other", 1, "2", "shared")]}
    plan = plan_of(own, [commit("k")], foreign=foreign, landed={"shared", "[3/3]: gone"},
                   live={"other"})
    assert [a.kind.value for a in plan.gone[0].actions] == ["forget", "submit"]


def test_lookups_lazy_and_once():
    calls = []

    def run(own, commits, foreign=None):
        calls.clear()
        return build_plan(
            branch="stack", own=own, foreign=foreign or {}, commits=commits,
            in_range={c.rev for c in commits},
            landed_subjects=lambda: calls.append("landed") or set(),
            branch_exists=lambda b: False,
            depends_on_of=lambda rid: calls.append(rid) or [],
        )

    run([entry("stack", 1, "1", "a")], [commit("a")])
    assert calls == []
    run([entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b"),
         entry("stack", 3, "3", "c")], [commit("a")])
    assert calls == ["landed"]
    foreign = {"dcb": [entry("dcb", 1, "2000", "d1"), entry("dcb", 2, "2001", "d2")]}
    run([], [commit("d1"), commit("d2")], foreign)
    assert calls == []  # first patch of series 1: its dependency is left alone
    run([entry("stack", 1, "1", "a")], [commit("a"), commit("d1"), commit("d2")], foreign)
    assert calls == ["2000"]
