from gg.matcher import ActionKind, NewCommit, SyncAction
from gg.review_store import ReviewEntry
from gg.series import GoneGroup, Series, SeriesPlan
from gg.sync_plan import format_series_plan, format_summary


def keep(rid, subject, pos=1, **kw):
    return SyncAction(ActionKind.KEEP, ReviewEntry("s", pos, rid, subject, "h"),
                      NewCommit("r" + rid, subject, "h"), pos, **kw)


def test_series_headers():
    plan = SeriesPlan(
        series=[
            Series(1, [keep("1000", "a")], origin="base"),
            Series(2, [SyncAction(ActionKind.CREATE, None, NewCommit("r9", "c", "h"), 2)],
                   new=True, depends_on="1000"),
        ],
        gone=[], warnings=[], adopted_from={"base"})
    out = format_series_plan(plan, upstream="oktet/master")
    assert "Series 1: 1 patch, from 'base'" in out
    assert "Series 2: 1 patch, new, depends on r/1000" in out


def test_gone_group_before_live_and_was():
    sub = SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "1", "x", "h"), None, None)
    plan = SeriesPlan(series=[Series(1, [keep("2", "y")], was=2)],
                      gone=[GoneGroup(1, [sub])], warnings=[], adopted_from=set())
    out = format_series_plan(plan, upstream="oktet/master")
    assert out.index("Series 1: 1 patch, landed in oktet/master") < out.index(
        "Series 1: 1 patch (was series 2)")


def test_outside_range_rows():
    hidden = keep("1", "a", outside_range=True)
    dep = keep("2", "b", pos=2, outside_range=True)
    dep.kind, dep.needs_dep_update = ActionKind.KEEP_DEP, True
    out = format_series_plan(SeriesPlan([Series(1, [hidden, dep])], [], [], set()), upstream="u")
    assert "Series 1: 2 patches, outside range" in out
    assert "r/1 " not in out
    assert out.rstrip().endswith("b  (outside range)")


def test_summary_counts():
    sub = SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "1", "x", "h"), None, None)
    fg = SyncAction(ActionKind.FORGET, ReviewEntry("s", 2, "2", "y", "h"), None, None)
    plan = SeriesPlan([Series(1, [keep("3", "z")])], [GoneGroup(1, [sub, fg])], [], set())
    assert format_summary(plan) == "Synced: 1 kept, 1 submitted, 1 forgotten"
