import re

from gg.matcher import ActionKind, NewCommit, SyncAction
from gg.rb_links import Links
from gg.review_store import ReviewEntry
from gg.series import GoneGroup, Series, SeriesPlan
from gg.sync_plan import format_markdown, format_series_plan, format_summary


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


def test_full_links_widen_the_review_column():
    plan = SeriesPlan(
        series=[
            Series(1, [keep("1000", "a"), keep("1001", "b", pos=2)]),
            Series(2, [SyncAction(ActionKind.CREATE, None, NewCommit("r9", "c", "h"), 3)],
                   new=True, depends_on="1001"),
        ],
        gone=[], warnings=[], adopted_from=set())
    links = Links("https://rb.example.com/", full=True)
    lines = format_series_plan(plan, upstream="u", links=links).splitlines()
    url = "https://rb.example.com/r/1000/"
    header = next(line for line in lines if line.startswith("#"))
    row = next(line for line in lines if url in line)
    create = next(line for line in lines if "create" in line)
    assert header.index("Subject") == row.rindex(" a") + 1 == create.rindex(" c") + 1
    assert "Series 2: 1 patch, new, depends on https://rb.example.com/r/1001/" in lines


def test_hyperlinks_keep_the_columns():
    plan = SeriesPlan([Series(1, [keep("1000", "a")])], [], [], set())
    links = Links("https://rb.example.com/", hyperlink=True)
    out = format_series_plan(plan, upstream="u", links=links)
    assert "\x1b]8;;https://rb.example.com/r/1000/\x1b\\r/1000\x1b]8;;\x1b\\" in out
    plain = format_series_plan(plan, upstream="u")
    assert re.sub(r"\x1b]8;;[^\x1b]*\x1b\\", "", out) == plain


def test_markdown_lists_series_with_links():
    plan = SeriesPlan(
        series=[
            Series(1, [keep("1000", "a"), keep("1001", "b", pos=2)], origin="base"),
            Series(2, [SyncAction(ActionKind.CREATE, None, NewCommit("r9", "c", "h"), 3)],
                   new=True, depends_on="1001"),
        ],
        gone=[GoneGroup(1, [SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "9", "x", "h"),
                                       None, None)])],
        warnings=[], adopted_from={"base"})
    out = format_markdown(plan, links=Links("https://rb.example.com/"))
    assert out == (
        "**Series 1**: 2 patches\n"
        "\n"
        "1. [r/1000](https://rb.example.com/r/1000/) a\n"
        "2. [r/1001](https://rb.example.com/r/1001/) b\n"
        "\n"
        "**Series 2**: 1 patch, depends on [r/1001](https://rb.example.com/r/1001/)\n"
        "\n"
        "1. c (not posted)"
    )


def test_markdown_skips_what_is_outside_the_range():
    outside = keep("1000", "a", outside_range=True)
    dep = keep("1001", "b", pos=2, outside_range=True)
    dep.kind, dep.needs_dep_update = ActionKind.KEEP_DEP, True
    plan = SeriesPlan(
        [Series(1, [outside]), Series(2, [dep, keep("1002", "c", pos=3)], depends_on="1000")],
        [], [], set())
    out = format_markdown(plan, links=Links("https://rb.example.com/"))
    assert "Series 1" not in out
    assert "r/1001" not in out
    assert "1. [r/1002](https://rb.example.com/r/1002/) c" in out


def test_markdown_without_server_shows_ids():
    plan = SeriesPlan([Series(1, [keep("1000", "a")])], [], [], set())
    assert format_markdown(plan, links=Links()).endswith("1. r/1000 a")


def test_markdown_escapes_subjects():
    plan = SeriesPlan([Series(1, [keep("1000", "fix *ptr in `f` [x] <y> _z_ of tapi_tool")])],
                      [], [], set())
    out = format_markdown(plan, links=Links())
    assert out.endswith(r"r/1000 fix \*ptr in \`f\` \[x\] \<y\> \_z\_ of tapi_tool")
