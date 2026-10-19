"""Interactive editing of rbt-sync plans."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from dataclasses import replace

from gg.matcher import ActionKind, SyncAction
from gg.numbering import number_series
from gg.series import GoneGroup, Series, SeriesPlan

_ACTION_KEYS: list[tuple[str, str]] = [
    ("update", "u"),
    ("keep", "k"),
    ("skip", "s"),
    ("create", "c"),
    ("discard", "d"),
    ("submit", "l"),
    ("forget", "y"),
]

_VIM_NAMES = frozenset({"vi", "vim", "nvim", "gvim", "mvim"})
_EMACS_NAMES = frozenset({"emacs", "emacs-nox", "xemacs", "emacsclient"})

_BASE_HEADER = """\
# gg rbt-sync -- edit the Action column, then save and quit.
# Empty file = abort. Lines starting with # are ignored.
#
# Valid actions: keep, update, create, discard, submit, forget, skip
#   update  -> keep     don't re-post, preserve existing review
#   keep+dep -> keep    skip dependency chain re-post
#   create  -> skip     don't create review (deferred to next sync)
#   discard -> skip     don't close review (deferred to next sync)
#   keep    -> update   force re-post even if diff is unchanged
#   discard <-> submit  close the review as discarded / as submitted
#   submit  -> skip     don't close review (deferred to next sync)
#   forget  -> skip, discard, submit
"""

_SEPARATORS_HEADER = (
    '# "---" lines separate series: delete one to merge, '
    "add one between two rows to split.\n"
)
_NO_SEPARATORS_HEADER = "# Series cannot be split or merged when a range is given.\n"

_SEPARATOR = "---"


def _make_header(editor: str = "", *, separators: bool = True) -> str:
    """Return the plan header, optionally with editor-specific shortcut hints."""
    base = _BASE_HEADER + (_SEPARATORS_HEADER if separators else _NO_SEPARATORS_HEADER)
    base_name = os.path.basename(editor)
    if base_name in _VIM_NAMES:
        shortcuts = "  ".join(f"g{key}={action}" for action, key in _ACTION_KEYS)
        return base + f"# Shortcuts (vim): {shortcuts}\n"
    if base_name in _EMACS_NAMES:
        shortcuts = "  ".join(
            f"C-c {key}={action}" for action, key in _ACTION_KEYS
        )
        return base + f"# Shortcuts (emacs): {shortcuts}\n"
    return base

# Which transitions are allowed from each action kind
_VALID_TRANSITIONS: dict[ActionKind, set[ActionKind]] = {
    ActionKind.KEEP: {ActionKind.KEEP, ActionKind.UPDATE},
    ActionKind.UPDATE: {ActionKind.UPDATE, ActionKind.KEEP},
    ActionKind.KEEP_DEP: {ActionKind.KEEP_DEP, ActionKind.KEEP},
    ActionKind.CREATE: {ActionKind.CREATE, ActionKind.SKIP},
    ActionKind.DISCARD: {ActionKind.DISCARD, ActionKind.SKIP, ActionKind.SUBMIT},
    ActionKind.SUBMIT: {ActionKind.SUBMIT, ActionKind.DISCARD, ActionKind.SKIP},
    ActionKind.FORGET: {
        ActionKind.FORGET, ActionKind.SKIP, ActionKind.DISCARD, ActionKind.SUBMIT,
    },
}


def _build_editor_cmd(editor: str, filepath: str) -> list[str]:
    """Build editor command with editor-specific keybindings injected."""
    base_name = os.path.basename(editor)
    if base_name in _VIM_NAMES:
        cmd = [editor]
        for action, key in _ACTION_KEYS:
            cmd.extend([
                "-c",
                f"nnoremap <buffer> g{key}"
                f" :s/^\\S\\+/{action:<11}/<CR>0",
            ])
        cmd.append(filepath)
        return cmd
    if base_name in _EMACS_NAMES:
        padded = {a: f"{a:<11}" for a, _ in _ACTION_KEYS}
        bindings = " ".join(
            f'(local-set-key (kbd "C-c {key}")'
            f" (lambda () (interactive)"
            f" (beginning-of-line)"
            f' (re-search-forward "\\\\S+" (line-end-position) t)'
            f' (replace-match "{padded[action]}")))'
            for action, key in _ACTION_KEYS
        )
        return [editor, filepath, "--eval", f"(progn {bindings})"]
    return [editor, filepath]


def get_editor() -> str:
    """Return the user's preferred editor."""
    for editor in (os.environ.get("VISUAL"), os.environ.get("EDITOR"), "vi"):
        if editor and shutil.which(editor):
            return editor
    raise RuntimeError(
        "no editor found: set VISUAL or EDITOR to a valid executable"
    )


