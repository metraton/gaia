---
name: scheduled-task
description: Use when the user wants something to run routinely / on a schedule rather than once now -- "tarea programada", "rutinariamente", "cada mañana", "cada N horas", "todas las noches", "schedule", "cron". Covers mounting, structuring, and running an unattended headless task that reports back, plus consuming its reports. NOT for work that runs once now in this session.
---

# Scheduled Task

A scheduled task is a Gaia task that runs **unattended on a recurring schedule**
via the OS crontab, executes `claude -p` headless, and leaves the user a report
in the notifications inbox instead of asking anything mid-run. This skill covers
the three flows of its lifecycle: creating one, executing it headless, and
consuming what it reports. Work the user wants done once, now, in this session
is an ordinary dispatch, not a scheduled task.

The load-bearing constraint that shapes everything below: a headless run has no
user to answer a prompt. So a scheduled task must complete everything it can
WITHOUT a T3 mutation, and when a T3 is unavoidable it must NOT try to ask --
it accumulates the `approval_id`, finishes the rest, and reports back so the
user can resume and grant later. Gaia's T3 layer gates independently of Claude
Code's permission dialog: `--dangerously-skip-permissions` removes the TUI
prompt, but Gaia still blocks/accumulates T3 mutations exactly the same.

## When to use

Trigger when the user asks for routine/scheduled execution: "cada noche corre
X", "rutinariamente revisa Y", "cada 6 horas", "prográmame Z". If they want it
run once now, this is the wrong skill -- that is an ordinary one-shot dispatch.

## Flow A -- Creation (mount the task)

Build three artifacts, in order. Heavy mechanics and the full wrapper rationale
are in `reference.md`; the runnable templates are in `scripts/`.

1. **Write the task as a read-only-first atomic prompt.** State the task's job
   as a self-contained prompt that opens with the headless preamble (Flow B).
   Front-load everything read-only; isolate any mutation as an explicit,
   clearly-labeled step so the headless run can skip-and-accumulate it cleanly.
   Store the prompt in its own file (one task = one prompt file).
2. **Copy the wrapper** `scripts/run-scheduled-task.sh` to a per-task file (e.g.
   `~/ws/me/scheduled-tasks/<task>.sh`) and edit its `==CONFIG==` block:
   `TASK_NAME`, `PROJECT_DIR`, `PROMPT_FILE`. The wrapper exports credentials and
   PATH **explicitly** (cron has almost no environment), runs the validated
   headless invocation, persists the session, and parses out the `session_id`.
   Do not drop `--output-format json` or add `--no-session-persistence` -- the
   session MUST stay resumable.
3. **Add a staggered crontab entry** from `scripts/crontab.template`. Give the
   wrapper an ABSOLUTE path, redirect to a per-task log, and offset the minute so
   no two tasks start in the same minute.

## Flow B -- Headless execution (what the task's prompt instructs)

Every scheduled-task prompt begins with this preamble, verbatim in spirit:

> Eres una tarea programada headless. Nadie está mirando y no puedes preguntar
> nada. Procede así:
>
> 1. **Intenta completar la tarea SIN ninguna mutación T3.** Haz todo el trabajo
>    read-only / T0-T2 que puedas.
> 2. **Si hay un T3 inevitable, NO llames AskUserQuestion.** El comando se
>    bloqueará con un `approval_id`. NO reintentes. ACUMULA cada `approval_id`
>    (con el comando exacto y por qué hace falta) y sigue con TODO lo demás que
>    sí puedas terminar.
> 3. **Captura tu `session_id`** leyendo `$CLAUDE_SESSION_ID` en la sesión
>    top-level (ver abajo) para poder estamparlo en el reporte.
> 4. **Redacta un mensaje final GENÉRICO** (sin nombres propios ni datos
>    sensibles) con el formato de abajo.
> 5. **Guarda ese mensaje** como último paso con `gaia notifications add` (T0).

### Capturing the session_id (`$CLAUDE_SESSION_ID`)

The report must carry the run's `session_id` so the user can
`claude --resume <session_id>` to grant the accumulated T3s. The headless
session reads its OWN id from its shell env var -- it is NOT parsed from
`--output-format json`, and NOT asked from the user:

```
echo $CLAUDE_SESSION_ID
```

**Read it at the TOP-LEVEL session, never inside a dispatched subagent.**
`$CLAUDE_SESSION_ID` does not propagate to a subagent's shell, so a task that
delegates its work to a subagent and reads the var there gets an empty value and
the report lands with `session_id` "-". Capture the id in the main session and
pass it through. If it is genuinely empty, omit `--session-id` and note in the
body that the user recovers pending grants with
`gaia approvals pending --all-sessions` (which does not depend on the id).

### Final message format (generic, no PII)

```
He terminado la tarea <nombre>: <qué hizo en una línea>.
Aprobaciones pendientes: <lista de approval_id + por qué cada uno>, o "ninguna".
session_id: <valor de $CLAUDE_SESSION_ID, o "ninguno — usa gaia approvals pending --all-sessions">.
```

The task's LAST action is to persist that message (substitute the value you read
for `$CLAUDE_SESSION_ID`):

```
gaia notifications add \
  --task "<nombre>" \
  --headline "He terminado la tarea <nombre>: <resumen>" \
  --body "<mensaje completo, incluidas las aprobaciones pendientes>" \
  --session-id "$CLAUDE_SESSION_ID"
```

