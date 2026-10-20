# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Git-helpers is a hybrid bash + Python toolkit that implements a branch-based development workflow on top of git. It supports both ReviewBoard (`rbt`) and GitHub pull request workflows.

- Bash side (`bashrc.gitgo`, `gitconfig.*`): branch lifecycle (`gowork/gopull/gopush/goclose/godiscard/gopublish`), GitHub PR helper (`gopr`), cross-repo sync (`gosyncfrom/gosyncto`), and log visualization.
- Python side (`src/gg/`): the `gg` CLI, which owns all ReviewBoard workflows -- posting, sync/reconcile, import, review comments, and state inspection.

## Installation

```shell
./install.sh          # shows the options
./install.sh --full   # symlinks bashrc.gitgo + installs gg via `uv tool install -e`
./install.sh --gg     # only (re)install the gg CLI
./install.sh --skill  # link skills/gg (agent guide to gg) into ~/.claude/skills/gg
```

`uv` is required for the Python side; if it's missing, the installer skips `gg` and prints the command to run later.

## Architecture

### Bash side

All `go*` git subcommands follow the same pattern: a git alias in `gitconfig.go` sources `~/.bashrc.gitgo` and calls the corresponding `git_go*()` bash function.

- **bashrc.gitgo** -- core workflow functions. Three sections: branch lifecycle, PR helpers (`gopr`), cross-repo sync (`gosyncfrom/gosyncto`).
- **gitconfig.go** -- git aliases wiring `git go*` to bash functions, plus `git gg`, `git gorbt` (-> `gg rbt`), and `git rbt` (-> `gg rbt-sync`).
- **gitconfig.tree** -- `git tree` / `git gotree` log visualization.
- **gitconfig.alias** -- small standalone aliases (`up`, `refresh`, `graft`, `show-stat`).
- **bashrc.vgit** -- `vgit` shell function; sourced directly from `.bashrc`.
- **vimrc.git** -- `:Gitt` command and `includeexpr` fix for git-style `a/b/` path prefixes.

### Python side (`src/gg/`)

Entry point: `gg.cli:main` (exposed as the `gg` script via pyproject). `cli.py` dispatches to subcommand modules, each of which registers its own argparse subparser:

- **rbt.py** -- `gg rbt`: post a commit series to ReviewBoard. With `-u --publish` it also calls `rbt publish` on unchanged drafts (looking up the review id from `reviews.db`).
- **sync.py** -- `gg rbt-sync`: reconcile the branch, as a list of patch series, against the posted reviews (keep/update/keep+dep/create/discard/submit/forget), with `-i` to edit the plan in `$EDITOR` (`---` lines split/merge series), `--new` to forget the branch's own record, `--close` to close everything. With `-p`, a KEEP entry is pushed through `rbt publish` only when `reviews.db` records an unpublished draft gg left on it.
- **publish.py** -- `gg publish`: read `reviews.db` for the current branch and run `rbt publish <id>` on every entry with an unpublished draft (`--all`: every entry), then clear their draft marks. The "post drafts now, publish later" half of the workflow.
- **rbt_import.py** -- `gg rbt-import`: walk an existing RB dependency chain and populate `reviews.db`.
- **db.py** -- `gg db`: list/clear/reinit state in `.gg/reviews.db`.
- **comments.py** -- `gg comments`: list the reviewers' discussions on the branch's review requests (the whole branch, `-s` series as in `rbt-sync`, or given ids) with the commented code; `gg comments reply` (routed here by `cli.main`) posts replies and issue status changes through `reply_post.py`. Unlike the rest of gg, which shells out to `rbt`, `gg comments` uses RBTools as a Python library (`rb_client.py`).

Supporting modules: `series.py` (series planner: splits the branch into series, picks up series recorded under other branches, classifies gone reviews, chains series with depends-on), `matcher.py` (commit/review reconciliation with fuzzy subject matching), `review_store.py` (SQLite schema + CRUD; subject-prefix parsing), `rb_api.py` (ReviewBoard API shim over `rbt api-get`), `rb_links.py` (review links: the RB server URL from `.reviewboardrc`, `r/N` as an OSC 8 hyperlink on a terminal or the full URL with `rbt-sync -l`), `rbt_post.py` / `rbt_close.py` / `rbt_publish.py` (shell out to `rbt post` / `rbt close` / `rbt publish`), `rbt_retry.py` (classifies transient `rbt` failures and retries on rate-limit / missing-base errors), `sync_plan.py` / `sync_edit.py` (plan formatting + interactive editor round-trip), `numbering.py` (fractional/full renumbering), `diff_cache.py` (commit diff hashing for change detection), `git.py` (git plumbing wrappers), `bugs.py` (bug-id extraction from summaries), `branch_plan.py` (plans the current branch as patch series; shared by `rbt-sync` and `gg comments`), `series_select.py` (the `-s` selection: parsing and resolving it), `rb_model.py` (the ReviewBoard data model and the `RBApi` protocol of `gg comments`), `rb_client.py` (`RBApi` over the RBTools library: reads, reply and issue-status writes, rate-limit retries), `discussions.py` (discussions, their states and code excerpts), `comments_format.py` (`gg comments` output), `reply_input.py` (the reply block format: parsing and formatting), `reply_post.py` (checks a reply batch, stops on unpublished reply drafts, posts one reply per review then the issue statuses, and prints what was not posted on failure).

### State

Per-repo state lives in `.gg/reviews.db` (SQLite, WAL mode) at the repo root, keyed by branch. Stores review entries (branch, position, review_id, subject, diff_hash, series -- the number of the patch series the review belongs to, 1 from the bottom of the branch; `position` runs along the whole branch; draft -- gg posted it without publishing, so an unpublished draft waits) and diff-hash cache. `.gg/` is gitignored.

### Workflow pattern

`gowork` creates a tracking branch -> make commits -> `gg rbt-sync` (or `gopr`) for review -> `gopull` to rebase -> `gopush` to land -> `goclose` to clean up.

## Testing

```shell
uv run pytest tests/ -v
```

Tests cover both the bash functions (via `bash -c 'source bashrc.gitgo; ...'`) and the Python CLI (via `python -m gg`). Key fixtures in `tests/conftest.py`:

- `git_repo` -- temporary bare origin + working clone with initial commit on `master`.
- `rbt_mock` -- fake `rbt` script on `PATH` that logs invocations as JSON and fakes review-request responses, so tests never touch a real ReviewBoard.

`gg comments` tests use `tests/fake_rb.py`'s `FakeRB`, an in-memory `RBApi` that logs write calls and can be told to fail; they run in-process (`cli.main([...])`) with `gg.comments.connect` monkeypatched to return it.

There is no linter configured. The Python code uses `from __future__ import annotations` and type hints throughout; match that style in new modules.

## Conventions

- Every bash function supports `-h`/`--help` via `is_help()`.
- Dry-run (`-d`/`--dry`) on any command that performs remote operations; `gg rbt-sync -d` prints the plan without executing.
- macOS: `gsed` is auto-detected and used instead of `sed` when available.
- `GG_GIT_HELPERS_FORKNAME` env var configures the remote used by `gosyncfrom/gosyncto`.
- Review subject prefixes `[i/N]:` (or fractional `[i.j/N]:`) are managed by `numbering.py`; don't hand-craft them.
