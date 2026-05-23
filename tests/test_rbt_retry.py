"""Unit tests for gg.rbt_retry -- the rbt subprocess retry helper."""

from __future__ import annotations

from gg.rbt_retry import RetryClass, classify


class TestClassify:
    def test_ok_on_zero_exit(self) -> None:
        assert classify(0, "Review request #1234 posted.\n") is RetryClass.OK

    def test_missing_base_by_code(self) -> None:
        out = (
            "Error Message: The file was not found in the repository.\n"
            "API Code: code: 207\n"
        )
        assert classify(1, out) is RetryClass.MISSING_BASE

    def test_missing_base_by_phrase(self) -> None:
        out = "rbt: The file was not found in the repository\n"
        assert classify(1, out) is RetryClass.MISSING_BASE

    def test_rate_limit_by_code(self) -> None:
        out = "API Code: code: 114\nError Message: Throttled\n"
        assert classify(1, out) is RetryClass.RATE_LIMIT

    def test_rate_limit_by_phrase(self) -> None:
        out = "Server returned: rate limit exceeded\n"
        assert classify(1, out) is RetryClass.RATE_LIMIT

    def test_rate_limit_hyphen_phrase(self) -> None:
        assert classify(1, "rate-limit hit\n") is RetryClass.RATE_LIMIT

    def test_fatal_on_unknown_failure(self) -> None:
        assert classify(1, "Authentication failed\n") is RetryClass.FATAL

    def test_fatal_on_empty_output(self) -> None:
        assert classify(2, "") is RetryClass.FATAL
