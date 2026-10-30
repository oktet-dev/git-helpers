"""Retry wrapper for transient ReviewBoard errors from `rbt`.

Classifies rbt failures from captured output, applies a backoff
schedule with a TTY countdown, and gives up after a configurable
number of attempts. See docs/superpowers/specs/2026-05-22-rbt-retry-design.md.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Callable, TextIO


class RetryClass(Enum):
    OK = "ok"
    RATE_LIMIT = "rate_limit"
    MISSING_BASE = "missing_base"
    FATAL = "fatal"


_RATE_LIMIT_RE = re.compile(
    r"API Code:\s*code:\s*114\b|rate[- ]?limit",
    re.IGNORECASE,
)
_MISSING_BASE_RE = re.compile(
    r"API Code:\s*code:\s*207\b|not found in the repository",
    re.IGNORECASE,
)
# rbt post prefixes a 207 with the file ReviewBoard could not fetch:
#   "<path> (revision <blob sha>): The file was not found in the repository."
_MISSING_FILE_RE = re.compile(
    r"^(?P<path>\S.*?) \(revision (?P<rev>[0-9a-fA-F]+)\): .*not found in the repository",
    re.IGNORECASE | re.MULTILINE,
)


def classify(returncode: int, output: str) -> RetryClass:
    """Classify an rbt invocation by its exit code and captured output."""
    if returncode == 0:
        return RetryClass.OK
    if _RATE_LIMIT_RE.search(output):
        return RetryClass.RATE_LIMIT
    if _MISSING_BASE_RE.search(output):
        return RetryClass.MISSING_BASE
    return RetryClass.FATAL


def missing_base_detail(output: str) -> tuple[str, str] | None:
    """Return (path, blob revision) ReviewBoard could not find, from rbt's output."""
    m = _MISSING_FILE_RE.search(output)
    if not m:
        return None
    return m.group("path"), m.group("rev")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def rate_limit_schedule() -> list[int]:
    """Delays (seconds) between rate-limit retries, derived from env."""
    retries = _int_env("GG_RBT_RATE_LIMIT_RETRIES", 3)
    initial = _int_env("GG_RBT_RATE_LIMIT_INITIAL_DELAY", 10)
    factor = _int_env("GG_RBT_RATE_LIMIT_FACTOR", 3)
    return [int(initial * factor ** i) for i in range(retries)]


def missing_base_schedule() -> list[int]:
    """Delays (seconds) between missing-base retries, derived from env."""
    retries = _int_env("GG_RBT_MISSING_BASE_RETRIES", 3)
    delay = _int_env("GG_RBT_MISSING_BASE_DELAY", 300)
    return [delay] * retries


def _fmt_mmss(seconds: int) -> str:
    m, s = divmod(max(seconds, 0), 60)
    return f"{m}m{s:02d}s"


def sleep_with_status(
    seconds: int,
    *,
    reason: str,
    attempt: int,
    total: int,
    stream: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Sleep ``seconds`` with a status line on ``stream`` (default stderr).

    On a TTY, a single line is updated in place with `\\r` every second
    showing the remaining countdown; the TTY check is performed against the
    resolved stream (``stream`` if given, else ``sys.stderr``). Off-TTY, one
    line is printed and followed by a single sleep call.
    """
    if seconds <= 0:
        return

    out = stream if stream is not None else sys.stderr

    if not out.isatty():
        out.write(f"[gg] {reason}; sleeping {seconds}s before retry {attempt}/{total}\n")
        out.flush()
        sleep(seconds)
        return

    deadline = now() + seconds
    last_len = 0
    while True:
        remaining = int(round(deadline - now()))
        if remaining <= 0:
            break
        line = (
            f"\r[gg] {reason}; retrying {attempt}/{total} "
            f"in {_fmt_mmss(remaining)} ..."
        )
        out.write(line)
        out.flush()
        last_len = len(line)
        sleep(min(1, remaining))
    # Replace the countdown line with the "now" frame, then newline
    final = f"\r[gg] {reason}; retrying {attempt}/{total} now"
    pad = " " * max(0, last_len - len(final))
    out.write(final + pad + "\n")
    out.flush()


_REASON = {
    RetryClass.RATE_LIMIT: "rate-limited",
    RetryClass.MISSING_BASE: "base commit not yet in the repository ReviewBoard reads",
}


def _reason(cls: RetryClass, output: str) -> str:
    """The status-line reason for a transient failure, with rbt's detail."""
    if cls is RetryClass.MISSING_BASE:
        detail = missing_base_detail(output)
        if detail:
            path, rev = detail
            return (
                f"ReviewBoard has no {path} at revision {rev[:8]} "
                "(is the branch pushed to the repository it reads?)"
            )
    return _REASON[cls]


def _pick_schedule(cls: RetryClass) -> list[int]:
    if cls is RetryClass.RATE_LIMIT:
        return rate_limit_schedule()
    if cls is RetryClass.MISSING_BASE:
        return missing_base_schedule()
    return []


def run_with_retry(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    input: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    status_stream: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd``, retrying on classified transient ReviewBoard errors.

    Schedule is locked at the first transient hit -- a class flip
    mid-loop keeps the original budget. Returns the last
    ``CompletedProcess`` (success, exhausted retries, or FATAL).
    """
    schedule: list[int] | None = None
    total_attempts = 1

    while True:
        r = runner(
            cmd, cwd=cwd, input=input, capture_output=True, text=True,
        )
        output = (r.stdout or "") + (r.stderr or "")
        cls = classify(r.returncode, output)
        if cls in (RetryClass.OK, RetryClass.FATAL):
            return r

        if schedule is None:
            schedule = _pick_schedule(cls)
            total_attempts = 1 + len(schedule)

        if not schedule:
            return r

        delay = schedule.pop(0)
        attempt = total_attempts - len(schedule)
        sleep_with_status(
            delay,
            reason=_reason(cls, output),
            attempt=attempt,
            total=total_attempts,
            stream=status_stream,
            sleep=sleep,
        )
