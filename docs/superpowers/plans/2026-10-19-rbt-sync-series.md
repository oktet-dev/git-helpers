# `rbt-sync`: a branch as a list of patch series — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `gg rbt-sync` treats a branch as an ordered list of patch series, each its own ReviewBoard chain numbered `[i/N]`, picks up series recorded under other branches, and closes landed reviews as submitted.

**Architecture:** A new pure module `src/gg/series.py` turns (own record, other branches' records, the branch's commits, the range) into a `SeriesPlan`, using the existing `reconcile()` as the matcher. `sync.py` gathers inputs from git / `reviews.db` / ReviewBoard, prints the plan via `sync_plan.py`, executes it series by series, and saves the record with a new `series` column. `sync_edit.py` gains series separators.

**Tech Stack:** Python 3.13, sqlite3, pytest, the existing `rbt` mock in `tests/conftest.py`.

**Spec:** `docs/superpowers/specs/2026-10-19-rbt-sync-series-design.md` (approved). Read it before any task.

## Global Constraints

- `reviews` gets `series INTEGER NOT NULL DEFAULT 1`, added with `ALTER TABLE` when the column is missing. Existing rows become series 1.
- `position` keeps its meaning: order along the whole branch, 1..M. Primary key `(branch, position)` unchanged.
- Never discard, submit, re-post or renumber a commit because it is outside the given range. The only out-of-range post is `keep+dep`.
- "Gone" is decided against the full branch (`git.rev_range()`), never against the given range.
- Never close a review that another branch still existing in git records (`forget` instead). `--close` is exempt.
- No ReviewBoard status checks. The only new ReviewBoard read is one `rbt api-get` per adopted series (also under `-d`).
- `gg rbt` and `gg rbt-import` are unchanged (they save series 1).
- Landed detection window: the last **500** commits of the upstream ref (`git.range_base()`).
- Plan header text, exactly: `Series {k}: {n} patch|patches[ (was series {w})][, from '{branch}'][, new][, depends on r/{id}][, outside range]`; gone header: `Series {k}: {n} patch|patches, landed in {upstream}` when every row is `submit`, else `Series {k}: {n} patch|patches, gone from the branch`.
- Python style: `from __future__ import annotations`, type hints, matching surrounding code. Commit messages: no AI/session trailers.
- Test command (the user's `/tmp` is often full): `T=$HOME/.cache/gg-pytest-tmp; mkdir -p $T; TMPDIR=$T uv run pytest -p no:cacheprovider --basetemp=$T/base <tests>`; written below as `PYTEST <tests>`.

## Review Focus

1. A range containing commits that are not on the branch (`other..X`): refuse with a message, post nothing. Test: Task 8 `test_range_off_branch_refused`.
2. Two commits with the same subject where that subject is recorded on another branch: the review is adopted once; the second commit is new. Test: Task 5 `test_duplicate_subject_adopted_once`.
3. A commit outside the range whose diff changed: stays `keep`, and the record keeps the old hash so a later full run shows `update`. Test: Task 8 `test_out_of_range_change_detected_later`.
4. `-f` together with a range: out-of-range commits are not re-posted. Test: Task 8 `test_force_respects_range`.
5. `-d` must not change `reviews.db` (no adoption saved, no source-branch cleanup). Test: Task 8 `test_dry_run_leaves_db_alone`.

---

## File Structure

- **Create** `src/gg/series.py` — `Series`, `GoneGroup`, `SeriesPlan`, `build_plan()`: adoption, series membership, range handling, gone classification, dependency marking. Pure: all git/DB/RB access is injected.
- **Create** `tests/test_series.py`, `tests/test_sync_plan.py`.
- **Modify** `src/gg/review_store.py` (series column), `src/gg/git.py` (`branch_exists`, `upstream_subjects`), `src/gg/rb_api.py` (`fetch_depends_on`), `src/gg/matcher.py` (new kinds, flags, `mark_deps`), `src/gg/numbering.py` (`number_series`), `src/gg/sync_plan.py` (series output), `src/gg/sync.py` (wiring, execute, save), `src/gg/sync_edit.py` (separators), `src/gg/db.py` (show series), `tests/conftest.py` (mock depends-on), `README.md`.

---

### Task 1: `series` column in `reviews.db`

**Files:**
- Modify: `src/gg/review_store.py` (`ReviewEntry`, `_connect`, `load_reviews`, `save_reviews`), `src/gg/db.py:_run_list`
- Test: `tests/test_review_store.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `ReviewEntry(branch, position, review_id, subject, diff_hash, series: int = 1)` — `series` is the **last** field with default 1, so every existing positional construction keeps working. `load_reviews()` returns entries with `series` filled; `save_reviews()` writes it.

- [ ] **Step 0: Drop the uncommitted range guard** (the spec replaces it). It is the only uncommitted change in these two files:

Run: `git diff --stat src/gg/sync.py tests/test_rbt_sync.py` — Expected: only the guard block in `sync.py` and `class TestRangeGuard` in the test file. Then `git restore src/gg/sync.py tests/test_rbt_sync.py`.

- [ ] **Step 1: Write the failing tests** (in `TestReviewCRUD`; `sqlite3` is already imported)

```python
    def test_series_round_trip(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        entries = [
            ReviewEntry("feature", 1, "1000", "a", "aaa", series=1),
            ReviewEntry("feature", 2, "1001", "b", "bbb", series=2),
        ]
        review_store.save_reviews(entries, cwd=git_repo.work_dir)
        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert [e.series for e in loaded] == [1, 2]

    def test_old_schema_gets_series_column(
        self, git_repo: GitRepo, rbt_mock: RbtMock,
    ) -> None:
        db = git_repo.work_dir / ".gg" / "reviews.db"
        db.parent.mkdir()
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE reviews (branch TEXT NOT NULL, position INTEGER NOT NULL, "
            "review_id TEXT NOT NULL, subject TEXT NOT NULL, diff_hash TEXT NOT NULL, "
            "PRIMARY KEY (branch, position))"
        )
        conn.execute("INSERT INTO reviews VALUES ('feature', 1, '1000', 'a', 'aaa')")
        conn.commit()
        conn.close()
        loaded = review_store.load_reviews("feature", cwd=git_repo.work_dir)
        assert [(e.review_id, e.series) for e in loaded] == [("1000", 1)]
```

In `tests/test_db.py`, add to the list tests:

```python
    def test_list_shows_series(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        review_store.save_reviews(
            [ReviewEntry("master", 1, "1000", "a", "aaaaaaaa", series=2)],
            cwd=git_repo.work_dir,
        )
        result = git_repo.run_gg("db")
        assert "s2  r/1000" in result.stdout
```

- [ ] **Step 2: Run them to verify they fail** — `PYTEST tests/test_review_store.py tests/test_db.py -k "series"` — Expected: FAIL (`unexpected keyword argument 'series'`).

- [ ] **Step 3: Implement.** `CREATE TABLE` gains `series INTEGER NOT NULL DEFAULT 1`; after it, `_connect` checks `PRAGMA table_info(reviews)` and runs `ALTER TABLE reviews ADD COLUMN series INTEGER NOT NULL DEFAULT 1` when missing. `load_reviews` selects `series` last; `save_reviews` inserts it. `db._run_list` prints `  #{position}  s{series}  r/{review_id}  {subject}  {hash[:8]}`.

- [ ] **Step 4: Verify** — `PYTEST tests/` — Expected: all pass.

- [ ] **Step 5: Commit** — `git commit -m "feat(review-store): record a series number per review"` (stage the three source/test files).

---

### Task 2: git helpers

**Files:**
- Modify: `src/gg/git.py`
- Test: `tests/test_git_readonly.py`

**Interfaces:**
- Produces: `branch_exists(name: str, *, cwd: Path | None = None) -> bool` (`git show-ref --verify --quiet refs/heads/<name>`; a non-zero exit means False, never raises). `upstream_subjects(ref: str, limit: int = 500, *, cwd: Path | None = None) -> set[str]` (subjects of the newest `limit` commits reachable from `ref`, via `git log -n <limit> --format=%s <ref>`).

- [ ] **Step 1: Write the failing tests**

```python
class TestBranchExists:
    def test_existing_branch(self, git_repo: GitRepo) -> None:
        assert git.branch_exists("master", cwd=git_repo.work_dir)

    def test_missing_branch(self, git_repo: GitRepo) -> None:
        assert not git.branch_exists("no-such-branch", cwd=git_repo.work_dir)


class TestUpstreamSubjects:
    def test_includes_recent_subjects(self, git_repo: GitRepo) -> None:
        git_repo.commit("landed fix")
        assert "landed fix" in git.upstream_subjects("master", cwd=git_repo.work_dir)

    def test_respects_limit(self, git_repo: GitRepo) -> None:
        git_repo.commit("older")
        git_repo.commit("newest")
        assert git.upstream_subjects("master", 1, cwd=git_repo.work_dir) == {"newest"}
```

- [ ] **Step 2: Verify they fail** — `PYTEST tests/test_git_readonly.py` — Expected: FAIL (`has no attribute 'branch_exists'`).
- [ ] **Step 3: Implement both functions in `src/gg/git.py`.**
- [ ] **Step 4: Verify** — `PYTEST tests/test_git_readonly.py` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(git): branch_exists and upstream_subjects helpers"`.

---

### Task 3: read a review's `depends-on`

**Files:**
- Modify: `src/gg/rb_api.py`, `tests/conftest.py` (mock `rbt`)
- Test: `tests/test_rb_api.py` (create)

**Interfaces:**
- Produces: `rb_api.fetch_depends_on(review_id: str, *, cwd: Path | None = None) -> list[str]` — review ids from `review_request["depends_on"]` hrefs (reuse `_parse_block_id`); `[]` when absent. Same error handling as `fetch_review`.
- Produces (tests): `RbtMock` gains a `state_dir: Path` field, set by the fixture to the existing `tmp_path / "rbt_state"`. `RbtMock.set_review(review_id: str, *, summary: str = "", depends_on: list[str] | None = None) -> None` writes `<state_dir>/<id>.json` (creating the directory). The mock's `api-get` returns `"depends_on": [{"href": f"https://reviews.example.com/api/review-requests/{d}/"} ...]` from state, and its `post` branch records `--depends-on=<id>` into the new review's state.

- [ ] **Step 1: Write the failing tests**

```python
from gg import rb_api
from tests.conftest import GitRepo, RbtMock


def test_fetch_depends_on(git_repo: GitRepo, rbt_mock: RbtMock) -> None:
    rbt_mock.set_review("19917", depends_on=["19997"])
    assert rb_api.fetch_depends_on("19917", cwd=git_repo.work_dir) == ["19997"]


def test_fetch_depends_on_none(git_repo: GitRepo, rbt_mock: RbtMock) -> None:
    rbt_mock.set_review("19917")
    assert rb_api.fetch_depends_on("19917", cwd=git_repo.work_dir) == []
```

- [ ] **Step 2: Verify they fail** — `PYTEST tests/test_rb_api.py` — Expected: FAIL (`no attribute 'set_review'`).
- [ ] **Step 3: Implement** the mock changes and `fetch_depends_on`.
- [ ] **Step 4: Verify** — `PYTEST tests/` — Expected: all pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(rb-api): read a review's depends-on"`.

---

### Task 4: matcher additions

**Files:**
- Modify: `src/gg/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Produces: `ActionKind.SUBMIT = "submit"`, `ActionKind.FORGET = "forget"`. `SyncAction` gains `outside_range: bool = False`, `moved: bool = False`, `forget_branch: str | None = None` (the branch that still records a forgotten review). `reconcile(old, new, *, mark_deps: bool = True)` — with `mark_deps=False` it skips `_mark_dep_updates`.

- [ ] **Step 1: Write the failing test**

```python
def test_reconcile_without_dep_marking() -> None:
    old = [ReviewEntry("b", 1, "1", "a", "ha"), ReviewEntry("b", 2, "2", "c", "hc")]
    new = [NewCommit("r1", "a", "ha"), NewCommit("rx", "x", "hx"), NewCommit("r2", "c", "hc")]
    assert [a.kind for a in reconcile(old, new)][2] is ActionKind.KEEP_DEP
    assert [a.kind for a in reconcile(old, new, mark_deps=False)][2] is ActionKind.KEEP
```

- [ ] **Step 2: Verify it fails** — `PYTEST tests/test_matcher.py -k without_dep` — Expected: FAIL (unexpected keyword `mark_deps`).
- [ ] **Step 3: Implement** the enum members, fields and parameter.
- [ ] **Step 4: Verify** — `PYTEST tests/` — Expected: all pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(matcher): submit/forget kinds and optional dep marking"`.

---

### Task 5: `build_plan` — adoption, membership, ranges

**Files:**
- Create: `src/gg/series.py`
- Test: `tests/test_series.py` (create)

**Interfaces:**
- Consumes: `reconcile(..., mark_deps=False)`, `ActionKind`, `SyncAction` fields from Task 4; `ReviewEntry.series` from Task 1.
- Produces:

```python
@dataclass
class Series:
    number: int                      # 1-based ordinal among live series
    actions: list[SyncAction]        # every member in branch order, incl. outside-range ones
    origin: str | None = None        # other branch it was adopted from
    was: int | None = None           # previous number, when it changed
    new: bool = False
    depends_on: str | None = None    # predecessor review of the first patch, if known

@dataclass
class GoneGroup:
    old_number: int
    actions: list[SyncAction]        # SUBMIT / DISCARD / FORGET (SKIP after -i)

@dataclass
class SeriesPlan:
    series: list[Series]
    gone: list[GoneGroup]
    warnings: list[str]
    adopted_from: set[str]           # branches whose entries were adopted

def build_plan(
    *,
    branch: str,
    own: list[ReviewEntry],                  # this branch's record, position order
    foreign: dict[str, list[ReviewEntry]],   # other branches' records
    commits: list[NewCommit],                # full branch, oldest first
    in_range: set[str],                      # revs the user's range selects
    landed_subjects: Callable[[], set[str]], # called only if something is gone
    branch_exists: Callable[[str], bool],
    depends_on_of: Callable[[str], list[str]],
    initial_depends: str | None = None,
) -> SeriesPlan
```

- In the returned plan, `action.old_entry.position` is the entry's **1-based rank inside its old series** (own or foreign), not the branch-wide position; `old_entry.series` is its old series number. Later tasks number and save from this.
- Task 5 leaves `gone` empty and does no `keep+dep` marking; Task 6 adds both.

Algorithm (decisions the tests do not fully pin):
1. **Adoption pre-pass.** For each commit whose stripped subject is not in the own record, look up other branches' entries with that stripped subject. One distinct review id → adopt (origin = alphabetically first branch recording it; a review id is adopted at most once). Several ids → skip the commit, one warning naming each `r/<id>` and branch. Warnings quote branch names: `'base'`. A foreign series that shares any review id with an own series is merged into that own series instead of being a series of its own.
2. Run `reconcile(own + adopted, commits_not_skipped, mark_deps=False)`; give adopted entries `position` = their commit's index + 1 for matching.
3. **Membership.** Key each matched commit by its series (own number, or `(branch, foreign number)`). While some key occurs in more than one run of consecutive matched commits: if it is a foreign key, drop that adoption (its commits are skipped, warning names the branch and series) and recompute; if own, keep its longest run (first on ties) and give each other run the key of the run before it (after it, if first) — those commits become `UPDATE` with `moved=True`.
4. **Unmatched commits.** Only commits in `in_range` are placed; others are dropped. Between two matched commits: the key of the commit before. Before the first: the first key. After the last matched commit (or no matches at all): one new series.
5. **Range.** A matched commit outside `in_range` stays a member with `outside_range=True` and kind `KEEP` whatever its hash/subject.
6. Number live series 1..K in branch order; set `was` when an own series' number changed, `origin` for adopted ones, `new` for the new one.

- [ ] **Step 1: Write the failing tests**

```python
from gg.matcher import NewCommit
from gg.review_store import ReviewEntry
from gg.series import build_plan


def entry(branch, pos, rid, subject, series=1, h=None):
    return ReviewEntry(branch, pos, rid, subject, h or f"h-{subject}", series=series)


def commit(subject, h=None, rev=None):
    return NewCommit(rev=rev or f"r-{subject}", subject=subject, diff_hash=h or f"h-{subject}")


def plan_of(own, commits, *, foreign=None, in_range=None, landed=(), live=(), deps=None, initial=None):
    return build_plan(
        branch="stack", own=own, foreign=foreign or {}, commits=commits,
        in_range=in_range if in_range is not None else {c.rev for c in commits},
        landed_subjects=lambda: set(landed),
        branch_exists=lambda b: b in live,
        depends_on_of=lambda rid: (deps or {}).get(rid, []),
        initial_depends=initial,
    )


def members(plan):
    return [(s.number, [a.new_commit.subject for a in s.actions]) for s in plan.series]


def action(plan, subject):
    return next(a for s in plan.series for a in s.actions if a.new_commit.subject == subject)


def test_adopts_foreign_series_and_starts_new_one():
    foreign = {"base": [entry("base", 1, "1000", "s1"), entry("base", 2, "1001", "s2")]}
    plan = plan_of([], [commit("s1"), commit("s2"), commit("c1")], foreign=foreign)
    assert members(plan) == [(1, ["s1", "s2"]), (2, ["c1"])]
    assert plan.series[0].origin == "base" and plan.series[1].new
    assert action(plan, "s2").old_entry.review_id == "1001"
    assert action(plan, "c1").kind.value == "create"
    assert plan.adopted_from == {"base"}


def test_recorded_series_stay_apart():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b", series=2)]
    plan = plan_of(own, [commit("a"), commit("b"), commit("d")])
    assert members(plan) == [(1, ["a"]), (2, ["b"]), (3, ["d"])]


def test_insert_inside_series_joins_it():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b")]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")])
    assert members(plan) == [(1, ["a", "x", "b"])]


def test_insert_at_boundary_joins_series_below():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b", series=2)]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")])
    assert members(plan) == [(1, ["a", "x"]), (2, ["b"])]


def test_moved_commit_joins_series_it_sits_in():
    own = [entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b"), entry("stack", 3, "3", "c"),
           entry("stack", 4, "4", "d", series=2), entry("stack", 5, "5", "e", series=2)]
    plan = plan_of(own, [commit(s) for s in ("a", "b", "d", "c", "e")])
    assert members(plan) == [(1, ["a", "b"]), (2, ["d", "c", "e"])]
    assert action(plan, "c").kind.value == "update" and action(plan, "c").moved


def test_foreign_overlap_joins_existing_series():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b")]
    foreign = {"base": [entry("base", 1, "1000", "a"), entry("base", 2, "1001", "b"),
                        entry("base", 3, "1002", "c")]}
    plan = plan_of(own, [commit("a"), commit("b"), commit("c")], foreign=foreign)
    assert members(plan) == [(1, ["a", "b", "c"])]
    assert action(plan, "c").old_entry.review_id == "1002"


def test_noncontiguous_foreign_series_skipped():
    own = [entry("stack", 1, "900", "x")]
    foreign = {"base": [entry("base", 1, "1000", "s1"), entry("base", 2, "1001", "s2")]}
    plan = plan_of(own, [commit("s1"), commit("x"), commit("s2")], foreign=foreign)
    assert members(plan) == [(1, ["x"])]
    assert any("'base'" in w for w in plan.warnings)


def test_conflicting_foreign_ids_skipped():
    foreign = {"one": [entry("one", 1, "1000", "s")], "two": [entry("two", 1, "2000", "s")]}
    plan = plan_of([], [commit("s"), commit("x")], foreign=foreign)
    assert members(plan) == [(1, ["x"])]
    assert any("r/1000" in w and "r/2000" in w for w in plan.warnings)


def test_duplicate_subject_adopted_once():
    foreign = {"base": [entry("base", 1, "1000", "s")]}
    plan = plan_of([], [commit("s", rev="r1"), commit("s", h="h2", rev="r2")], foreign=foreign)
    ids = [a.old_entry.review_id for s in plan.series for a in s.actions if a.old_entry]
    assert ids == ["1000"]
    assert members(plan) == [(1, ["s"]), (2, ["s"])]


def test_out_of_range_commit_kept_even_if_changed():
    own = [entry("stack", 1, "1000", "a", h="old")]
    plan = plan_of(own, [commit("a", h="new"), commit("n")], in_range={"r-n"})
    a = action(plan, "a")
    assert a.kind.value == "keep" and a.outside_range
    assert members(plan) == [(1, ["a"]), (2, ["n"])]


def test_range_end_leaves_later_commits_out():
    plan = plan_of([], [commit("n1"), commit("n2")], in_range={"r-n1"})
    assert members(plan) == [(1, ["n1"])]
```

- [ ] **Step 2: Verify they fail** — `PYTEST tests/test_series.py` — Expected: FAIL (`No module named 'gg.series'`).
- [ ] **Step 3: Implement `build_plan` and the dataclasses in `src/gg/series.py`** following the algorithm above.
- [ ] **Step 4: Verify** — `PYTEST tests/test_series.py` — Expected: PASS; then `PYTEST tests/` all pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(series): plan a branch as a list of series"`.

---

### Task 6: `build_plan` — gone commits and dependencies

**Files:**
- Modify: `src/gg/series.py`
- Test: `tests/test_series.py`

**Interfaces:**
- Consumes: Task 5's `build_plan` and test helpers.
- Produces: `SeriesPlan.gone` filled; `KEEP_DEP` + `needs_dep_update` marked; `Series.depends_on` set.

Rules:
- **Gone** = own entries `reconcile` left unmatched. `FORGET` (with `forget_branch`) if its review id is in any other branch's record and `branch_exists(that branch)`; else `SUBMIT` if its stripped subject is in `landed_subjects()`; else `DISCARD`. Grouped by old series number, ascending. Call `landed_subjects` at most once, and only when something is gone.
- **Expected predecessor** of each live action = review id of the previous live action in branch order across series (`None` after a `CREATE`). `Series.depends_on` = expected predecessor of its first action; for series 1, `initial_depends`.
- **Current predecessor** of a matched action = the entry before it in its old record order (own record by position; foreign series by its order). For the first entry of an adopted foreign series: `depends_on_of(review_id)[0]` or `None` — one call per adopted series.
- `KEEP` becomes `KEEP_DEP` when expected != current, except: the first action of series 1 never does; and a current predecessor that is `SUBMIT` in this plan counts as satisfied. Outside-range actions follow the same rule (keeping `outside_range=True`).

- [ ] **Step 1: Write the failing tests**

```python
def test_gone_entries_classified_and_grouped():
    own = [entry("stack", 1, "1", "landed"), entry("stack", 2, "2", "shared"),
           entry("stack", 3, "3", "dropped"), entry("stack", 4, "4", "k", series=2)]
    foreign = {"other": [entry("other", 1, "2", "shared")]}
    plan = plan_of(own, [commit("k")], foreign=foreign, landed={"landed"}, live={"other"})
    [group] = plan.gone
    assert group.old_number == 1
    assert [(a.kind.value, a.old_entry.review_id) for a in group.actions] == [
        ("submit", "1"), ("forget", "2"), ("discard", "3")]
    assert group.actions[1].forget_branch == "other"
    assert plan.series[0].number == 1 and plan.series[0].was == 2


def test_forget_needs_existing_branch():
    own = [entry("stack", 1, "2", "shared"), entry("stack", 2, "4", "k")]
    foreign = {"gone-branch": [entry("gone-branch", 1, "2", "shared")]}
    plan = plan_of(own, [commit("k")], foreign=foreign, live=())
    assert plan.gone[0].actions[0].kind.value == "discard"


def test_keep_dep_after_insert():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b")]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")])
    assert action(plan, "b").kind.value == "keep+dep"


def test_adopted_series_rechained_only_if_rb_differs():
    own = [entry("stack", 1, "1001", "c")]
    foreign = {"dcb": [entry("dcb", 1, "2000", "d1"), entry("dcb", 2, "2001", "d2")]}
    commits = [commit("c"), commit("d1"), commit("d2")]
    stale = plan_of(own, commits, foreign=foreign, deps={"2000": ["999"]})
    assert action(stale, "d1").kind.value == "keep+dep"
    chained = plan_of(own, commits, foreign=foreign, deps={"2000": ["1001"]})
    assert action(chained, "d1").kind.value == "keep"
    assert chained.series[1].depends_on == "1001"


def test_series_one_first_patch_never_rechained():
    own = [entry("stack", 1, "999", "z"), entry("stack", 2, "1000", "a")]
    plan = plan_of(own, [commit("a")])
    assert action(plan, "a").kind.value == "keep"


def test_submitted_predecessor_not_rechained():
    own = [entry("stack", 1, "1", "a"), entry("stack", 2, "2", "b", series=2),
           entry("stack", 3, "3", "c", series=2)]
    plan = plan_of(own, [commit("a"), commit("c")], landed={"b"})
    assert action(plan, "c").kind.value == "keep"


def test_out_of_range_keep_dep_marked():
    own = [entry("stack", 1, "1000", "a"), entry("stack", 2, "1001", "b", series=2)]
    plan = plan_of(own, [commit("a"), commit("x"), commit("b")], in_range={"r-x"})
    b = action(plan, "b")
    assert b.kind.value == "keep+dep" and b.outside_range
```

- [ ] **Step 2: Verify they fail** — `PYTEST tests/test_series.py` — Expected: the new tests FAIL (`gone` empty / kinds still `keep`).
- [ ] **Step 3: Implement the rules in `src/gg/series.py`.**
- [ ] **Step 4: Verify** — `PYTEST tests/` — Expected: all pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(series): classify gone reviews and chain series"`.

---

### Task 7: numbering and plan output

**Files:**
- Modify: `src/gg/numbering.py`, `src/gg/sync_plan.py`
- Test: `tests/test_numbering.py`, `tests/test_sync_plan.py` (create)

**Interfaces:**
- Consumes: `Series`, `GoneGroup`, `SeriesPlan` (Task 5).
- Produces: `numbering.number_series(series: Series, *, renumber: bool = False) -> list[tuple[SyncAction, str]]` — `assign_numbers` over the series' actions (N = len(actions)), with `moved` actions numbered as inserts. `sync_plan.format_series_plan(plan: SeriesPlan, *, upstream: str, renumber=False, publish=False, reviewers=None, groups=None, force=False) -> str` and `sync_plan.format_summary(plan: SeriesPlan) -> str` (replaces `sync._format_summary`; adds `submitted` and `forgotten` counts in the order kept, updated, created, discarded, submitted, forgotten, skipped).

Output: reviewer/force header and the column header line as today, then each gone group (header per Global Constraints, `--` number, `forget` rows end with `  (still on '<branch>')`), then each live series header followed by its rows. Outside-range rows are hidden unless `keep+dep`, which end with `  (outside range)`; a series whose every action is outside the range gets `, outside range` in its header.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_numbering.py
def test_number_series_moved_counts_as_insert():
    a = SyncAction(ActionKind.KEEP, ReviewEntry("b", 1, "1", "a", "h"), NewCommit("r1", "a", "h"), 1)
    m = SyncAction(ActionKind.UPDATE, ReviewEntry("b", 3, "3", "m", "h"), NewCommit("r2", "m", "h"), 2, moved=True)
    b = SyncAction(ActionKind.KEEP, ReviewEntry("b", 2, "2", "b", "h"), NewCommit("r3", "b", "h"), 3)
    labels = [n for _, n in number_series(Series(number=1, actions=[a, m, b]))]
    assert labels == ["[1/3]", "[1.1/3]", "[2/3]"]
```

```python
# tests/test_sync_plan.py
from gg.matcher import ActionKind, NewCommit, SyncAction
from gg.review_store import ReviewEntry
from gg.series import GoneGroup, Series, SeriesPlan
from gg.sync_plan import format_series_plan, format_summary


def keep(rid, subject, pos=1, **kw):
    return SyncAction(ActionKind.KEEP, ReviewEntry("s", pos, rid, subject, "h"),
                      NewCommit("r" + rid, subject, "h"), pos, **kw)


def test_series_headers():
    plan = SeriesPlan(
        series=[
            Series(1, [keep("1000", "a")], origin="base"),
            Series(2, [SyncAction(ActionKind.CREATE, None, NewCommit("r9", "c", "h"), 2)],
                   new=True, depends_on="1000"),
        ],
        gone=[], warnings=[], adopted_from={"base"})
    out = format_series_plan(plan, upstream="oktet/master")
    assert "Series 1: 1 patch, from 'base'" in out
    assert "Series 2: 1 patch, new, depends on r/1000" in out


def test_gone_group_before_live_and_was():
    sub = SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "1", "x", "h"), None, None)
    plan = SeriesPlan(series=[Series(1, [keep("2", "y")], was=2)],
                      gone=[GoneGroup(1, [sub])], warnings=[], adopted_from=set())
    out = format_series_plan(plan, upstream="oktet/master")
    assert out.index("Series 1: 1 patch, landed in oktet/master") < out.index(
        "Series 1: 1 patch (was series 2)")


def test_outside_range_rows():
    hidden = keep("1", "a", outside_range=True)
    dep = keep("2", "b", pos=2, outside_range=True)
    dep.kind, dep.needs_dep_update = ActionKind.KEEP_DEP, True
    out = format_series_plan(SeriesPlan([Series(1, [hidden, dep])], [], [], set()), upstream="u")
    assert "Series 1: 2 patches, outside range" in out
    assert "r/1 " not in out
    assert out.rstrip().endswith("b  (outside range)")


def test_summary_counts():
    sub = SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "1", "x", "h"), None, None)
    fg = SyncAction(ActionKind.FORGET, ReviewEntry("s", 2, "2", "y", "h"), None, None)
    plan = SeriesPlan([Series(1, [keep("3", "z")])], [GoneGroup(1, [sub, fg])], [], set())
    assert format_summary(plan) == "Synced: 1 kept, 1 submitted, 1 forgotten"
