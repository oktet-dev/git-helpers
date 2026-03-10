"""Tests for gg.numbering -- fractional and full renumbering."""

from gg.matcher import ActionKind, NewCommit, SyncAction
from gg.numbering import assign_numbers
from gg.review_store import ReviewEntry


def _keep(pos: int, rid: str, subject: str) -> SyncAction:
    return SyncAction(
        kind=ActionKind.KEEP,
        old_entry=ReviewEntry("b", pos, rid, subject, "h"),
        new_commit=NewCommit(rev="abc", subject=subject, diff_hash="h"),
        new_position=pos + 1,
    )


def _create(subject: str) -> SyncAction:
    return SyncAction(
        kind=ActionKind.CREATE,
        old_entry=None,
        new_commit=NewCommit(rev="abc", subject=subject, diff_hash="h"),
        new_position=1,
    )


def _discard(pos: int, rid: str, subject: str) -> SyncAction:
    return SyncAction(
        kind=ActionKind.DISCARD,
        old_entry=ReviewEntry("b", pos, rid, subject, "h"),
        new_commit=None,
        new_position=None,
    )


class TestRenumber:
    def test_simple_renumber(self) -> None:
        actions = [_keep(0, "100", "first"), _keep(1, "101", "second")]
        result = assign_numbers(actions, renumber=True)
        assert result[0][1] == "[1/2]"
        assert result[1][1] == "[2/2]"

    def test_discards_get_dashes(self) -> None:
        actions = [_keep(0, "100", "first"), _discard(1, "101", "dropped")]
        result = assign_numbers(actions, renumber=True)
        assert result[0][1] == "[1/1]"
        assert result[1][1] == "--"

    def test_create_included_in_total(self) -> None:
        actions = [_keep(0, "100", "first"), _create("new")]
        result = assign_numbers(actions, renumber=True)
        assert result[0][1] == "[1/2]"
        assert result[1][1] == "[2/2]"


class TestFractional:
    def test_matched_keep_original_position(self) -> None:
        actions = [_keep(0, "100", "first"), _keep(1, "101", "second")]
        result = assign_numbers(actions, renumber=False)
        # Position 0 -> display "1", position 1 -> display "2"
        assert result[0][1] == "[1/2]"
        assert result[1][1] == "[2/2]"

    def test_insert_gets_fractional(self) -> None:
        actions = [_keep(0, "100", "first"), _create("inserted"), _keep(1, "101", "second")]
        result = assign_numbers(actions, renumber=False)
        assert len(result) == 3
        # First keeps position 1
        assert result[0][1] == "[1/3]"
        # Insert between 1 and 2 gets fractional
        assert ".1" in result[1][1] or ".2" in result[1][1] or "1." in result[1][1]
        # Second keeps position 2
        assert result[2][1] == "[2/3]"
