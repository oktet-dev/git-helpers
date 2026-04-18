# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Git-helpers is a hybrid bash + Python toolkit that implements a branch-based development workflow on top of git. It supports both ReviewBoard (`rbt`) and GitHub pull request workflows.

- Bash side (`bashrc.gitgo`, `gitconfig.*`): branch lifecycle (`gowork/gopull/gopush/goclose/godiscard/gopublish`), GitHub PR helper (`gopr`), cross-repo sync (`gosyncfrom/gosyncto`), and log visualization.
- Python side (`src/gg/`): the `gg` CLI, which owns all ReviewBoard workflows -- posting, sync/reconcile, import, and state inspection.

## Installation

```shell
./install.sh          # symlinks bashrc.gitgo + installs gg via `uv tool install -e`
./install.sh --gg     # only (re)install the gg CLI
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

- **rbt.py** -- `gg rbt`: post a commit series to ReviewBoard.
- **sync.py** -- `gg rbt-sync`: reconcile the current commit series against the last posted set (keep/update/create/discard), with `-i` to edit the plan in `$EDITOR`, `--new` to start a fresh series, `--close` to close everything.
- **rbt_import.py** -- `gg rbt-import`: walk an existing RB dependency chain and populate `reviews.db`.
- **db.py** -- `gg db`: list/clear/reinit state in `.gg/reviews.db`.

Supporting modules: `matcher.py` (commit/review reconciliation with fuzzy subject matching), `review_store.py` (SQLite schema + CRUD; subject-prefix parsing), `rb_api.py` (ReviewBoard API shim over `rbt api-get`), `rbt_post.py` / `rbt_close.py` (shell out to `rbt post` / `rbt close`), `sync_plan.py` / `sync_edit.py` (plan formatting + interactive editor round-trip), `numbering.py` (fractional/full renumbering), `diff_cache.py` (commit diff hashing for change detection), `git.py` (git plumbing wrappers), `bugs.py` (bug-id extraction from summaries).

### State

Per-repo state lives in `.gg/reviews.db` (SQLite, WAL mode) at the repo root, keyed by branch. Stores review entries (branch, position, review_id, subject, diff_hash) and diff-hash cache. `.gg/` is gitignored.

### Workflow pattern

`gowork` creates a tracking branch -> make commits -> `gg rbt` or `gg rbt-sync` (or `gopr`) for review -> `gopull` to rebase -> `gopush` to land -> `goclose` to clean up.

## Testing

```shell
uv run pytest tests/ -v
```

Tests cover both the bash functions (via `bash -c 'source bashrc.gitgo; ...'`) and the Python CLI (via `python -m gg`). Key fixtures in `tests/conftest.py`:

- `git_repo` -- temporary bare origin + working clone with initial commit on `master`.
- `rbt_mock` -- fake `rbt` script on `PATH` that logs invocations as JSON and fakes review-request responses, so tests never touch a real ReviewBoard.

There is no linter configured. The Python code uses `from __future__ import annotations` and type hints throughout; match that style in new modules.

## Conventions

- Every bash function supports `-h`/`--help` via `is_help()`.
- Dry-run (`-d`/`--dry`) on any command that performs remote operations; `gg rbt-sync -d` prints the plan without executing.
- macOS: `gsed` is auto-detected and used instead of `sed` when available.
- `GG_GIT_HELPERS_FORKNAME` env var configures the remote used by `gosyncfrom/gosyncto`.
- Review subject prefixes `[i/N]:` (or fractional `[i.j/N]:`) are managed by `numbering.py`; don't hand-craft them.
