---
name: reminders
description: Use when the user wants to be reminded of something at a time, or wants something offered to them on a recurring basis -- "recuérdame mañana a las 4", "avísame el viernes", "cada viernes a las 5 cargar el timesheet", "rutinariamente", "remind me to", "every morning". Also when a due reminder or routine shows at session start or in the prompt counter and has to be acted on. NOT for work to do once now in this session.
---

# Reminders

A reminder is a row that comes due, not a job that starts. Gaia has no scheduler, no daemon, no system timer and no unattended AI session: nothing runs while the user is away. A row is due when a read finds it open with its time passed, and the reads happen when the user is present -- the `Recurring work pending` line at session start and the `N notifications due` counter on the next prompt. A reminder for 16:00 shows after 16:00, the next time Gaia is used.

## The three kinds

All three are `gaia notifications` rows; the kind decides how one ends.

| Kind | What it is | `ack` does |
|---|---|---|
| report | What a task or agent left behind; due at once | marks it seen |
| reminder | Once, at an absolute time (`--at`) | closes it for good, also a year later |
| routine | Recurring, by `--cron` or `--every` | moves it to the next occurrence after now; missed ones collapse into one |

## Creating one

One sentence becomes exactly one `gaia notifications add`. The orchestrator runs it itself, with no dispatch.

1. Read the time with `gaia now`. Nothing else in the session tells you the current local time, and a guessed clock or zone puts the reminder hours off.
2. From that output, turn the phrase into an absolute local date and time -- "mañana a las 4" is tomorrow's date at 16:00, "el viernes" the next Friday, "en media hora" the time `gaia now` printed plus 30 minutes. A time with no zone is local. If the phrase gives no time, ask; do not invent one. A time already past is refused, and so is an `--at` without a date.
3. Run the one command. A reminder takes `--at YYYY-MM-DDTHH:MM`, a routine takes one of `--cron '<five fields>'` (local time) or `--every <90m|6h|1d|1w>`.
4. Return what the CLI prints (`Added reminder #7, due Thu 2026-10-01 16:00 -03 (local time)` and its `now:` line) so the user confirms the date. If it is wrong, `gaia notifications cancel <id>` and add it again.

"In half an hour", when `gaia now` printed `now: Thu 2026-10-01 11:20 -03:00 (America/Santiago)`:

```
gaia notifications add --kind reminder --at 2026-10-01T11:50 --headline 'Call back the vendor'
```

```
gaia notifications add --kind reminder --at 2026-10-01T16:00 --headline 'Review the release notes'
gaia notifications add --kind routine --cron '0 17 * * 5' --headline 'Load the timesheet' --memory aaxis:project_aaxis_timesheet
gaia notifications add --kind routine --every 1d --headline 'Triage mail' --skill gmail-triage
```

A reminder or routine is global, seen from every workspace, unless `--workspace` names one.

## Choosing the pointer

A pointer says where the user, or the orchestrator on their behalf, picks the work up when it comes due. Give at most one; the CLI checks that it resolves when the row is written, so a wrong name fails on the spot, not on the day.

| Pointer | Use it when | Flag |
|---|---|---|
| skill | a technique already exists for the work | `--skill gmail-triage` |
| memory | the procedure or context lives in a curated memory | `--memory WORKSPACE:SLUG` |
| project | the work belongs to a registered project | `--project WORKSPACE/NAME` |

With no pointer the headline has to carry everything.

## When something is due

Offer the user three ways out, in their words.

- **Do it now.** Follow the pointer: load the skill, read the memory with `gaia memory show <slug> --workspace <w>`, or bring the project with `gaia context project <name>`. Work in a domain goes to the specialist the procedure names. With the user present, anything that needs approval asks for it.
- **Later.** `gaia notifications snooze <id> --for 2h`, or `--until <local time>`. It hides until then.
- **Done.** `gaia notifications ack <id>`. A reminder closes; a routine advances to its next occurrence.

`gaia notifications cancel <id>` ends one for good; a routine does not come back. `gaia notifications list --upcoming` shows what is coming and `show <id>` one row in full. A report is read with `show <id>` and closed with `ack`; `ack --all` sweeps reports only, never a reminder or routine.

## What this gives up

A monitor that acts while the user is away cannot exist, because nothing starts without them. Recurring work is offered when the user returns and done with them. Scheduled tasks that predate this were converted by migration into routines that point at the skill of the same name; if no such skill exists, cancel that routine.
