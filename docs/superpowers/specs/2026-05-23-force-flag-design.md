# `--force` flag for `gg rbt-sync` and `gg rbt -u`

**Status:** approved
**Date:** 2026-05-23

## Problem

Both `gg rbt-sync` and `gg rbt -u` shortcut their work for commits that
look unchanged:

- `gg rbt -u` skips any commit whose diff hash matches the cache.
- `gg rbt-sync` marks any matched commit as `KEEP` (or `KEEP_DEP`), and
  the executor preserves the existing review entry without invoking
  `rbt post`.

There is no clean way to say "I know nothing changed, re-post the
whole series anyway." Today's workarounds are crude: `gg db --clear`
plus a fresh `gg rbt` (loses review IDs); or hand-amend each commit
with an empty change to fool the diff hash.

## Goals

1. Add `-f/--force` to both commands.
2. On `gg rbt-sync -f`: convert every `KEEP` / `KEEP_DEP` action into
   `UPDATE` so the existing review request gets a fresh `rbt post -r
   <id>` invocation. Preserve existing review IDs and the dependency
   chain.
3. On `gg rbt -u -f`: ignore the diff-hash cache; every commit posts
   via `--update`.
4. Make the flag visible in the dry-run / interactive plan so the user
   sees it's active.

## Non-goals

- Forcing a fresh series (new review IDs). That's what `gg rbt-sync
  --new` already does.
- A separate flag on `gg rbt` without `-u` (force-creating a series
  that doesn't exist isn't a coherent operation).
- A "force" mode for `gg publish` — it already publishes
  unconditionally; nothing to force.

## Surface

### `gg rbt-sync`

```
-f, --force          re-post every matched commit, ignoring diff hash
```

When set, `KEEP` and `KEEP_DEP` actions are converted to `UPDATE`
after `reconcile()` and after the interactive editor (`-i`) — so the
user can still skip individual entries from the editor. `CREATE`,
`DISCARD`, and `SKIP` actions are untouched.

The plan header gains a `force: yes` marker (parallel to existing
`publish: yes` and `renumber: yes` lines).

Combined with `--renumber`: orthogonal. `--force` re-posts every
matched commit unconditionally; `--renumber`'s "re-post if prefix
changed" rule is then redundant for matched commits but still drives
the numbering format.

Combined with `--publish` (`-p`): each forced re-post lands with `-p`
on the `rbt post` invocation, so the result is also published. No
separate `rbt publish` call is needed for these commits (the
post+publish bugfix shortcut for unchanged drafts is bypassed because
nothing is "unchanged" once forced).

### `gg rbt`

```
-f, --force          re-post every commit, ignoring diff-hash cache
                     (requires -u)
```

`-f` without `-u` is a hard error:

```
[gg] --force requires --update
```

(stderr, exit 1). Reason: forcing a fresh post isn't meaningful;
that's just `gg rbt` without `-u`.

When `-u -f` is set, `cached` is loaded as the empty set, so
`_is_unchanged()` always returns False. Every commit follows the
existing "posting" path with `--update --guess-description=yes`.

## Implementation

### `src/gg/sync.py`

1. Add `-f/--force` to the argparse subparser.
2. Plumb `args.force` into the conversion step in `run()`:

   ```python
   if args.force:
       for a in actions:
           if a.kind in (ActionKind.KEEP, ActionKind.KEEP_DEP):
               a.kind = ActionKind.UPDATE
               a.needs_dep_update = False
   ```

   This block runs **after** `reconcile()` and **after** the
   `edit_plan()` call (interactive mode), and **before**
   `format_plan()` so the displayed plan accurately shows UPDATEs.

3. Plumb `force=args.force` into `format_plan()` so the header shows
   the flag.

`_execute()` needs no changes — the UPDATE branch already invokes
`post_one(..., review_id=...)` with `--depends-on` chaining.

### `src/gg/sync_plan.py`

`format_plan` gains a `force: bool = False` keyword parameter. The
header line for force renders the same way as `publish: yes` /
`renumber: yes`.

### `src/gg/rbt.py`

1. Add `-f/--force` to the argparse subparser.
2. Add the validation:

   ```python
   if args.force and not args.update:
       print("[gg] --force requires --update", file=sys.stderr)
       return 1
   ```

3. Modify the cache load:

   ```python
   cached = (
       diff_cache.load_hashes(cwd=cwd, branch=branch_name)
       if (args.update and not args.force)
       else set()
   )
   ```

The `hash_to_id` map (added in the publish-unchanged-drafts work)
stays — but it's never consulted under `-f` because no commit lands
in the "unchanged" branch.

## Testing

### `tests/test_rbt_sync.py` — new `TestForceFlag`, 4 tests

- `test_force_converts_keep_to_update`: post 2 commits, no edits,
  `gg rbt-sync -f` → 2 `rbt post -r` calls.
- `test_force_keeps_create_and_discard`: post 2, drop one, add one,
  `gg rbt-sync -f` → 1 update + 1 close + 1 create (force does not
  touch CREATE/DISCARD).
- `test_force_with_publish_publishes_each`: `gg rbt-sync -f -p` → 2
  `rbt post -r -p` calls; no `rbt publish` invocations (the
  unchanged-draft shortcut isn't taken when force converts to UPDATE).
- `test_force_with_interactive_skip`: `gg rbt-sync -f -i` with an
  editor that comments out one patch → 1 update + 1 skip. Confirms
  the conversion ordering with interactive mode (conversion runs
  after editor).

### `tests/test_gorbt.py` — new `TestForceFlag`, 3 tests

- `test_force_without_update_errors`: `gg rbt -f` (no `-u`) → exit 1,
  stderr contains `--force requires --update`.
- `test_force_ignores_cache_and_posts_all`: post 2 commits, then
  `gg rbt -u -f` → 2 new `rbt post --update` calls, no skips.
- `test_force_with_publish`: `gg rbt -u -f -p` → 2 calls each with
  `-p` flag.

### No new unit tests for the conversion

The conversion is 4 lines (the `if args.force` block in `sync.py`).
The integration tests above exercise it through the full CLI; a
separate unit test would only duplicate them. YAGNI.

## Migration

No CLI breakage. No state-file changes. The flag is opt-in; default
behavior matches today.
