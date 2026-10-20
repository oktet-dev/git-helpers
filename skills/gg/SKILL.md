---
name: gg
description: Use when working with ReviewBoard (RB, review requests, r/NNNN) or the gg / git-helpers CLI - posting or updating a branch's patch series (gg rbt-sync, git rbt), publishing drafts, sharing review links, reading or answering reviewer comments (gg comments), or inspecting .gg/reviews.db.
---

# gg: ReviewBoard from git

`gg` (git-helpers) posts a branch's commits to ReviewBoard as patch series,
keeps them in sync as the branch changes, and lets you read and answer
reviewer comments. `git gg ...` is the same as `gg ...`; `git rbt ...` is
`gg rbt-sync ...`.

Exact syntax lives in `gg <command> --help`; every help text is complete.
This skill says when to use what, and what needs the user's go.

## Ground rules

- **Nothing reaches ReviewBoard without the user's explicit go for that
  action**: no posting, publishing, closing, replying or issue status
  change. Ask, then run exactly what was agreed.
- Read-only is always fine: `gg rbt-sync -d`, `gg rbt-sync --md`,
  `gg comments`, `gg comments reply -d`, `gg db`, `rbt api-get`.
- Before any real `gg rbt-sync`, run it with `-d` and show the plan. Rows
  `discard`, `submit` and `forget` close or drop reviews: point them out.
- Never print or copy the `API_TOKEN` from `.reviewboardrc`.

## How gg sees a branch

- The base is the branch's upstream (`git branch --set-upstream-to=...`);
  gg says so when there is none.
- The commits above it are an ordered list of **patch series**. Each series
  is one RB chain numbered `[i/N]: subject`; its first patch depends on the
  last patch of the series below. New commits on top of the last posted
  series form a new series.
- `.gg/reviews.db` (repo root) records which commit is which review, per
  branch and series. Series recorded under other branch names are picked up
  by subject. `gg db` shows the records.
- Plan actions: `keep` (unchanged), `update` (diff or commit message
  changed: re-post), `keep+dep` (only its depends-on changes), `create`,
  `discard` (commit gone: close as discarded), `submit` (landed upstream:
  close as submitted), `forget` (still recorded on another branch: drop
  the record only), `skip`.

## Commands

| Command | Use it to |
|---|---|
| `gg rbt-sync -d` | see the plan; always first |
| `gg rbt-sync` | post / update the branch's series (drafts) |
| `gg rbt-sync -s 3` / `-s $` / `-s 4:1-3` | act on series 3 / the last series / patches 1-3 of series 4 (`$` = last series or patch) |
| `gg rbt-sync -s $:1-3` | post only the first 3 unposted commits as the next series |
| `gg rbt-sync -p` | publish while posting (only when asked) |
| `gg rbt-sync -U alice,bob -G team` | reviewers and groups for new reviews |
| `gg rbt-sync --md -s $` | Markdown list of review links to paste into chat or mail |
| `gg rbt-sync -l` | full URLs instead of `r/N` |
| `gg rbt-sync -i` / `--new` / `--close` / `--renumber` / `-f` | rarer; read `--help` and ask first |
| `gg publish` | publish the drafts of every review on the branch |
| `gg comments` | reviewer discussions with the code they point at |
| `gg comments reply` | post replies and issue status changes |
| `gg rbt-import FIRST_ID` | record an existing RB chain in `reviews.db` |
| `gg db` | inspect or clear the records |
| `gg rbt` (`git gorbt`) | older single-series poster; prefer `rbt-sync` |

## Posting a series

1. `gg rbt-sync -d` (with `-s` or a range if only part is meant). Show the
   plan and explain anything that is not `keep`/`create`/`update`.
2. On the user's go, the same command without `-d` (add `-p` only if they
   asked to publish).
3. Report the review ids; offer `--md` if they want to share the links.

Notes: a failed post stops the run and the next run retries it. `rbt
publish` saying "it may already be published ... API Error 100" just means
there was no draft. Fix a branch's records with `gg db` / `--new` only with
the user.

## Answering review comments

1. `gg comments` (or `-s N`, or `r/N`). Diff comments show the code from
   the diff revision the reviewer saw; read the current code in the local
   commit.
2. Give the user a short triage: what you would fix, where you would reply
   (disagree, ask back, already fixed), and why.
3. Show the proposed replies in the chat; the user edits them. Post only
   on their go, exactly as approved, with a quoted heredoc
   (`gg comments reply <<'EOF' ... EOF`); `-d` first is fine.
4. Fixes go into the commit of the review request they belong to
   (`git commit --fixup=<sha>`, then
   `GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash <upstream>`); a
   separate commit only when the change spans later commits. The reply
   says where the fix is.
5. Upload with `gg rbt-sync` (after `-d` and the user's go) and publish.
   Only then mark the fixed issues: a status-only block such as
   `=== r/19917 #37502 fixed`. Leave the complicated ones open.

If `gg comments reply` reports an unpublished draft, show it to the user:
they publish or discard it on ReviewBoard; never work around it. If posting
stops midway, gg prints the blocks under "Not posted (send again as is):";
tell the user and resend only those.

## More

The git-helpers checkout (`$GG_GIT_HELPERS`, if set) has README.md with
every command and docs/superpowers/specs/ with the design notes.
