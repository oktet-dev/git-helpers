"""Reply input format for `gg comments reply`.

The input is a list of blocks, each starting with a header line
(`=== r/<request> <target>[ <status word>]`) followed by the reply text,
which runs up to the next header. This module only parses and formats that
text; posting the replies is done elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATUS_WORDS = {"fixed": "resolved", "dropped": "dropped", "open": "open"}

_HEADER_PREFIX = "=== "
_HEADER_RE = re.compile(
    r"=== (?:r/)?(?P<request>\d+) "
    r"(?P<target>#[ga]?\d+|review \d+)"
    r"(?: (?P<status>" + "|".join(STATUS_WORDS) + r"))?"
)


@dataclass(frozen=True)
class Block:
    """One reply: where it goes, an optional status change, and its text.

    request is the review request id ("19954"); target is "#37441" (diff
    comment), "#g812" (general comment), "#a95" (file attachment comment)
    or "review 36005"; status is the word as written, or None.
    """

    request: str
    target: str
    status: str | None
    text: str


class ReplyInputError(Exception):
    """The reply input is malformed; the message lists every problem."""


def parse_blocks(text: str) -> list[Block]:
    """Parse reply input into blocks, or raise ReplyInputError.

    Leading and trailing blank lines of each block's text are trimmed;
    everything else is kept as is. All problems are collected and reported
    together, each with its line number.
    """
    errors: list[str] = []
    # (header line number, request, target, status, text lines)
    raw: list[tuple[int, str, str, str | None, list[str]]] = []
    current: list[str] | None = None
    seen_header = False

    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.startswith(_HEADER_PREFIX):
            seen_header = True
            m = _HEADER_RE.fullmatch(line.rstrip())
            if m is None:
                errors.append(f"line {lineno}: bad header: {line!r}")
                current = None
                continue
            target = m["target"]
            status = m["status"]
            if target.startswith("review") and status is not None:
                errors.append(
                    f"line {lineno}: a status word is not allowed on a "
                    f"review header: {line!r}")
            current = []
            raw.append((lineno, m["request"], target, status, current))
        elif current is not None:
            current.append(line)
        elif line.strip() and not seen_header:
            errors.append(f"line {lineno}: text before the first header")
            seen_header = True  # report it once

    blocks: list[Block] = []
    seen: dict[tuple[str, str], int] = {}
    for lineno, request, target, status, lines in raw:
        body = "\n".join(_trim_blank_lines(lines))
        if not body and status is None:
            errors.append(
                f"line {lineno}: block for r/{request} {target} has no "
                f"text and no status word")
        key = (request, target)
        if key in seen:
            errors.append(
                f"line {lineno}: r/{request} {target} already has a block "
                f"(line {seen[key]})")
        else:
            seen[key] = lineno
        blocks.append(Block(request, target, status, body))

    if not seen_header and not errors:
        errors.append("no blocks in the input")
    if errors:
        errors.sort(key=_error_line)
        raise ReplyInputError("\n".join(errors))
    return blocks


def _trim_blank_lines(lines: list[str]) -> list[str]:
    """Drop blank lines from both ends of lines."""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _error_line(message: str) -> int:
    m = re.match(r"line (\d+):", message)
    return int(m.group(1)) if m else 0


def format_blocks(blocks: list[Block]) -> str:
    """Format blocks as reply input; parse_blocks(format_blocks(b)) == b."""
    out: list[str] = []
    for b in blocks:
        header = f"=== r/{b.request} {b.target}"
        if b.status:
            header += f" {b.status}"
        out.append(header)
        if b.text:
            out.append(b.text)
    return "\n".join(out) + "\n"
