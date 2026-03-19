Git Helpers
===========

A collection of shell functions and git aliases that implement a
branch-based development workflow on top of git. Supports both
ReviewBoard (`rbt`) and GitHub pull request workflows.

Installation
------------

```shell
git clone <repo>
cd git-helpers
./install.sh
```

The installer will:

1. Symlink `bashrc.gitgo` to `~/.bashrc.gitgo`
2. Install the Python `gg` CLI tool via `uv tool install`
3. Print snippets to add to your `~/.bashrc` and `~/.gitconfig`

If `uv` is not installed, the script warns and skips the Python CLI.
You can install it later with:

```shell
./install.sh --gg
```

On macOS, make sure `gsed` is in PATH.

Command reference
-----------------

All commands support `-h`/`--help`. Most commands that perform remote
operations support `-d`/`--dry` for dry-run.

### Branch lifecycle (bash, via git aliases)

| Command | Description |
|---------|-------------|
| `git gowork <name>` | Create a tracking branch from the current branch |
| `git gopull` | Fetch tracking branch and rebase on top of it |
| `git gopush` | Push current branch to origin's tracking branch |
| `git goclose` | Switch to tracking branch and delete the current one |
| `git godiscard` | Discard all changes and delete the branch |
| `git gopublish` | Push branch to origin as `user/<UID>/<branch>` |
| `git gostatus` | Show current branch with verbose tracking info |
| `git golog` | Git log for commits since tracking branch |
| `git goshow` | Git show for commits since tracking branch |

### ReviewBoard (Python CLI, via `git gg`)

| Command | Description |
|---------|-------------|
| `git gg rbt` | Post commit series to ReviewBoard |
| `git gg rbt-sync` | Reconcile series with ReviewBoard (keep/update/create/discard) |
| `git gg rbt-sync -i` | Interactive mode -- edit the sync plan in `$EDITOR` before executing |
| `git gg rbt-sync -U alice -G devteam` | Override reviewers/groups for new reviews |
| `git gg rbt-sync --no-numbers` | Suppress `[i/N]:` prefix on posted reviews |
| `git gg rbt-import` | Import an existing ReviewBoard chain into `reviews.db` |
| `git gg db` | Inspect and manage `.gg/reviews.db` (list/clear/reinit) |

The legacy `git gorbt` alias still works and delegates to `git gg rbt`.

### Pull requests (bash)

| Command | Description |
|---------|-------------|
| `git gopr` | Create a GitHub pull request from the current branch |

### Cross-repo sync (bash)

| Command | Description |
|---------|-------------|
| `git gosyncfrom` | Push current branch to the configured fork |
| `git gosyncto` | Fetch current branch from the configured fork |

Set `GG_GIT_HELPERS_FORKNAME` to configure the fork remote name.

### Utility aliases (gitconfig)

| Alias | Description |
|-------|-------------|
| `git up` | `pull --rebase` |
| `git refresh` | `pull --rebase` for the tracking branch |
| `git tree` / `git gotree` | Graph-based log visualization |
| `git graft` | Cherry-pick a commit |
| `git show-stat` | `show --stat` |
| `git branchname` | Print branch name of a revision |
| `git summary` | Print subject line of a revision |

Typical workflows
-----------------

### Single patch

```shell
git checkout master
git gowork Bug239

# edit, test...
git commit -a -m "Bug239: ensure that a neighbour is really deleted"

# Post for review
git gorbt

# After "Ship it!" -- rebase, push, clean up
git gopull
git gopush
git goclose
```

### Multi-patch series

```shell
git gowork Bug533
git commit -m "Bug 533: add information about alias help"
git commit -m "Bug 533: add info about dry runs for rbt commands"

# Preview the rbt commands
git gorbt -p -d -U kostik

# Post for real
git gorbt -p -U kostik
```

### Syncing a modified series

After posting a multi-patch series, you amend/reorder/add/drop commits
and want to update ReviewBoard to match:

```shell
# See what changed
git gg rbt-sync -d

# Edit the plan before executing (skip a discard, defer a create, etc.)
git gg rbt-sync -i

# Or just execute the plan directly
git gg rbt-sync

# Override reviewers/groups for any newly created reviews
git gg rbt-sync -U alice -G devteam

# Suppress [i/N]: numbering prefix
git gg rbt-sync --no-numbers
```

### Importing an existing ReviewBoard chain

If you already have reviews posted outside of git-helpers:

```shell
git gg rbt-import <review-id>
```

This walks the dependency chain, displays the reviewers/groups from the
first review, and saves state to `reviews.db` so that `rbt-sync` can
manage the series going forward.

### Upstream branches

Sometimes you need your branch upstream (backup, collaboration):

```shell
git gowork foo
# ... work ...

# First push -- creates user/<UID>/foo on origin
git gopublish --initial

# Subsequent pushes
git gopublish

# After a rebase
git gopublish -f
```

When the branch tracks origin/user/... instead of master, specify the
range explicitly for review:

```shell
git gorbt master..foo
```

Push back to master when done:

```shell
git gopush -t master
```

### Forgot to branch

```shell
git checkout master
# ... work ...
git commit -m "bug239: cool fix"
# oh, forgot to branch!

git gowork bug239
git checkout master
git reset --keep HEAD~1
git checkout bug239
```

Running tests
-------------

```shell
uv run pytest tests/ -v
```

Contributing
------------

All changes should be done via ReviewBoard with at least `git-helpers` group
set as reviewers. Project for rbt -- ol-git-helpers.

You MUST get at least **two** acks from **kostik/osadakov** if
you're not one of them, in which case one is enough.

If you're leading a project that uses git-helpers you **should** mail
<kushakov@oktet.co.il> and get yourself into the `git-helpers` list.
