---
name: session-reflection
description: Use at the end of a session with substantial conversational work to offer the user a structured reflection before closing -- briefs closed, decisions taken, components modified
---

# Session Reflection

Help the user close the conversational arc of a session by offering a short,
structured reflection. This is **not** a technical log summary, a commit
recap, or a status report. It is a return of *what was agreed* from the
user's side, in the language the conversation already produced.

The orchestrator loaded this skill because the session carried enough
conversational weight that closing without reflection would lose the arc.
Your job is to recover that arc -- not re-narrate the actions.

## When to Activate

Activate when the session has at least two of:

- 50+ turns or multiple subagent dispatches.
- One or more decisions where the user said yes/no to a concrete proposal.
- A brief opened, edited, or closed.
- A skill, agent, hook, or routing config modified.
- Multiple follow-ups that surfaced and were either deferred or absorbed.

Skip when the session was purely executive (commands run, no agreements
exchanged). Better to honestly say "this session was mostly execution --
no conversational arc to reflect on" than to inflate three bullets.

## The long-session failure mode (read this first)

In dense sessions (50+ turns, multiple agents, several briefs in flight),
the orchestrator forgets at scale. Symptoms the user has flagged:

- Mid-session decisions get dropped because later topics overwrote them.
- Follow-ups get mixed with closures -- "we agreed to defer X" turns into
  "we closed X".
- Conclusions get invented to fill the three-section structure when
  honest sections would have been shorter.
- The user's plain-language framing gets re-technified ("dejémoslo así"
  becomes "decided to maintain current state").

The Process below is built to defend against this. The recovery pass in
Step 1 is the antidote -- skipping it produces exactly the failure mode
above.

## Process

### Step 1: Recover the arc, not the log

Before drafting any bullet, scan the session transcript for **agreement
markers** and **deferral markers**. These are the anchors of the real arc.

**Read the whole arc, from the first turn -- not the recent window.**
The failure the user named is reflecting from the last few turns because
they are closest to hand; a point settled at turn 12 and never revisited
is exactly the one that gets dropped. Scan forward from the session's
opening to its close -- do not walk backward from the end until it "feels
like enough". If the transcript is long, that is the reason to be more
thorough, not less: length is where the early arc hides.

| Marker type | User phrases (Spanish + English) |
|-------------|----------------------------------|
| Agreement | "ok", "exacto", "vayamos por eso", "let's go with that", "confirmado", "dale", "sí", "yes" |
| Rejection | "no, mejor", "actually no", "esperá", "wait", "cambiemos", "let's change" |
| Deferral | "eso lo vemos después", "for later", "captura como brief", "leave it open", "ya veremos" |
| Closure | "dejémoslo", "cerralo", "close it", "listo", "done", "ya está" |

For dense sessions (50+ turns, multiple subagents), walk through these
checkpoints explicitly before drafting:

1. **Decision verbs**: every user message that contained a verb committing
   to or rejecting a path. Note both sides -- what was accepted *and* what
   was rejected.
2. **"Vayamos con X" / "let's go with X"**: every explicit affirmative the
   user emitted in response to a proposal. These are the strongest agreement
   signals; do not lose them.
3. **Dispatch reactions**: every subagent result the user reacted to. The
   reaction (approval, rework, deferral) is the agreement, not the result
   itself.
4. **Briefs mentioned but not implemented**: deferred work that surfaces
   under "what stayed open", not under "what we agreed".

As you walk these, keep **two disjoint lists**: **closed** (reached a
conclusion both sides accepted) and **open** (surfaced and did not
conclude). Every item belongs to exactly one -- never both. When you
are unsure which, it is **open**: a pending mislabeled as closed
vanishes, while a closed item mislabeled as open only costs a harmless
extra thread later. This asymmetry is the safe default; err toward open.

This is the antidote to "orchestrator forgets at scale". List these
mentally (or in a scratch buffer) before writing the first bullet.

### Step 2: Three-section structure with objective criteria

Produce a short response with three sections. Each section has an objective
admission criterion -- if the criterion is not met, the section is empty.

**What we agreed**
2-4 bullets naming decisions that emerged. Admission criterion: the user
responded affirmatively to a concrete proposal. A topic that was discussed
without affirmative response is *not* an agreement.

**What stayed open**
What was raised but not closed -- ideas, deferred questions, follow-ups
that surfaced and did not reach closure. Admission criterion: the topic
appeared in the session and was *not* concluded. Do not force items;
"nothing significant stayed open" is valid output.

**What deserves to crystallize**
Optional suggestion of which decisions or learnings would be worth
persisting. Propose; do not prescribe. The user decides.

### Step 3: Use the user's own vocabulary

If the user said "DB-canonical", use "DB-canonical". If they said "mover a
la base de datos", do not translate to "migrate to substrate". If they said
"dejémoslo como está", do not write "decided to maintain current state".

The continuity of language is what makes the reflection feel like the
user's session, not the agent's report. Re-technifying plain Spanish (or
plain English) into jargon breaks that continuity.

User quotes can stay in the original language even when the surrounding
prose is English. The reflection is for the user; the user's words win.

### Step 4: Verify against pending state

Before closing the reflection, confirm against any objective state in the
session:

- Briefs in `draft` that the conversation discussed -- still draft, or
  moved to `open`?
- Commits not yet pushed -- should they appear under "stayed open"?
- Subagent dispatches that returned `BLOCKED` or `NEEDS_INPUT` -- those
  are open, not closed.
- Approvals requested but not granted -- open.