def _visible(action: SyncAction) -> bool:
    """Rows the plan output shows; the others are not editable."""
    return not action.outside_range or action.kind == ActionKind.KEEP_DEP


def _row(action: SyncAction, num_str: str) -> str:
    review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
    if action.new_commit:
        subject = action.new_commit.subject
    else:
        subject = action.old_entry.subject if action.old_entry else ""
    return f"{action.kind.value:<11}{num_str:<10} {review:<10} {subject}"


def serialize_plan(
    plan: SeriesPlan, *, renumber: bool = False, editor: str = "",
    separators: bool = True,
) -> str:
    """Format the plan as editable text: live series, then gone rows."""
    lines = [_make_header(editor, separators=separators)]

    first = True
    for series in plan.series:
        rows = [
            _row(a, n) for a, n in number_series(series, renumber=renumber)
            if _visible(a)
        ]
        if not rows:
            continue
        if separators and not first:
            lines.append(_SEPARATOR)
        first = False
        lines.extend(rows)

    for group in plan.gone:
        lines.extend(_row(a, "--") for a in group.actions)

    return "\n".join(lines) + "\n"


def parse_plan(
    text: str, plan: SeriesPlan, *, separators: bool = True,
) -> SeriesPlan | None:
    """Parse edited plan text and return the modified plan.

    Returns None if the file is empty (abort).
    Raises ValueError on invalid input.
    """
    lines = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return None

    # Editable rows in file order: visible live rows, then gone rows.
    live_rows = [a for s in plan.series for a in s.actions if _visible(a)]
    originals = live_rows + [a for g in plan.gone for a in g.actions]

    data: list[str] = []
    cuts: list[int] = []         # number of rows before each separator
    for line in lines:
        if line != _SEPARATOR:
            data.append(line)
            continue
        if not separators:
            raise ValueError("Series cannot be split or merged when a range is given")
        if not data:
            raise ValueError("Separator '---' before the first row")
        if cuts and cuts[-1] == len(data):
            raise ValueError("Two separators '---' in a row")
        cuts.append(len(data))

    if len(data) != len(originals):
        raise ValueError(f"Expected {len(originals)} action lines, got {len(data)}")
    if cuts and cuts[-1] >= len(live_rows):
        raise ValueError("Separator '---' after the last series")

    edited = [
        _apply_line(i, line, orig)
        for i, (line, orig) in enumerate(zip(data, originals), 1)
    ]
    new_by_id = {id(o): n for o, n in zip(originals, edited)}

    groups = _regroup(plan, new_by_id, cuts, separators)
    gone = [
        GoneGroup(g.old_number, [new_by_id[id(a)] for a in g.actions])
        for g in plan.gone
    ]
    return SeriesPlan(_rebuild_series(plan, groups), gone, plan.warnings, plan.adopted_from)


