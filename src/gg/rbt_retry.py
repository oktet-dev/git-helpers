"""Retry wrapper for transient ReviewBoard errors from `rbt`.

Classifies rbt failures from captured output, applies a backoff
schedule with a TTY countdown, and gives up after a configurable
number of attempts. See docs/superpowers/specs/2026-05-22-rbt-retry-design.md.
"""

from __future__ import annotations

import os
import re
import sys
import time
from enum import Enum
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


def classify(returncode: int, output: str) -> RetryClass:
    """Classify an rbt invocation by its exit code and captured output."""
    if returncode == 0:
        return RetryClass.OK
    if _RATE_LIMIT_RE.search(output):
        return RetryClass.RATE_LIMIT
    if _MISSING_BASE_RE.search(output):
        return RetryClass.MISSING_BASE
    return RetryClass.FATAL


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