```

- [ ] **Step 2: Verify they fail** — `PYTEST tests/test_numbering.py tests/test_sync_plan.py` — Expected: FAIL (import errors).
- [ ] **Step 3: Implement** `number_series`, `format_series_plan` and `format_summary` next to the existing `format_plan`, which stays until Task 8 switches `sync.py` over and deletes it.
- [ ] **Step 4: Verify** — `PYTEST tests/` — Expected: all pass.
- [ ] **Step 5: Commit** — `git commit -m "feat(sync-plan): show the plan series by series"`.

---

### Task 8: wire `rbt-sync` to series plans

**Files:**
- Modify: `src/gg/sync.py` (`run`, `_execute`, `_build_new_commits`; remove `_format_summary`), `src/gg/sync_plan.py` (delete the old `format_plan`), `README.md`
- Test: `tests/test_rbt_sync.py`

**Interfaces:**
- Consumes: everything above. `rbt_close.close_submitted` (exists). `format_series_plan`/`format_summary` (Task 7).
- Produces: `_execute(plan: SeriesPlan, *, branch_name, tracking, renumber, publish, verbose, explicit_branch, initial_depends, reviewers, groups, no_numbers, cwd) -> list[ReviewEntry]`.

`run()` flow:
1. `--close` path unchanged.
2. `full = git.list_revs(git.rev_range())`; with `args.range`, `selected = git.list_revs(args.range)`; if any selected rev is not in `full`: print `[gg] {range} has commits that are not on '{branch}' ({base}..HEAD).` to stderr, return 1. Without a range, `selected = full`. No commits selected: `No commits in range.`, return 1 (as today).
3. `own = [] if args.new else load_reviews(branch)`; `foreign = {b: load_reviews(b) for b in list_branches() if b != branch}`.
4. `plan = build_plan(...)` with `landed_subjects=lambda: git.upstream_subjects(git.range_base())`, `branch_exists=git.branch_exists`, `depends_on_of=rb_api.fetch_depends_on`, `initial_depends=args.depends_on`. Print each warning as `[gg] <warning>` to stderr. Print `[gg] No existing reviews; posting as a fresh series.` when `own` is empty, nothing was adopted and `--new` was not given.
5. `-i`: Task 9. `--force`: in-range `KEEP`/`KEEP_DEP` → `UPDATE` (never outside-range ones).
6. Print `format_series_plan(...)`; `-d` stops here (no DB writes).
7. `_execute`: phase 1 over `plan.gone` — `DISCARD` → `close_discarded`, `SUBMIT` → `close_submitted`, `FORGET`/`SKIP` → nothing. Phase 2: series in order, rows numbered by `number_series`; outside-range `KEEP` rows are not posted; everything else posts exactly as today (prefix from the series numbering, `depends_on` = previous review id carried across series, reviewer inheritance as today).
8. Save: entries in branch order, `position` 1..M, `series` = series number. A posted row (create/update/keep+dep) saves its new commit's subject and hash; an unposted outside-range row saves its old entry's subject and hash; other kept rows save the new commit's. Gone `SKIP` entries are appended as today with their old series number. Then `diff_cache.save_hashes` as today, then `review_store.clear_branch(b)` for each `b` in `plan.adopted_from` where `not git.branch_exists(b)`. Print `format_summary(plan)` to stderr.

- [ ] **Step 1: Write the failing tests** (new `class TestSeries` in `tests/test_rbt_sync.py`; reuse `_plain`; add `from gg import review_store`). The mock numbers every call, `api-get` included, so ids are read from the record, never hardcoded. `master` tracks `origin/master`, so the full branch is `origin/master..HEAD` and "landed" means pushed.

```python
class TestSeries:
    def _stack(self, git_repo: GitRepo) -> None:
        """base: s1,s2 posted; stack = base + c1,c2 + n1."""
        git_repo.create_branch("base", "master")
        git_repo.commit("s1")
        git_repo.commit("s2")
        assert git_repo.run_gg("rbt-sync").returncode == 0
        git_repo.git("checkout", "-b", "stack", "--track", "master")
        git_repo.git("merge", "--ff-only", "base")
        git_repo.commit("c1")
        git_repo.commit("c2")
        git_repo.commit("n1")

    def _ids(self, git_repo: GitRepo, branch: str = "stack") -> list[str]:
        return [e.review_id for e in review_store.load_reviews(branch, cwd=git_repo.work_dir)]

    def test_stacked_branch_shows_series(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        base_ids = self._ids(git_repo, "base")
        assert git_repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
        ids = self._ids(git_repo)  # s1, s2 (adopted), c1, c2
        assert ids[:2] == base_ids
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert "Series 1: 2 patches" in out
        assert f"Series 2: 2 patches, depends on r/{ids[1]}" in out
        assert f"Series 3: 1 patch, new, depends on r/{ids[3]}" in out
        assert re.search(r"\[1/1\]\s+create", out)

    def test_range_without_saved_series_keeps_them(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        r = git_repo.run_gg("rbt-sync", "HEAD~1..HEAD")
        assert r.returncode == 0, r.stderr
        assert not [c for c in rbt_mock.calls() if c and c[0] == "close"]
        posts = [c for c in rbt_mock.calls() if c and c[0] == "post"]
        assert "--summary=[1/1]: n1" in posts[-1]

    def test_landed_series_submitted(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        ids = self._ids(git_repo)
        git_repo.git("checkout", "master")
        git_repo.git("merge", "--ff-only", "base")
        git_repo.git("push", "origin", "master")
        git_repo.git("branch", "-D", "base")
        git_repo.git("checkout", "stack")
        r = git_repo.run_gg("rbt-sync", "-d")
        assert "landed in" in r.stdout and "submit" in r.stdout
        git_repo.run_gg("rbt-sync")
        closes = [c for c in rbt_mock.calls() if c and c[0] == "close"]
        assert ["close", "--close-type=submitted", ids[0]] in closes

    def test_dropped_shared_series_forgotten(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        git_repo.git("rebase", "--onto", "master", "base", "stack")
        r = git_repo.run_gg("rbt-sync")
        assert "forget" in r.stdout
        assert not [c for c in rbt_mock.calls() if c and c[0] == "close"]

    def test_record_of_deleted_source_branch_cleared(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.git("branch", "-D", "base")
        assert git_repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
        assert "base" not in review_store.list_branches(cwd=git_repo.work_dir)

    def test_range_off_branch_refused(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.git("checkout", "-b", "side", "master")
        git_repo.commit("elsewhere")
        git_repo.git("checkout", "stack")
        calls = rbt_mock.call_count()
        r = git_repo.run_gg("rbt-sync", "master..side")
        assert r.returncode == 1 and "not on 'stack'" in r.stderr
        assert rbt_mock.call_count() == calls

    def test_out_of_range_change_detected_later(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        c1_id = self._ids(git_repo)[2]
        c1 = git_repo.git("rev-parse", "HEAD~2").stdout.strip()
        (git_repo.work_dir / "c1-extra").write_text("x\n")
        git_repo.git("add", "c1-extra")
        git_repo.git("commit", "--fixup", c1)
        git_repo.git("-c", "sequence.editor=:", "rebase", "-i", "--autosquash", "master")
        assert git_repo.run_gg("rbt-sync", "HEAD~1..HEAD").returncode == 0
        out = _plain(git_repo.run_gg("rbt-sync", "-d").stdout)
        assert re.search(rf"\[1/2\]\s+update\s+\S+\s+r/{c1_id}\s+c1", out)

    def test_force_respects_range(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        git_repo.run_gg("rbt-sync", "master..HEAD~1")
        calls = rbt_mock.call_count()
        git_repo.run_gg("rbt-sync", "-f", "HEAD~1..HEAD")
        posts = [c for c in rbt_mock.calls()[calls:] if c and c[0] == "post"]
        assert len(posts) == 1

    def test_dry_run_leaves_db_alone(self, git_repo: GitRepo, rbt_mock: RbtMock) -> None:
        self._stack(git_repo)
        before = git_repo.run_gg("db", "--all").stdout
        git_repo.run_gg("rbt-sync", "-d")
        assert git_repo.run_gg("db", "--all").stdout == before
```

- [ ] **Step 2: Verify they fail** — `PYTEST tests/test_rbt_sync.py -k TestSeries` — Expected: FAIL (no `Series` headers; `forget`/`submit` absent).
- [ ] **Step 3: Implement the flow above in `src/gg/sync.py`.** Update existing `tests/test_rbt_sync.py` expectations only where the output now has series headers or the old behaviour is intentionally replaced (a range no longer discards); list each such change in the commit message body.
- [ ] **Step 4: Verify** — `PYTEST tests/` — Expected: all pass.
- [ ] **Step 5: Document** in `README.md`, in the `rbt-sync` section: several series on one branch (range end starts a new series), series picked up from other branches, `submit`/`forget` for gone reviews, the header format.
- [ ] **Step 6: Commit** — `git commit -m "feat(rbt-sync): sync a branch as a list of series"`.

---

### Task 9: series separators in `-i`

**Files:**
- Modify: `src/gg/sync_edit.py`, `src/gg/sync.py` (call site), `README.md`
- Test: `tests/test_sync_edit.py`

**Interfaces:**
- Consumes: `SeriesPlan` (Task 5), `number_series` (Task 7).
- Produces: `serialize_plan(plan: SeriesPlan, *, renumber=False, editor="", separators=True) -> str`, `parse_plan(text: str, plan: SeriesPlan, *, separators=True) -> SeriesPlan | None`, `edit_plan(plan: SeriesPlan, *, renumber=False, separators=True) -> SeriesPlan | None`. `sync.run` passes `separators=args.range is None`.

Format and rules:
- Live series rows first, a line `---` between series; gone rows after the last live row. The header gains: `# "---" lines separate series: delete one to merge, add one between two rows to split.` With `separators=False` no `---` lines are written, the header says `# Series cannot be split or merged when a range is given.`, and any `---` is a parse error.
- Action lines still match original actions by order (the count check ignores `---`). `---` before the first row, after the last live row, or twice in a row is a parse error (`ValueError`, like other parse errors).
- Transitions: existing ones, plus `discard` ↔ `submit`, `submit` → `skip`, `forget` → `skip`/`discard`/`submit`.
- After parsing, series are rebuilt from the separators. Every series whose membership changed is renumbered plain `1..N` and its kept rows become `UPDATE`; untouched series stay as planned. Series are renumbered 1..K.
- Existing tests that call `serialize_plan(actions, ...)` / `parse_plan(text, actions)` are migrated to build a one-series `SeriesPlan` (helper in the test file), with discards in a `GoneGroup`.

- [ ] **Step 1: Write the failing tests**

```python
def _plan(*series_actions, gone=()):
    return SeriesPlan([Series(i, list(a)) for i, a in enumerate(series_actions, 1)],
                      [GoneGroup(1, list(gone))] if gone else [], [], set())


def test_separator_written_between_series():
    plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)])
    lines = [l for l in serialize_plan(plan).splitlines() if l and not l.startswith("#")]
    assert lines[1] == "---"


def test_deleting_separator_merges_and_updates():
    plan = _plan([_keep("1", "a")], [_keep("2", "b", pos=1)])
    text = serialize_plan(plan).replace("\n---\n", "\n")
    merged = parse_plan(text, plan)
    assert [len(s.actions) for s in merged.series] == [2]
    assert [a.kind.value for a in merged.series[0].actions] == ["update", "update"]


def test_adding_separator_splits():
    plan = _plan([_keep("1", "a"), _keep("2", "b", pos=2)])
    rows = serialize_plan(plan).splitlines()
    i = next(n for n, l in enumerate(rows) if l.endswith(" b"))
    split = parse_plan("\n".join(rows[:i] + ["---"] + rows[i:]), plan)
    assert [len(s.actions) for s in split.series] == [1, 1]


def test_separator_rejected_with_range():
    plan = _plan([_keep("1", "a"), _keep("2", "b", pos=2)])
    rows = serialize_plan(plan, separators=False).splitlines()
    with pytest.raises(ValueError):
        parse_plan("\n".join(rows[:-1] + ["---"] + rows[-1:]), plan, separators=False)


def test_submit_can_become_discard():
    sub = SyncAction(ActionKind.SUBMIT, ReviewEntry("s", 1, "9", "x", "h"), None, None)
    plan = _plan([_keep("1", "a")], gone=[sub])
    text = "\n".join("discard" + l[len("submit"):] if l.startswith("submit") else l
                     for l in serialize_plan(plan).splitlines())
    assert parse_plan(text, plan).gone[0].actions[0].kind is ActionKind.DISCARD
```

(`_keep(rid, subject, pos=1)` builds a `KEEP` `SyncAction` like `keep()` in Task 7's tests.)

- [ ] **Step 2: Verify they fail** — `PYTEST tests/test_sync_edit.py` — Expected: FAIL.
- [ ] **Step 3: Implement**, migrate the existing tests, and switch `sync.run` to the new `edit_plan`.
- [ ] **Step 4: Verify** — `PYTEST tests/` — Expected: all pass.
- [ ] **Step 5: Document** the separators (and that a range disables them) in `README.md` next to the `-i` description.
- [ ] **Step 6: Commit** — `git commit -m "feat(rbt-sync): split and merge series in the -i editor"`.