def _apply_line(i: int, line: str, orig: SyncAction) -> SyncAction:
    raw_kind = line.split()[0]
    try:
        new_kind = ActionKind(raw_kind)
    except ValueError:
        raise ValueError(f"Line {i}: unknown action '{raw_kind}'")

    allowed = _VALID_TRANSITIONS.get(orig.kind)
    if allowed is None or new_kind not in allowed:
        raise ValueError(
            f"Line {i}: cannot change '{orig.kind.value}' to '{new_kind.value}'"
        )
    if new_kind == orig.kind:
        return orig
    modified = replace(orig, kind=new_kind)
    if orig.kind == ActionKind.KEEP_DEP and new_kind == ActionKind.KEEP:
        modified.needs_dep_update = False
    return modified


def _regroup(
    plan: SeriesPlan, new_by_id: dict[int, SyncAction], cuts: list[int],
    separators: bool,
) -> list[list[SyncAction]]:
    """Group the rows into series.

    Without separators membership never changes: each original series is
    one group. Otherwise the groups are cut at the separators and hidden
    rows join the row before.
    """
    if not separators:
        return [[new_by_id.get(id(a), a) for a in s.actions] for s in plan.series]
    groups: list[list[SyncAction]] = [[]]
    seen = 0
    for s in plan.series:
        for a in s.actions:
            if _visible(a):
                if seen in cuts:
                    groups.append([])
                seen += 1
            groups[-1].append(new_by_id.get(id(a), a))
    return [g for g in groups if g]


def _rebuild_series(plan: SeriesPlan, groups: list[list[SyncAction]]) -> list[Series]:
    """Number the groups 1..K.

    A group that is not exactly one of the original series is renumbered
    plain 1..N and its kept rows become updates; it takes the origin and
    `new` flag of its first member's original series.
    """
    flat_old = [(s, a) for s in plan.series for a in s.actions]
    flat_new = [a for g in groups for a in g]
    owner = {id(n): s for (s, _), n in zip(flat_old, flat_new)}
    first_in_old = {
        id(n) for (s, o), n in zip(flat_old, flat_new) if o is s.actions[0]
    }
    prev_review: dict[int, str | None] = {}
    prev: SyncAction | None = None
    for a in flat_new:
        prev_review[id(a)] = prev.old_entry.review_id if prev and prev.old_entry else None
        prev = a

    result: list[Series] = []
    for number, group in enumerate(groups, 1):
        src = owner[id(group[0])]
        if len(group) == len(src.actions) and all(owner[id(a)] is src for a in group):
            was = src.number if src.number != number else src.was
            result.append(replace(src, number=number, was=was, actions=group))
            continue
        actions = []
        for place, a in enumerate(group, 1):
            if a.kind in (ActionKind.KEEP, ActionKind.KEEP_DEP) and not a.outside_range:
                a = replace(a, kind=ActionKind.UPDATE, needs_dep_update=False)
            if a.old_entry is not None:
                a = replace(a, old_entry=replace(a.old_entry, position=place))
            a.new_position = place
            actions.append(a)
        first = group[0]
        result.append(Series(
            number=number,
            actions=actions,
            origin=src.origin,
            was=src.number if src.number != number else None,
            new=src.new,
            depends_on=src.depends_on if id(first) in first_in_old else prev_review[id(first)],
        ))
    return result


def edit_plan(
    plan: SeriesPlan, *, renumber: bool = False, separators: bool = True,
) -> SeriesPlan | None:
    """Open editor for the user to modify the sync plan.

    Returns the modified plan, or None if the user aborted (empty file).
    Raises ValueError on parse errors.
    """
    editor = get_editor()
    text = serialize_plan(
        plan, renumber=renumber, editor=editor, separators=separators,
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".gg-sync", prefix="rbt-sync-", delete=False,
    ) as f:
        f.write(text)
        tmpfile = f.name

    try:
        subprocess.run(_build_editor_cmd(editor, tmpfile), check=True)
        edited = open(tmpfile).read()
    finally:
        os.unlink(tmpfile)

    return parse_plan(edited, plan, separators=separators)
