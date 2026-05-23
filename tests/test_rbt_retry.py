"""Unit tests for gg.rbt_retry -- the rbt subprocess retry helper."""

from __future__ import annotations

import io
import subprocess

from gg.rbt_retry import (
    RetryClass,
    classify,
    rate_limit_schedule,
    missing_base_schedule,
    sleep_with_status,
    _fmt_mmss,
    run_with_retry,
)


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


class TestFmtMmss:
    def test_zero(self) -> None:
        assert _fmt_mmss(0) == "0m00s"

    def test_under_minute(self) -> None:
        assert _fmt_mmss(7) == "0m07s"

    def test_over_minute(self) -> None:
        assert _fmt_mmss(125) == "2m05s"


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["rbt", "post"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class _ScriptedRunner:
    """Returns a queued CompletedProcess on each call; records calls."""

    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(
        self, cmd: list[str], *, cwd=None, capture_output: bool = True,
        text: bool = True, input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        return self._results.pop(0)


class TestRunWithRetry:
    def test_ok_returns_without_sleep(self, monkeypatch) -> None:
        monkeypatch.delenv("GG_RBT_RATE_LIMIT_RETRIES", raising=False)
        runner = _ScriptedRunner([_proc(0, stdout="Review request #1 posted.\n")])
        sleep = _FakeSleep()
        r = run_with_retry(["rbt", "post"], runner=runner, sleep=sleep)
        assert r.returncode == 0
        assert sleep.calls == []
        assert len(runner.calls) == 1

    def test_fatal_returns_without_sleep(self, monkeypatch) -> None:
        runner = _ScriptedRunner([_proc(1, stderr="Authentication failed\n")])
        sleep = _FakeSleep()
        r = run_with_retry(["rbt", "post"], runner=runner, sleep=sleep)
        assert r.returncode == 1
        assert sleep.calls == []
        assert len(runner.calls) == 1

    def test_rate_limit_uses_schedule(self, monkeypatch) -> None:
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_RETRIES", "3")
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", "10")
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_FACTOR", "3")
        # 4 attempts, all rate-limit. With non-TTY status_stream each
        # retry sleeps exactly once with the scheduled delay.
        rl = "API Code: code: 114\n"
        runner = _ScriptedRunner([_proc(1, stderr=rl) for _ in range(4)])
        sleep = _FakeSleep()
        status = io.StringIO()  # isatty() == False
        r = run_with_retry(
            ["rbt", "post"], runner=runner, sleep=sleep, status_stream=status,
        )
        assert r.returncode == 1
        assert len(runner.calls) == 4
        assert sleep.calls == [10, 30, 90]

    def test_recovery_on_third_attempt(self, monkeypatch) -> None:
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", "0")
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_FACTOR", "1")
        rl = "API Code: code: 114\n"
        runner = _ScriptedRunner([
            _proc(1, stderr=rl),
            _proc(1, stderr=rl),
            _proc(0, stdout="Review request #1 posted.\n"),
        ])
        sleep = _FakeSleep()
        status = io.StringIO()
        r = run_with_retry(
            ["rbt", "post"], runner=runner, sleep=sleep, status_stream=status,
        )
        assert r.returncode == 0
        assert len(runner.calls) == 3

    def test_class_flip_keeps_original_schedule(self, monkeypatch) -> None:
        """First transient is 114; later attempts return 207. The
        helper must stay on the rate-limit schedule and not refresh
        the budget."""
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_RETRIES", "2")
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_INITIAL_DELAY", "0")
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_FACTOR", "1")
        monkeypatch.setenv("GG_RBT_MISSING_BASE_RETRIES", "5")  # would be ignored
        monkeypatch.setenv("GG_RBT_MISSING_BASE_DELAY", "0")
        rl = "API Code: code: 114\n"
        mb = "API Code: code: 207\n"
        # Initial 114, then 207, 207 -- helper picks rate-limit schedule
        # of length 2, so 3 calls total (1 + 2 retries) then gives up.
        runner = _ScriptedRunner([
            _proc(1, stderr=rl),
            _proc(1, stderr=mb),
            _proc(1, stderr=mb),
        ])
        sleep = _FakeSleep()
        status = io.StringIO()
        r = run_with_retry(
            ["rbt", "post"], runner=runner, sleep=sleep, status_stream=status,
        )
        assert r.returncode == 1
        assert len(runner.calls) == 3

    def test_zero_retries_returns_first_failure(self, monkeypatch) -> None:
        monkeypatch.setenv("GG_RBT_RATE_LIMIT_RETRIES", "0")
        runner = _ScriptedRunner([_proc(1, stderr="API Code: code: 114\n")])
        sleep = _FakeSleep()
        status = io.StringIO()
        r = run_with_retry(
            ["rbt", "post"], runner=runner, sleep=sleep, status_stream=status,
        )
        assert r.returncode == 1
        assert len(runner.calls) == 1


class TestRbtMockFailureQueue:
    def test_queued_failure_then_success(
        self, git_repo, rbt_mock,
    ) -> None:
        rbt_mock.queue_failure(
            output="Error Message: The file was not found in the repository.\n"
                   "API Code: code: 207\n",
            returncode=1,
            count=1,
        )
        # First direct call to the mock should fail.
        r1 = subprocess.run(
            [str(rbt_mock.script_dir / "rbt"), "post"],
            capture_output=True, text=True,
        )
        assert r1.returncode == 1
        assert "code: 207" in (r1.stdout + r1.stderr)

        # Second direct call should succeed normally.
        r2 = subprocess.run(
            [str(rbt_mock.script_dir / "rbt"), "post"],
            capture_output=True, text=True,
        )
        assert r2.returncode == 0
        assert "Review request" in r2.stdout
