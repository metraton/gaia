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

## The `gaia schedule` desired-state registry (verb surface)

`gaia schedule` (`bin/cli/schedule.py`) is a DB-backed, OS-agnostic desired-
state registry for recurring tasks -- a different mechanism from the raw
`crontab.template` mounting in Flow A. A task is reachable through it only if
it was registered into it explicitly (`gaia schedule register --name <task>
(--cron '...'|--every <interval>|--spec <json>) [--prompt-file F|--prompt
TEXT] [--project-dir D]`, or `--adopt --match <substr>` to derive the schedule
from an existing unmarked crontab line). Flow A above does not do this
registration, so the two mounting paths do not currently converge -- a task
built by hand-editing `crontab.template` is invisible to `gaia schedule`
until someone also runs `register` for it.

| Verb | Tier | What it does |
|------|:---:|---|
| `register` / `add` | T0 | Upsert a task's desired state (schedule, prompt, project dir, machine scope). Never installs anywhere. |
| `list [--all-workspaces]` | T0 | List tasks for a workspace, with state (`active`/`disabled`/`suspended`) and a note for a live or lapsed suspension. Without `--all-workspaces`, a trailing line reports how many tasks exist in OTHER workspaces instead of staying silent about them (see below). |
| `show <name\|id>` | T0 | One task's full row plus its native (e.g. cron) translation on this machine. |
| `status` | T0 | Reconcile desired state vs. this machine's local scheduler; also lists every live/lapsed suspension. |
| `enable <name\|id>` / `disable <name\|id>` | T0 | Permanent switch, no deadline. `enable` is the only way back from `disable`. |
| `suspend (<name\|id>\|--all) (--for DUR\|--until DATE\|--indefinitely) [--reason TEXT]` | T0 | Off with an expiry, or explicitly none via `--indefinitely`. Expiry is evaluated when the row is read -- no daemon. `--until` rejects a deadline that has already passed, naming `--indefinitely` and `disable` as the two working alternatives, instead of silently suspending a task that stays `active` while the SessionStart LAPSED notice fires forever. |
| `resume (<name\|id>\|--all)` | T0 | Lift a live suspension early, or acknowledge a lapsed one. Never reinstalls anything on the machine. |
| `remove <name\|id>` | T3 | Delete the desired-state row -- irreversible (`disable` is the reversible way to stop a task). |
| `sync` | T3 | Materialize desired state into the OS scheduler (writes crontab). The ONLY verb here that touches the machine. |

`enable`/`disable`/`suspend`/`resume` sit at T0 for the reason given in the
module's own docstring: they are reversible desired-state bookkeeping in
gaia.db and never touch the machine scheduler; only `sync` (materializes) and
`remove` (deletes the row outright) are T3.

### `<name|id>` -- the bracketed id `list` prints is a real identifier

Every verb above that names ONE task accepts either its name or the bracketed
`[id]` `list` prints beside it (`[3] docs-verify-temp -- ...`). This closes a
measured incident: `gaia schedule remove 3` failed with `no scheduled task
named '3'` even though task `#3` existed, because every verb used to resolve
by NAME only while `list` printed a number that looked usable and was not. A
purely numeric argument is now looked up by id FIRST, across every workspace
-- ids are one global sequence, not scoped per workspace, so this is strictly
more precise than a name+workspace pair. It falls back to a plain name lookup
only when no such id exists, so a name that happens to be all-digits still
gets an ordinary (and still useful) error.

A second factor compounded that incident: `gaia schedule list` (without
`--all-workspaces`) hid the task the agent was looking for, so the failed
`remove` read as "the task no longer exists" rather than "wrong workspace."
`list`'s default (workspace-scoped) output now adds a trailing note when
other workspaces hold tasks it is not showing, and every not-found error names
the workspace it searched and, when the task exists elsewhere, which one --
so a diagnosis made with the default verbs can no longer conclude a task does
not exist when it exists in a sibling workspace.

### `disable` vs `suspend` -- the schema's own framing

