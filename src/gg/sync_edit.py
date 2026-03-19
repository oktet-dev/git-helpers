"""Interactive editing of rbt-sync plans."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from gg.matcher import ActionKind, SyncAction
from gg.numbering import assign_numbers

_ACTION_KEYS: list[tuple[str, str]] = [
    ("update", "u"),
    ("keep", "k"),
    ("skip", "s"),
    ("create", "c"),
    ("discard", "d"),
]

_VIM_NAMES = frozenset({"vi", "vim", "nvim", "gvim", "mvim"})
_EMACS_NAMES = frozenset({"emacs", "emacs-nox", "xemacs", "emacsclient"})

_BASE_HEADER = """\
# gg rbt-sync -- edit the Action column, then save and quit.
# Empty file = abort. Lines starting with # are ignored.
#
# Valid actions: keep, update, create, discard, skip
#   update  -> keep     don't re-post, preserve existing review
#   keep+dep -> keep    skip dependency chain re-post
#   create  -> skip     don't create review (deferred to next sync)
#   discard -> skip     don't close review (deferred to next sync)
#   keep    -> update   force re-post even if diff is unchanged
"""


def _make_header(editor: str = "") -> str:
    """Return the plan header, optionally with editor-specific shortcut hints."""
    base_name = os.path.basename(editor)
    if base_name in _VIM_NAMES:
        shortcuts = "  ".join(f"g{key}={action}" for action, key in _ACTION_KEYS)
        return _BASE_HEADER + f"# Shortcuts (vim): {shortcuts}\n"
    if base_name in _EMACS_NAMES:
        shortcuts = "  ".join(
            f"C-c {key}={action}" for action, key in _ACTION_KEYS
        )
        return _BASE_HEADER + f"# Shortcuts (emacs): {shortcuts}\n"
    return _BASE_HEADER


_HEADER = _make_header("")

# Which transitions are allowed from each action kind
_VALID_TRANSITIONS: dict[ActionKind, set[ActionKind]] = {
    ActionKind.KEEP: {ActionKind.KEEP, ActionKind.UPDATE},
    ActionKind.UPDATE: {ActionKind.UPDATE, ActionKind.KEEP},
    ActionKind.KEEP_DEP: {ActionKind.KEEP_DEP, ActionKind.KEEP},
    ActionKind.CREATE: {ActionKind.CREATE, ActionKind.SKIP},
    ActionKind.DISCARD: {ActionKind.DISCARD, ActionKind.SKIP},
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


def serialize_plan(
    actions: list[SyncAction], *, renumber: bool = False, editor: str = "",
) -> str:
    """Format actions as editable text."""
    numbered = assign_numbers(actions, renumber=renumber)
    lines = [_make_header(editor)]

    for action, num_str in numbered:
        kind_label = action.kind.value
        if action.kind == ActionKind.DISCARD:
            review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
            subject = action.old_entry.subject if action.old_entry else ""
        else:
            review = f"r/{action.old_entry.review_id}" if action.old_entry else "--"
            subject = action.new_commit.subject if action.new_commit else ""

        lines.append(f"{kind_label:<11}{num_str:<10} {review:<10} {subject}")

    return "\n".join(lines) + "\n"


def parse_plan(
    text: str, original_actions: list[SyncAction],
) -> list[SyncAction] | None:
    """Parse edited plan text and return modified actions.

    Returns None if the file is empty (abort).
    Raises ValueError on invalid input.
    """
    data_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not data_lines:
        return None

    if len(data_lines) != len(original_actions):
        raise ValueError(
            f"Expected {len(original_actions)} action lines, got {len(data_lines)}"
        )

    result: list[SyncAction] = []
    for i, (line, orig) in enumerate(zip(data_lines, original_actions), 1):
        tokens = line.split()
        if not tokens:
            raise ValueError(f"Line {i}: empty action")

        raw_kind = tokens[0].strip()
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
            result.append(orig)
            continue

        # Apply transition
        modified = SyncAction(
            kind=new_kind,
            old_entry=orig.old_entry,
            new_commit=orig.new_commit,
            new_position=orig.new_position,
            needs_dep_update=orig.needs_dep_update,
        )

        if orig.kind == ActionKind.KEEP_DEP and new_kind == ActionKind.KEEP:
            modified.needs_dep_update = False

        result.append(modified)

    return result


def edit_plan(
    actions: list[SyncAction], *, renumber: bool = False,
) -> list[SyncAction] | None:
    """Open editor for the user to modify the sync plan.

    Returns modified actions, or None if the user aborted (empty file).
    Raises ValueError on parse errors.
    """
    editor = get_editor()
    text = serialize_plan(actions, renumber=renumber, editor=editor)

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

    return parse_plan(edited, actions)
