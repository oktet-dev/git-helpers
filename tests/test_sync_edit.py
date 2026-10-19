"""Unit tests for gg.sync_edit -- interactive plan editing."""

from __future__ import annotations

import os

import pytest

from gg.matcher import ActionKind, NewCommit, SyncAction
from gg.review_store import ReviewEntry
from gg.series import GoneGroup, Series, SeriesPlan
from gg.sync_edit import (
    _ACTION_KEYS,
    _build_editor_cmd,
    _make_header,
    get_editor,
    parse_plan,
    serialize_plan,
)


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


def _plan(*series_actions, gone=()):
    return SeriesPlan([Series(i, list(a)) for i, a in enumerate(series_actions, 1)],
                      [GoneGroup(1, list(gone))] if gone else [], [], set())


def _keep(rid: str, subject: str, pos: int = 1) -> SyncAction:
    return _action(ActionKind.KEEP, rid=rid, rev=f"r{rid}", pos=pos, subject=subject)


def _split(actions: list[SyncAction]) -> SeriesPlan:
    """One-series plan; discards go into a gone group."""
    live = [a for a in actions if a.kind != ActionKind.DISCARD]
    gone = [a for a in actions if a.kind == ActionKind.DISCARD]
    return _plan(live, gone=gone)


def _kinds(plan: SeriesPlan) -> list[ActionKind]:
    return [a.kind for s in plan.series for a in s.actions] + [
        a.kind for g in plan.gone for a in g.actions
    ]


class TestSerializeRoundtrip:
    def test_serialize_then_parse_unchanged(self) -> None:
        actions = [
            _action(ActionKind.KEEP, rid="1000", rev="a1", pos=1, subject="fix crash"),
            _action(ActionKind.UPDATE, rid="1001", rev="a2", pos=2, subject="add tests"),
            _action(ActionKind.CREATE, rev="a3", pos=3, subject="new feature"),
            _action(ActionKind.DISCARD, rid="1002", subject="temporary hack"),
        ]
        text = serialize_plan(_split(actions), renumber=True)
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert len(_kinds(result)) == len(actions)
        assert _kinds(result) == _kinds(_split(actions))


