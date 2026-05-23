# `gg rbt-sync` auto-falls into `--new` when `reviews.db` is empty

**Status:** approved
**Date:** 2026-05-23

## Problem

`gg rbt-sync` errors out on a branch that has nothing recorded in
`reviews.db`:

```
$ gg rbt-sync
No existing reviews found. Use `gg rbt` to post the initial series.
$ echo $?
1
```

This forces a two-command mental model — `gg rbt` for the first post,
`gg rbt-sync` for subsequent reconciliation. There's no real reason
for it: `reconcile([], new_commits)` already produces a sensible plan
(every commit becomes a `CREATE`), and `--new` already exposes
exactly that behaviour.

## Goal

When `gg rbt-sync` finds an empty `reviews.db` for the current
branch, treat the run as if `--new` were passed and emit a one-line
stderr notice so the user sees what happened.

`--new` remains useful when `reviews.db` **is** populated: it lets
the user explicitly forget the existing series and post a fresh one.

## Non-goals

- Deprecating `gg rbt`. It still has legitimate uses (single-commit
  posting without numbering, the `-C/--continue-from` workflow, etc.)
  and the test suite for it is unchanged.
- Changing `--close` behaviour. Its early-return guard
  (`if not old: print("No reviews to close.")`) is independent of
  this change and stays in place — closing requires something to
  close.
- Silencing the notice. We always print it on the auto path so the
  user sees that fresh-series semantics kicked in.

## Surface

After the change:

| reviews.db state | `gg rbt-sync` | `gg rbt-sync --new` |
|------------------|---------------|----------------------|
| Empty            | Auto-new + stderr notice | Same plan; no notice (already explicit) |
| Populated        | Reconcile against existing | Drop existing, post fresh |

Stderr line on the auto path:

```
[gg] No existing reviews; posting as a fresh series.
```

## Implementation

`src/gg/sync.py` — replace the current guard:

```python
old = review_store.load_reviews(branch_name, cwd=cwd)
if not old and not args.new:
    print("No existing reviews found. Use `gg rbt` to post the initial series.")
    return 1
```

with:

```python
old = review_store.load_reviews(branch_name, cwd=cwd)
if not old and not args.new:
    print("[gg] No existing reviews; posting as a fresh series.", file=sys.stderr)
    args.new = True
```

The downstream `reconcile([] if args.new else old, new)` already
produces a `CREATE` action for every commit when `old` is empty.
Nothing else needs to change in `_execute()` or in the plan
formatter.

## Testing

Three tests in `tests/test_rbt_sync.py`:

1. **`test_no_existing_reviews_auto_new`** (rename of the existing
   `test_no_existing_reviews_errors`):
   - Create a branch with one commit, do NOT post anything.
   - Run `gg rbt-sync -d`.
   - Assert exit 0, plan contains `create`, stderr contains
     `No existing reviews; posting as a fresh series`.

2. **`test_auto_new_executes`** (non-dry):
   - Create a branch with two commits, do NOT post anything.
   - Run `gg rbt-sync` (no `--new`).
   - Assert exit 0, two `rbt post` calls in the mock log,
     `reviews.db` has two entries afterwards.

3. **`test_explicit_new_when_db_populated`** (regression — no
   stderr notice on the explicit path):
   - Create a branch with two commits, post via `gg rbt`.
   - Run `gg rbt-sync --new`.
   - Assert exit 0, behaviour same as today, stderr does NOT contain
     `No existing reviews; posting as a fresh series` (notice is only
     for the auto path).

## Migration

- Breaking change in the strict sense: `gg rbt-sync` no longer exits
  1 when the DB is empty. Anyone scripting on the old failure mode
  needs to switch to checking the actual return value of the work
  (zero on success, plus whatever they were really trying to detect).
  In practice nobody is doing this; the failure was a usability
  speed-bump, not a documented contract.
- Documentation: update README's "Syncing a modified series" section
  to mention that the first run is the same command, and drop the
  "first post with `gg rbt`" two-step language from the
  Workflow-pattern section in `CLAUDE.md`.
