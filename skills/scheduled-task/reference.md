# Scheduled Task -- Reference

Deep mechanics for building and running a Gaia headless scheduled task: the
validated headless invocation, the cron-environment gotchas the wrapper exists
to absorb, the T3 accumulate-and-resume model, and an end-to-end walkthrough.
Read on demand; the numbered flows live in `SKILL.md`.

## The validated headless invocation

Empirically confirmed. Run verbatim -- each flag earns its place:

```
claude -p "<prompt>" \
  --dangerously-skip-permissions \
  --disallowedTools AskUserQuestion \
  --output-format json
```

| Flag | Why it is there | Why NOT the alternative |
|------|-----------------|-------------------------|
| `-p` / `--print` | One-shot, non-interactive run. | The interactive TUI has no place in cron. |
| `--dangerously-skip-permissions` | Removes Claude Code's interactive permission dialog, which would hang forever with no user. | Leaving it on makes every prompt block until the cron job is killed. **This does NOT disable Gaia's own T3 layer** -- see below. |
| `--disallowedTools AskUserQuestion` | Makes "ask the user" structurally impossible, not merely discouraged. | Relying on the prompt to "please don't ask" is hope, not enforcement. |
| `--output-format json` | Machine-readable result; the wrapper parses `session_id` out of it. | Text output is not reliably parseable for the resume id. |
| *(absent)* `--no-session-persistence` | **Deliberately omitted.** The session must persist so `claude --resume <session_id>` works later. | Adding it destroys the resume path and breaks accumulate-and-resume entirely. |

### Gaia's T3 layer is independent of Claude Code's permissions

`--dangerously-skip-permissions` only removes Claude Code's *interactive* dialog.
Gaia's PreToolUse hook still classifies every operation and still blocks/queues
T3 mutations with an `approval_id` -- confirmed: a headless run cannot silently
`git push` or `kubectl apply`. That is the whole reason the headless preamble
tells the task to accumulate approvals rather than assume the flag lets it
mutate freely. The flag and Gaia's consent layer are orthogonal.

Tier reminders relevant here: `gaia notifications add` is **T0** (local,
reversible inbox write -- see `COMMAND_SUBCOMMAND_TIER_EXCEPTIONS` for
`("gaia","notifications")` in `hooks/modules/security/mutative_verbs.py`), so the
task can always record its report. `gaia memory add` is T0 too, but memory
`edit`/`delete` are T3.

## Why a wrapper (cron environment gotchas)

cron does not run your login shell, so almost nothing you take for granted in an
interactive terminal is present:

- **PATH is minimal** -- often just `/usr/bin:/bin`. `claude`, `gaia`, `python3`
  may not resolve. The wrapper exports a full PATH.
- **No profile is sourced** -- `~/.bashrc` / `~/.profile` do not run, so
  credentials and env vars you export there are absent. The wrapper sources a
  per-task env file (`~/.gaia/scheduled-tasks/<task>.env`) and exports what the
  run needs explicitly.
- **cwd is `$HOME`** -- not your project. The wrapper `cd`s to `PROJECT_DIR`.
- **A crash before reporting would be silent** -- if `claude -p` dies before the
  task calls `gaia notifications add`, the user learns nothing. The wrapper has a
  fallback that records a minimal error notification on non-zero exit.

One wrapper per task (not one shared wrapper with switches) keeps `TASK_NAME`,
`PROJECT_DIR`, and the prompt file self-contained and keeps crontab lines simple.

## The accumulate-and-resume model

```
cron fires
  -> wrapper exports env, cd, runs `claude -p ... --output-format json`
       -> task does all read-only / T0-T2 work
       -> hits an unavoidable T3
            -> Gaia blocks it, returns approval_id  (no AskUserQuestion possible)
            -> task RECORDS approval_id + reason, does NOT retry, continues
       -> task finishes everything else
       -> task writes ONE generic report via `gaia notifications add` (T0),
          including every accumulated approval_id and why
  -> session persists (session_id captured)

later, interactively:
  user sees counter / SessionStart list
  -> `gaia notifications show <id>`  (full body + pending approvals + resume line)
  -> `claude --resume <session_id>`  (re-enters the SAME session)
       -> now interactive: grants the T3s through the normal consent flow
  -> `gaia notifications ack <id>`   (clears the report)
```

The accumulated approvals are recoverable from a DIFFERENT session because
Gaia's pending-approval store is DB-backed and session-agnostic: resuming the
original `session_id` re-enters the context where the blocked commands live, and
the user grants them there. The notification is the durable pointer that makes
that session findable days later.

## End-to-end walkthrough (illustrative)

Task: "cada noche corre los tests y avísame si fallan."

1. **Creation.** Prompt file `nightly-tests.prompt` opens with the headless
   preamble, then: "Run the test suite (read-only). Summarize pass/fail counts
   and the first failing test per file. Do NOT push, tag, or open a PR." Wrapper
   `nightly-tests.sh` sets `TASK_NAME=nightly-tests`,
   `PROJECT_DIR=/home/jorge/ws/me`, `PROMPT_FILE=.../nightly-tests.prompt`.
   crontab: `7 3 * * * ... nightly-tests.sh >> .../nightly-tests.log 2>&1`.
2. **Execution (03:07).** Tests run read-only -- no T3, nothing to accumulate.
   The task writes:
   `gaia notifications add --task nightly-tests --headline "He terminado la tarea
   nightly-tests: 2 fallos" --body "2 tests fallan (uno por archivo listado).
   Aprobaciones pendientes: ninguna." --session-id <sid>`.
3. **Consumption (morning).** First prompt shows `🔔 1 task notification sin
   ver`; SessionStart lists it. `gaia notifications show 1` prints the failures.
   No approvals pending, so nothing to resume; `gaia notifications ack 1` clears
   it.

A task that DID hit a T3 (say it wanted to `git push` a fixup) would instead
carry `Aprobaciones pendientes: P-xxxx (git push origin main -- para subir el
fix)` in the body, and the user would `claude --resume <sid>` to grant it.

## Building the newsletter task (out of scope here)

The "repo newsletter" task is the first real task built ON this framework -- it
is NOT part of the framework itself and is built separately. When it is, it
follows Flow A verbatim: a read-only-first prompt, a per-task wrapper copy, a
staggered crontab entry, and the same generic-report + notifications contract.
