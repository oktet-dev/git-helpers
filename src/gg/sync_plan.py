"""Format sync plan as a table for display."""

from __future__ import annotations

import shutil

from gg.matcher import ActionKind, SyncAction
from gg.numbering import assign_numbers


def _will_post(action: SyncAction) -> bool:
    """True if this action will post to ReviewBoard."""
    return action.kind in (ActionKind.UPDATE, ActionKind.CREATE) or action.needs_dep_update


def _pub_label(action: SyncAction, publish: bool) -> str:
    """Pub column value for an action."""
    if not _will_post(action):
        return "--"
    return "yes" if publish else "draft"


def _format_reviewer_header(
    reviewers: list[str], groups: list[str],
) -> list[str]:
    """Format a reviewer/group header that fits the terminal width."""
    if not reviewers and not groups:
        return []
    parts = []
    if reviewers:
        parts.append(f"Reviewers: {', '.join(reviewers)}")
    if groups:
        parts.append(f"Groups: {', '.join(groups)}")
    single = "  ".join(parts)
    cols = shutil.get_terminal_size().columns
    if len(single) <= cols:
        return [single, ""]
    return parts + [""]


def format_plan(
    actions: list[SyncAction],
    *,
    renumber: bool = False,
    publish: bool = False,
    reviewers: list[str] | None = None,
    groups: list[str] | None = None,
) -> str:
    """Format sync actions as a human-readable plan table."""
    numbered = assign_numbers(actions, renumber=renumber)
    show_pub = any(_will_post(a) for a in actions)

    if show_pub:
        header = f"{'#':<10} {'Action':<12} {'Pub':<7} {'Review':<10} Subject"
    else:
        header = f"{'#':<10} {'Action':<12} {'Review':<10} Subject"
    lines = _format_reviewer_header(reviewers or [], groups or [])
    lines.append(header)
    lines.append("-" * len(header))

    for action, num_str in numbered:
        kind_label = action.kind.value
        if action.kind in (ActionKind.DISCARD, ActionKind.SKIP):
            if action.new_commit:
                # Skipped create: show commit subject
                review = "--"
                subject = action.new_commit.subject
            else:
                # Discard or skipped discard: show old entry
                review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
                subject = action.old_entry.subject if action.old_entry else ""
        else:
            review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
            subject = action.new_commit.subject if action.new_commit else ""

        if show_pub:
            pub = _pub_label(action, publish)
            lines.append(
                f"{num_str:<10} {kind_label:<12} {pub:<7} {review:<10} {subject}"
            )
        else:
            lines.append(f"{num_str:<10} {kind_label:<12} {review:<10} {subject}")

    return "\n".join(lines)
