# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Git-helpers is a collection of bash shell functions and git config snippets that implement a branch-based development workflow on top of git. It supports both ReviewBoard (`rbt`) and GitHub pull request workflows.

There is no build system, test suite, or linter. The codebase is pure bash and gitconfig.

## Installation

```shell
./install.sh
```

This symlinks `bashrc.gitgo` to `~/.bashrc.gitgo` and prints manual instructions for `.bashrc` and `.gitconfig` additions.

## Architecture

All `go*` git subcommands follow the same pattern: a git alias in `gitconfig.go` sources `~/.bashrc.gitgo` and calls the corresponding `git_go*()` bash function.

### File roles

- **bashrc.gitgo** - Core workflow functions. The main file; all `go*` commands live here. Three sections: branch lifecycle (`gowork/gopull/gopush/goclose/godiscard/gopublish`), ReviewBoard helpers (`gorbt`), and pull request helpers (`gopr`). Also contains sync primitives (`gosyncfrom/gosyncto`) for copying branches between firewalled repos via a GitHub fork.
- **gitconfig.go** - Git aliases that wire `git go*` subcommands to the bash functions in `bashrc.gitgo`.
- **gitconfig.tree** - `git tree` and `git gotree` aliases for graph-based log visualization.
- **gitconfig.alias** - Small standalone aliases (`up`, `refresh`, `graft`, `show-stat`).
- **bashrc.vgit** - `vgit` shell function that opens git output in vim's terminal mode. Sourced directly from `.bashrc`.
- **vimrc.git** - Vim helpers: `:Gitt` command and `includeexpr` fix for git-style `a/b/` path prefixes.

### Workflow pattern

`gowork` creates a tracking branch -> make commits -> `gorbt` or `gopr` for review -> `gopull` to rebase -> `gopush` to land -> `goclose` to clean up.

### Key conventions

- Every function supports `-h`/`--help` via `is_help()`.
- Dry-run mode (`-d`/`--dry`) is available on commands that perform remote operations.
- macOS compatibility: `gsed` is auto-detected and used instead of `sed` when available.
- `GG_GIT_HELPERS_FORKNAME` env var configures sync commands.