class TestTransitions:
    def test_update_to_keep(self) -> None:
        actions = [_action(ActionKind.UPDATE, rid="1000", rev="a1", subject="fix")]
        text = serialize_plan(_split(actions), renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("update"):
                lines[i] = line.replace("update", "keep  ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.KEEP

    def test_keep_to_update(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1", subject="fix")]
        text = serialize_plan(_split(actions), renumber=True)
        # Replace action token in data line (not in header comments)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("keep"):
                lines[i] = line.replace("keep", "update", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.UPDATE

    def test_keepdep_to_keep(self) -> None:
        actions = [
            _action(ActionKind.KEEP_DEP, rid="1000", rev="a1", needs_dep=True),
        ]
        text = serialize_plan(_split(actions), renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("keep+dep"):
                lines[i] = line.replace("keep+dep", "keep     ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.KEEP
        assert result.series[0].actions[0].needs_dep_update is False

    def test_create_to_skip(self) -> None:
        actions = [_action(ActionKind.CREATE, rev="a1", pos=1, subject="new")]
        text = serialize_plan(_split(actions), renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("create"):
                lines[i] = line.replace("create", "skip   ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.SKIP

    def test_discard_to_skip(self) -> None:
        actions = [_action(ActionKind.DISCARD, rid="1002", subject="temp")]
        text = serialize_plan(_split(actions), renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("discard"):
                lines[i] = line.replace("discard", "skip   ", 1)
                break
        text = "\n".join(lines)
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.SKIP

    def test_invalid_transition_raises(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        text = serialize_plan(_split(actions), renumber=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("keep"):
                lines[i] = line.replace("keep", "skip", 1)
                break
        text = "\n".join(lines)
        with pytest.raises(ValueError, match="cannot change"):
            parse_plan(text, _split(actions))


class TestParseEdgeCases:
    def test_empty_file_returns_none(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        assert parse_plan("", _split(actions)) is None

    def test_only_comments_returns_none(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        assert parse_plan("# just a comment\n# another\n", _split(actions)) is None

    def test_wrong_line_count_raises(self) -> None:
        actions = [
            _action(ActionKind.KEEP, rid="1000", rev="a1"),
            _action(ActionKind.UPDATE, rid="1001", rev="a2"),
        ]
        text = "keep  [1/2]  r/1000  test\n"
        with pytest.raises(ValueError, match="Expected 2"):
            parse_plan(text, _split(actions))

    def test_comments_and_blanks_ignored(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1", subject="fix")]
        text = serialize_plan(_split(actions), renumber=True)
        # Add extra comments and blank lines
        text = "# extra comment\n\n" + text + "\n# trailing\n\n"
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.KEEP

    def test_unknown_action_raises(self) -> None:
        actions = [_action(ActionKind.KEEP, rid="1000", rev="a1")]
        text = "bogus  [1/1]  r/1000  test\n"
        with pytest.raises(ValueError, match="unknown action"):
            parse_plan(text, _split(actions))


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


class TestBuildEditorCmd:
    def test_vim_gets_c_flags(self) -> None:
        cmd = _build_editor_cmd("vim", "/tmp/plan.txt")
        c_flags = [i for i, arg in enumerate(cmd) if arg == "-c"]
        assert len(c_flags) == len(_ACTION_KEYS)

    def test_vim_mapping_strings(self) -> None:
        cmd = _build_editor_cmd("vim", "/tmp/plan.txt")
        for action, key in _ACTION_KEYS:
            assert any(f"g{key}" in arg and action in arg for arg in cmd)

    def test_vim_filepath_last(self) -> None:
        cmd = _build_editor_cmd("vim", "/tmp/plan.txt")
        assert cmd[-1] == "/tmp/plan.txt"

    def test_nvim_detected(self) -> None:
        cmd = _build_editor_cmd("nvim", "/tmp/plan.txt")
        c_flags = [arg for arg in cmd if arg == "-c"]
        assert len(c_flags) == len(_ACTION_KEYS)

    def test_absolute_path_vim(self) -> None:
        cmd = _build_editor_cmd("/usr/bin/vim", "/tmp/plan.txt")
        c_flags = [arg for arg in cmd if arg == "-c"]
        assert len(c_flags) == len(_ACTION_KEYS)
        assert cmd[0] == "/usr/bin/vim"

    def test_emacs_gets_eval_after_filepath(self) -> None:
        cmd = _build_editor_cmd("emacs", "/tmp/plan.txt")
        fp_idx = cmd.index("/tmp/plan.txt")
        eval_idx = cmd.index("--eval")
        assert eval_idx > fp_idx

    def test_emacs_bindings_contain_keys(self) -> None:
        cmd = _build_editor_cmd("emacs", "/tmp/plan.txt")
        eval_arg = cmd[cmd.index("--eval") + 1]
        for action, key in _ACTION_KEYS:
            assert f"C-c {key}" in eval_arg
            assert action in eval_arg

    def test_absolute_path_emacs(self) -> None:
        cmd = _build_editor_cmd("/usr/bin/emacs", "/tmp/plan.txt")
        assert "--eval" in cmd
        assert cmd[0] == "/usr/bin/emacs"

    def test_unknown_editor_passthrough(self) -> None:
        cmd = _build_editor_cmd("nano", "/tmp/plan.txt")
        assert cmd == ["nano", "/tmp/plan.txt"]

    def test_unknown_absolute_path_passthrough(self) -> None:
        cmd = _build_editor_cmd("/usr/bin/nano", "/tmp/plan.txt")
        assert cmd == ["/usr/bin/nano", "/tmp/plan.txt"]


class TestMakeHeader:
    def test_vim_header_has_shortcuts(self) -> None:
        header = _make_header("vim")
        assert "Shortcuts (vim):" in header
        assert "gu=update" in header
        assert "gk=keep" in header

    def test_emacs_header_has_shortcuts(self) -> None:
        header = _make_header("emacs")
        assert "Shortcuts (emacs):" in header
        assert "C-c u=update" in header
        assert "C-c k=keep" in header

    def test_unknown_editor_no_shortcut_line(self) -> None:
        header = _make_header("nano")
        assert "Shortcuts" not in header

    def test_empty_editor_no_shortcut_line(self) -> None:
        header = _make_header("")
        assert "Shortcuts" not in header

    def test_all_headers_contain_valid_actions(self) -> None:
        for editor in ("vim", "emacs", "nano", ""):
            header = _make_header(editor)
            assert "Valid actions" in header

    def test_absolute_path_vim(self) -> None:
        header = _make_header("/usr/local/bin/nvim")
        assert "Shortcuts (vim):" in header

    def test_headers_say_rows_stay_in_place(self) -> None:
        for separators in (True, False):
            header = _make_header("", separators=separators)
            assert "# Do not reorder or delete rows; change the action word only.\n" in header


class TestSerializeWithEditor:
    def test_roundtrip_with_vim(self) -> None:
        actions = [
            _action(ActionKind.KEEP, rid="1000", rev="a1", pos=1, subject="fix"),
            _action(ActionKind.UPDATE, rid="1001", rev="a2", pos=2, subject="add"),
        ]
        text = serialize_plan(_split(actions), renumber=True, editor="vim")
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert len(_kinds(result)) == len(actions)
        assert _kinds(result) == _kinds(_split(actions))

    def test_roundtrip_with_emacs(self) -> None:
        actions = [
            _action(ActionKind.CREATE, rev="a1", pos=1, subject="new"),
        ]
        text = serialize_plan(_split(actions), renumber=True, editor="emacs")
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.CREATE

    def test_no_editor_backward_compat(self) -> None:
        actions = [
            _action(ActionKind.KEEP, rid="1000", rev="a1", pos=1, subject="fix"),
        ]
        text = serialize_plan(_split(actions), renumber=True)
        result = parse_plan(text, _split(actions))
        assert result is not None
        assert _kinds(result)[0] == ActionKind.KEEP


class TestSeparators:
    def test_separator_written_between_series(self) -> None:
        plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)])
        lines = [l for l in serialize_plan(plan).splitlines() if l and not l.startswith("#")]
        assert lines[1] == "---"

    def test_deleting_separator_merges_and_updates(self) -> None:
        plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)])
        text = serialize_plan(plan).replace("\n---\n", "\n")
        merged = parse_plan(text, plan)
        assert [len(s.actions) for s in merged.series] == [2]
        assert [a.kind.value for a in merged.series[0].actions] == ["update", "update"]

    def test_adding_separator_splits(self) -> None:
        plan = _plan([_keep("1", "a"), _keep("2", "b", pos=2)])
        rows = serialize_plan(plan).splitlines()
        i = next(n for n, l in enumerate(rows) if l.endswith(" b"))
        split = parse_plan("\n".join(rows[:i] + ["---"] + rows[i:]), plan)
        assert [len(s.actions) for s in split.series] == [1, 1]
        assert [s.number for s in split.series] == [1, 2]
        assert _kinds(split) == [ActionKind.UPDATE, ActionKind.UPDATE]

    def test_separator_rejected_with_range(self) -> None:
        plan = _plan([_keep("1", "a"), _keep("2", "b", pos=2)])
        text = serialize_plan(plan, separators=False)
        rows = text.splitlines()
        assert "---" not in rows
        assert "cannot be split or merged" in text
        with pytest.raises(ValueError):
            parse_plan("\n".join(rows[:-1] + ["---"] + rows[-1:]), plan, separators=False)

    def test_submit_can_become_discard(self) -> None:
        sub = SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "9", "x", "h"), None, None)
        plan = _plan([_keep("1", "a")], gone=[sub])
        text = "\n".join("discard" + l[len("submit"):] if l.startswith("submit") else l
                         for l in serialize_plan(plan).splitlines())
        assert parse_plan(text, plan).gone[0].actions[0].kind is ActionKind.DISCARD

    def test_forget_transitions(self) -> None:
        fg = SyncAction(ActionKind.FORGET, ReviewEntry("s", 1, "9", "x", "h"), None, None)
        plan = _plan([_keep("1", "a")], gone=[fg])
        lines = serialize_plan(plan).splitlines()
        for target in ("skip", "discard", "submit"):
            edited = "\n".join(
                f"{target:<7}" + l[len("forget "):] if l.startswith("forget ") else l
                for l in lines
            )
            assert parse_plan(edited, plan).gone[0].actions[0].kind.value == target

    def test_bad_separator_placement(self) -> None:
        sub = SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "9", "x", "h"), None, None)
        plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)], gone=[sub])
        rows = [l for l in serialize_plan(plan).splitlines() if l and not l.startswith("#")]
        a, sep, b, gone = rows
        for bad in ([sep, a, b, gone], [a, sep, sep, b, gone],
                    [a, sep, b, sep, gone], [a, sep, b, gone, sep]):
            with pytest.raises(ValueError):
                parse_plan("\n".join(bad), plan)

    def test_unchanged_series_keep_actions_and_renumber(self) -> None:
        plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)], [_keep("3", "c", pos=1)])
        text = serialize_plan(plan).replace("\n---\n", "\n", 1)
        edited = parse_plan(text, plan)
        assert [s.number for s in edited.series] == [1, 2]
        assert [a.kind.value for a in edited.series[1].actions] == ["keep"]
        assert edited.series[1].was == 3

    def test_hidden_rows_not_written(self) -> None:
        hidden = _keep("2", "hid", pos=2)
        hidden.outside_range = True
        plan = _plan([_keep("1", "a"), hidden])
        text = serialize_plan(plan, separators=False)
        assert "hid" not in text
        assert parse_plan(text, plan, separators=False).series[0].actions[1] is hidden

    def test_range_round_trip_keeps_series(self) -> None:
        plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)])
        text = serialize_plan(plan, separators=False)
        out = parse_plan(text, plan, separators=False)
        assert [len(s.actions) for s in out.series] == [1, 1]
        assert _kinds(out) == [ActionKind.KEEP, ActionKind.KEEP]

    def test_range_kind_edit_changes_only_that_row(self) -> None:
        plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)])
        lines = serialize_plan(plan, separators=False).splitlines()
        text = "\n".join(
            "update" + l[len("keep"):] if l.startswith("keep ") and l.endswith(" b") else l
            for l in lines
        )
        out = parse_plan(text, plan, separators=False)
        assert [s.number for s in out.series] == [1, 2]
        assert _kinds(out) == [ActionKind.KEEP, ActionKind.UPDATE]
