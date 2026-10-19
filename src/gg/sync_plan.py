"""Format sync plan as a table for display."""

from __future__ import annotations

import shutil

from gg.matcher import ActionKind, SyncAction
from gg.numbering import assign_numbers, number_series
from gg.series import GoneGroup, Series, SeriesPlan


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
    force: bool = False,
) -> str:
    """Format sync actions as a human-readable plan table."""
    numbered = assign_numbers(actions, renumber=renumber)
    show_pub = any(_will_post(a) for a in actions)

    if show_pub:
        header = f"{'#':<10} {'Action':<12} {'Pub':<7} {'Review':<10} Subject"
    else:
        header = f"{'#':<10} {'Action':<12} {'Review':<10} Subject"
    lines = _format_reviewer_header(reviewers or [], groups or [])
    if force:
        lines.append("Force: yes")
        lines.append("")
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


def _patches(n: int) -> str:
    return f"{n} patch" if n == 1 else f"{n} patches"


def _series_header(series: Series) -> str:
    text = f"Series {series.number}: {_patches(len(series.actions))}"
    if series.was is not None:
        text += f" (was series {series.was})"
    if series.origin:
        text += f", from '{series.origin}'"
    if series.new:
        text += ", new"
    if series.depends_on:
        text += f", depends on r/{series.depends_on}"
    if series.actions and all(a.outside_range for a in series.actions):
        text += ", outside range"
    return text


def _gone_header(group: GoneGroup, upstream: str) -> str:
    text = f"Series {group.old_number}: {_patches(len(group.actions))}"
    if all(a.kind == ActionKind.SUBMIT for a in group.actions):
        return f"{text}, landed in {upstream}"
    return f"{text}, gone from the branch"


def _visible(action: SyncAction) -> bool:
    return not action.outside_range or action.kind == ActionKind.KEEP_DEP


def format_series_plan(
    plan: SeriesPlan,
    *,
    upstream: str,
    renumber: bool = False,
    publish: bool = False,
    reviewers: list[str] | None = None,
    groups: list[str] | None = None,
    force: bool = False,
) -> str:
    """Format a series plan: gone groups first, then each live series."""
    show_pub = any(
        _will_post(a) for s in plan.series for a in s.actions if _visible(a)
    )
    if show_pub:
        header = f"{'#':<10} {'Action':<12} {'Pub':<7} {'Review':<10} Subject"
    else:
        header = f"{'#':<10} {'Action':<12} {'Review':<10} Subject"
    lines = _format_reviewer_header(reviewers or [], groups or [])
    if force:
        lines.append("Force: yes")
        lines.append("")
    lines.append(header)
    lines.append("-" * len(header))

    def row(num_str: str, action: SyncAction, review: str, subject: str) -> str:
        kind_label = action.kind.value
        if show_pub:
            pub = _pub_label(action, publish)
            return f"{num_str:<10} {kind_label:<12} {pub:<7} {review:<10} {subject}"
        return f"{num_str:<10} {kind_label:<12} {review:<10} {subject}"

    first = True

    def section(title: str) -> None:
        nonlocal first
        if not first:
            lines.append("")
        first = False
        lines.append(title)

    for group in plan.gone:
        section(_gone_header(group, upstream))
        for action in group.actions:
            if action.new_commit and action.old_entry is None:
                review, subject = "--", action.new_commit.subject
            else:
                entry = action.old_entry
                review = f"r/{entry.review_id}" if entry else "--"
                subject = entry.subject if entry else ""
            if action.kind == ActionKind.FORGET and action.forget_branch:
                subject += f"  (still on '{action.forget_branch}')"
            lines.append(row("--", action, review, subject))

    for series in plan.series:
        section(_series_header(series))
        for action, num_str in number_series(series, renumber=renumber):
            if not _visible(action):
                continue
            review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
            subject = action.new_commit.subject if action.new_commit else ""
            if action.outside_range:
                subject += "  (outside range)"
            lines.append(row(num_str, action, review, subject))

    return "\n".join(lines)


def format_summary(plan: SeriesPlan) -> str:
    """One-line summary of sync action counts across the whole plan."""
    kinds = {
        ActionKind.KEEP: "kept",
        ActionKind.KEEP_DEP: "kept",
        ActionKind.UPDATE: "updated",
        ActionKind.CREATE: "created",
        ActionKind.DISCARD: "discarded",
        ActionKind.SUBMIT: "submitted",
        ActionKind.FORGET: "forgotten",
        ActionKind.SKIP: "skipped",
    }
    counts: dict[str, int] = {}
    actions = [a for s in plan.series for a in s.actions]
    actions += [a for g in plan.gone for a in g.actions]
    for a in actions:
        key = kinds.get(a.kind)
        if key:
            counts[key] = counts.get(key, 0) + 1
    order = ("kept", "updated", "created", "discarded", "submitted", "forgotten", "skipped")
    return "Synced: " + ", ".join(f"{counts[k]} {k}" for k in order if k in counts)
