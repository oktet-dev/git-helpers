# `gg comments`: work through ReviewBoard comments from a session

**Status:** draft
**Date:** 2026-10-20

## Why

When reviewers comment on a series, the user's loop today is:

1. Open the review request in the browser and read every comment.
2. Reply where they disagree.
3. Fix where they agree, and upload the new diff (`gg rbt-sync`).
4. Mark as fixed the issues they are sure about. Leave open the
   complicated ones, where they are waiting for the reviewer to accept
   the proposed solution.

The user wants an agent (Claude Code, in the user's session) to do most
of this with them. A typical exchange:

- User: "look at r/19954" (or "look at series 3").
- Agent fetches the comments, reads the code, and reports back briefly:
  "6 comments: I'd fix 3, and reply in 3 places (one disagreement, one
  question back, one already fixed in the latest diff)."
- User: "show me the replies." The agent shows them in the chat; the
  user edits them by telling the agent what to change.
- On the user's go, the agent posts the replies, makes the fixes in the
  matching commits, uploads them with `gg rbt-sync`, and then marks the
  fixed issues as fixed.

gg provides the plumbing for this: fetch the discussions, post replies,
change issue statuses. **The agent uses gg; gg never runs an agent.** A
previous attempt in which an external tool called agents did not work
well for the user.

## Goals

- `gg comments` prints, for a branch, a series or given review requests,
  the discussions that need attention, with the code each one points at,
  in a form both the user and an agent can read.
- `gg comments reply` posts replies and issue status changes given on
  stdin (or in a file), in a plain text format.
- `gg comments --help` and `gg comments reply --help` document the
  selection syntax and the full reply format with an example. An agent
  with no other context can use the commands from the help alone.

## Non-goals

- Changing `gg rbt-sync`. It is used only to upload new diffs; none of
  this logic goes into it.
- gg starting, calling or embedding an agent or any AI. gg judges
  nothing; it fetches and posts.
- Reviewing other people's review requests (posting reviews, Ship It).
  *(My call: the user talked only about comments on their own requests.)*
- Editing or deleting replies that are already published.
- Polling or notifications.

## Anti-goals

- Anything reaching reviewers that the user did not approve. The
  approval itself happens in the chat (a rule for the agent, not gg), but
  gg must never publish more than it was given: in particular, never the
  user's own unfinished reply draft (see "Existing reply drafts").
- A half-posted batch that leaves the user unsure what went out. gg
  checks the whole input before posting anything and reports exactly
  what was posted.

## What ReviewBoard gives us

Checked read-only against rb.oktet.co.il (Review Board 8.0, RBTools 7.0
installed with pipx):

- A review request has reviews. A review has a top text (`body_top`),
  diff comments, general comments and file attachment comments.
- Any comment can open an issue: `issue_status` is `open`, `resolved`
  (shown as Fixed) or `dropped`. A comment can require the reviewer to
  verify the fix (`extra_data.require_verification`); RB then has
  `verifying-resolved` / `verifying-dropped` states. Comments carry a
  severity in `extra_data.severity` (e.g. `info`).
- A diff comment points at a file diff of one diff revision, with
  `first_line` and `num_lines`. `first_line` is a **row of RB's
  side-by-side diff view**, not a file line number. The file diff's
  diff data (`Accept: application/vnd.reviewboard.org.diff.data+json`)
  maps rows to old/new line numbers and text (HTML-escaped).
- A reply is a review-like object under a review. Each user has at most
  one draft reply per review; it holds reply comments, each pointing at
  the comment it answers (`reply_to`), and an optional reply to the
  review's top text (`body_top_reply_to`). Nothing is visible to others
  until the reply is published.
- Example of the user's earlier replies (r/19059): "Disagree (keep). ..."
  and "In standalone commit 05349320c (...) — kept standalone because
  ...".
- A live example: r/19954 has a Ship It from ark-slav and one open issue,
  "Why does it matter?", on `net-drv-ts/dcb/dcb_ets.c` rows 141-143
  (the `ets_cap < 3` warning).

- Diff comments, general comments and file attachment comments are
  separate tables in RB: their ids are separate sequences and can
  collide.