`gaia notifications add` is **T0** by design, so a headless run can always
record its report without stalling on a gate. The message stays generic because
a notification surfaces later out of context -- proper nouns and sensitive data
do not belong in an inbox line.

## Flow C -- Consumption (how the user sees and acts on reports)

The report surfaces through four escalating touchpoints; the user pulls detail
on demand rather than being interrupted:

1. **Per-prompt counter** -- while there are unread reports, each prompt gets a
   cheap one-line `🔔 N task notifications sin ver` (nothing when N=0).
2. **SessionStart list** -- a compact `## Task Notifications (unread)` block,
   one line per report (task + headline + time + `session_id`).
3. **Detail on demand** -- `gaia notifications show <id>` prints the full body,
   including the pending `approval_id`s and the resume line
   `claude --resume <session_id>`. The user resumes that session to grant the
   accumulated T3s. Granting happens through the **orchestrated consent flow**
   (the AskUserQuestion dialog with the nonce) -- it is NOT approved by typing
   free text into the resumed session (confirmed empirically). If the report has
   no `session_id` (it came back "-" because the id was read inside a subagent),
   the pending grants are still reachable session-agnostically via
   `gaia approvals pending --all-sessions`.
4. **Clear** -- `gaia notifications ack <id>` (or `ack --all`) marks reports
   seen so the counter and list go quiet.

## Flow D -- Pausing (`disable`/`enable` vs `suspend`/`resume`)

A task's crontab entry (Flow A) and its `gaia schedule` row are two different
things: `gaia schedule` is a separate, DB-backed desired-state registry
(`register`/`list`/`show`/`status`/`enable`/`disable`/`suspend`/`resume`/
`remove`/`sync`) that a task only enters if it is **also** registered into it
(`gaia schedule register --name <task> --cron '...'|--every <interval>`, a T0
alternative to hand-editing `crontab.template`). A name never registered there
makes `gaia schedule suspend/disable <name>` answer "no scheduled task named
...". Once a task IS a `gaia schedule` row, two ways to stop it exist and they
are NOT interchangeable:

- **`gaia schedule disable <name>`** -- permanent, no deadline. Off until an
  explicit `gaia schedule enable <name>` turns it back on. Silent afterward:
  nothing announces it again.
- **`gaia schedule suspend (<name>|--all) (--for <duration>|--until <date>|
  --indefinitely) [--reason TEXT]`** -- off WITH an expiry. When the deadline
  passes the task is active again on its own -- a comparison made when the row
  is read, no daemon involved -- and the next SessionStart reports the lapse
  until it is acknowledged. `--indefinitely` is still NOT `disable`: it also
  carries no deadline, but it keeps announcing itself at every SessionStart
  until resumed, where `disable` stays silent. `--all` is the explicit
  workspace-wide switch; a bare `suspend`/`resume` naming neither a task nor
  `--all` is refused rather than assumed.
- **`gaia schedule resume (<name>|--all)`** -- lifts a live suspension early,
  or acknowledges a lapsed one so the SessionStart notice stops repeating.
  Never reinstalls anything on the machine. The form must match the
  suspension's OWN scope, not either interchangeably: `resume <name>` only
  clears a suspension taken on that task; it is a no-op on a workspace-wide
  (`--all`) suspension, which clears only with `--all` itself (`resume`
  looks the row up by task id, and a global row has none). Every hint Gaia
  prints for a lapse -- SessionStart, `status`, `list`, `show` -- already
  names the correct form for that specific suspension.

All four verbs are **T0**: reversible desired-state bookkeeping in gaia.db,
never touching the machine's crontab. Only `gaia schedule sync` (T3)
materializes desired state onto a machine -- suspending or disabling changes
nothing there until sync runs (or runs again, to remove what is now off).

See `reference.md` for the full `gaia schedule` verb surface, tiers, and a
live-verified worked example.

## Anti-patterns

- **Asking the user from a headless run.** There is nobody there. Forbidding
  `AskUserQuestion` and accumulating `approval_id`s is the only correct move;
  a run that blocks waiting for an answer hangs until the cron kills it.
- **Retrying a blocked T3 in the same run.** The gate did not misfire -- it
  needs consent the headless run cannot give. Accumulate and report; do not loop.
- **Proper nouns / secrets in the report.** A notification is read later, out of
  context, from an inbox. Keep the message generic.
- **Dropping session persistence.** Without a resumable session the user cannot
  grant the accumulated approvals -- the whole accumulate-and-resume design
  collapses. Never add `--no-session-persistence`.
- **Un-staggered schedules.** Two tasks in the same minute contend for resources
  and interleave their logs; offset every entry.
- **Treating this as in-session work.** Scheduling is OS crontab + headless,
  with no user present to answer anything. Work that runs now, with the user
  there, is an ordinary dispatch. Do not conflate them.
- **Reaching for `disable` to pause something temporarily, or for `suspend
  --indefinitely` expecting silence.** `disable` has no deadline and never
  announces itself again; every `suspend`, `--indefinitely` included, is
  repeated at each SessionStart until `gaia schedule resume` acknowledges it.
  Pick the verb for the actual intent, not the one that happens to compile.
