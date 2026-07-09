---
name: scheduled-task
description: Use when the user wants something to run routinely / on a schedule rather than once now -- "tarea programada", "rutinariamente", "cada mañana", "cada N horas", "todas las noches", "schedule", "cron". Covers mounting, structuring, and running an unattended headless task that reports back, plus consuming its reports. NOT for a live in-session agentic loop (that is agentic-loop).
metadata:
  user-invocable: false
  type: technique
---

# Scheduled Task

A scheduled task is a Gaia task that runs **unattended on a recurring schedule**
via the OS crontab, executes `claude -p` headless, and leaves the user a report
in the notifications inbox instead of asking anything mid-run. This skill covers
the three flows of its lifecycle: creating one, executing it headless, and
consuming what it reports. For a task that iterates in a live session toward a
metric, that is `agentic-loop`, not this.

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
run once now, or iterated live toward a threshold in this session, this is the
wrong skill (one-shot dispatch / `agentic-loop` respectively).

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
> 3. **Redacta un mensaje final GENÉRICO** (sin nombres propios ni datos
>    sensibles) con el formato de abajo.
> 4. **Guarda ese mensaje** como último paso con `gaia notifications add` (T0).

### Final message format (generic, no PII)

```
He terminado la tarea <nombre>: <qué hizo en una línea>.
Aprobaciones pendientes: <lista de approval_id + por qué cada uno>, o "ninguna".
```

The task's LAST action is to persist that message:

```
gaia notifications add \
  --task "<nombre>" \
  --headline "He terminado la tarea <nombre>: <resumen>" \
  --body "<mensaje completo, incluidas las aprobaciones pendientes>" \
  --session-id "<el session_id de este run>"
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
   accumulated T3s through the normal consent flow.
4. **Clear** -- `gaia notifications ack <id>` (or `ack --all`) marks reports
   seen so the counter and list go quiet.

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
- **Treating this as a live loop.** Iterating toward a metric in-session is
  `agentic-loop`; scheduling is OS crontab + headless. Do not conflate them.
