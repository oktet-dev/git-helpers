Git Helpers
===========

Installation
------------

```shell
git clone <repo>
cd git-helpers
./install.sh
```

and follow the instructions.

### Python CLI (`gg`)

Some commands (starting with `gorbt`) have been rewritten in Python.
Install the `gg` tool with:

```shell
uv tool install -e /path/to/git-helpers
```

This places the `gg` binary in `~/.local/bin/`. The git aliases
automatically add this to PATH.

Generic rule
------------

All wrappers have help that can be invoked via `-h/--help`:

```
[130] $ git gorbt -h
usage: gg rbt [-h] [-d] [-n] [-p] [-U USERS] [-G GROUPS] [-b BRANCH] [-u]
              [-C N] [-D ID]
              [range]

positional arguments:
  range                 revision range (default: tracking..HEAD)

options:
  -h, --help            show this help message and exit
  -d, --dry             print rbt commands without executing
  -n, --no-numbers      don't number the patches
  -p, --publish         publish review requests
  -U USERS, --users USERS
                        reviewer (--target-people)
  -G GROUPS, --groups GROUPS
                        review group (--target-groups)
  -b BRANCH, --branch BRANCH
                        explicit branch for --branch arg
  -u, --update          update existing review requests
  -C N, --continue-from N
                        continue numbering from patch N
  -D ID, --depends-on ID
                        first patch depends on review request ID
```

Simple one-patch workflow
-------------------------

Ensure I'm on the right branch:
`git checkout master`
```
Switched to branch 'master'
Your branch is up to date with 'origin/master'.
```

I'm going to fix bug Bug239.  Let's use it as my local branch name:
`git gowork Bug239`
```
Branch 'Bug239' set up to track local branch 'master'.
Switched to a new branch 'Bug239'
```

Edit things, look at the result with `git diff`.  Now I want to store it
all:
`git commit -a -m"Bug239: ensure that a neighbour is really deleted"`
```
[Bug239 ab1747b9e] Bug239: make everyonce life even better
 1 file changed, 10 insertions(+), 3 deletions(-)
```
Some people think that you should not specify your commit message via `-m`,
but use the editor `git` spawns for you.  I say: "Do as you like!"

Run `git show` to look at your last commit.  Use `git commit --amend` to
fix things.

Now it looks good; it is time to publish it on the Review Board:
`git gorbt`
```
Review request #14587 posted.

https://reviewboard.oktet.co.il/r/14587/
https://reviewboard.oktet.co.il/r/14587/diff/
```
You see that bug number is already filled in from your commit message, but
you have to write down the "Testing done" section.

If you want to know in advance which rbt command will be
called - `git gorbt -d <other options>` will tell you.

You can set reviewers, ask to publish etc. from the command line:
`git gorbt -p -U kostik -G te`

Hooray, I've got "Ship it!".  First of all, let's check that my patch applies
to the recent codebase: `git gopull`.
It spawns an editor asking how do you want to rebase on top of the recent
changes.  For now, just quit.
```
From https://github.com/oktet-dev/life
   226cbe768..f5a52133c  master     -> master
   226cbe768..f5a52133c  master     -> origin/master
Successfully rebased and updated refs/heads/Bug239
```

Let's re-test it.

It's OK.  We can push:
`git gopush`


This branch is not needed any more.
`git goclose`

Multi-patch
-----------

Actually it's the same as single. You have some convenience switches.

```shell
$ git gowork Bug533
$ git commit -m "Bug 533: add information about alias help
$ git commit -m "Bug 533: add info about dry runs for rbt commands"
```

Check what will happen:

```
$ git gorbt -p -d -U kostik
rbt post -p --target-people kostik --summary=[1/2]: doc: add information about alias help --branch=master --tracking-branch=master --bugs-closed=... 6ff614c
rbt post -p --target-people kostik --summary=[2/2]: doc: add info about dry runs for rbt commands --branch=master --tracking-branch=master --bugs-closed=... e601c8d
```

And if you're fine:

```
$ git gorbt -p -U kostik                                                                                                                                                                                                                                                   x
Review request #14680 posted.

https://reviewboard.oktet.co.il/r/14680/
https://reviewboard.oktet.co.il/r/14680/diff/
Review request #14681 posted.

https://reviewboard.oktet.co.il/r/14681/
https://reviewboard.oktet.co.il/r/14681/diff/
```

Upstream branches
-----------------

Sometimes you create a user branch:

```shell
git checkout master
git gowork foo

...
```

Now you think you need this branch upstream, may be your friend asks you to
pull from it or you want to backup.

```
git gopublish --initial
```

will push your branch to `user/<UID>/foo`. If you want to push more changes:

```
git gopublish
```

will do it or if you did a rebase:

```
git gopublish -f
```

Now you have a problem, cause your branch tracks origin/user/kostik/foo, not
master. So if you do: `git gorbt` it will compare against your branch in
origin. Sometimes it's OK, but if you want to post all changes since master
for review:

```
git gorbt master..foo
```

comes to help. It checks revisions between master and your branch. When you
want to push things back to master:

```
git gopush -t master
```

will push your branch to `origin/master`. Note, that you need to pull those
changes from your local master.

Issues solving
--------------

### Forgot to branch ###

You did:

```shell
git checkout master
# work
git commit -m "bug239: cool fix"
# oh, forgot to branch!
```

What we do is:

```shell
# Branch
git gowork bug239

# Move master back
git checkout master
git reset --keep HEAD~1

# Return to branch and work
git checkout bug239
```

Running tests
-------------

```shell
uv run pytest tests/ -v
```

Changes
-------

All changes should be done via reviewboard with at least `git-helpers` group
set as reviewers. Project for rbt - ol-git-helpers.

You MUST get at least **two** acks from **kostik/osadakov** if
you're not one of them in which case one is enough.

If you're leading a project that uses git-helpers you **should** mail
<kushakov@oktet.co.il> and get yourself into `git-helpers` list.
And you're welcome to do so if you want to be involved in review or be
notified about changes should they happen.
