---
name: session-reflection
description: Use when the user asks to reflect on a session, or when closing substantial work that contains decisions, learnings, unresolved threads, or Gaia improvements worth reviewing
---

# Session Reflection

Session reflection is everything the session lived, contrasted against what
belongs to each project, until no loop is left dangling. It recovers the whole
arc, reconciles it against the durable corpus in both directions, and leaves
every item — settled or open — with an owner a later session can find.

Upstream are the transcript, the specialist contracts, and the injected digest.
Downstream is memory curation: reflection ends when the corpus is correct.
Compaction is a separate act, performed only when the user asks for it.

## Process

1. **Recover the whole arc.** Scan from the session opening for accepted and
   rejected proposals, deferrals, closures, user corrections, and specialist
   reactions — recency is not weight, an early settled choice still stands.
   Include `cross_layer_impacts`, `open_gaps`, and `failure_report` findings
   even where the user never reacted: they are observations about Gaia, not
   conversational agreements. `reference.md` holds the full recovery pass.
2. **Reconcile in both directions.** For each initiative the session touched,
   read its live corpus — `gaia memory get-relevant --initiative=<key>`
   returns that whole pending set uncapped, with bodies — and ask: what did
   this session produce with no home yet, and what already-open pending did
   it close, advance, or invalidate? A topic search only answers whether your
   own phrasing has a row; the closure you owe is usually phrased in terms
   that predate the session that solved it. Read briefs, plans, tasks, and
   approvals the same way — a conversation cannot close an object the
   substrate still shows open. `reference.md` holds the reverse-sweep
   mechanics and the objective-state checks that verify a `SKIP`.
3. **Classify disjointly.** Separate settled decisions and learnings,
   genuinely open work, and Gaia improvements. When closure is uncertain,
   classify as open; a lost pending costs more than an extra review.
4. **Give every item a home, and know what the home does.** The pending
   worklist that returns to the user each session selects `class=thread` with
   status `carry_forward` or `open` only — an `anchor` still reaches a
   dispatched agent as held knowledge, but never comes back as work. Filing
   live work as an anchor hides it; filing settled knowledge as a thread
   turns the worklist into noise. `SKIP` is a home only when you name the
   canonical object that owns the item — already-canonical work is
   referenced, never copied.
5. **Present the reflection and the exact proposal.** Show scope, operation,
   values, and verification before any write. Consent mechanics belong to
   `memory`.
6. **Run the confirmed curation, closures included.** Materialize the step 2
   closures alongside the new rows; `reclassify` and `append` are
   non-mutative, so nothing but omission keeps a resolved thread open. A
   closing arc that passes the milestone test in `reference.md` uses
   `checkpoint`, one atomic write; an ordinary close does not.
7. **State the resume point.** One line naming what the next session picks
   up — a pointer, not a container: everything it names already has a row.

## Output

No section holds a bare sentence. Every item leaves with a home and an
operation — a display that cannot state an item without stating its owner
cannot lose one to prose. Omit an empty section instead of inventing content,
and use the user's own vocabulary and language.

`SAVE`, `APPEND`, `TRANSITION`, `LINK`, and `SKIP` are the proposal verbs
`memory` adjudicates; a closure or graduation is a `TRANSITION`, materialized
as `reclassify` — `memory/reference.md` holds the exact forms.

### What we settled

Accepted decisions, closures, and reusable learnings, each with evidence of
agreement or objective completion. Most rows are `SKIP` naming the object
that already holds them; a decision that will constrain a future choice is
`SAVE`, filed as knowledge rather than as work.

| Item | Home | Operation |
|---|---|---|
| the decision, in the user's words | slug, commit, or brief/plan/task id | SAVE, APPEND, LINK, or SKIP |

### Open work

One row per unresolved concern, plus one row per pending the step 2 sweep
found this session resolved — a pending you closed is a `TRANSITION`, and
leaving it out is how a worklist grows past the attention anyone can give it.

| Item | Home | Operation |
|---|---|---|
| the concern, in the user's words | slug, brief/plan/task id, or initiative | SAVE, APPEND, TRANSITION, LINK, or SKIP |

### What Gaia should improve

    Symptom      observable behavior, not diagnosis
    Component    one precise owner
    Evidence     observed proof
    Reproduction exact repeatable route, or unknown
    → feedback_<component>_<symptom> · type feedback · SAVE or APPEND

Displayed in full, not summarized — consent to a defect whose evidence was
never shown is consent to a slug. `reference.md` holds the field definitions
and the `gaia_system` retrieval query a wrong initiative or type hides from.

### Resume point

One line. If the user asks to compact after this, `gaia-compact` builds its
own handoff; reflection hands it a pointer, not a container.

## Ownership and consent

The orchestrator recovers, reconciles, and adjudicates; the user corrects the
displayed proposal; the orchestrator or `gaia-operator` materializes the
exact confirmed batch. Independent operations are best-effort, a checkpoint
stays atomic. Reflection itself is not a new durable object.

## Handoffs

- Load `memory` for curation policy, lifecycle verbs, and consent.
- Load `reference.md` for dense-session recovery, the reverse sweep, the
  Gaia-improvement shape, the milestone test, and the anti-patterns to avoid.
- Load `examples.md` for the integrated reflection → curation flow.
- Load `gaia-compact` only when the user asks to compact.
