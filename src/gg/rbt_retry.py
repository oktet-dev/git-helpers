"""Retry wrapper for transient ReviewBoard errors from `rbt`.

Classifies rbt failures from captured output, applies a backoff
schedule with a TTY countdown, and gives up after a configurable
number of attempts. See docs/superpowers/specs/2026-05-22-rbt-retry-design.md.
"""

from __future__ import annotations

import os
import re
from enum import Enum


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
