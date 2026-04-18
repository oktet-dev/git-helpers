"""Unit tests for rbt_post helpers and post_one command construction."""

from __future__ import annotations

from gg.rbt_post import clean_output, post_one

from tests.conftest import GitRepo, RbtMock


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


class TestPostOneUpdateWithReviewId:
    """post_one called with review_id (the rbt-sync UPDATE path)."""

    def test_passes_r_and_guess_description(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """re-post with -r ID must include --guess-description yes so that
        the commit body propagates to RB even when the diff is unchanged."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: reworded subject")
        result = post_one(
            "HEAD", "master",
            review_id="100",
            dry_run=True,
            cwd=git_repo.work_dir,
        )
        assert "-r 100" in result.output
        assert "--guess-description yes" in result.output
        # A re-post on an existing review must not claim --tracking-branch
        assert "--tracking-branch" not in result.output
