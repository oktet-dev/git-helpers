# `gg rbt-sync`: a branch as a list of patch series

**Status:** approved
**Date:** 2026-10-19

## Problem

One branch often carries a stack of patches that go to ReviewBoard as
several patch series, posted one after another and chained with
`depends-on`. Example: `mqprio-redesign` in `te`, on top of
`oktet/master`:

| Commits | Series | On RB | Recorded in `reviews.db` under |
|---------|--------|-------|--------------------------------|
| 1-5     | scenario      | r/19986-19990 | `scenario` |
| 6-11    | configurator  | r/19992-19997 (first depends on r/19990) | `mqprio-redesign` |
| 12-23   | dcb           | r/19917-19926, r/19916, r/19927 | `dcb` |
| 24-42   | mqprio        | not posted | - |

`reviews.db` holds exactly one series per branch name, and `rbt-sync`
only reads the current branch's record. So today:

- `gg rbt-sync oktet/master..HEAD` plans to create the scenario and dcb
  commits again, and numbers everything against 42 (`[0.1/42]`).
- Syncing any range that leaves out the recorded series plans to discard
  that series. Posting a second series from the same branch means
  `--new`, which forgets the first one.

## Goal

`gg rbt-sync` treats a branch as an ordered list of series. Each series
is a contiguous run of commits, posted as one ReviewBoard chain and
numbered `[i/N]` within that series. The user posts the stack series by
series and gg keeps track of all of them.

On `mqprio-redesign`, with the records above, `gg rbt-sync -d` shows:

```
Series 1: 5 patches, from 'scenario'
[1/5]    keep      r/19986   scripts/scenario: keep the injected scenario out of the last paragraph
...
Series 2: 6 patches, depends on r/19990
[1/6]    keep      r/19992   engine/configurator: skip uncommitted sets on restore before lookup
...
Series 3: 12 patches, from 'dcb', depends on r/19997
[1/12]   keep+dep  r/19917   unix/conf: add DCB configuration support
...
Series 4: 19 patches, new, depends on r/19927
[1/19]   create    --        engine/configurator: tell whether an instance exists only locally
...
```

(`keep+dep` on r/19917 only if its current `depends-on` on RB is not
r/19997; see "Chaining".)

## Non-goals

- Changing `gg rbt` (the non-sync poster). It stays single-series and
  saves everything as series 1.
- Changing `gg rbt-import`. It imports one chain as series 1.
- Naming series. They are referred to by number only.
- Checking review status on ReviewBoard (pending / submitted /
  discarded) when picking up or matching records.
- Persisting `-D`. As today, it applies to the run it is given on.

## Anti-goals

These would make the feature a failure even if it works:

- A review is discarded or closed because its commit is outside the
  range given on the command line.
- A series that is already posted is re-posted or renumbered when
  nothing about it changed.
- The same commit is posted to ReviewBoard twice because gg did not
  recognise an existing review for it.

## Model

- A branch's record is an ordered list of series. Series are numbered
  1, 2, 3... from the bottom of the stack (closest to the upstream).
- The number is only an ordinal within its branch. It is not an
  identity: it is rewritten on every save, so splitting series 2 makes
  the old series 3 become 4. Nothing outside gg refers to it.
- `[i/N]` is per series: `i` is the commit's place in its series, `N`
  the series size.

## Storage

- `reviews` gets one column: `series INTEGER NOT NULL DEFAULT 1`. Add it
  with `ALTER TABLE` when the column is missing, so existing records
  become series 1 with no other migration.
- `position` keeps its meaning: order along the whole branch, 1..M. The
  primary key `(branch, position)` is unchanged.
- The within-series index (`i` of `[i/N]`) is derived from the record
  (rank inside the series), not stored.
- `diff_hashes` is unchanged.

Example rows for `mqprio-redesign` after the first sync:

```
branch           series  position  review_id
mqprio-redesign  1       1         19986
...
mqprio-redesign  1       5         19990
mqprio-redesign  2       6         19992
...
mqprio-redesign  2       11        19997
mqprio-redesign  3       12        19917
...
mqprio-redesign  3       23        19927
```

