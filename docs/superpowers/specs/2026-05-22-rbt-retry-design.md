# rbt retry on transient ReviewBoard errors

**Status:** approved
**Date:** 2026-05-22

## Problem

`gg rbt`, `gg rbt-sync`, and `gg publish` shell out to `rbt post`, `rbt
publish`, and `rbt close`. When ReviewBoard returns a transient failure
the current code surfaces the error and stops:

- **API 114 (rate limit)** — the server throttled us; the right answer
  is to wait a few seconds and try again.
- **API 207 ("The file was not found in the repository.")** — the RB
  backend mirrors the git repo and is sometimes behind the commit we're
  posting from. The fix is to wait several minutes for the mirror to
  catch up, then retry.

Both errors are recoverable, but today every transient hit forces the
user to re-run the command (and often re-post several patches by hand).
The series can be left half-posted, with the unfortunate patch logged
as a failure that wasn't really a failure.

## Goals

1. Detect and retry the two known transient RB errors.
2. Pass any other failure straight through (auth errors, bad args, real
   repo problems must still fail fast).
3. Make retries visible — the user should never be staring at a stalled
   terminal without knowing why.
4. Keep retry timing tunable via environment variables.
5. No new CLI flags.

## Non-goals

- Honoring HTTP `Retry-After` headers (rbtools doesn't expose them in
  captured output).
- Retrying `rbt api-get`. It runs inside reconcile loops; a 15-min
  sleep there would be hostile. Out of scope.
- Retry on partial-series progress (where patches 1..N succeeded but
  N+1 failed). The retry happens at the single-patch level; the
  outer loop in `rbt.py` / `sync.py` continues to be the authority on
  series-level state. Whatever it does today after a single-patch
  success is what it'll do after a retry-recovered single-patch
  success.

## Architecture

One new module, `gg.rbt_retry`, contains a helper that wraps
`subprocess.run(["rbt", ...])` and re-runs it on classified transient
failures. `rbt_post.post_one`, `rbt_publish.publish_one`, and
`rbt_close.close_*` swap their bare `subprocess.run` for this helper.
`rb_api.py` (api-get) is unchanged.

```
post_one ──┐
publish_one ─┼──► run_with_retry(cmd) ──► subprocess.run(cmd)
close_*    ──┘         │
                       └─► classify(output) ──► sleep+status (retry) or return
```

The helper owns:

- error classification,
- the two retry schedules,
- the sleep/countdown UI,
- environment-variable parsing.

Callers stay simple: they get back the same `CompletedProcess` they get
today, with success-or-final-failure semantics. Calling-site behavior
(parsing the review id, deciding whether to abort the series) does not
change.

## Error classification

`classify(returncode, output) -> RetryClass` returns one of:

- `OK` — `returncode == 0`. No retry.
- `RATE_LIMIT` — output matches API code `114` or the phrase
  `rate limit` (case-insensitive). Use the rate-limit schedule.
- `MISSING_BASE` — output matches API code `207` or the phrase
  `not found in the repository`. Use the missing-base schedule.
- `FATAL` — non-zero exit, no transient marker matched. No retry;
  bubble up like today (auth errors, bad arguments, real repo issues).

Patterns:

```python
RATE_LIMIT_RE = re.compile(
    r"API Code:\s*code:\s*114\b|rate[- ]?limit",
    re.IGNORECASE,
)
MISSING_BASE_RE = re.compile(
    r"API Code:\s*code:\s*207\b|not found in the repository",
    re.IGNORECASE,
)
```

Sample 207 output that the patterns must match (provided by user):

```
Error Message: The file was not found in the repository.
API Code: code: 207
```

The 114 pattern mirrors the 207 sample's shape and adds a phrase
fallback. It will need a real sample to tighten if false positives
appear.

## Retry schedules

Both schedules give 3 retries (4 attempts total) by default:

| Class           | Delays (seconds)        | Total wait |
|-----------------|-------------------------|------------|
| `RATE_LIMIT`    | `10, 30, 90`            | ~130s      |
| `MISSING_BASE`  | `300, 300, 300`         | ~15min     |

The schedule is **locked at the first transient hit** — if the error
class changes mid-loop (e.g. 114 on attempt 2, 207 on attempt 3) we
stay on the original schedule rather than refreshing the budget.

Retry loop:

