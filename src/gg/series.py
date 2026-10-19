"""Plan a branch as a list of review series for rbt-sync.

The branch's record -- plus, for commits it does not record, other
branches' records -- is matched against the branch's commits. A matched
commit belongs to the series it was recorded in, series stay contiguous
along the branch, and unrecorded commits join the series they sit in or
form one new series at the top. Recorded reviews whose commits left the
branch are forgotten, submitted or discarded, and kept reviews whose
predecessor changed are re-chained (keep+dep).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache

from gg.matcher import ActionKind, NewCommit, SyncAction, reconcile
from gg.review_store import ReviewEntry, strip_prefix

# Series membership while planning: an own series number, an adopted
# foreign series as (branch, series number), or None for the new series.
_Key = int | tuple[str, int] | None


@dataclass
class Series:
    """A live series: consecutive commits of the branch posted as one chain."""

    number: int                      # 1-based ordinal among live series
    actions: list[SyncAction]        # every member in branch order, incl. outside-range ones
    origin: str | None = None        # other branch it was adopted from
    was: int | None = None           # previous number, when it changed
    new: bool = False
    depends_on: str | None = None    # predecessor review of the first patch, if known


@dataclass
class GoneGroup:
    """Recorded entries of one old series whose commits left the branch."""

    old_number: int
    actions: list[SyncAction]        # SUBMIT / DISCARD / FORGET (SKIP after -i)


@dataclass
class SeriesPlan:
    """The sync plan for a whole branch."""

    series: list[Series]
    gone: list[GoneGroup]
    warnings: list[str]
    adopted_from: set[str]           # branches whose entries were adopted


@dataclass
class _Source:
    """A recorded entry that may be matched to a commit."""

    entry: ReviewEntry               # as recorded; never mutated
    key: _Key                        # series it joins while planning
    rank: int                        # 1-based place inside its old series
    origin: str | None = None        # foreign branch, for adopted entries
    # Review recorded just before it: along the own record (across series),
    # or inside its foreign series.
    pred: str | None = None


@dataclass
class _Adoption:
    """A foreign entry taken over for one commit of this branch."""

    commit: int                      # index into the full commit list
    source: _Source


@dataclass
class _Row:
    """One commit placed in the plan, in branch order."""

    action: SyncAction
    source: _Source | None           # None for an unrecorded commit
    key: _Key = None


def build_plan(
    *,
    branch: str,
    own: list[ReviewEntry],
    foreign: dict[str, list[ReviewEntry]],
    commits: list[NewCommit],
    in_range: set[str],
    landed_subjects: Callable[[], set[str]],
    branch_exists: Callable[[str], bool],
    depends_on_of: Callable[[str], list[str]],
    initial_depends: str | None = None,
) -> SeriesPlan:
    """Decide which commits of the branch form which series.

    `own` is this branch's record, `foreign` other branches' records,
    `commits` the full branch (oldest first) and `in_range` the revs the
    user's range selects. Returned actions carry copies of the recorded
    entries whose `position` is their rank inside their old series.

    `landed_subjects` (upstream's recent subjects) is called at most once,
    only when a recorded commit is gone; `depends_on_of` (a review's
    dependencies on ReviewBoard) at most once per adopted series.
    """
    own = sorted(own, key=lambda e: e.position)
    own_sources = _own_sources(own)
    adoptions, skipped, warnings = _adopt(branch, own, foreign, commits)

    while True:
        rows, unmatched = _match(own_sources, adoptions, commits, skipped)
        split = [k for k in _split_keys(rows) if isinstance(k, tuple)]
        if not split:
            break
        dropped = [a for a in adoptions if a.source.key == split[0]]
        adoptions = [a for a in adoptions if a.source.key != split[0]]
        skipped.update(a.commit for a in dropped)
        warnings.append(_noncontiguous_warning(split[0], dropped))

    _join_moved_runs(rows)
    rows = _place_unrecorded(rows, in_range)
    _hold_outside_range(rows, in_range)
    series = _number(rows)
    gone = _gone(branch, unmatched, foreign, landed_subjects, branch_exists)
    submitted = {
        a.old_entry.review_id
        for group in gone for a in group.actions if a.kind == ActionKind.SUBMIT
    }
    _chain(series, rows, submitted, depends_on_of, initial_depends)

    return SeriesPlan(
        series=series,
        gone=gone,
        warnings=warnings,
        adopted_from={r.source.origin for r in rows if r.source and r.source.origin},
    )


def _own_sources(own: list[ReviewEntry]) -> list[_Source]:
    ranks: Counter[int] = Counter()
    sources = []
    pred: str | None = None
    for entry in own:
        ranks[entry.series] += 1
        sources.append(_Source(entry, entry.series, ranks[entry.series], pred=pred))
        pred = entry.review_id
    return sources


def _adopt(
    branch: str,
    own: list[ReviewEntry],
    foreign: dict[str, list[ReviewEntry]],
    commits: list[NewCommit],
) -> tuple[list[_Adoption], set[int], list[str]]:
    """Look up commits this branch does not record in other branches' records.

    Returns the adoptions, the indices of commits skipped because branches
    disagree on their review, and a warning per skipped commit.
    """
    own_subjects = {strip_prefix(e.subject) for e in own}
    own_series = {e.review_id: e.series for e in own}
    index = _foreign_index(branch, foreign, own_series)
    taken = set(own_series)
    adoptions: list[_Adoption] = []
    skipped: set[int] = set()
    warnings: list[str] = []

    for ci, commit in enumerate(commits):
        subject = strip_prefix(commit.subject)
        if subject in own_subjects or subject not in index:
            continue
        by_id = index[subject]
        if len(by_id) > 1:
            skipped.add(ci)
            recorded = ", ".join(
                f"r/{rid} on '{s.origin}'" for rid, ss in by_id.items() for s in ss
            )
            warnings.append(f"skipping '{subject}': recorded as {recorded}")
            continue
        [(rid, sources)] = by_id.items()
        if rid in taken:
            continue
        taken.add(rid)
        # Branches are indexed alphabetically, so this is the first one.
        adoptions.append(_Adoption(ci, sources[0]))

    return adoptions, skipped, warnings


def _foreign_index(
    branch: str,
    foreign: dict[str, list[ReviewEntry]],
    own_series: dict[str, int],
) -> dict[str, dict[str, list[_Source]]]:
    """Index other branches' entries: stripped subject -> review id -> sources.

    Sources of one review id are in branch-name order. A foreign series
    sharing a review id with an own series joins that own series.
    """
    index: dict[str, dict[str, list[_Source]]] = {}
    for name in sorted(foreign):
        if name == branch:
            continue
        by_series: dict[int, list[ReviewEntry]] = {}
        for entry in sorted(foreign[name], key=lambda e: e.position):
            by_series.setdefault(entry.series, []).append(entry)
        for number, entries in by_series.items():
            for i, entry in enumerate(entries):
                target = _merge_target(entries, i, own_series)
                key = (name, number) if target is None else target
                pred = entries[i - 1].review_id if i else None
                source = _Source(entry, key, i + 1, origin=name, pred=pred)
                ids = index.setdefault(strip_prefix(entry.subject), {})
                ids.setdefault(entry.review_id, []).append(source)
    return index


def _merge_target(
    entries: list[ReviewEntry], i: int, own_series: dict[str, int],
) -> int | None:
    """Own series of the nearest entry of a foreign series this branch records.

    Entries at or before `i` are preferred over later ones.
    """
    for j in [*range(i, -1, -1), *range(i + 1, len(entries))]:
        number = own_series.get(entries[j].review_id)
        if number is not None:
            return number
    return None


def _match(
    own_sources: list[_Source],
    adoptions: list[_Adoption],
    commits: list[NewCommit],
    skipped: set[int],
) -> tuple[list[_Row], list[_Source]]:
    """Reconcile own and adopted entries with the commits not skipped.

    Returns a row per commit in branch order and the own entries no commit
    matched.
    """
    kept = [ci for ci in range(len(commits)) if ci not in skipped]
    place = {ci: n for n, ci in enumerate(kept, 1)}
    old = [s.entry for s in own_sources]
    # An adopted entry has no place in this branch's record yet: give it
    # its commit's, so position tie-breaks favour that commit.
    old += [replace(a.source.entry, position=place[a.commit]) for a in adoptions]
    by_entry = {
        id(e): s for e, s in zip(old, own_sources + [a.source for a in adoptions])
    }

    rows: list[_Row] = []
    unmatched: list[_Source] = []
    for action in reconcile(old, [commits[ci] for ci in kept], mark_deps=False):
        source = by_entry[id(action.old_entry)] if action.old_entry else None
        if action.kind == ActionKind.DISCARD:
            # Foreign entries without a commit are ignored, never discarded.
            if source.origin is None:
                unmatched.append(source)
            continue
        if source:
            action.old_entry = replace(source.entry, position=source.rank)
        rows.append(_Row(action, source, source.key if source else None))
    return rows, unmatched


def _runs(rows: list[_Row]) -> list[tuple[_Key, list[_Row]]]:
    """Group consecutive matched rows of the same series."""
    runs: list[tuple[_Key, list[_Row]]] = []
    for row in rows:
        if row.source is None:
            continue
        if runs and runs[-1][0] == row.key:
            runs[-1][1].append(row)
        else:
            runs.append((row.key, [row]))
    return runs


def _split_keys(rows: list[_Row]) -> list[_Key]:
    """Series whose matched commits form more than one run, in branch order."""
    keys = [key for key, _ in _runs(rows)]
    counts = Counter(keys)
    return list(dict.fromkeys(k for k in keys if counts[k] > 1))


def _noncontiguous_warning(key: tuple[str, int], dropped: list[_Adoption]) -> str:
    name, number = key
    ids = ", ".join(f"r/{a.source.entry.review_id}" for a in dropped)
    return (
        f"skipping {ids} (series {number} of '{name}'): "
        "its commits are not contiguous on this branch"
    )


def _join_moved_runs(rows: list[_Row]) -> None:
    """Keep each own series in its longest run; other runs join a neighbour.

    A run that moves joins the series of the run before it (after it, for
    the first run), and its commits are re-posted as moved.
    """
    while split := _split_keys(rows):
        runs = _runs(rows)
        mine = [i for i, (key, _) in enumerate(runs) if key == split[0]]
        longest = max(mine, key=lambda i: len(runs[i][1]))  # first on ties
        for i in mine:
            if i == longest:
                continue
            target = runs[i - 1][0] if i > 0 else runs[i + 1][0]
            for row in runs[i][1]:
                row.key = target
                row.action.kind = ActionKind.UPDATE
                row.action.moved = True


def _place_unrecorded(rows: list[_Row], in_range: set[str]) -> list[_Row]:
    """Give in-range unrecorded commits a series; drop the others.

    Between recorded commits they join the series of the one before, before
    the first they join its series, after the last they form the new series.
    """
    matched = [i for i, row in enumerate(rows) if row.source]
    last = matched[-1] if matched else -1
    key = rows[matched[0]].key if matched else None
    placed = []
    for i, row in enumerate(rows):
        if row.source:
            key = row.key
        elif row.action.new_commit.rev not in in_range:
            continue
        else:
            row.key = key if i < last else None
        placed.append(row)
    return placed


def _hold_outside_range(rows: list[_Row], in_range: set[str]) -> None:
    """Recorded commits outside the range stay members but are not re-posted."""
    for row in rows:
        if row.source and row.action.new_commit.rev not in in_range:
            row.action.kind = ActionKind.KEEP
            row.action.outside_range = True


def _number(rows: list[_Row]) -> list[Series]:
    """Number the live series 1..K in branch order."""
    groups: dict[_Key, list[SyncAction]] = {}
    for row in rows:
        groups.setdefault(row.key, []).append(row.action)
    series = []
    for number, (key, actions) in enumerate(groups.items(), 1):
        for place, action in enumerate(actions, 1):
            action.new_position = place
        series.append(Series(
            number=number,
            actions=actions,
            origin=key[0] if isinstance(key, tuple) else None,
            was=key if isinstance(key, int) and key != number else None,
            new=key is None,
        ))
    return series


def _gone(
    branch: str,
    unmatched: list[_Source],
    foreign: dict[str, list[ReviewEntry]],
    landed_subjects: Callable[[], set[str]],
    branch_exists: Callable[[str], bool],
) -> list[GoneGroup]:
    """Classify own entries whose commits left the branch, by old series.

    A review that another existing branch records is forgotten (never
    closed), else one whose subject landed upstream is submitted, else it
    is discarded.
    """
    recorded_on: dict[str, dict[str, None]] = {}
    for name in sorted(foreign):
        if name != branch:
            for entry in foreign[name]:
                recorded_on.setdefault(entry.review_id, {})[name] = None
    exists = cache(branch_exists)
    landed: set[str] | None = None

    groups: dict[int, list[SyncAction]] = {}
    for source in sorted(unmatched, key=lambda s: s.entry.position):
        entry = source.entry
        holder = next((b for b in recorded_on.get(entry.review_id, {}) if exists(b)), None)
        if holder is not None:
            kind = ActionKind.FORGET
        else:
            if landed is None:
                landed = {strip_prefix(s) for s in landed_subjects()}
            landed_here = strip_prefix(entry.subject) in landed
            kind = ActionKind.SUBMIT if landed_here else ActionKind.DISCARD
        groups.setdefault(entry.series, []).append(SyncAction(
            kind=kind,
            old_entry=replace(entry, position=source.rank),
            new_commit=None,
            new_position=None,
            forget_branch=holder,
        ))
    return [GoneGroup(number, groups[number]) for number in sorted(groups)]


def _chain(
    series: list[Series],
    rows: list[_Row],
    submitted: set[str],
    depends_on_of: Callable[[str], list[str]],
    initial_depends: str | None,
) -> None:
    """Set each series' dependency and re-chain kept reviews that need it.

    A commit's expected predecessor is the review of the commit before it
    along the branch, across series (none after a create). A kept review
    whose current predecessor differs becomes keep+dep, unless that
    predecessor is being submitted and no new commit took its place. The
    first patch of series 1 is never re-chained.
    """
    sources = {id(row.action): row.source for row in rows}
    expected: str | None = None
    after_create = False
    for s in series:
        s.depends_on = initial_depends if s.number == 1 else expected
        for i, action in enumerate(s.actions):
            if action.kind == ActionKind.KEEP and (s.number, i) != (1, 0):
                current = _current_pred(sources[id(action)], depends_on_of)
                landed = current in submitted and not after_create
                if current != expected and not landed:
                    action.kind = ActionKind.KEEP_DEP
                    action.needs_dep_update = True
            expected = action.old_entry.review_id if action.old_entry else None
            after_create = action.kind == ActionKind.CREATE


def _current_pred(
    source: _Source, depends_on_of: Callable[[str], list[str]],
) -> str | None:
    """The review a recorded entry depends on now.

    That is the one recorded before it, except for the first entry of an
    adopted foreign series, whose record has none: ReviewBoard is asked.
    The first entry of a foreign series merged into an own one has none
    either and is not looked up (no ReviewBoard read beyond one per
    adopted series), so it counts as depending on nothing.
    """
    if isinstance(source.key, tuple) and source.rank == 1:
        deps = depends_on_of(source.entry.review_id)
        return deps[0] if deps else None
    return source.pred
