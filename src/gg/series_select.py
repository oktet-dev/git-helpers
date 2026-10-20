"""The `-s` series selection of `gg rbt-sync`: parsing and resolving it."""

from __future__ import annotations

import argparse

from gg.series import SeriesPlan

# One -s selection: a series number and its patch ranges (None: all patches).
SeriesSpec = tuple[int, list[tuple[int, int]] | None]


# "$" in a -s value: the last series of the plan, or the last patch of a series.
LAST = 0


def _positive(token: str, what: str) -> int:
    if token.strip() == "$":
        return LAST
    if not token.strip().isdigit() or int(token) < 1:
        raise argparse.ArgumentTypeError(f"invalid series {what}: {token!r}")
    return int(token)


def series_spec(text: str) -> list[SeriesSpec]:
    """Parse a -s value: "3", "3,4" (series) or "4:1-3,5" (patches of series 4).

    "$" stands for the last series ("$:1-3") or the last patch ("4:2-$").
    """
    if ":" not in text:
        return [(_positive(token, "number"), None) for token in text.split(",")]
    number, patches = text.split(":", 1)
    ranges = []
    for token in patches.split(","):
        first, _, last = token.partition("-")
        a = _positive(first, "patch")
        b = _positive(last, "patch") if last else a
        if (a == LAST and b != LAST) or (LAST not in (a, b) and b < a):
            raise argparse.ArgumentTypeError(f"invalid series patch range: {token!r}")
        ranges.append((a, b))
    return [(_positive(number, "number"), ranges)]


def selected_revs(
    plan: SeriesPlan, specs: list[SeriesSpec], *, branch_name: str,
) -> set[str] | str:
    """Commits picked by -s specs in a whole-branch plan, or an error message."""
    revs: set[str] = set()
    for number, ranges in specs:
        name = str(number)
        if number == LAST:
            number = len(plan.series)
            name = f"{number} ($)"
        if number > len(plan.series):
            return f"no series {number} on '{branch_name}' (it has {len(plan.series)})."
        series = plan.series[number - 1]
        actions = series.actions
        last = len(actions)
        spans = [(1, last)] if ranges is None else [
            (last if a == LAST else a, last if b == LAST else b) for a, b in ranges
        ]
        top = max(max(span) for span in spans)
        if top > last:
            return f"series {name} has {last} patches; no patch {top}."
        picked = [k for a, b in spans for k in range(a, b + 1)]
        if series.new and min(picked) != 1:
            return (
                f"series {name} is new: the selection must start at patch 1 "
                "(patches left before it would later join the series below)."
            )
        revs.update(actions[k - 1].new_commit.rev for k in picked)
    return revs
