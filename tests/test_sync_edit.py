"""Unit tests for gg.sync_edit -- interactive plan editing."""

from __future__ import annotations

import os

import pytest

from gg.matcher import ActionKind, NewCommit, SyncAction
from gg.review_store import ReviewEntry
from gg.sync_edit import get_editor, parse_plan, serialize_plan


def _entry(rid: str, pos: int, subject: str = "test") -> ReviewEntry:
    return ReviewEntry(
        branch="feature", position=pos,
        review_id=rid, subject=subject, diff_hash=f"hash-{rid}",
    )


def _commit(rev: str, subject: str = "test") -> NewCommit:
    return NewCommit(rev=rev, subject=subject, diff_hash=f"dhash-{rev}")


def _action(
    kind: ActionKind,
    rid: str | None = None,
    rev: str | None = None,
    pos: int | None = 1,
    subject: str = "test",
    needs_dep: bool = False,
) -> SyncAction:
    old = _entry(rid, pos or 1, subject) if rid else None
    new = _commit(rev or "abc", subject) if kind not in (ActionKind.DISCARD,) else None
    if kind == ActionKind.DISCARD:
        pos = None
    return SyncAction(
        kind=kind, old_entry=old, new_commit=new,
        new_position=pos, needs_dep_update=needs_dep,
    )


class TestSerializeRoundtrip:
    def test_serialize_then_parse_unchanged(self) -> None:
        actions = [
            _action(ActionKind.KEEP, rid="1000", rev="a1", pos=1, subject="fix crash"),
            _action(ActionKind.UPDATE, rid="1001", rev="a2", pos=2, subject="add tests"),
            _action(ActionKind.CREATE, rev="a3", pos=3, subject="new feature"),
            _action(ActionKind.DISCARD, rid="1002", subject="temporary hack"),
        ]
        text = serialize_plan(actions, renumber=True)
        result = parse_plan(text, actions)
        assert result is not None
        assert len(result) == len(actions)
        for orig, parsed in zip(actions, result):
            assert parsed.kind == orig.kind


class TestTransitions:
    def test_update_to_keep(self) -> None:
        actions = [_action(ActionKind.UPDATE, rid="1000", rev="a1", subject="fix")]
        text = serialize_plan(actions, renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("update"):
                lines[i] = line.replace("update", "keep  ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, actions)
        assert result is not None
        assert result[0].kind == ActionKind.KEEP

    def test_keep_to_update(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1", subject="fix")]
        text = serialize_plan(actions, renumber=True)
        # Replace action token in data line (not in header comments)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("keep"):
                lines[i] = line.replace("keep", "update", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, actions)
        assert result is not None
        assert result[0].kind == ActionKind.UPDATE

    def test_keepdep_to_keep(self) -> None:
        actions = [
            _action(ActionKind.KEEP_DEP, rid="1000", rev="a1", needs_dep=True),
        ]
        text = serialize_plan(actions, renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("keep+dep"):
                lines[i] = line.replace("keep+dep", "keep     ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, actions)
        assert result is not None
        assert result[0].kind == ActionKind.KEEP
        assert result[0].needs_dep_update is False

    def test_create_to_skip(self) -> None:
        actions = [_action(ActionKind.CREATE, rev="a1", pos=1, subject="new")]
        text = serialize_plan(actions, renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("create"):
                lines[i] = line.replace("create", "skip   ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, actions)
        assert result is not None
        assert result[0].kind == ActionKind.SKIP

    def test_discard_to_skip(self) -> None:
        actions = [_action(ActionKind.DISCARD, rid="1002", subject="temp")]
        text = serialize_plan(actions, renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("discard"):
                lines[i] = line.replace("discard", "skip   ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, actions)
        assert result is not None
        assert result[0].kind == ActionKind.SKIP

    def test_invalid_transition_raises(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        text = serialize_plan(actions, renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("keep"):
                lines[i] = line.replace("keep", "skip", 1)
                break
        text = "\n".join(lines)
        with pytest.raises(ValueError, match="cannot change"):
            parse_plan(text, actions)


class TestParseEdgeCases:
    def test_empty_file_returns_none(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        assert parse_plan("", actions) is None

    def test_only_comments_returns_none(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        assert parse_plan("# just a comment\n# another\n", actions) is None

    def test_wrong_line_count_raises(self) -> None:
        actions = [
            _action(ActionKind.KEEP, rid="1000", rev="a1"),
            _action(ActionKind.UPDATE, rid="1001", rev="a2"),
        ]
        text = "keep  [1/2]  r/1000  test\n"
        with pytest.raises(ValueError, match="Expected 2"):
            parse_plan(text, actions)

    def test_comments_and_blanks_ignored(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1", subject="fix")]
        text = serialize_plan(actions, renumber=True)
        # Add extra comments and blank lines
        text = "# extra comment\n\n" + text + "\n# trailing\n\n"
        result = parse_plan(text, actions)
        assert result is not None
        assert result[0].kind == ActionKind.KEEP

    def test_unknown_action_raises(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        text = "bogus  [1/1]  r/1000  test\n"
        with pytest.raises(ValueError, match="unknown action"):
            parse_plan(text, actions)


class TestGetEditor:
    def _patch_which(self, monkeypatch: pytest.MonkeyPatch, found: set[str]) -> None:
        """Mock shutil.which to resolve only editors in *found*."""
        monkeypatch.setattr(
            "gg.sync_edit.shutil.which",
            lambda cmd: f"/usr/bin/{cmd}" if cmd in found else None,
        )

    def test_visual_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VISUAL", "code")
        monkeypatch.setenv("EDITOR", "nano")
        self._patch_which(monkeypatch, {"code", "nano", "vi"})
        assert get_editor() == "code"

    def test_editor_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setenv("EDITOR", "nano")
        self._patch_which(monkeypatch, {"nano", "vi"})
        assert get_editor() == "nano"

    def test_vi_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        self._patch_which(monkeypatch, {"vi"})
        assert get_editor() == "vi"

    def test_visual_not_found_falls_back_to_editor(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("VISUAL", "_emacs")
        monkeypatch.setenv("EDITOR", "nano")
        self._patch_which(monkeypatch, {"nano", "vi"})
        assert get_editor() == "nano"

    def test_no_valid_editor_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VISUAL", "_emacs")
        monkeypatch.setenv("EDITOR", "_nano")
        self._patch_which(monkeypatch, set())
        with pytest.raises(RuntimeError, match="no editor found"):
            get_editor()
