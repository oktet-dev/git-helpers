"""Reconcile old review entries with new commits for rbt-sync."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import Enum

from gg.review_store import ReviewEntry, strip_prefix

_FUZZY_THRESHOLD = 0.6
_SUBJECT_WEIGHT = 0.7
_POSITION_WEIGHT = 0.3


class ActionKind(Enum):
    KEEP = "keep"
    UPDATE = "update"
    KEEP_DEP = "keep+dep"
    CREATE = "create"
    DISCARD = "discard"


@dataclass
class NewCommit:
    """A commit from the current branch to be matched against old reviews."""

    rev: str
    subject: str
    diff_hash: str


@dataclass
class SyncAction:
    """One action in the sync plan."""

    kind: ActionKind
    # Set for keep/update/keep_dep/discard; None for create
    old_entry: ReviewEntry | None
    # Set for keep/update/keep_dep/create; None for discard
    new_commit: NewCommit | None
    # Position in new series (1-based), None for discard
    new_position: int | None
    needs_dep_update: bool = False


def _subject_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _position_proximity(old_pos: int, new_pos: int, total: int) -> float:
    """Score 0..1 for how close positions are (1 = same position)."""
    if total <= 1:
        return 1.0
    return 1.0 - abs(old_pos - new_pos) / total


def reconcile(
    old: list[ReviewEntry], new: list[NewCommit],
) -> list[SyncAction]:
    """Match old reviews to new commits and produce a sync plan.

    Returns actions sorted: non-discard in new series order, then discards.
    """
    total = max(len(old), len(new), 1)
    matched_old: set[int] = set()  # indices into old
    matched_new: set[int] = set()  # indices into new
    # Maps: new_idx -> old_idx
    matches: dict[int, int] = {}

    # Pass 1: exact subject match
    old_by_subject: dict[str, list[int]] = {}
    for i, entry in enumerate(old):
        subj = strip_prefix(entry.subject)
        old_by_subject.setdefault(subj, []).append(i)

    for ni, commit in enumerate(new):
        subj = strip_prefix(commit.subject)
        candidates = old_by_subject.get(subj, [])
        candidates = [c for c in candidates if c not in matched_old]
        if not candidates:
            continue
        # Tiebreak by position proximity
        best = min(candidates, key=lambda oi: abs(old[oi].position - ni))
        matches[ni] = best
        matched_old.add(best)
        matched_new.add(ni)

    # Pass 2: fuzzy match on remaining
    unmatched_old = [i for i in range(len(old)) if i not in matched_old]
    unmatched_new = [i for i in range(len(new)) if i not in matched_new]

    if unmatched_old and unmatched_new:
        scored: list[tuple[float, int, int]] = []
        for ni in unmatched_new:
            for oi in unmatched_old:
                ratio = _subject_ratio(
                    strip_prefix(old[oi].subject),
                    strip_prefix(new[ni].subject),
                )
                if ratio < _FUZZY_THRESHOLD:
                    continue
                prox = _position_proximity(old[oi].position, ni + 1, total)
                score = _SUBJECT_WEIGHT * ratio + _POSITION_WEIGHT * prox
                scored.append((score, ni, oi))

        # Greedy best-first matching
        scored.sort(key=lambda t: t[0], reverse=True)
        for _, ni, oi in scored:
            if ni in matched_new or oi in matched_old:
                continue
            matches[ni] = oi
            matched_old.add(oi)
            matched_new.add(ni)

    # Build actions
    actions: list[SyncAction] = []

    for ni in range(len(new)):
        commit = new[ni]
        if ni in matches:
            entry = old[matches[ni]]
            if commit.diff_hash != entry.diff_hash:
                kind = ActionKind.UPDATE
            else:
                kind = ActionKind.KEEP
            actions.append(SyncAction(
                kind=kind, old_entry=entry, new_commit=commit,
                new_position=ni + 1,
            ))
        else:
            actions.append(SyncAction(
                kind=ActionKind.CREATE, old_entry=None, new_commit=commit,
                new_position=ni + 1,
            ))

    # Discards: old entries not matched to any new commit
    for oi in range(len(old)):
        if oi not in matched_old:
            actions.append(SyncAction(
                kind=ActionKind.DISCARD, old_entry=old[oi], new_commit=None,
                new_position=None,
            ))

    # Dependency chain: detect when predecessor changed
    _mark_dep_updates(actions, old)

    return actions


def _mark_dep_updates(actions: list[SyncAction], old: list[ReviewEntry]) -> None:
    """Mark actions where the predecessor review changed."""
    non_discard = [a for a in actions if a.kind != ActionKind.DISCARD]

    # Build old predecessor map: review_id -> predecessor review_id
    old_pred: dict[str, str | None] = {}
    for i, entry in enumerate(old):
        old_pred[entry.review_id] = old[i - 1].review_id if i > 0 else None

    prev_review_id: str | None = None
    for action in non_discard:
        if action.old_entry and action.kind == ActionKind.KEEP:
            expected_pred = old_pred.get(action.old_entry.review_id)
            if prev_review_id != expected_pred:
                action.kind = ActionKind.KEEP_DEP
                action.needs_dep_update = True
        prev_review_id = (
            action.old_entry.review_id if action.old_entry else None
        )
