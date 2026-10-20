"""Tests for gg.reply_input: the text format fed to `gg comments reply`."""

from __future__ import annotations

import pytest

from gg.reply_input import Block, ReplyInputError, format_blocks, parse_blocks

SPEC_EXAMPLE = """\
=== r/19954 #37441
Fewer than 3 traffic classes is legal, but then the 3-TC
configurations below are skipped; the warning says why.
=== r/19917 #37502 fixed
Fixed in this diff: the error path now restores the old config.
=== r/19920 #37510 dropped
Agreed with ark-slav: out of scope here, tracked in BUG-1234.
=== r/19954 review 36005
Thanks!
"""

SPEC_BLOCKS = [
    Block("19954", "#37441", None,
          "Fewer than 3 traffic classes is legal, but then the 3-TC\n"
          "configurations below are skipped; the warning says why."),
    Block("19917", "#37502", "fixed",
          "Fixed in this diff: the error path now restores the old config."),
    Block("19920", "#37510", "dropped",
          "Agreed with ark-slav: out of scope here, tracked in BUG-1234."),
    Block("19954", "review 36005", None, "Thanks!"),
]


def test_parses_spec_example():
    assert parse_blocks(SPEC_EXAMPLE) == SPEC_BLOCKS


def test_r_prefix_optional_and_letter_ids():
    blocks = parse_blocks("=== 19954 #g812\nhi\n=== r/19954 #a95 open\nagain\n")
    assert blocks == [
        Block("19954", "#g812", None, "hi"),
        Block("19954", "#a95", "open", "again"),
    ]


def test_text_keeps_code_and_inner_blank_lines():
    text = "=== r/1 #2\n\nSee:\n\n```c\nif (x)\n    y();\n```\n\n"
    assert parse_blocks(text)[0].text == "See:\n\n```c\nif (x)\n    y();\n```"


def test_status_only_block():
    assert parse_blocks("=== r/19917 #37502 fixed\n") == [
        Block("19917", "#37502", "fixed", "")]


def test_errors_are_collected():
    text = (
        "=== r/19954 37441\n"
        "text\n"
        "=== r/2 review 1 fixed\n"
        "text\n"
        "=== r/3 #5\n"
        "a\n"
        "=== 3 #5\n"
        "b\n"
    )
    with pytest.raises(ReplyInputError) as exc:
        parse_blocks(text)
    msg = str(exc.value)
    assert "line 1" in msg
    assert "line 3" in msg
    assert "#5" in msg


def test_empty_block_and_leading_text_rejected():
    with pytest.raises(ReplyInputError) as exc:
        parse_blocks("stray\n=== r/1 #2\n=== r/1 #3 open\n")
    msg = str(exc.value)
    assert "line 1" in msg      # text before the first header
    assert "line 2" in msg      # block with no text and no status


def test_no_blocks_rejected():
    for text in ("", "\n\n"):
        with pytest.raises(ReplyInputError):
            parse_blocks(text)


def test_round_trip():
    blocks = SPEC_BLOCKS + [Block("19917", "#g7", "open", "")]
    assert parse_blocks(format_blocks(blocks)) == blocks
