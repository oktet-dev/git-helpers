"""Format sync plan as a table for display."""

from __future__ import annotations

from gg.matcher import ActionKind, SyncAction
from gg.numbering import assign_numbers


def format_plan(actions: list[SyncAction], *, renumber: bool = False) -> str:
    """Format sync actions as a human-readable plan table."""
    numbered = assign_numbers(actions, renumber=renumber)

    header = f"{'#':<10} {'Action':<12} {'Review':<10} Subject"
    lines = [header]
    lines.append("-" * len(header))

    for action, num_str in numbered:
        kind_label = action.kind.value
        if action.kind == ActionKind.DISCARD:
            review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
            subject = action.old_entry.subject if action.old_entry else ""
        else:
            review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
            subject = action.new_commit.subject if action.new_commit else ""

        lines.append(f"{num_str:<10} {kind_label:<12} {review:<10} {subject}")

    return "\n".join(lines)
