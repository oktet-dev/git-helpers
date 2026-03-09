"""Post a single commit to ReviewBoard via rbt."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from gg import bugs, git

_REVIEW_RE = re.compile(r"^Review request #(\d+) posted\.", re.MULTILINE)


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


def post_one(
    rev: str,
    branch: str,
    *,
    first_post: bool = True,
    publish: bool = False,
    dry_run: bool = False,
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

    cmd.append(f"--summary={full_summary}")

    if first_post:
        branch_arg = explicit_branch or branch
        cmd.append(f"--branch={branch_arg}")
        cmd.append(f"--tracking-branch={branch}")
        if bug_ids:
            cmd.append(f"--bugs-closed={bug_ids}")
    else:
        cmd.extend(["--update", "--guess-description", "yes"])

    if depends_on:
        cmd.append(f"--depends-on={depends_on}")

    cmd.append(rev)

    if dry_run:
        line = _shell_join(cmd)
        print(line)
        return PostResult(review_id=None, output=line)

    # In update mode rbt prompts "Update Review Request #NNN?" -- auto-confirm
    stdin = "yes\n" if not first_post else None
    r = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    output = r.stdout + r.stderr
    print(output, end="")

    m = _REVIEW_RE.search(r.stdout)
    review_id = m.group(1) if m else None

    return PostResult(review_id=review_id, output=output)