```python
def run_with_retry(cmd, *, cwd, sleep=time.sleep, runner=subprocess.run):
    schedule: list[int] | None = None
    last_result = None
    attempt = 1
    while True:
        r = runner(cmd, cwd=cwd, capture_output=True, text=True)
        last_result = r
        cls = classify(r.returncode, r.stdout + r.stderr)
        if cls in (OK, FATAL):
            return r
        if schedule is None:
            schedule = pick_schedule(cls)  # locked at first transient
        if not schedule:
            return r  # exhausted
        delay = schedule.pop(0)
        attempt += 1
        sleep_with_status(
            delay, reason=REASON[cls], attempt=attempt,
            total=1 + len(pick_schedule(cls)), sleep=sleep,
        )
```

`sleep` and `runner` are kwargs so unit tests can inject fakes without
burning wall time.

## UI

`sleep_with_status` writes to stderr.

**TTY (interactive)** — one line that updates in place with `\r`:

```
[gg] rate-limited; retrying 2/4 in 0m29s ...
```

Updated every second via a `time.monotonic()` deadline loop with
`time.sleep(min(1, remaining))`. When the deadline hits, the line is
replaced with `[gg] rate-limited; retrying 2/4 now`, then a newline,
then normal output resumes.

**Non-TTY (CI, piped to `tee`)** — one well-formed line per retry,
terminated with `\n`, no `\r`:

```
[gg] base commit not yet in RB mirror; sleeping 300s before retry 2/4
```

Detected with `sys.stderr.isatty()`.

Reason strings, keyed by retry class:

| Class           | Reason text                              |
|-----------------|------------------------------------------|
| `RATE_LIMIT`    | `rate-limited`                           |
| `MISSING_BASE`  | `base commit not yet in RB mirror`       |

## Configurability

Five environment variables, each holding a single number, parsed once
at module import time:

```
GG_RBT_RATE_LIMIT_RETRIES=3        # number of retries
GG_RBT_RATE_LIMIT_INITIAL_DELAY=10 # seconds for first retry
GG_RBT_RATE_LIMIT_FACTOR=3         # each subsequent delay multiplies by FACTOR

GG_RBT_MISSING_BASE_RETRIES=3
GG_RBT_MISSING_BASE_DELAY=300      # seconds; uniform delay between every retry
```

Schedules are derived from these at import time:

```python
RATE_LIMIT_DELAYS = [
    int(INITIAL * FACTOR ** i) for i in range(RETRIES)
]
MISSING_BASE_DELAYS = [DELAY] * RETRIES
```

Defaults reproduce `[10, 30, 90]` and `[300, 300, 300]`. Setting
`RETRIES=0` for either class disables retries for that class without
removing the classifier or affecting the other class. No CLI flags.

## Testing

Three test surfaces.

### `tests/test_rbt_retry.py` — unit tests against the helper

Fake `runner` callable returns pre-programmed
`subprocess.CompletedProcess` objects; fake `sleep` appends durations
to a list. No real wall time, no real `rbt`.

- `classify` returns the expected class for: zero exit; the literal 207
  sample; a 114 sample; a `rate limit` phrase; an auth error
  (`FATAL`); a stderr-only error message.
- `run_with_retry` returns immediately on `OK`, without calling sleep.
- `run_with_retry` returns immediately on `FATAL`, without calling sleep.
- `run_with_retry` retries 3 times on a persistent `RATE_LIMIT`, with
  delays `[10, 30, 90]`, then returns the last failing result.
- `run_with_retry` recovers when a transient succeeds on the 3rd attempt.
- Mid-loop class flip (114 then 207) keeps the original schedule
  rather than restarting from the new class's budget.
- Env-var overrides honored: setting `GG_RBT_RATE_LIMIT_RETRIES=1`
  and re-importing yields a 1-retry schedule.

### `tests/conftest.py` — failure injection in `rbt_mock`

Extend the mock so a test can pre-program failure responses for the
first N calls, then succeed:

```python
rbt_mock.queue_failure(
    output="Error Message: The file was not found in the repository.\n"
           "API Code: code: 207\n",
    returncode=1,
    count=2,
)
```

After two failures the mock returns its normal success response.

### `tests/test_rbt_post.py` and `tests/test_rbt_sync.py` — end-to-end

Integration tests through the CLI, with retry delays zeroed via
`monkeypatch.setenv` so they run in ~0s:

- `gg rbt` recovers when two simulated 207s precede success on attempt
  3 → exit 0, exactly one `Review request posted` line, mock invoked
  3 times.
- `gg rbt` gives up after 4 attempts on a persistent failure → exit 1,
  the original failing output is printed once (not 4 times).
- `gg rbt-sync` and `gg publish` mirror the same two scenarios.

## Migration

No DB schema changes, no on-disk format changes, no CLI surface
changes. Existing callers swap one function call. Behavior on
unrecognized failures is identical to today.