If your draft reflection contradicts the actual state (e.g. you wrote
"we closed brief X" but `gaia brief show X` shows `status: open`), align
the reflection to reality before presenting.

This check is also the gate for Step 6: only the items this step
confirms are *genuinely* open become carry-forward threads on save. A
pending that objective state shows already resolved is not a thread; a
"closure" the state contradicts is not a closure. Reconcile here so the
save decomposes the real arc, not the drafted one.

### Step 5: Length budget

The reflection itself is **<= 200 words**. Honest brevity beats padded
structure. If a section has nothing real to say, omit it or say so.

The skill *instructions* (this file) can be longer; the *output* cannot.

### Step 6: Persistence is opt-in -- and decomposed

After presenting the reflection, you may offer to save what is worth
keeping. Never persist without explicit user consent; the reflection
itself writes nowhere -- it is offered, the user accepts or declines. If
the user accepts, save **decomposed**, never as one packed anchor.

**A pending never travels inside the summary body.** A single
`gaia memory add` that folds the whole session -- closures *and*
pendings -- into one anchor is the failure this step exists to prevent:
an anchor's body is never re-injected at SessionStart (only
`class=thread status=carry_forward` notes resurface), so a real pending
buried in that body becomes invisible and has to be rescued by hand.
The save is a record and its threads -- but write it as **one atomic
command**, not an `add` per row.

**Use `gaia memory checkpoint`.** It persists the whole reflection in a
single transaction: the record anchor, one carry-forward thread per
pending, and a `derived_from` edge from each thread back to the record.
It is all-or-nothing -- if any row is invalid the checkpoint writes
*zero* rows, so you never end a session with a half-written save. Build a
JSON payload and pass it via `--file` (or `--file -` to stream it on
stdin):

```json
{
  "resumen": {
    "name": "project_session_2026-07-14_<topic>",
    "type": "project",
    "description": "<one-line summary of the arc>",
    "body": "<the durable account of what happened -- NO live pendings>"
  },
  "pendientes": [
    {
      "name": "project_<pending_topic>",
      "description": "<the single pending, in one line>",
      "body": "<the pending's detail>"
    }
  ]
}
```

```bash
gaia memory checkpoint --file /tmp/session_checkpoint.json \
  --project=<project> --workspace=<ws>
```

`resumen` becomes the record anchor (`class=anchor`); each `pendientes`
entry becomes a `class=thread status=carry_forward` row that inherits the
record's `--type` and is linked `derived_from` the record. **One pending
= one entry** in the `pendientes` list -- never a single thread with a
`## PENDIENTE` list. This is the same **"One thread = one note"** rule the
`memory` skill enforces for handoffs: the `status` column is what
resurfaces a pending at the next SessionStart, so each concern needs its
own row and its own `status`.

If there were no open pendings, `pendientes` is `[]` -- the record anchor
alone is a complete, honest save. Do not invent a thread to fill
structure, and do not relabel a pending as closed to avoid writing one
(Step 4's two-list rule already forbids this). If the record body itself
reads like it hides a pending (a `TODO`, a "próximo paso", a `- [ ]`
line) while `pendientes` is empty, `checkpoint` returns a non-blocking
**warning** -- heed it and lift the pending into a `pendientes` entry.

`checkpoint` is non-mutative (T0, no approval) and idempotent: the
fecha-stamped `project_session_<date>_<topic>` slug avoids collisions, so
re-running the same payload UPSERTs the same rows rather than duplicating
them.

See `memory/SKILL.md` -- **Write flow** for the slug↔type rules and
UPSERT semantics, and **Carry-forward / handoff** for the
`derived_from` grouping and why a thread is closed by its `status`, not
by editing its body.

## Anti-Patterns

- **Re-summarizing commit hashes the user already saw** -- they read the
  output during the session; repeating it is not reflection, it is noise.
- **Forcing three sections when nothing is open** -- if no follow-ups
  surfaced, omit the section. Padding for shape destroys the signal.
- **Translating user's plain Spanish to technical English** -- "dejémoslo"
  is not "we will maintain the current state". Keep the user's framing.
- **Auto-persisting to memory without explicit consent** -- the
  crystallize section is a proposal. Silence is not approval; ask first.
- **Inflating bullets to fill structure** -- an honest "nothing
  significant stayed open" beats four invented follow-ups.
- **Skipping the recovery pass on dense sessions** -- without Step 1, the
  reflection drifts to "what I remember from the last few turns" instead
  of "what we actually agreed across the whole arc".
- **Mixing follow-ups with closures** -- a deferred topic is open, not
  agreed. A topic accepted via "vayamos con eso" is agreed, not open.
  The objective criteria in Step 2 exist to keep these separate.
- **Saving the session as one packed anchor** -- folding closures and
  pendings into a single anchor body buries the real pendings where
  SessionStart never re-injects them, so they go invisible. Save
  decomposed with `gaia memory checkpoint`: one record anchor plus one
  carry-forward thread per open pending, in one atomic call (Step 6). A
  pending never rides inside the summary body.

## Filesystem behavior (DEPRECATED)

Earlier iterations of this skill suggested writing to `MEMORY.md` directly.
That path is **legacy** -- the curated memory layer is now the `memory`
table in the Gaia substrate (`~/.gaia/gaia.db`), accessed through
`gaia memory add`. See `memory/SKILL.md`.

If you find code, docs, or other skills that still describe writing
reflections to `MEMORY.md` or `~/.claude/projects/.../memory/*.md`, flag
them in `cross_layer_impacts` -- do not edit them as a side effect of a
reflection task.
