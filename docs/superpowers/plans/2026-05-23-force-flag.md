# `--force` flag for `gg rbt-sync` and `gg rbt -u` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `-f/--force` flag to both `gg rbt-sync` and `gg rbt` so users can force-re-post a whole series without manually invalidating caches.

**Architecture:** `gg rbt-sync -f` converts every `KEEP` / `KEEP_DEP` action into `UPDATE` after `reconcile()` and after the interactive editor, so the existing `_execute()` UPDATE branch re-posts each commit via `rbt post -r <id>`. `gg rbt -f` (only meaningful with `-u`) bypasses the diff-hash cache by loading it as an empty set; `-f` without `-u` is a hard error.

**Tech Stack:** Python 3.13, argparse, existing `gg.matcher`/`gg.sync_plan` modules, pytest.

**Spec:** `docs/superpowers/specs/2026-05-23-force-flag-design.md`.

---

## File Structure

- **Modify**
  - `src/gg/sync.py` — argparse flag + KEEP→UPDATE conversion
  - `src/gg/sync_plan.py` — `format_plan` gains a `force=False` kwarg and emits a `Force: yes` header line
  - `src/gg/rbt.py` — argparse flag + `-f` requires `-u` validation + cache-load bypass
  - `tests/test_rbt_sync.py` — `TestForceFlag` (4 integration tests)
  - `tests/test_gorbt.py` — `TestForceFlag` (3 integration tests)
  - `README.md` — document the new flag

Each existing module already has one clear purpose; this plan extends them rather than splitting anything. No new files.

---

## Task 1: `gg rbt-sync --force`

**Files:**
- Modify: `src/gg/sync.py`
- Modify: `src/gg/sync_plan.py`
- Modify: `tests/test_rbt_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rbt_sync.py`:

```python
class TestForceFlag:
    def test_force_converts_keep_to_update(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync", "-f")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial:]
        post_calls = [c for c in new_calls if c and c[0] == "post"]
        # Both kept commits get re-posted with -r <id>
        assert len(post_calls) == 2
        for c in post_calls:
            assert any(arg == "-r" for arg in c), c

    def test_force_keeps_create_and_discard(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("to drop")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        # Drop the second commit; add a new one
        git_repo.git("reset", "--hard", "HEAD~1")
        git_repo.commit("new feature")

        r = git_repo.run_gg("rbt-sync", "-f")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial:]
        post_calls = [c for c in new_calls if c and c[0] == "post"]
        close_calls = [c for c in new_calls if c and c[0] == "close"]
        # 1 forced update (the kept "fix crash") + 1 create (the new commit)
        assert len(post_calls) == 2
        # 1 discard of the dropped review
        assert len(close_calls) == 1

    def test_force_with_publish_publishes_each(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")
        _post_series(git_repo)
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("rbt-sync", "-f", "-p")
        assert r.returncode == 0

        new_calls = rbt_mock.calls()[initial:]
        post_calls = [c for c in new_calls if c and c[0] == "post"]
        publish_calls = [c for c in new_calls if c and c[0] == "publish"]
        # 2 forced re-posts, each with -p
        assert len(post_calls) == 2
        for c in post_calls:
            assert "-p" in c, c
        # No separate rbt publish calls (UPDATE+-p covers it)
        assert publish_calls == []

    def test_force_header_appears_in_plan(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)
        r = git_repo.run_gg("rbt-sync", "-f", "-d")
        assert r.returncode == 0
        assert "Force: yes" in r.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rbt_sync.py::TestForceFlag -v`
Expected: 4 failures — `unrecognized arguments: -f`.

- [ ] **Step 3: Add `-f/--force` to the sync argparse + conversion logic**

In `src/gg/sync.py`, find the `add_parser` function and add the flag near the other flags (alongside `--renumber`):

```python
    p.add_argument("-f", "--force", action="store_true",
                   help="re-post every matched commit, ignoring diff hash")
```

In the `run()` function, after the `if args.interactive:` / `edit_plan(...)` block and **before** the `# Show plan` / `format_plan` block, add:

```python
    # --force: re-post every matched commit, ignoring diff hash.
    # Runs AFTER the interactive editor so the user can still skip
    # individual entries from the plan.
    if args.force:
        for a in actions:
            if a.kind in (ActionKind.KEEP, ActionKind.KEEP_DEP):
                a.kind = ActionKind.UPDATE
                a.needs_dep_update = False
```

Then pass `force=args.force` into the `format_plan(...)` call. Find the existing call:

```python
    plan = format_plan(
        actions, renumber=args.renumber, publish=args.publish,
        reviewers=args.users, groups=args.groups,
    )
```

Change to:

```python
    plan = format_plan(
        actions, renumber=args.renumber, publish=args.publish,
        reviewers=args.users, groups=args.groups,
        force=args.force,
    )
```

- [ ] **Step 4: Extend `format_plan` to accept and render `force`**

In `src/gg/sync_plan.py`, change the `format_plan` signature to accept `force`:

```python
def format_plan(
    actions: list[SyncAction],
    *,
    renumber: bool = False,
    publish: bool = False,
    reviewers: list[str] | None = None,
    groups: list[str] | None = None,
    force: bool = False,
) -> str:
```

After the existing reviewer/group header lines are appended, but before the table header, insert a `Force: yes` line when `force` is set. Find this block:

```python
    lines = _format_reviewer_header(reviewers or [], groups or [])
    lines.append(header)
    lines.append("-" * len(header))
```

Change to:

```python
    lines = _format_reviewer_header(reviewers or [], groups or [])
    if force:
        lines.append("Force: yes")
        lines.append("")
    lines.append(header)
    lines.append("-" * len(header))
```

- [ ] **Step 5: Run the tests and verify they pass**

Run: `uv run pytest tests/test_rbt_sync.py::TestForceFlag -v`
Expected: 4 passed.

Then: `uv run pytest tests/ -v`
Expected: full suite green (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/gg/sync.py src/gg/sync_plan.py tests/test_rbt_sync.py
git commit -m "feat(rbt-sync): --force flag to re-post every matched commit"
```

---

## Task 2: `gg rbt --force`

**Files:**
- Modify: `src/gg/rbt.py`
- Modify: `tests/test_gorbt.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gorbt.py`:

```python
class TestForceFlag:
    def test_force_without_update_errors(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        r = git_repo.run_gg("rbt", "-f")
        assert r.returncode == 1
        assert "--force requires --update" in (r.stdout + r.stderr)

    def test_force_ignores_cache_and_posts_all(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.commit("BUG-2: second")
        git_repo.run_gg("rbt")
        initial = rbt_mock.call_count()
        assert initial == 2

        # Without -f, -u would skip both as unchanged. With -f, both
        # post again via --update.
        r = git_repo.run_gg("rbt", "-u", "-f")
        assert r.returncode == 0
        new_calls = rbt_mock.calls()[initial:]
        assert len(new_calls) == 2
        for c in new_calls:
            assert "--update" in c, c

    def test_force_with_publish(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("BUG-1: first")
        git_repo.run_gg("rbt")
        initial = rbt_mock.call_count()

        r = git_repo.run_gg("rbt", "-u", "-f", "-p")
        assert r.returncode == 0
        new_calls = rbt_mock.calls()[initial:]
        assert len(new_calls) == 1
        c = new_calls[0]
        assert "--update" in c
        assert "-p" in c
        # No separate rbt publish invocations
        publish_calls = [x for x in new_calls if x and x[0] == "publish"]
        assert publish_calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gorbt.py::TestForceFlag -v`
Expected: 3 failures — `unrecognized arguments: -f`.

- [ ] **Step 3: Add `-f/--force` to `gg rbt` argparse + cache bypass**

In `src/gg/rbt.py`, in `add_parser`, add the flag near `-u/--update`:

```python
    p.add_argument("-f", "--force", action="store_true",
                   help="re-post every commit, ignoring the diff-hash cache (requires -u)")
```

In `run()`, immediately after `args = ...` parsing (or at the very top of `run()`), add the validation. Find this block at the start of `run()`:

```python
def run(args: argparse.Namespace) -> int:
    """Execute the rbt subcommand."""
    cwd = Path.cwd()
    first_post = not args.update
    show_progress = args.progress or args.verbose
```

Add right after `show_progress = ...`:

```python
    if args.force and not args.update:
        print("[gg] --force requires --update", file=sys.stderr)
        return 1
```

Then find the cache-load line:

```python
    cached = diff_cache.load_hashes(cwd=cwd, branch=branch_name) if args.update else set()
```

Change to:

```python
    cached = (
        diff_cache.load_hashes(cwd=cwd, branch=branch_name)
        if (args.update and not args.force)
        else set()
    )
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_gorbt.py::TestForceFlag -v`
Expected: 3 passed.

Then: `uv run pytest tests/ -v`
Expected: full suite green (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/gg/rbt.py tests/test_gorbt.py
git commit -m "feat(rbt): --force flag to re-post every commit ignoring cache"
```

---

## Task 3: Document the flag in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the flag to the ReviewBoard command table**

In `README.md`, find the existing row for `git gg rbt-sync --renumber`:

```
| `git gg rbt-sync --renumber` | Full `[1/N]..[N/N]` renumber instead of fractional |
```

Insert a new row right after it:

```
| `git gg rbt-sync -f` | Re-post every matched commit, ignoring the diff-hash cache |
```

- [ ] **Step 2: Mention `-f` in the workflow example**

Find the "Syncing a modified series" section, look for the block that lists the various rbt-sync flags. After the `--renumber` example block (`# Force a full renumber -- re-posts every matched commit with its new [i/N] prefix` ... `git gg rbt-sync --renumber`), add:

```shell
# Re-post every matched commit even if nothing changed -- e.g. after
# manual edits to a draft on the RB web UI, or to recover from a
# cache that's gone stale.
git gg rbt-sync -f

# Or on the older command: bypass the diff-hash cache
git gg rbt -u -f
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document --force flag for rbt-sync and rbt -u"
```