`gaia/store/schema.sql` keeps the two on separate tables on purpose:
`scheduled_tasks.enabled` is the permanent switch with no deadline;
`schedule_suspensions` is "a TIME-BOUNDED pause laid over desired state" with
an optional `until` (NULL means indefinite but still a suspension, not a
disable) and an optional free-text `reason`. A suspension's `task_id` is NULL
for the workspace-wide switch (`--all`) or a specific row for one task; a
unique index enforces at most one live suspension per scope. Expiry is a
comparison made at read time (`SessionStart`, `list`, `status`, `show`), never
a background job.

### SessionStart announcement blocks

`hooks/modules/session/session_manifest.py::build_schedule_suspension_block`
is DETECT-ONLY (T0) and zero-noise, and renders up to two headers, LAPSED
first because it is the one that changed what is running:

- `## Scheduled Tasks — SUSPENSION LAPSED (running again)` -- one line per
  lapsed suspension, with how long ago the deadline passed and which tasks
  came back. Does NOT self-clear; it repeats every SessionStart until `gaia
  schedule resume <name>|--all` acknowledges it.
- `## Scheduled Tasks (suspended)` -- one line per LIVE suspension, with the
  remaining time (or "indefinitely") and the `--reason`, if any.

A separate, pre-existing block, `build_schedule_reconciliation_block`
(`## Scheduled Tasks (drift on <machine>)`), covers desired state vs. this
machine's crontab and is unrelated to suspensions -- both are detect-only and
never install, reactivate, or sync anything on their own.

### Live-verified worked example

Run against the live `gaia.db` in an isolated scratch workspace
(`docs-verify-temp`) so it cannot collide with a real task, using
`python3 bin/gaia schedule ...` from the repo root:

```
$ python3 bin/gaia schedule register --name docs-verify-temp --every 6h \
    --prompt "placeholder prompt for doc verification" --workspace docs-verify-temp
Registered scheduled task 'docs-verify-temp' (#3) -- cada 6h
Not yet installed on any machine. Run `gaia schedule sync` (T3) to materialize.

$ python3 bin/gaia schedule suspend docs-verify-temp --for 8h \
    --reason "documentation verification run" --workspace docs-verify-temp
Suspended task 'docs-verify-temp' until 2026-08-11T08:20:43Z.
Desired state only -- the machine scheduler is untouched. Run `gaia schedule sync` (T3) to stop it on this machine.
It becomes active again on its own at the deadline; session start will report the lapse.

$ python3 bin/gaia schedule list --workspace docs-verify-temp
[3] docs-verify-temp -- cada 6h (suspended, all)
        task scope, until 2026-08-11T08:20:43Z (7h 59m left), reason: documentation verification run

$ python3 bin/gaia schedule resume docs-verify-temp --workspace docs-verify-temp
Resumed task 'docs-verify-temp' -- suspension lifted early.
Desired state only. Run `gaia schedule sync` (T3) to reinstall it on this machine if a previous sync removed it.

$ python3 bin/gaia schedule disable docs-verify-temp --workspace docs-verify-temp
Task 'docs-verify-temp' disabled. Run `gaia schedule sync` (T3) to apply on this machine.

$ python3 bin/gaia schedule list --workspace docs-verify-temp
[3] docs-verify-temp -- cada 6h (disabled, all)
```

Note the last two `list` lines side by side against the earlier suspended
one: `disabled` carries no deadline note at all, while `suspended` always
shows the window and, if given, the reason -- the CLI's own rendering is the
clearest evidence the two states are not the same thing. `gaia schedule sync`
(T3) was never run in this walkthrough, so nothing was written to this
machine's crontab at any point; task `#3` remains in `gaia.db`, disabled,
under the scratch workspace `docs-verify-temp` -- remove it with `gaia
schedule remove docs-verify-temp --workspace docs-verify-temp` (T3) if it is
no longer wanted.

## Building the newsletter task (out of scope here)

The "repo newsletter" task is the first real task built ON this framework -- it
is NOT part of the framework itself and is built separately. When it is, it
follows Flow A verbatim: a read-only-first prompt, a per-task wrapper copy, a
staggered crontab entry, and the same generic-report + notifications contract.
