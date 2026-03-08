"""Extract bug IDs from git commit summary lines."""

from __future__ import annotations

import re


def summary_to_bugs(summary: str) -> str:
    """Extract bug IDs from a commit summary.

    Returns comma-separated bug IDs, or empty string if none found.
    Matches the behavior of gitgo_summary2bugs in bashrc.gitgo.
    """
    if not summary:
        return ""

    # Guard: generic prefix like "fixup:", "squash:", "refactor:" etc.
    # Must end with a letter or underscore before the colon.
    if re.match(r"^[A-Za-z0-9_-]+[A-Za-z_-]:", summary):
        return ""

    # JIRA-style: "PROJ-123: message"
    if re.match(r"^[A-Za-z]+-[0-9]+:", summary):
        # Only extract uppercase JIRA keys (bash grep uses [A-Z]+)
        bugs = re.findall(r"[A-Z]+-[0-9]+", summary)
        # Only take bugs that appeared as "BUG-123:" tokens (with colon)
        token_bugs = []
        for bug in bugs:
            if f"{bug}:" in summary:
                token_bugs.append(bug)
        return ", ".join(token_bugs)

    # Legacy: "Bug 42:" or "Task 99:"
    m = re.match(r"^(?:Bug|Task)\s*(\d+):", summary, re.IGNORECASE)
    if m:
        return m.group(1)

    return ""
