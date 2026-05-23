"""Unit tests for gg.rbt_retry -- the rbt subprocess retry helper."""

from __future__ import annotations

from gg.rbt_retry import RetryClass, classify, rate_limit_schedule, missing_base_schedule


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


class TestSchedules:
    def test_rate_limit_defaults(self, monkeypatch) -> None:
        monkeypatch.delenv("GG_RBT_RATE_LIMIT_RETRIES", raising=False)
        monkeypatch.delenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", raising=False)
        monkeypatch.delenv("GG_RBT_RATE_LIMIT_FACTOR", raising=False)
        assert rate_limit_schedule() == [10, 30, 90]

    def test_rate_limit_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_RETRIES", "2")
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", "5")
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_FACTOR", "2")
        assert rate_limit_schedule() == [5, 10]

    def test_rate_limit_zero_retries(self, monkeypatch) -> None:
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_RETRIES", "0")
        assert rate_limit_schedule() == []

    def test_missing_base_defaults(self, monkeypatch) -> None:
        monkeypatch.delenv("GG_RBT_MISSING_BASE_RETRIES", raising=False)
        monkeypatch.delenv("GG_RBT_MISSING_BASE_DELAY", raising=False)
        assert missing_base_schedule() == [300, 300, 300]

    def test_missing_base_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("GG_RBT_MISSING_BASE_RETRIES", "2")
        monkeypatch.setenv("GG_RBT_MISSING_BASE_DELAY", "60")
        assert missing_base_schedule() == [60, 60]

    def test_missing_base_zero_retries(self, monkeypatch) -> None:
        monkeypatch.setenv("GG_RBT_MISSING_BASE_RETRIES", "0")
        assert missing_base_schedule() == []


import io
from gg.rbt_retry import sleep_with_status


class _FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class TestSleepWithStatus:
    def test_non_tty_single_line(self) -> None:
        sleep = _FakeSleep()
        err = io.StringIO()  # isatty() is False by default on StringIO
        sleep_with_status(
            seconds=30,
            reason="rate-limited",
            attempt=2,
            total=4,
            stream=err,
            sleep=sleep,
        )
        out = err.getvalue()
        assert out == "[gg] rate-limited; sleeping 30s before retry 2/4\n"
        assert sleep.calls == [30]

    def test_tty_countdown_writes_carriage_return(self) -> None:
        sleep = _FakeSleep()

        class TtyBuf(io.StringIO):
            def isatty(self) -> bool:
                return True

        err = TtyBuf()

        # Mock the now() function to advance time with each sleep call
        current_time = [0.0]

        def fake_now() -> float:
            return current_time[0]

        def advancing_sleep(seconds: float) -> None:
            sleep(seconds)
            current_time[0] += seconds

        sleep_with_status(
            seconds=3,
            reason="rate-limited",
            attempt=2,
            total=4,
            stream=err,
            sleep=advancing_sleep,
            now=fake_now,
        )
        out = err.getvalue()
        # Three 1-second sleeps for the countdown
        assert sleep.calls == [1, 1, 1]
        # Countdown frames use \r
        assert "\r" in out
        # Final "now" line ends the sequence
        assert "retrying 2/4 now" in out
        # Numbers visible in countdown
        assert "0m03s" in out or "0m3s" in out
        assert "rate-limited" in out

    def test_zero_seconds_is_a_noop(self) -> None:
        sleep = _FakeSleep()
        err = io.StringIO()
        sleep_with_status(
            seconds=0,
            reason="rate-limited",
            attempt=2,
            total=4,
            stream=err,
            sleep=sleep,
        )
        # No sleep, no output
        assert sleep.calls == []
        assert err.getvalue() == ""
