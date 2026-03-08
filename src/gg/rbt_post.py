"""Post a single commit to ReviewBoard via rbt."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from gg import bugs, git

_REVIEW_RE = re.compile(r"^Review request #(\d+) posted\.", re.MULTILINE)


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
        print(" ".join(cmd))
        return PostResult(review_id=None, output=" ".join(cmd))

    # In update mode, confirm with user (if interactive)
    if not first_post and sys.stdin.isatty():
        yn = input(f"Update {summary_text}? patch (y/n): ")
        if yn.lower() != "y":
            return PostResult(review_id=None, output="skipped")

    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    output = r.stdout + r.stderr
    print(output, end="")

    m = _REVIEW_RE.search(r.stdout)
    review_id = m.group(1) if m else None

    return PostResult(review_id=review_id, output=output)
