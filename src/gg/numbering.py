"""Number formatting for rbt-sync plan actions."""

from __future__ import annotations

from gg.matcher import ActionKind, SyncAction


def assign_numbers(
    actions: list[SyncAction], *, renumber: bool = False,
) -> list[tuple[SyncAction, str]]:
    """Assign display numbers to sync actions.

    Returns (action, number_string) pairs.
    number_string is e.g. "[1/5]", "[2.1/5]", or "--" for discards.

    Fractional (default): matched commits keep original position number,
    inserts between P and Q get P.1, P.2, etc. Appends get next integer.

    --renumber: plain [1/N]..[N/N].
    """
    excluded = (ActionKind.DISCARD, ActionKind.SKIP)
    non_discard = [a for a in actions if a.kind not in excluded]
    discards = [a for a in actions if a.kind in excluded]
    total = len(non_discard)

    if renumber:
        result: list[tuple[SyncAction, str]] = []
        for i, action in enumerate(non_discard, 1):
            result.append((action, f"[{i}/{total}]"))
        for action in discards:
            result.append((action, "--"))
        return result

    # Fractional numbering: matched commits keep old position
    labels: list[str | None] = [None] * len(non_discard)

    # First pass: assign integer positions to matched commits
    for i, action in enumerate(non_discard):
        if action.old_entry is not None:
            labels[i] = str(action.old_entry.position + 1)

    # Second pass: fractional positions for inserts *between* matched commits
    for i, action in enumerate(non_discard):
        if labels[i] is not None:
            continue
        # Only use fractional if there's a matched commit after this one
        has_following_match = any(
            labels[j] is not None for j in range(i + 1, len(non_discard))
        )
        if not has_following_match:
            continue
        # Find preceding matched position
        prev_int = 0
        for j in range(i - 1, -1, -1):
            if non_discard[j].old_entry is not None:
                prev_int = non_discard[j].old_entry.position + 1
                break
        # Count consecutive inserts after prev_int
        frac_idx = 0
        for j in range(i, -1, -1):
            if non_discard[j].old_entry is not None:
                break
            if j <= i:
                frac_idx += 1
        labels[i] = f"{prev_int + 1}.{frac_idx}" if prev_int > 0 else f"0.{frac_idx}"

    # Third pass: integer positions for appends after all matched commits
    next_int = 0
    for i in range(len(non_discard) - 1, -1, -1):
        if non_discard[i].old_entry is not None:
            next_int = non_discard[i].old_entry.position + 2
            break
    for i, action in enumerate(non_discard):
        if labels[i] is None:
            labels[i] = str(next_int)
            next_int += 1

    result = []
    for i, action in enumerate(non_discard):
        result.append((action, f"[{labels[i]}/{total}]"))
    for action in discards:
        result.append((action, "--"))

    return result