## Behaviour

### Matching recorded series

The whole record (all series, in position order) is matched against the
branch's commits with the existing matcher. keep / update / keep+dep
work as today.

Series membership follows the commit's position on the branch, so
series stay contiguous. A matched commit normally stays in its recorded
series; if a rebase moved it into another series' run of commits, it
joins the series it now sits in and becomes `update` (new number and
predecessor). *(Design call.)*

### Commits not in the record

- After the last recorded series (and inside the range): together they
  form **one new series**.
- Between two commits of the same series: they join that series, with
  fractional numbering as today (`[2.1/5]`).
- At a boundary between two recorded series: they join the series
  before the boundary. Before series 1: they join series 1 (`[0.1/N]`,
  as today). *(Design call, not discussed with the user.)*

To post only part of the new commits, the user ends the range there
(`gg rbt-sync oktet/master..<rev>`). The rest wait for a later run, which
makes them another new series.

### Ranges

- The full branch is `upstream..HEAD` (the default range today).
- A range given on the command line only limits which commits are acted
  on. Recorded commits that are on the branch but outside the range are
  left untouched: never discarded, re-posted or renumbered.
- A series entirely outside the range is shown as one header line
  (`Series 1: 5 patches, outside range`) with no rows. *(Design call.)*
- Whether a recorded commit is "gone" is decided against the full
  branch, never against the given range.
- Exception: a `keep+dep` (dependency-only re-post) is still done for a
  commit outside the range, and listed in the plan with
  `outside range`. The predecessor is derived from record order, not
  stored, so a skipped re-chain would never be retried once the new order
  is saved. A dependency fix changes no diff, number or status.
  *(Design call.)*
- The range guard added on 2026-10-19 (uncommitted: "shares no commits
  with the series saved for ...") is dropped; this section replaces it.

### Commits gone from the branch: landed or dropped

A recorded commit that is no longer in `upstream..HEAD`:

- If its review is also recorded under another branch that still exists
  in git, the plan shows `forget`: the entry is dropped from this
  branch's record and the review is not closed. Records are shared, and
  gg never closes a review another existing branch still records.
  *(Decided by the user: "shared" over "one owner at a time".)* In `-i`,
  `forget` can be changed to `skip`, `discard` or `submit` to override.
- Otherwise, if a commit with the same subject (prefix stripped) is in the upstream
  branch's recent history, the plan shows `submit`: close the review as
  submitted (same call as `--close`, `rbt close --close-type=submitted`).
- Otherwise the plan shows `discard`, as today.
- Both are only executed on a real run. In `-i` they can be changed to
  `skip` (kept in the record, as skipped discards are today) and into
  each other (`discard` <-> `submit`), for when a maintainer reworded a
  subject.
- Executed `submit`/`discard` entries leave the record. A series left
  empty disappears and the series above it move down a number.
- If only part of a series lands, the rest stays a series. Its reviews
  are not re-posted just because their predecessor was submitted (no
  `keep+dep` for that), so on ReviewBoard they keep `[4/5]`, `[5/5]`. The
  plan shows the derived numbers (`[1/2]`, `[2/2]`), as the current code
  does after discards; a later `update` or `--renumber` posts the
  derived number. No stored labels. *(Design call.)*
- How far back "recent history" goes is left to the builder (a bounded
  window such as the last few hundred upstream commits; it must not scan
  all of history on every run).

Plan output for a landed series (approved by the user):

```
Series 1: 5 patches, landed in oktet/master
--       submit    r/19986   scripts/scenario: keep the injected scenario out of the last paragraph
...
Series 1: 6 patches (was series 2)
[1/6]    keep      r/19992   engine/configurator: skip uncommitted sets on restore before lookup
...
```

### Series recorded under other branches

- Before matching, commits not matched by the current branch's record
  are looked up in the other branches' records, by subject (prefix
  stripped, exact).