- An issue status is changed with a PUT on the commented comment
  itself (under the reviewer's review). It takes effect at once; it is
  not part of a reply draft. The review request's owner may change it.

`rbt` has no commands for comments, and `rbt api-get` can neither send
the diff-data `Accept` header nor write. **My call:** `gg comments` uses
RBTools as a Python library, added to gg's dependencies. The rest of gg
keeps shelling out to `rbt` as today. After the change the user
reinstalls gg (`./install.sh --gg`).

Checked in the recon: `rbtools.config.loader.load_config()` reads the
same `.reviewboardrc` as `rbt` (the user's `te` checkout has
`REVIEWBOARD_URL` and `API_TOKEN` there), and
`RBClient(url, api_token=...)` with it reads the reviews, the diff data
(`get_diff_data()`) and the session. The logged-in user name comes from
`/session/` (`kostik`). Use the client the way `rbt` does, so a cookie
login from `rbt login` works too.

## `gg comments`: fetch

```
gg comments                 # every review request of the current branch
gg comments -s 3            # series 3; same -s syntax as rbt-sync ($, N:P-Q)
gg comments r/19954 19917   # given review requests (with or without r/)
gg comments --all           # also closed threads and quiet requests
```

### Scope

- No arguments: every live review request in the branch's plan, i.e.
  the series rows `gg rbt-sync -d` shows with a review id (the branch's
  own series and those picked up from other branches). Gone reviews
  (landed, discarded, forgotten) are not included.
- `-s`: the same selection as `gg rbt-sync -s`, with the same series and
  patch numbers. Explicit ids and `-s` cannot be combined.
- Explicit ids: any review request, also one gg has no record of.

The plan is built the way `rbt-sync` builds it, including its RB lookups
(`rbt api-get`, which `rbt_mock` already fakes in tests). Planning code
may be refactored so both commands share it, as long as `rbt-sync`
behaves and prints exactly as before.

### What a discussion is

A discussion is one top-level comment (diff, general or file attachment)
and every reply comment pointing at it, in time order, from anyone. A
review's top text with its replies is a discussion too, when it says more
than a bare "Ship It!".

Each discussion gets a state:

- **needs you**: the issue is open (or it is a comment without an issue)
  and the last word is not the user's.
- **waiting on reviewer**: the issue is open, or in a verifying state,
  and the last word is the user's.
- **done**: the issue is resolved or dropped, or a comment without an
  issue whose last word is the user's.

"The user" is the account gg is logged in as (RB session). Only
published messages count for "the last word"; the user's own unpublished
draft does not. A top-level comment by the user counts as the user's
word.

By default `gg comments` shows "needs you" and "waiting on reviewer"
discussions, and only the requests that have one. `--all` also shows
done discussions and lists every request in scope.

### Output

Plain text, readable by the user and an agent. For each request:

- the request id (an `rb_links` link on a terminal), its summary, and
  the local commit it belongs to with its series number when gg knows it
  (from the branch plan);
- who gave a Ship It;
- each discussion: its id, the reviewer, the issue status and severity,
  the state, and "diff N of M" (the revision it was made on and the
  latest one);
- for diff comments: the file and the real line numbers, and the
  commented lines (new side; old side for removed lines) with a few lines
  of context, the commented ones marked. The code is from the diff
  revision the comment was made on, i.e. what the reviewer saw; the
  agent reads the current code in the local commit. A comment on an
  interdiff is labelled as such, with the newer side's code. If the diff
  data cannot be had, gg prints the file and rows and says the code is
  not available. *(My calls.)*
- for general comments: the text only; for file attachment comments:
  the attachment's file name and the text;
- the thread: each message with its author and date, the text as
  written.

A last line counts discussions per state. Rough shape (the builder may
refine it):

```
r/19954  [12/14]: dcb/dcb_ets: test valid ETS configurations via dcb
         series 3, patch 12, commit 1a2b3c4d   Ship It: ark-slav
  #37441  ark-slav  issue open (info)  needs you  diff 1 of 1
    net-drv-ts/dcb/dcb_ets.c:141-143
        139      TEST_VERDICT("Failed to retrieve DCB configuration: %r", rc);
        140
      > 141  if (current_ets.ets_cap < 3)
      > 142      WARN_VERDICT("IUT supports fewer than 3 Traffic Classes: %u",
      > 143                   current_ets.ets_cap);
    ark-slav, 2026-10-20: Why does it matter?

1 discussion needs you, 0 wait on reviewers.
```

Discussion ids are the RB ids of the top-level comment, with a letter for
the rarer comment types since their ids can collide with diff comment
ids: `#37441` (diff comment), `#g812` (general comment), `#a95` (file
attachment comment); or `review 36005` for a review's top text. They are
what `gg comments reply` takes. *(My call.)*

## `gg comments reply`: post

Input on stdin (or a file given as an argument): blocks, each starting
with a header line, the reply text running up to the next header.

```
gg comments reply <<'EOF'
=== r/19954 #37441
Fewer than 3 traffic classes is legal, but then the 3-TC
configurations below are skipped; the warning says why.
=== r/19917 #37502 fixed
Fixed in this diff: the error path now restores the old config.
=== r/19920 #37510 dropped
Agreed with ark-slav: out of scope here, tracked in BUG-1234.
=== r/19954 review 36005
Thanks!
EOF
```

- Header: `=== r/<request> #<comment>` (the comment id as `gg comments`
  prints it, `#g…`/`#a…` included) or `=== r/<request> review <review>`,
  optionally followed by a status word: `fixed`, `dropped` or `open`
  (reopen). The `r/` is optional. A status word on a `review` header is
  an error.
- Any line starting with `=== ` is a header; one that does not parse is
  an error. Reply text therefore cannot contain such a line (no escape;
  rare enough). Two blocks for the same comment are an error.
- Text: Markdown, posted as is (leading and trailing blank lines
  trimmed). An empty text with a status word only changes the status.
  `open` may follow any status, with or without text.
- A file argument replaces stdin.
- gg groups the blocks into one reply per review and publishes each
  reply. After a review's reply is published, it applies that review's
  status changes (PUT on each comment). It prints what it did per
  request, with links, and the resulting status (RB may answer
  `fixed` with `verifying-resolved` when the reviewer asked to verify;
  such a discussion then shows as "waiting on reviewer").
- `-d`: parse, check, and print what would be posted, without posting.
- Everything is checked before anything is posted: every request,
  review and comment exists, the comment belongs to that request, a
  status word is only given for a comment that has an issue. On any
  error nothing is posted.
- If posting fails midway (network, permissions), gg stops. A draft
  reply gg was building and could not publish is deleted, so nothing
  half-done stays behind. gg reports what was posted and prints the
  blocks that were not, in the input format, so they can be sent again
  as they are. *(My calls.)*

### Existing reply drafts

RB keeps one draft reply per user per review, so a draft the user started
in the browser would be published along with gg's reply. Before posting
anything, gg checks the user's draft reply on every review the input
touches. A draft with content (any reply comment, or reply text to the
review's top text) stops the whole batch: nothing is posted, and gg
prints each such draft's content so the agent can show it to the user.
An empty draft is simply used. *(My call: the whole batch, not just that
review, so a stopped run never leaves a partial set of replies.)*

## The agent's side (documented, not built)

How the session uses the commands, to go in the README next to the
commands, and in short in the `--help` epilog:

1. `gg comments` (or `-s N`, or ids); read the code around each
   discussion.
2. Report a short triage to the user: what to fix, where to reply, and
   why.
3. Show replies in the chat; post with `gg comments reply` only after
   the user approves them, exactly as approved.
4. Fixes go into the commit of the review request they belong to; a
   separate commit only when the change spans later commits. The reply
   says where the fix is. *(Guess from the user's r/19059 replies;
   the user did not object.)*
5. Mark issues fixed only after the new diff is uploaded with
   `gg rbt-sync` and published.

## Constraints

- Tests never reach a real ReviewBoard. RB access sits behind a small
  interface that tests replace with a fake (the existing `rbt_mock`
  covers only the `rbt` executable).
- Speed: the `te` branch (about 40 review requests) should be fetched in
  well under a minute. That is several hundred API calls, so fetch
  requests concurrently with a small pool (around 8), and retry
  rate-limited calls the way `rbt_retry.py` does for `rbt`. No server
  rate limit is known.
- Style: `from __future__ import annotations`, type hints, match the
  existing modules; `-h/--help` on both commands.

## Left to the builder

- Module layout, the RB interface (which RBTools calls; private RBTools
  modules are acceptable where the public ones fall short), and how
  planning is shared with `rbt-sync` for the default scope and `-s`
  (series and patch numbers must match `rbt-sync -d`).
- Exact output layout and context size, within the content listed above.
- How replies and status changes map to RB API calls, and their order
  within one review.
