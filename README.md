Git Helpers
===========

A collection of shell functions, git aliases, and a Python CLI (`gg`)
that implement a branch-based development workflow on top of git.
Supports both ReviewBoard (`rbt`) and GitHub pull request workflows.

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
| `git gg rbt-sync` | Reconcile the branch's series with ReviewBoard (keep/update/create/discard/submit/forget) |
| `git gg rbt-sync <range>` | Post only the commits in `<range>`; posted commits outside it are not re-posted (reviews of commits gone from the branch are still closed or forgotten) |
| `git gg rbt-sync -i` | Interactive mode -- edit the sync plan in `$EDITOR` before executing; `---` lines separate series (delete one to merge two series, add one between two rows to split; changed series are renumbered and re-posted). With a range, series cannot be split or merged |
| `git gg rbt-sync -U alice -G devteam` | Override reviewers/groups for new reviews |
| `git gg rbt-sync --no-numbers` | Suppress `[i/N]:` prefix on posted reviews |
| `git gg rbt-sync --renumber` | Full `[1/N]..[N/N]` renumber instead of fractional |
| `git gg rbt-sync -f` | Re-post every matched commit, ignoring the diff-hash cache |
| `git gg rbt-sync --new` | Forget this branch's reviews and post the current commits fresh (series recorded under other branches are still picked up) |
| `git gg rbt-sync --close` | Close all reviews as submitted and clear the DB |
| `git gg publish` | Publish drafts of every review request on the current branch |
| `git gg rbt-import` | Import an existing ReviewBoard chain into `reviews.db` |
| `git gg db` | Inspect and manage `.gg/reviews.db` (list/clear/reinit) |

Shortcut aliases:

- `git rbt ...` -> `git gg rbt-sync ...` (the most common command)
- `git gorbt ...` -> `git gg rbt ...` (legacy)

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

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GG_GIT_HELPERS_FORKNAME` | (unset) | Remote name used by `gosyncfrom` / `gosyncto` |
| `GG_RBT_RATE_LIMIT_RETRIES` | `3` | Retries on RB API 114 rate-limit |
| `GG_RBT_RATE_LIMIT_INITIAL_DELAY` | `10` | Seconds before first rate-limit retry |
| `GG_RBT_RATE_LIMIT_FACTOR` | `3` | Multiplier between rate-limit retries |
| `GG_RBT_MISSING_BASE_RETRIES` | `3` | Retries on RB API 207 (base commit missing from mirror) |
| `GG_RBT_MISSING_BASE_DELAY` | `300` | Seconds between missing-base retries |

Set any `RETRIES` var to `0` to disable retries for that class.

### Utility aliases (gitconfig)

| Alias | Description |
|-------|-------------|
| `git up` | `checkout` |
| `git refresh` | `commit -a --amend --no-edit` |
| `git graft` | `cherry-pick -x` |
| `git show-stat` | `show --stat` |
| `git tree` | Graph-based log visualization |
| `git gotree` | `git tree` restricted to the current branch's range |
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

`gg rbt-sync` is the everyday command. The first run on a branch
posts the series fresh (it auto-detects that `reviews.db` has nothing
for the branch and prints `[gg] No existing reviews; posting as a
fresh series.`; no notice is printed when it picks up a series recorded
under another branch instead, see below).
Subsequent runs reconcile the current commits against the last
posted set — amend/reorder/insert/drop commits and re-run to update
ReviewBoard to match (commits added on top become a new series, see
below). A commit whose subject or body changed is re-posted even if
the diff is unchanged, so RB's summary and description track the git
commit:

```shell
# See what changed
git gg rbt-sync -d

# Edit the plan before executing (skip a discard, defer a create, etc.).
# Delete a `---` line to merge two series, add one to split a series.
git gg rbt-sync -i

# Or just execute the plan directly
# (re-posts commits whose diff OR commit message changed)
git gg rbt-sync

# Force a full renumber -- re-posts every matched commit with its new
# [i/N] prefix
git gg rbt-sync --renumber

# Re-post every matched commit even if nothing changed -- e.g. after
# manual edits to a draft on the RB web UI, or to recover from a
# cache that's gone stale.
git gg rbt-sync -f

# Or on the older command: bypass the diff-hash cache
git gg rbt -u -f