- A foreign series whose matching commits are contiguous on this branch
  is adopted: its entries become a series in this branch's record, in
  their foreign order. The plan header says `from '<branch>'`. Foreign
  entries with no matching commit are ignored (not discarded).
- "Contiguous" ignores unrecorded commits: a new commit sitting between
  commits of a foreign series joins the adopted series as an insert
  (fractional number), as it would for a recorded series. Only commits
  that belong to another recorded or adopted series break contiguity.
  *(Design call.)*
- If a foreign series shares review ids with a series already in this
  branch's record (the normal state after an adoption, when both
  branches still record it), its extra entries join that existing series
  as kept entries rather than becoming a series of their own.
  *(Design call.)*
- Adopted entries are then treated like recorded ones: if the commit's
  diff hash differs from the recorded one, the action is `update`.
- Conflicts are never guessed. If the matching commits are not
  contiguous, or two branches record different review ids for the same
  subject, nothing is adopted for those commits: print a warning naming
  the branches and reviews, and leave those commits out of the plan
  (`skip`) so nothing is posted twice. *(Design call.)*
- On a real run, after the record is saved: if the source branch no
  longer exists in git (renamed or deleted), its record is deleted. If
  it still exists, its record is left alone. *(Design call, told to the
  user.)*
- This also covers renaming a branch: the next sync on the new name
  picks up the old name's series. No command needed.

### Chaining

- The first patch of series k > 1 depends on the last patch of series
  k-1. The first patch of series 1 depends on `-D` if given; without
  `-D` its dependency is left as it is on ReviewBoard and it never gets
  `keep+dep` (so syncing a series from a branch with less below it does
  not fight over its dependency). *(Design call.)*
- For recorded series the existing predecessor check applies over the
  whole record in position order, so a moved boundary or a removed
  series below triggers `keep+dep` where needed.
- For an adopted series the record has no predecessor for its first
  patch. Read its current `depends-on` from ReviewBoard (one
  `rbt api-get` per adopted series, also under `-d`) and show `keep+dep`
  only if it differs from the expected predecessor. *(Design call, told
  to the user.)*
- When a whole series is submitted, the next series' first patch keeps
  depending on the submitted review. It is not re-chained. *(Design
  call, told to the user.)*

### Plan output

- A header line before each series:
  `Series k: n patches[, from '<branch>'][, new][, depends on r/<id>][, outside range]`.
- Rows as today, with `[i/N]` per series.
- `submit` / `discard` / `forget` rows are shown under a header for the
  series they came from, before the live series, as in the landed
  example above. A `forget` row names the branch that still records the
  review.
- The `Synced:` summary line gains `submitted` and `forgotten` counts.

### Interactive editing (`-i`)

- The editor file shows a separator line between series.
- Deleting a separator merges the two series around it; adding one
  between two commits splits a series. Series stay in commit order.
- Kept commits whose `[i/N]` changes because of a merge or split become
  `update` (their summary prefix changes); everything else stays as
  planned.
- The separator's exact syntax is left to the builder; it must not be
  mistaken for an action line, and a malformed separator is a parse
  error like any other.

## Other commands

- `gg rbt-sync --new`: forgets every series of the branch, as today.
- `gg rbt-sync --close`: closes every series of the branch as submitted,
  as today. It is an explicit request, so the shared-record rule above
  does not apply to it.
- `gg publish`: publishes every recorded review of the branch, as today.
- `gg db` (list): shows the series number for each entry.

## Tests

Follow the existing style (`git_repo` + `rbt_mock`, `gg` run as a
subprocess). The `mqprio-redesign` situation above, scaled down, is the
acceptance case: two branches with recorded series, a stacked branch
containing both plus a posted series of its own plus new commits, and
`gg rbt-sync -d` showing four series with the right actions, numbers and
`depends on`.

## Left to the builder

- The upstream history window for landed detection.
- The `-i` separator syntax.
- How `ReviewEntry` carries the series number, and the order of the
  implementation steps.
