"""Post a single commit to ReviewBoard via rbt."""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from gg import bugs, git

_REVIEW_RE = re.compile(r"^Review request #(\d+) posted\.", re.MULTILINE)
# Matches a tqdm-style progress line: "<label><bar chars> [i/N]".
# Bar chars include block glyphs and spaces.
_PROGRESS_RE = re.compile(r"^(.*?)\s*[\u2580-\u259F ]*\s*\[\d+/\d+\]\s*$")


def clean_output(text: str) -> str:
    """Strip rbt progress-bar noise from captured output.

    rbtools uses a tqdm-style progress bar. On a TTY it rewrites the line
    with \\r; under ``subprocess.run(capture_output=True)`` stdout is not a
    TTY and each frame lands on its own newline-terminated line, producing
    duplicated bars in captured output. Collapse both cases:

    - within a line, keep only the last \\r-delimited frame
    - across lines, deduplicate consecutive progress frames for the same label
    """
    lines: list[str] = []
    prev_label: str | None = None
    for raw in text.splitlines():
        line = raw.split("\r")[-1]
        m = _PROGRESS_RE.match(line)
        if m:
            label = m.group(1).rstrip()
            if label and label == prev_label:
                lines[-1] = line
            else:
                lines.append(line)
                prev_label = label
        else:
            lines.append(line)
            prev_label = None
    out = "\n".join(lines)
    if text.endswith("\n"):
        out += "\n"
    return out


def _shell_quote_arg(arg: str) -> str:
    """Quote an argument, keeping --key= prefix unquoted for readability."""
    if "=" in arg and arg.startswith("--"):
        key, _, value = arg.partition("=")
        return f"{key}={shlex.quote(value)}"
    return shlex.quote(arg)


def _shell_join(cmd: list[str]) -> str:
    return " ".join(_shell_quote_arg(a) for a in cmd)


@dataclass
class PostResult:
    """Result of posting a single review request."""

    review_id: str | None
    output: str
    returncode: int = 0


def post_one(
    rev: str,
    branch: str,
    *,
    review_id: str | None = None,
    first_post: bool = True,
    publish: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    reviewers: list[str] | None = None,
    groups: list[str] | None = None,
    explicit_branch: str | None = None,
    num_string: str = "",
    depends_on: str | None = None,
    cwd: Path | None = None,
) -> PostResult:
    """Post one commit to ReviewBoard.

    Returns PostResult with the review ID parsed from rbt output (if any).
    """
    summary_text = git.summary(rev, cwd=cwd)
    bug_ids = bugs.summary_to_bugs(summary_text)

    full_summary = f"{num_string}{summary_text}"

    cmd: list[str] = ["rbt", "post"]

    if publish:
        cmd.append("-p")

    if reviewers:
        for user in reviewers:
            cmd.extend(["--target-people", user])
    if groups:
        for group in groups:
            cmd.extend(["--target-groups", group])

    if review_id:
        cmd.extend(["-r", review_id])

    cmd.append(f"--summary={full_summary}")

    if first_post and not review_id:
        branch_arg = explicit_branch or branch
        cmd.append(f"--branch={branch_arg}")
        cmd.append(f"--tracking-branch={branch}")
        if bug_ids:
            cmd.append(f"--bugs-closed={bug_ids}")
    elif not review_id:
        cmd.extend(["--update", "--guess-description", "yes"])

    if depends_on:
        cmd.append(f"--depends-on={depends_on}")

    cmd.append(rev)

    if dry_run:
        line = _shell_join(cmd)
        print(line)
        return PostResult(review_id=None, output=line)

    # In update mode rbt prompts "Update Review Request #NNN?" -- auto-confirm
    stdin = "yes\n" if (not first_post or review_id) else None
    r = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    cleaned = clean_output(r.stdout + r.stderr)

    if r.returncode != 0:
        sys.stderr.write(f"\n[gg] rbt post failed (exit {r.returncode})\n")
        sys.stderr.write(f"[gg] command: {_shell_join(cmd)}\n")
        sys.stderr.write(cleaned)
        if not cleaned.endswith("\n"):
            sys.stderr.write("\n")
    elif verbose:
        sys.stdout.write(cleaned)
        if not cleaned.endswith("\n"):
            sys.stdout.write("\n")

    m = _REVIEW_RE.search(r.stdout)
    review_id = m.group(1) if m else None

    return PostResult(review_id=review_id, output=cleaned, returncode=r.returncode)
