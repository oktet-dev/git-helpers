"""Tests for comments_format.py: the text output of `gg comments`."""

from __future__ import annotations

from gg.comments_format import Place, RequestView, format_totals, format_view
from gg.discussions import DONE, NEEDS_YOU, WAITING, ExcerptLine, build_discussions
from gg.rb_links import Links
from gg.rb_model import Comment, RequestInfo, Review

T1 = "2026-10-20T10:00:00Z"
INFO = RequestInfo("19954", "[12/14]: dcb/dcb_ets: test valid ETS configurations via dcb", 1)
PLACE = Place(3, 12, "1a2b3c4d")


def diff_comment(**kw) -> Comment:
    args = dict(kind="diff", id=37441, author="ark-slav", timestamp=T1,
                text="Why does it matter?", issue_opened=True, issue_status="open",
                severity="info", path="net-drv-ts/dcb/dcb_ets.c", filediff_id=5,
                diff_revision=1, first_row=141, num_rows=3)
    args.update(kw)
    return Comment(**args)


def review(*comments: Comment, body_top="", ship_it=False) -> Review:
    return Review(36005, "ark-slav", T1, True, ship_it, body_top, list(comments), [])


def view(*comments: Comment, code=None, place=PLACE, ship=("ark-slav",), body_top="",
         info=INFO) -> RequestView:
    ds = build_discussions([review(*comments, body_top=body_top)], user="kostik")
    return RequestView(info, place, list(ship), ds, code or {})


def fmt(v: RequestView, show_all=False) -> str:
    return format_view(v, links=Links(), show_all=show_all)


def excerpt(rows):
    return [ExcerptLine(m, n, t) for m, n, t in rows]


def test_spec_example_layout():
    code = {"#37441": excerpt([
        (False, 139, '    TEST_VERDICT("Failed to retrieve DCB configuration: %r", rc);'),
        (False, 140, ""),
        (True, 141, "    if (current_ets.ets_cap < 3)"),
        (True, 142, '        WARN_VERDICT("IUT supports fewer than 3 Traffic Classes: %u",'),
        (True, 143, "                     current_ets.ets_cap);"),
        (False, 144, ""),
        (False, 145, "    if (current_ets.willing)"),
    ])}
    assert fmt(view(diff_comment(), code=code)) == (
        "r/19954  [12/14]: dcb/dcb_ets: test valid ETS configurations via dcb\n"
        "         series 3, patch 12, commit 1a2b3c4d   Ship It: ark-slav\n"
        "  #37441  ark-slav  issue open (info)  needs you  diff 1 of 1\n"
        "    net-drv-ts/dcb/dcb_ets.c:141-143\n"
        '        139      TEST_VERDICT("Failed to retrieve DCB configuration: %r", rc);\n'
        "        140\n"
        "      > 141      if (current_ets.ets_cap < 3)\n"
        '      > 142          WARN_VERDICT("IUT supports fewer than 3 Traffic Classes: %u",\n'
        "      > 143                       current_ets.ets_cap);\n"
        "        144\n"
        "        145      if (current_ets.willing)\n"
        "    ark-slav, 2026-10-20: Why does it matter?"
    )


def test_removed_lines_show_old_numbers():
    code = {"#37441": [
        ExcerptLine(False, 58, "a"),
        ExcerptLine(False, 59, "b"),
        ExcerptLine(True, 60, "gone one", old=True),
        ExcerptLine(True, 61, "gone two", old=True),
        ExcerptLine(False, 60, "c"),
    ]}
    lines = fmt(view(diff_comment(), code=code)).splitlines()
    assert lines[3:9] == [
        "    net-drv-ts/dcb/dcb_ets.c:60-61 (removed lines; old line numbers)",
        "        58   a",
        "        59   b",
        "      > 60-  gone one",
        "      > 61-  gone two",
        "        60   c",
    ]


def test_partly_removed_lines_say_so():
    code = {"#37441": [
        ExcerptLine(True, 60, "gone", old=True),
        ExcerptLine(True, 141, "kept"),
    ]}
    lines = fmt(view(diff_comment(), code=code)).splitlines()
    assert lines[3:6] == [
        "    net-drv-ts/dcb/dcb_ets.c:141 (with removed lines; old line numbers end in -)",
        "      >  60-  gone",
        "      > 141   kept",
    ]


def test_comment_without_issue_and_general_comment():
    c = diff_comment(id=812, kind="general", issue_opened=False, issue_status="",
                     severity=None, path=None, diff_revision=None, text="Nice.")
    text = fmt(view(c, place=None, ship=()))
    assert text == (
        "r/19954  [12/14]: dcb/dcb_ets: test valid ETS configurations via dcb\n"
        "  #g812  ark-slav  comment  needs you\n"
        "    ark-slav, 2026-10-20: Nice."
    )


def test_attachment_comment():
    c = diff_comment(id=95, kind="attachment", attachment="log.txt", path=None,
                     diff_revision=None)
    assert "    attachment: log.txt\n" in fmt(view(c)) + "\n"


def test_review_top_text_line():
    ds = build_discussions([review(body_top="Please rework.")], user="kostik")
    v = RequestView(INFO, None, [], ds, {})
    assert fmt(v).splitlines()[1:] == [
        "  review 36005  ark-slav  needs you",
        "    ark-slav, 2026-10-20: Please rework.",
    ]


def test_code_not_available():
    text = fmt(view(diff_comment(), code={"#37441": None}))
    assert "    net-drv-ts/dcb/dcb_ets.c (rows 141-143): code not available\n" in text + "\n"


def test_interdiff_and_unknown_latest():
    c = diff_comment(interfilediff_id=6, interdiff_revision=2, diff_revision=1)
    head = fmt(view(c, code={"#37441": None}, info=RequestInfo("19954", "s", 3))).splitlines()[2]
    assert head.endswith("needs you  interdiff 1-2 of 3")
    head = fmt(view(diff_comment(), info=RequestInfo("19954", "s", None))).splitlines()[2]
    assert head.endswith("needs you  diff 1")


def test_multiline_message_indent():
    c = diff_comment(text="First\n\n  second  line\nthird", kind="general", path=None)
    lines = fmt(view(c, place=None, ship=())).splitlines()
    assert lines[2:] == [
        "    ark-slav, 2026-10-20: First",
        "",
        "        second  line",
        "      third",
    ]


def test_blank_line_between_discussions_and_no_discussions():
    v = view(diff_comment(id=1, kind="general"), diff_comment(id=2, kind="general"))
    assert "\n\n  #g2" in fmt(v)
    empty = RequestView(INFO, None, [], [], {})
    assert fmt(empty, show_all=True).splitlines()[-1] == "  no discussions"


def test_done_hidden_unless_all():
    c = diff_comment(issue_status="resolved", kind="general", path=None)
    assert "#g37441" not in fmt(view(c))
    assert "#g37441" in fmt(view(c), show_all=True)


def _views(needs, waiting, done) -> list[RequestView]:
    class D:
        def __init__(self, state):
            self.state = state
    ds = [D(NEEDS_YOU)] * needs + [D(WAITING)] * waiting + [D(DONE)] * done
    return [RequestView(INFO, None, [], ds, {})]  # type: ignore[arg-type]


def test_totals_wording():
    assert format_totals(_views(1, 0, 3), show_all=False) == \
        "1 discussion needs you, 0 wait on reviewers."
    assert format_totals(_views(2, 1, 3), show_all=False) == \
        "2 discussions need you, 1 waits on reviewers."
    assert format_totals(_views(2, 1, 3), show_all=True) == \
        "2 discussions need you, 1 waits on reviewers, 3 done."
