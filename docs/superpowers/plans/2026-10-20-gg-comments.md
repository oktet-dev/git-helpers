# `gg comments` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `gg comments` (fetch the ReviewBoard discussions of a branch, series or given requests, with the code they point at) and `gg comments reply` (post replies and issue status changes from a plain text format), for an agent in the user's session to use.

**Architecture:** RB data is read and written through a small `RBApi` protocol (`rb_model.py`); the real implementation wraps the RBTools Python library (`rb_client.py`), tests use an in-memory fake (`tests/fake_rb.py`). Pure modules turn RB data into discussions with states and code excerpts (`discussions.py`), render them (`comments_format.py`), and parse the reply format (`reply_input.py`); `reply_post.py` validates and posts; `comments.py` is the CLI. The branch planning `rbt-sync` uses is pulled out into `branch_plan.py` / `series_select.py` so both commands share it.

**Tech Stack:** Python ≥3.10, argparse, RBTools ≥7.0 (as a library), pytest.

**Spec:** `docs/superpowers/specs/2026-10-20-gg-comments-design.md`

## Global Constraints

- gg never starts, calls or embeds an agent or any AI; it fetches and posts only.
- `gg rbt-sync` behaves and prints exactly as before; the full existing test suite must pass unchanged after every task.
- Tests never reach a real ReviewBoard: all RB access in the new code goes through `RBApi`; CLI tests run in-process (`cli.main([...])`) with `gg.comments.connect` monkeypatched to return `tests/fake_rb.FakeRB`.
- RBTools is used as a library only by the new commands; everything else keeps shelling out to `rbt`.
- Discussion keys: `#<id>` (diff comment), `#g<id>` (general comment), `#a<id>` (file attachment comment), `review <id>` (a review's top text).
- Status words: `fixed` → RB `resolved`, `dropped` → `dropped`, `open` → `open`.
- States: `needs you`, `waiting on reviewer`, `done`.
- Fetch concurrency: a pool of 8; rate-limited RB calls retried with `rbt_retry.rate_limit_schedule()`.
- Style: `from __future__ import annotations`, type hints, docstrings like the existing modules; `-h/--help` on both commands.
- Commit messages carry no AI/Claude attribution, co-author or session trailers.
- Test command (`/tmp` is full on this machine): `TMPDIR=/home/kostik/.cache/gg-pytest-tmp uv run pytest -q -p no:cacheprovider --basetemp=/home/kostik/.cache/gg-pytest-tmp/base tests/`
- Never post, publish or change anything on the real ReviewBoard. Manual checks against rb.oktet.co.il are read-only (`gg comments`, never `gg comments reply` without `-d`).

## Review Focus

1. A diff comment on removed lines (rows with only an old side) — the excerpt and the `path:lines` use the old line numbers and text. Test: Task 2 `test_excerpt_of_removed_lines`.
2. Reply text with Markdown code fences, inner blank lines and trailing blank lines — posted exactly, inner blanks kept, outer blanks trimmed. Test: Task 3 `test_text_keeps_code_and_inner_blank_lines`.
3. The user has a draft with content on one review while the batch also touches another review — nothing at all is posted and the draft is shown. Test: Task 6 `test_draft_with_content_stops_whole_batch`.
4. A reviewer answers after the user's reply on an open issue — the discussion is "needs you" again. Test: Task 2 `test_reviewer_answer_after_user_needs_you_again`.
5. A text+status block whose reply is published but whose status change fails — the unsent part is printed as a status-only block that can be sent again as is. Test: Task 6 `test_status_failure_prints_status_only_block`.

---

### Task 1: Share branch planning and `-s` selection

Pure refactor: move what `rbt-sync` uses to plan a branch and resolve `-s` into two modules, so `gg comments` can reuse them.

**Files:**
- Create: `src/gg/series_select.py`, `src/gg/branch_plan.py`
- Modify: `src/gg/sync.py` (remove the moved code, use the new modules)
- Test: `tests/test_series_select.py`, `tests/test_branch_plan.py`

**Interfaces:**
- Produces (`series_select.py`, moved from `sync.py` with the same behavior and messages):
  - `SeriesSpec = tuple[int, list[tuple[int, int]] | None]`, `LAST = 0`
  - `series_spec(text: str) -> list[SeriesSpec]` (argparse `type=`; was `_series_spec`)
  - `selected_revs(plan: SeriesPlan, specs: list[SeriesSpec], *, branch_name: str) -> set[str] | str` (was `_selected_revs`)
- Produces (`branch_plan.py`):
  ```python
  @dataclass
  class BranchPlanner:
      branch: str
      base: str            # git.range_base()
      full: list[str]      # base..HEAD, oldest first
      plan_for: Callable[[set[str]], SeriesPlan]

  def branch_planner(*, cwd: Path, new: bool = False,
                     depends_on: str | None = None) -> BranchPlanner
  ```
  `branch_planner` runs only `git.branchname`, `git.range_base` and `git.list_revs`. Loading `reviews.db` (own and foreign), building `NewCommit`s and the cached RB lookups (`landed`, `depends_on_of`) happen on the first `plan_for` call and are cached, so `rbt-sync`'s checks and messages keep their order.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_series_select.py
from gg.series_select import LAST, series_spec

def test_series_spec_values():
    assert series_spec("3,4") == [(3, None), (4, None)]
    assert series_spec("$:1-3") == [(LAST, [(1, 3)])]
    assert series_spec("2:2-$") == [(2, [(2, LAST)])]
```

```python
# tests/test_branch_plan.py
from gg.branch_plan import branch_planner
from tests.conftest import GitRepo, RbtMock
from tests.test_rbt_sync import TestSeries

def test_plans_the_whole_branch(git_repo: GitRepo, rbt_mock: RbtMock) -> None:
    TestSeries._stack(None, git_repo)
    assert git_repo.run_gg("rbt-sync", "master..HEAD~1").returncode == 0
    planner = branch_planner(cwd=git_repo.work_dir)
    assert planner.branch == "stack"
    plan = planner.plan_for(set(planner.full))
    assert [len(s.actions) for s in plan.series] == [2, 2, 1]
```

- [ ] **Step 2: Run them to see them fail** — `ModuleNotFoundError: gg.series_select`.

- [ ] **Step 3: Create the two modules and switch `sync.py` to them.** `sync.run` gets `planner = branch_planner(cwd=cwd, new=args.new, depends_on=args.depends_on)` and uses `planner.base`, `planner.full`, `planner.plan_for`; nothing else in `sync.py` changes.

- [ ] **Step 4: Run the full suite** — all pass, existing tests untouched.

- [ ] **Step 5: Commit** — `refactor(rbt-sync): share branch planning and -s selection`

---

### Task 2: RB model and discussions

**Files:**
- Create: `src/gg/rb_model.py`, `src/gg/discussions.py`
- Test: `tests/test_discussions.py`

**Interfaces:**
- Produces (`rb_model.py`): frozen dataclasses and the API protocol. Request ids are `str` (as in `reviews.db`); ids of RB objects inside a request are `int`; timestamps are RB's ISO 8601 strings; all text is plain (already HTML-unescaped by the client).
  ```python
  Comment(kind: str,            # "diff" | "general" | "attachment"
          id: int, author: str, timestamp: str, text: str,
          issue_opened: bool,
          issue_status: str,    # "" | "open" | "resolved" | "dropped" | "verifying-resolved" | "verifying-dropped"
          severity: str | None = None,
          path: str | None = None, filediff_id: int | None = None,
          interfilediff_id: int | None = None, diff_revision: int | None = None,
          interdiff_revision: int | None = None,
          first_row: int | None = None, num_rows: int | None = None,
          attachment: str | None = None)
  ReplyComment(kind: str, reply_to: int, author: str, timestamp: str, text: str)
  Reply(id: int, author: str, timestamp: str, public: bool, body_top: str,
        comments: list[ReplyComment])   # body_top: reply to the review's top text, "" if none
  Review(id: int, author: str, timestamp: str, public: bool, ship_it: bool,
         body_top: str, comments: list[Comment], replies: list[Reply])
  RequestInfo(id: str, summary: str, latest_diff: int | None)
  DiffRow(row: int, old_line: int | None, old_text: str, new_line: int | None, new_text: str)
  class RBError(Exception)   # a ReviewBoard call failed; the message says which and why

  class RBApi(Protocol):
      def user(self) -> str: ...
      def request(self, rid: str) -> RequestInfo: ...
      def reviews(self, rid: str) -> list[Review]: ...   # incl. the user's unpublished replies (public=False)
      def diff_rows(self, rid: str, comment: Comment) -> list[DiffRow]: ...
      def draft_reply(self, rid: str, review_id: int) -> Reply | None: ...
      def create_reply(self, rid: str, review_id: int) -> int: ...   # the user's draft reply: existing or new
      def add_reply_comment(self, rid: str, review_id: int, reply_id: int,
                            comment: Comment, text: str) -> None: ...
      def set_reply_top(self, rid: str, review_id: int, reply_id: int, text: str) -> None: ...
      def publish_reply(self, rid: str, review_id: int, reply_id: int) -> None: ...
      def delete_reply(self, rid: str, review_id: int, reply_id: int) -> None: ...
      def set_issue_status(self, rid: str, review_id: int, comment: Comment,
                           status: str) -> str: ...   # returns the status RB reports
  ```
  Every method raises `RBError` on failure.
- Produces (`discussions.py`):
  ```python
  NEEDS_YOU = "needs you"; WAITING = "waiting on reviewer"; DONE = "done"
  Message(author: str, timestamp: str, text: str)                      # frozen
  Discussion(key: str, review: Review, comment: Comment | None,       # comment None: review top text
             messages: list[Message], state: str)
  ExcerptLine(marked: bool, line: int | None, text: str)              # frozen
  def comment_key(comment: Comment) -> str
  def build_discussions(reviews: list[Review], *, user: str) -> list[Discussion]
  def ship_its(reviews: list[Review]) -> list[str]
  def code_excerpt(rows: list[DiffRow], first_row: int, num_rows: int,
                   *, context: int = 2) -> list[ExcerptLine]
  def line_span(excerpt: list[ExcerptLine]) -> str      # "141-143", or "141"
  ```

Rules `build_discussions` implements (spec, "What a discussion is"):
- Only public reviews; within them, only public replies count. Messages: the top-level comment, then the reply comments with `reply_to == comment.id` and the same kind, by timestamp.
- A review's top text is a discussion (`review <id>`) when `body_top.strip()` is non-empty and not `"Ship It!"`, or a public reply has a non-empty `body_top`; its messages are the top text, then those reply `body_top`s.
- State: issue `resolved`/`dropped` → `done`. Otherwise, if the last message's author is `user`: `waiting on reviewer` when the comment has an issue (open or verifying), else `done`. Otherwise `needs you`.
- Order: reviews as given, comments in each review in the order given.

`code_excerpt` marks rows `first_row .. first_row + num_rows - 1` and adds `context` rows on each side that exist in `rows`. Each line uses the new side (`new_line`, `new_text`) when `new_line` is set, else the old side.

- [ ] **Step 1: Write the failing tests** (`tests/test_discussions.py`; small builders for `Comment`/`Reply`/`Review` at the top of the file):
  - `test_keys`: `comment_key` gives `"#37441"`, `"#g812"`, `"#a95"` for diff/general/attachment.
  - `test_open_issue_from_reviewer_needs_you`: ark-slav's open issue, no replies → one discussion, key `#37441`, state `needs you`, messages `[Message("ark-slav", ts, "Why does it matter?")]`.
  - `test_user_reply_waits_on_reviewer`: + public reply by `kostik` → `waiting on reviewer`, 2 messages.
  - `test_reviewer_answer_after_user_needs_you_again`: + later reply by ark-slav → `needs you`, 3 messages in time order.
  - `test_unpublished_reply_does_not_count`: user's reply with `public=False` → `needs you`, 1 message.
  - `test_closed_issues_are_done`: `resolved` and `dropped` → `done`.
  - `test_verifying_waits_on_reviewer`: `verifying-resolved`, user last → `waiting on reviewer`.
  - `test_comment_without_issue`: reviewer last → `needs you`; after the user's reply → `done`.
  - `test_review_top_text`: body_top `"Looks fine, see below"` → discussion `review 36005`; body_top `"Ship It!"` with no top-text replies → none.
  - `test_ship_its`: two ship-it reviews by the same reviewer and one by another → `["ark-slav", "ark-roma"]` (first-seen order, unique).
  - `test_excerpt_marks_commented_rows`: rows 137-147 (new side 137-147), `first_row=141, num_rows=3, context=2` → lines 139-145, `marked` exactly for 141-143; `line_span` → `"141-143"`.
  - `test_excerpt_of_removed_lines`: commented rows have `new_line=None`, `old_line` 60-61 → excerpt uses old numbers/texts; `line_span` → `"60-61"`.
  - `test_excerpt_at_file_start`: `first_row=1`, `context=2` → starts at row 1.

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement the two modules.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit** — `feat(comments): RB model and discussion states`

---

### Task 3: Reply input format

**Files:**
- Create: `src/gg/reply_input.py`
- Test: `tests/test_reply_input.py`

**Interfaces:**
- Produces:
  ```python
  STATUS_WORDS = {"fixed": "resolved", "dropped": "dropped", "open": "open"}
  Block(request: str, target: str, status: str | None, text: str)   # frozen
  #   request "19954"; target "#37441" | "#g812" | "#a95" | "review 36005";
  #   status: the word as written ("fixed" | "dropped" | "open") or None
  class ReplyInputError(Exception)   # message lists every problem, with line numbers
  def parse_blocks(text: str) -> list[Block]
  def format_blocks(blocks: list[Block]) -> str   # the input format; parse_blocks(format_blocks(b)) == b
  ```

Rules (spec, "`gg comments reply`: post"):
- A header is any line starting with `=== `; it must match `=== [r/]<digits> (#[ga]?<digits>|review <digits>)[ <status word>]`, else an error.
- Text runs to the next header; leading and trailing blank lines are trimmed, everything else is kept as is.
- Errors (all collected, then raised together): a header that does not parse; a status word on a `review` header; two blocks with the same request and target; a block with no text and no status word; non-blank text before the first header; no blocks at all.

- [ ] **Step 1: Write the failing tests:**
  - `test_parses_spec_example`: the four-block example from the spec → `[Block("19954", "#37441", None, "Fewer than 3 traffic classes is legal, but then the 3-TC\nconfigurations below are skipped; the warning says why."), Block("19917", "#37502", "fixed", ...), Block("19920", "#37510", "dropped", ...), Block("19954", "review 36005", None, "Thanks!")]`.
  - `test_r_prefix_optional_and_letter_ids`: `=== 19954 #g812` and `=== r/19954 #a95 open`.
  - `test_text_keeps_code_and_inner_blank_lines`: text `"\nSee:\n\n```c\nif (x)\n    y();\n```\n\n"` → `"See:\n\n```c\nif (x)\n    y();\n```"`.
  - `test_status_only_block`: `=== r/19917 #37502 fixed` with no text → `Block(..., "fixed", "")`.
  - `test_errors_are_collected`: input with a bad header on line 1 (`=== r/19954 37441`), a `review 1 fixed` header on line 3, and a duplicate `#5` → one `ReplyInputError` whose message contains `line 1`, `line 3` and `#5`.
  - `test_empty_block_and_leading_text_rejected`; `test_no_blocks_rejected`.
  - `test_round_trip`: `parse_blocks(format_blocks(blocks)) == blocks` for the spec example plus a status-only block.

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement `reply_input.py`.**
- [ ] **Step 4: Run the full suite.**
- [ ] **Step 5: Commit** — `feat(comments): parse the reply format`

---

### Task 4: RBTools client

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (via `uv add 'rbtools>=7.0'`)
- Create: `src/gg/rb_client.py`
- Test: `tests/test_rb_client.py`

**Interfaces:**
- Consumes: `rb_model` (Task 2).
- Produces:
  ```python
  def connect(*, cwd: Path) -> RBApi            # an RBToolsApi
  class RBToolsApi:                             # implements RBApi
  # pure translators (JSON payload dicts as the RB API returns them):
  def comment_from_json(kind: str, data: dict, *, path: str | None = None,
                        attachment: str | None = None) -> Comment
  def reply_comment_from_json(kind: str, data: dict) -> ReplyComment
  def diff_rows_from_json(data: dict) -> list[DiffRow]
  def call_with_retry(fn: Callable[[], T], *, sleep: Callable[[float], None] = time.sleep) -> T
  ```

Notes:
- Client setup the way `rbt` does it (`rbtools/commands/base/commands.py:get_api`): `rbtools.config.loader.load_config()` from `cwd`, then `RBClient(REVIEWBOARD_URL, username=…, password=…, api_token=API_TOKEN, verify_ssl=not DISABLE_SSL_VERIFICATION, ca_certs=…, client_key=…, client_cert=…)`; the default cookie file, so an `rbt login` session works. Private RBTools modules are acceptable.
- `comment_from_json`: `diff_revision` / `filediff_id` from the `filediff` link href (`…/diffs/<rev>/files/<id>/`), `interdiff_revision` / `interfilediff_id` likewise from `interfilediff` when present; `first_line`/`num_lines` → `first_row`/`num_rows`; `severity` from `extra_data`; `issue_status` `""` when `issue_opened` is false; author from `links.user.title`.
- `reply_comment_from_json`: `reply_to` is the last path segment of `links.reply_to.href`.
- `diff_rows_from_json`: rows from every chunk's `lines` (`[row, old_no, old_text, _, new_no, new_text, …]`); an empty line number → `None`; texts `html.unescape`d.
- `diff_rows`: the file diff's diff data (`get_diff_data()`; for an interdiff comment, the interdiff's), cached per `(rid, revision, filediff, interfilediff)`. `path` comes from the file diff's `dest_file`, attachment names from the attachment's `filename`; cache both.
- `reviews(rid)`: reviews with their diff, general and file attachment comments and their replies with reply comments; one RB call per list, all pages.
- `create_reply`: POST a reply (RB returns the user's existing draft if there is one).
- `call_with_retry`: an RBTools `APIError` with error code 114 or HTTP status 429 is retried after each delay of `rbt_retry.rate_limit_schedule()`, printing `[gg] ReviewBoard rate limit; retrying in <N>s` to stderr; any other RBTools error, or running out of retries, → `RBError(<what> failed: <message>)`. Every RB call in `RBToolsApi` goes through it.

- [ ] **Step 1: Add the dependency** — `uv add 'rbtools>=7.0'`; `uv run python -c "import rbtools"` works.
- [ ] **Step 2: Write the failing tests** (payloads shaped like the recon: r/19954 diff comment 37441, a reply comment pointing at it, a diff-data chunk with `&quot;`):
  - `test_diff_comment_from_json` → `Comment("diff", 37441, "ark-slav", ts, "Why does it matter?", True, "open", "info", path="net-drv-ts/dcb/dcb_ets.c", filediff_id=234050, diff_revision=1, first_row=141, num_rows=3)`.
  - `test_comment_without_issue_has_empty_status`.
  - `test_interdiff_comment_from_json` → `interdiff_revision` and `interfilediff_id` set.
  - `test_reply_comment_from_json` → `reply_to == 37441`.
  - `test_diff_rows_from_json` → `DiffRow(141, None, "", 141, "    if (current_ets.ets_cap < 3)")`, and `&quot;` unescaped to `"`.
  - `test_retry_on_rate_limit`: a function raising `APIError` with code 114 once, then returning 5 → returns 5, `sleep` called once with `rate_limit_schedule()[0]` (set `GG_RBT_RATE_LIMIT_INITIAL_DELAY=0` via monkeypatch).
  - `test_other_error_becomes_rb_error`.
- [ ] **Step 3: Run to see them fail.**
- [ ] **Step 4: Implement `rb_client.py`.**
- [ ] **Step 5: Run the full suite.**
- [ ] **Step 6: Read-only smoke check against the real server** (from `/home/kostik/prj/amd/x4a/te`): `uv run --project <worktree> python -c "from pathlib import Path; from gg.rb_client import connect; api = connect(cwd=Path.cwd()); print(api.user()); rv = api.reviews('19954'); print([(c.id, c.issue_status, c.path) for r in rv for c in r.comments]); print(api.diff_rows('19954', rv[0].comments[0])[140])"` → `kostik`, `[(37441, 'open', 'net-drv-ts/dcb/dcb_ets.c')]`, row 141 with the `ets_cap < 3` line. (The issue may have been answered since; any consistent output is fine.)
- [ ] **Step 7: Commit** — `feat(comments): ReviewBoard client over RBTools`

---

### Task 5: `gg comments`

**Files:**
- Create: `src/gg/comments_format.py`, `src/gg/comments.py`, `tests/fake_rb.py`
- Modify: `src/gg/cli.py` (register; route `comments reply`), `src/gg/git.py` (`has_upstream`)
- Test: `tests/test_comments_format.py`, `tests/test_comments_cmd.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4 (`branch_planner`, `series_spec`, `selected_revs`, `build_discussions`, `ship_its`, `code_excerpt`, `line_span`, `connect`), `rb_links.links_for`.
- Produces:
  ```python
  # comments_format.py
  Place(series: int, patch: int, commit: str)          # frozen; commit: first 8 chars
  RequestView(info: RequestInfo, place: Place | None, ship_its: list[str],
              discussions: list[Discussion],
              code: dict[str, list[ExcerptLine] | None])  # diff discussions by key; None: not available
  def format_view(view: RequestView, *, links: Links, show_all: bool) -> str
  def format_totals(views: list[RequestView], *, show_all: bool) -> str
  # comments.py
  def add_parser(subparsers) -> None
  def run(args: argparse.Namespace) -> int
  def connect(cwd: Path) -> RBApi        # rb_client.connect; tests monkeypatch this name
  # git.py
  def has_upstream(*, cwd: Path | None = None) -> bool
  # tests/fake_rb.py
  class FakeRB:   # implements RBApi in memory
      def __init__(self, user: str = "kostik") -> None
      def add_request(self, rid: str, summary: str, *, latest_diff: int | None = 1) -> None
      def add_review(self, rid: str, review: Review) -> None
      def set_rows(self, filediff_id: int, rows: list[DiffRow]) -> None
      def set_draft(self, rid: str, review_id: int, draft: Reply) -> None
      log: list[tuple]          # every write call: (method name, args...)
      fail: dict[str, tuple[int, RBError]]  # method name -> (n, error): raise on its n-th call (1-based)
      status_result: dict[int, str]   # comment id -> status set_issue_status reports
  ```
  `FakeRB` raises `RBError` for unknown request ids, keeps drafts per (rid, review), `create_reply` returns the existing draft id or a new one, `publish_reply` makes the reply public in `reviews()`.

Output layout (`format_view`), pinned by the tests:

```
r/19954  [12/14]: dcb/dcb_ets: test valid ETS configurations via dcb
         series 3, patch 12, commit 1a2b3c4d   Ship It: ark-slav
  #37441  ark-slav  issue open (info)  needs you  diff 1 of 1
    net-drv-ts/dcb/dcb_ets.c:141-143
        139      TEST_VERDICT("Failed to retrieve DCB configuration: %r", rc);
        140
      > 141      if (current_ets.ets_cap < 3)
      > 142          WARN_VERDICT("IUT supports fewer than 3 Traffic Classes: %u",
      > 143                       current_ets.ets_cap);
        144
        145      if (current_ets.willing)
    ark-slav, 2026-10-20: Why does it matter?
```

- Request line: `links.show(id)`, two spaces, summary. Second line (9 spaces) only when there is a place or a Ship It: the place part and `Ship It: a, b`, joined by three spaces.
- Discussion line: two spaces, key, reviewer (author of the first message), then `issue <status>` with ` (<severity>)` when known, or `comment` when there is no issue, then the state; diff comments add `diff N of M` (`diff N` when the latest is unknown), interdiff comments `interdiff N-K of M`. Fields joined by two spaces. Review top text: `  review <id>  <author>  <state>`.
- Diff comments: `    <path>:<line_span>`, then the excerpt: `      > ` (marked) or 8 spaces, the line number right-aligned to the widest, two spaces, the text (right-stripped; no trailing spaces on an empty line). Code not available: `    <path> (rows <first>-<last>): code not available`.
- Attachment comments: `    attachment: <name>`.
- Messages: `    <author>, <YYYY-MM-DD>: <first line>`, further lines indented 6 spaces (empty lines stay empty).
- A blank line between discussions; with `show_all`, a request without discussions shows `  no discussions`.
- `format_totals`: `1 discussion needs you, 0 wait on reviewers.` — `discussion needs`/`discussions need`, `waits`/`wait`; with `show_all`, `, <n> done` before the period.

Command behavior (`gg comments [ids...] [-s SPEC] [--all]`):
- Ids `r/123` or `123` (else argparse error). Ids and `-s` together → `[gg] -s and review request ids cannot be combined.`, exit 1.
- Default scope: the live series rows of `branch_planner(cwd).plan_for(set(full))` that have a review (`old_entry`), in plan order. `-s`: only those whose commit is in `selected_revs(...)` (its error string → `[gg] <msg>`, exit 1). Ids: as given; places from the branch plan when `git.has_upstream()`, else none.
- Empty scope → `[gg] nothing posted to ReviewBoard in this scope.`, exit 1.
- `api = connect(cwd)`; `user = api.user()`; requests fetched with `ThreadPoolExecutor(max_workers=8)`; diff rows fetched only for diff discussions that will be shown, cached per file diff within a request; a failed `diff_rows` → `None` (code not available).
- Shown: discussions in `needs you`/`waiting on reviewer` (all with `--all`); requests with shown discussions (all with `--all`); then a blank line and `format_totals`. A request whose fetch fails → `[gg] r/<id>: <error>` on stderr, the others still shown, exit 1; otherwise exit 0.
- `--help` (RawDescription epilog): the three selection forms, `--all`, the states, the discussion key forms, and "To reply: gg comments reply --help". `cli.main`: when `argv[:2] == ["comments", "reply"]` (argv defaulting to `sys.argv[1:]`), call `comments.run_reply(argv[2:])` (defined in Task 6; until then a stub that prints `not implemented` and returns 1).

- [ ] **Step 1: Write the failing format tests** (`tests/test_comments_format.py`): `test_spec_example_layout` (the block above, exactly, for a view built from Task 2 objects and `Links()`), `test_comment_without_issue_and_general_comment`, `test_review_top_text_line`, `test_code_not_available`, `test_multiline_message_indent`, `test_totals_wording` (`(1, 0)` → `1 discussion needs you, 0 wait on reviewers.`; `(2, 1)` → `2 discussions need you, 1 waits on reviewers.`; show_all adds `, 3 done`).
- [ ] **Step 2: Write the failing command tests** (`tests/test_comments_cmd.py`, in-process: `monkeypatch.chdir(git_repo.work_dir)`, `monkeypatch.setattr(comments, "connect", lambda cwd: fake)`, `cli.main([...])`, `capsys`):
  - `test_explicit_id`: fake r/19954 as in the spec → exit 0; output has `#37441`, `needs you`, `net-drv-ts/dcb/dcb_ets.c:141-143`, `1 discussion needs you`.
  - `test_done_hidden_unless_all`: a resolved issue → not shown; `--all` shows it and `, 1 done`.
  - `test_default_scope_is_the_branch`: `TestSeries._stack` + `rbt-sync master..HEAD~1` (rbt_mock ids), fake has an open issue on the review of `c2` → output shows its id with `series 2, patch 2, commit <first 8 of c2>`; with `-s 1` → exit 0 and the c2 request is not in the output.
  - `test_new_series_only_scope_is_empty`: `-s 3` (only `n1`, unposted) → exit 1, `nothing posted to ReviewBoard in this scope`.
  - `test_ids_and_series_refused`.
  - `test_request_error_reported_others_shown`: one unknown id + one good id → exit 1, stderr `r/<bad>`, stdout shows the good one.
  - `test_help_lists_forms`: `cli.main(["comments", "--help"])` → `SystemExit(0)`, stdout contains `gg comments -s`, `review <id>`, `gg comments reply --help`.
- [ ] **Step 3: Run to see them fail.**
- [ ] **Step 4: Implement** `comments_format.py`, `comments.py`, `git.has_upstream`, `tests/fake_rb.py`, the `cli.py` registration and routing.
- [ ] **Step 5: Run the full suite.**
- [ ] **Step 6: Read-only check on the real branch** (from `/home/kostik/prj/amd/x4a/te`): `uv run --project <worktree> gg comments r/19954` and `uv run --project <worktree> gg comments -s 3` — r/19954's discussion (or none if answered) with real code; series 3 within well under a minute.
- [ ] **Step 7: Commit** — `feat(comments): gg comments lists discussions with their code`

---

### Task 6: `gg comments reply`, docs

**Files:**
- Create: `src/gg/reply_post.py`
- Modify: `src/gg/comments.py` (`run_reply`), `README.md`, `CLAUDE.md`
- Test: `tests/test_comments_reply.py`

**Interfaces:**
- Consumes: Task 3 (`parse_blocks`, `format_blocks`, `Block`, `STATUS_WORDS`, `ReplyInputError`), Task 2 (`comment_key`, `RBApi`, `RBError`), Task 5 (`connect`, `FakeRB`).
- Produces:
  ```python
  def post_blocks(api: RBApi, blocks: list[Block], *, dry_run: bool, links: Links) -> int
  def run_reply(argv: list[str]) -> int     # in comments.py; parser prog "gg comments reply"
  ```

`gg comments reply [FILE] [-d]` reads FILE, or stdin when none. `post_blocks`, in order:
1. **Check.** `api.reviews()` for every request in the input (an `RBError` is an error for that request). Resolve each block: comment key → that request's comment (any review), `review <id>` → that request's review. Errors: unknown request, comment or review; a status word on a comment without an issue. All collected; any error → printed to stderr, exit 1, no write call.
2. **Drafts.** `api.draft_reply()` for every review the input touches. Drafts with content (a reply comment, or non-empty `body_top`) → print `[gg] r/<id> review <id>: you have an unpublished reply draft there; nothing was posted.` and the draft's texts, exit 1, no write call.
3. **Dry run** (`-d`): print one line per block, `r/<id> <target>  reply` / `, set <status word>`, exit 0.
4. **Post**, review by review in order of first appearance: if the review has blocks with text, `create_reply`, then `add_reply_comment` / `set_reply_top` per block in input order, then `publish_reply`; then `set_issue_status(STATUS_WORDS[word])` for its blocks with a status word. Print per block: `<links.show(rid)> <target>  replied` and/or the status RB reports (`resolved`, `verifying-resolved`, …), comma-joined.
5. **Failure** (`RBError` while posting): print the error to stderr; if a reply was created but not published, `delete_reply` it (an error there is printed too). Print `Not posted (send again as is):` and `format_blocks()` of everything not done: blocks of the failed review whose reply was not published, all later blocks, and, for a block whose reply was published but whose status change failed, a status-only block. Exit 1.

- [ ] **Step 1: Write the failing tests** (`tests/test_comments_reply.py`, in-process, stdin via `monkeypatch.setattr("sys.stdin", io.StringIO(...))`, fake as in Task 5):
  - `test_one_reply_per_review_then_status`: two blocks on review 36005 (`#37441` text, `#37442 fixed` text) → `fake.log` = create, add ×2, publish, set_issue_status(37442, "resolved"); stdout `r/19954 #37441  replied` and `r/19954 #37442  replied, resolved`.
  - `test_verifying_result_is_reported`: `status_result[37442] = "verifying-resolved"` → printed.
  - `test_review_top_reply`: `=== r/19954 review 36005` → `set_reply_top`.
  - `test_status_only_block_posts_no_reply`: no create/publish, only the status call.
  - `test_errors_post_nothing`: unknown comment, a comment of another request, a status on a no-issue comment → exit 1, stderr names all three, `fake.log == []`.
  - `test_parse_error_posts_nothing`: a bad header → exit 1, line number on stderr, no writes.
  - `test_draft_with_content_stops_whole_batch`: draft with a reply comment on review A, blocks on A and on review B → exit 1, the draft's text printed, `fake.log == []`.
  - `test_empty_draft_is_used`: empty draft on the review → its id is the one published.
  - `test_dry_run_posts_nothing`.
  - `test_failure_deletes_draft_and_prints_unsent`: one block on review A, then one on review B; `fail["add_reply_comment"] = (2, RBError("boom"))` → first review published; `delete_reply` for the second; stdout ends with `Not posted (send again as is):` + the second review's blocks and later ones, parseable by `parse_blocks`; exit 1.
  - `test_status_failure_prints_status_only_block`: `fail["set_issue_status"] = (1, RBError("boom"))` → reply published, unsent part is `=== r/19954 #37442 fixed` with no text.
  - `test_reads_file_argument`.
  - `test_help_documents_format`: `gg comments reply --help` → contains `=== r/<request> #<comment> [fixed|dropped|open]`, `=== r/<request> review <review>`, `#g`, `#a`, `<<'EOF'`, `-d`.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement** `reply_post.py` and `run_reply` (help: the format, the rules from step list above in short, the spec's example, and the agent workflow from the spec's "The agent's side").
- [ ] **Step 4: Docs.** README: a "Review comments" subsection under the ReviewBoard commands — the command table rows (`gg comments`, `-s`, ids, `--all`, `gg comments reply`, `-d`), the reply format with the example, the agent workflow (spec "The agent's side"), and that gg needs reinstalling once for the RBTools dependency (`./install.sh --gg`). CLAUDE.md: the new modules in "Supporting modules", and that `gg comments` uses RBTools as a library while the rest shells out to `rbt`.
- [ ] **Step 5: Run the full suite.**
- [ ] **Step 6: Read-only check:** from `te`, `uv run --project <worktree> gg comments reply -d <<'EOF'` with a block for an existing open comment → the dry-run line, nothing posted. Never run it without `-d`.
- [ ] **Step 7: Commit** — `feat(comments): gg comments reply posts replies and issue statuses`
