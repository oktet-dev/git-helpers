"""Unit tests for rbt_post.clean_output."""

from __future__ import annotations

from gg.rbt_post import clean_output


def test_passthrough_for_normal_output() -> None:
    text = (
        "Review request #18557 posted.\n"
        "https://example/r/18557/\n"
        "https://example/r/18557/diff/\n"
    )
    assert clean_output(text) == text


def test_collapses_cr_delimited_progress_within_a_line() -> None:
    line = "Validating commits...                           [0/1]\rValidating commits... \u2588\u2588 [1/1]\n"
    out = clean_output(line)
    assert out == "Validating commits... \u2588\u2588 [1/1]\n"


def test_dedupes_consecutive_newline_separated_progress() -> None:
    # rbtools under non-TTY stdout: one frame per newline-terminated line
    text = (
        "Validating commits...                                      [0/1]\n"
        "Validating commits... \u2588\u2588\u2588\u2588 [1/1]\n"
        "Validating commits... \u2588\u2588\u2588\u2588 [1/1]\n"
    )
    assert clean_output(text) == "Validating commits... \u2588\u2588\u2588\u2588 [1/1]\n"


def test_keeps_distinct_progress_labels() -> None:
    text = (
        "Validating commits...                                      [0/1]\n"
        "Validating commits... \u2588\u2588\u2588\u2588 [1/1]\n"
        "Uploading commits...                                       [0/1]\n"
        "Uploading commits... \u2588\u2588\u2588\u2588 [1/1]\n"
    )
    out = clean_output(text)
    assert out == (
        "Validating commits... \u2588\u2588\u2588\u2588 [1/1]\n"
        "Uploading commits... \u2588\u2588\u2588\u2588 [1/1]\n"
    )


def test_preserves_error_lines_around_progress() -> None:
    text = (
        "Validating commits...                                      [0/1]\n"
        "Validating commits...                                      [0/1]\n"
        "ERROR: Error validating diff\n"
        "\n"
        "some/path.c: The file was not found in the repository.\n"
    )
    out = clean_output(text)
    assert out == (
        "Validating commits...                                      [0/1]\n"
        "ERROR: Error validating diff\n"
        "\n"
        "some/path.c: The file was not found in the repository.\n"
    )


def test_trailing_newline_preserved_or_omitted() -> None:
    assert clean_output("hello\n") == "hello\n"
    assert clean_output("hello") == "hello"


def test_empty_string() -> None:
    assert clean_output("") == ""