# Override reviewers/groups for any newly created reviews
git gg rbt-sync -U alice -G devteam

# Suppress [i/N]: numbering prefix
git gg rbt-sync --no-numbers

# Start a fresh series, forgetting old reviews
git gg rbt-sync --new

# After "Ship it!" on the whole series -- close everything and clear state
git gg rbt-sync --close
```

### Several series on one branch

`gg rbt-sync` treats a branch as an ordered list of patch series. Each
series is a contiguous run of commits, posted as one ReviewBoard chain
and numbered `[i/N]` within that series; the first patch of a series
depends on the last patch of the series below it.

- **New commits.** Commits added after the last posted series form one
  new series. To post only some of them, end the range there; the rest
  become another new series on a later run:

  ```shell
  git gg rbt-sync origin/master..HEAD~3   # post up to HEAD~3 as a new series
  git gg rbt-sync                         # later: the rest as the next series
  ```

  New commits between two commits of a series join that series
  (`[2.1/5]`), as before.
- **Ranges.** A range only limits which commits on the branch are
  posted. Posted commits outside it are never discarded, re-posted or
  renumbered; the one exception is a dependency-only re-post
  (`keep+dep`) of an unchanged commit, listed with `(outside range)`. A
  commit outside the range that changed waits for a run that includes
  it, which posts it as an update. Reviews whose commits are gone from
  the branch are handled branch-wide (`forget`/`submit`/`discard`
  below), whatever the range. A range must lie on the branch
  (`<upstream>..HEAD`), otherwise rbt-sync refuses it.
- **Series from other branches.** Commits this branch has no record of
  are looked up, by subject, in other branches' records. A series whose
  commits sit together on this branch is picked up as one of its series
  (`from '<branch>'`), so stacking a branch on top of another one, or
  renaming a branch, needs no extra command. Conflicting records are
  never guessed: rbt-sync prints a `[gg]` warning and leaves those
  commits out. After a real run, if the source branch no longer exists
  in git, the reviews picked up from it are removed from its record (and
  the record goes once nothing is left); otherwise both keep them.
- **Gone commits.** A posted commit that is no longer on the branch is
  - `forget` -- another existing branch still records its review: it is
    dropped from this branch's record and **not** closed;
  - `submit` -- its subject is in the upstream's last 500 commits: the
    review is closed as submitted (`rbt close --close-type=submitted`);
  - `discard` -- otherwise: the review is closed as discarded.

The plan shows a header before each series:

```
Series k: n patches[ (was series w)][, from '<branch>'][, new][, depends on r/<id>][, outside range]
```

Gone reviews come first, under `Series k: n patches, landed in
<upstream>` when every one is `submit`, else `Series k: n patches, gone
from the branch` (`k` is the number the series had). A series entirely
outside the range shows only its header. For example:

```
$ git gg rbt-sync -d
#          Action       Pub     Review     Subject
--------------------------------------------------
Series 1: 2 patches, landed in origin/master
--         submit       --      r/101      net: fix the queue length check
--         submit       --      r/102      net: count dropped packets

Series 1: 2 patches (was series 2)
[1/2]      keep         --      r/103      conf: add DCB support
[2/2]      update       draft   r/104      conf: restore DCB on reboot

Series 2: 2 patches, from 'mqprio', depends on r/104
[1/2]      keep+dep     draft   r/120      tc: add mqprio qdisc
[2/2]      keep         --      r/121      tc: offload mqprio

Series 3: 1 patch, new, depends on r/121
[1/1]      create       draft   --         tc: document mqprio options
```

`--new` forgets every series of the branch (series recorded under
other branches are still picked up); `--close` closes every series of
the branch as submitted.

If a post fails, rbt-sync stops there, saves what it has done and
exits with status 1 (`[gg] stopped after a failed post; run gg
rbt-sync again to retry.`); the next run re-posts what was left.

### Post, review, publish

Drafts on ReviewBoard are private until published. To post a series as
drafts, look it over (or have a teammate poke at it), then publish:

```shell
# Post -- no -p, so reviews stay as drafts
git gg rbt

# (optional) eyeball things on RB, tweak via the web UI...

# Publish every draft for this branch
git gg publish
```

`gg rbt -u --publish` and `gg rbt-sync -p` also publish unchanged
drafts (in addition to creating/updating reviews whose diff changed).

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
