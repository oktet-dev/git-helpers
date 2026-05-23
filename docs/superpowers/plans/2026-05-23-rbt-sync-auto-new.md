# `rbt-sync` auto-new on empty `reviews.db` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gg rbt-sync` post a fresh series when `reviews.db` is empty for the current branch instead of erroring out, with a one-line stderr notice.

**Architecture:** Replace the "no existing reviews, exit 1" guard in `src/gg/sync.py:run()` with `args.new = True` plus a stderr notice. The downstream `reconcile([] if args.new else old, new)` already produces a CREATE-everything plan when `old` is empty.

**Tech Stack:** Python 3.13, existing `gg.sync` / `gg.matcher` modules, pytest.

**Spec:** `docs/superpowers/specs/2026-05-23-rbt-sync-auto-new-design.md`.

---

## File Structure

- **Modify**
  - `src/gg/sync.py` — replace the empty-DB guard
  - `tests/test_rbt_sync.py` — rename one test + add two
  - `README.md` — one-line clarification in "Syncing a modified series"

`gg rbt` (`src/gg/rbt.py`) is intentionally untouched — it still has legitimate uses (single-commit no-numbering, `-C/--continue-from` resumption).

---

## Task 1: Auto-new in `sync.py`

**Files:**
- Modify: `src/gg/sync.py:232-235`
- Modify: `tests/test_rbt_sync.py`

- [ ] **Step 1: Replace the existing failing-test, write the new ones**

In `tests/test_rbt_sync.py`, find the existing `test_no_existing_reviews_errors`:

```python
    def test_no_existing_reviews_errors(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 1
        assert "No existing reviews" in r.stdout
```

Replace it with three tests (the test belongs in `class TestSyncExecution` where the original sits):

```python
    def test_no_existing_reviews_auto_new(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Empty reviews.db: `gg rbt-sync -d` auto-falls into --new."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        r = git_repo.run_gg("rbt-sync", "-d")
        assert r.returncode == 0
        # Plan shows the commit as a CREATE
        assert "create" in r.stdout
        # Stderr notice on the auto path
        assert "No existing reviews; posting as a fresh series" in r.stderr

    def test_auto_new_executes(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """Empty reviews.db: non-dry `gg rbt-sync` posts every commit."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        git_repo.commit("add tests")

        r = git_repo.run_gg("rbt-sync")
        assert r.returncode == 0

        post_calls = [c for c in rbt_mock.calls() if c and c[0] == "post"]
        assert len(post_calls) == 2

        # reviews.db now has two entries
        from gg import review_store
        entries = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert len(entries) == 2

    def test_explicit_new_no_auto_notice(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        """`--new` with populated DB does not emit the auto-new notice."""
        git_repo.create_branch("feature", "master")
        git_repo.commit("fix crash")
        _post_series(git_repo)

        # Add one more commit so the explicit --new has something to replace
        git_repo.commit("new feature")
        r = git_repo.run_gg("rbt-sync", "--new", "-d")
        assert r.returncode == 0
        assert "No existing reviews; posting as a fresh series" not in r.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rbt_sync.py::TestSyncExecution::test_no_existing_reviews_auto_new tests/test_rbt_sync.py::TestSyncExecution::test_auto_new_executes tests/test_rbt_sync.py::TestSyncExecution::test_explicit_new_no_auto_notice -v`

Expected:
- `test_no_existing_reviews_auto_new` FAILS: `returncode == 1`, error text `No existing reviews found` is in stdout (not stderr) and the stderr notice doesn't exist yet.
- `test_auto_new_executes` FAILS: `returncode == 1`, no post calls happen.
- `test_explicit_new_no_auto_notice` likely PASSES already (the notice doesn't exist yet, so `not in r.stderr` is vacuously true). That's expected.

- [ ] **Step 3: Replace the guard in `sync.py`**

In `src/gg/sync.py`, find lines 232-235:

```python
    old = review_store.load_reviews(branch_name, cwd=cwd)
    if not old and not args.new:
        print("No existing reviews found. Use `gg rbt` to post the initial series.")
        return 1
```

Replace with:

```python
    old = review_store.load_reviews(branch_name, cwd=cwd)
    if not old and not args.new:
        print(
            "[gg] No existing reviews; posting as a fresh series.",
            file=sys.stderr,
        )
        args.new = True
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `uv run pytest tests/test_rbt_sync.py -v`
Expected: 3 new tests pass; full file passes (no regressions in the rest of `test_rbt_sync.py`).

Then: `uv run pytest tests/ -v`
Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/gg/sync.py tests/test_rbt_sync.py
git commit -m "feat(rbt-sync): auto-fall into --new on empty reviews.db"
```

---

## Task 2: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Note the auto-new behavior in the README**

In `README.md`, find the "Syncing a modified series" section. Currently it begins:

```
### Syncing a modified series

After posting a multi-patch series, you amend/reorder/add/drop commits
or just edit commit messages and want to update ReviewBoard to match.
A commit whose subject or body changed is re-posted even if the diff
is unchanged, so RB's summary and description track the git commit:
```

Replace that opening paragraph with:

```
### Syncing a modified series

`gg rbt-sync` is the everyday command. The first run on a branch
posts the series fresh (it auto-detects an empty `reviews.db` and
prints `[gg] No existing reviews; posting as a fresh series.`).
Subsequent runs reconcile the current commits against the last
posted set — amend/reorder/add/drop commits and re-run to update
ReviewBoard to match. A commit whose subject or body changed is
re-posted even if the diff is unchanged, so RB's summary and
description track the git commit:
```

(The shell-block examples below this paragraph stay as-is.)

- [ ] **Step 2: Drop the "gg rbt or" from CLAUDE.md's workflow pattern**

In `CLAUDE.md`, find the "Workflow pattern" line:

```
`gowork` creates a tracking branch -> make commits -> `gg rbt` or `gg rbt-sync` (or `gopr`) for review -> `gopull` to rebase -> `gopush` to land -> `goclose` to clean up.
```

Replace with:

```
`gowork` creates a tracking branch -> make commits -> `gg rbt-sync` (or `gopr`) for review -> `gopull` to rebase -> `gopush` to land -> `goclose` to clean up.
```

(`gg rbt` is still available for the single-commit and `--continue-from` workflows, but `gg rbt-sync` is now the default first-post path.)

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: note rbt-sync auto-new on first run"
```
