---
name: memory
description: Use when reading, searching, saving, or curating Gaia memory, deciding whether a session finding should persist, or triaging injected memory at session start
---

# Memory

Memory is the curation technique that turns selected experience into useful
continuity. It keeps durable knowledge small, keeps live work visible, and
leaves raw operational history on the automatic event floors where it belongs.

## The three floors

| Floor | Purpose | Lifecycle |
|---|---|---|
| Events | Commands, dispatches, session events, and other operational facts | Automatic and short-lived |
| Episodes | Searchable agent-turn outcomes and anomalies | Automatic, retained for diagnosis |
| Curated memory | User-governed knowledge and work that must affect future decisions | Deliberate and long-lived |

Events and episodes are evidence, not durable truth. Promote from them only
when a future decision would change and the information has no better home.
The table names, retention, and query mechanics live in `reference.md`.

## Think in roles, not storage vocabulary

Every curated item serves one human-facing role:

- **Durable knowledge**: stable facts, accepted decisions, useful dead ends,
  user preferences, and meaningful project milestones.
- **Live thread**: one actionable concern that must reappear in a later session.
- **Historical log**: append-only context useful for audit but not reinjection.

The internal `class`, `status`, `type`, slug, and link enums implement these
roles. Consult `reference.md` only when materializing or debugging them.

## Other home first

Before saving, ask: **does this already have a canonical home?**

- Work in flight belongs in a brief, plan, or task.
- Domain state belongs in project context or the owning system.
- Raw execution detail belongs in events, episodes, or the transcript.
- Curated memory holds only cross-cutting knowledge, closed decisions and dead
  ends, meaningful milestones, and homeless work that must survive the session.

Do not copy a fact into memory merely because it matters. A second source of
truth becomes stale; a durable reference to the canonical object is enough.

## Process

1. **Read the injected memory, then sweep what you are about to touch.** The
   digest and anchors in context are a worklist under a SessionStart budget, not
   the corpus. Before writing into an initiative — or whenever the question is
   what it still owes — read its whole live-pending set with
   `gaia memory get-relevant --initiative=<key>`, uncapped and with bodies. A
   reader who only sees the digest can add rows and never retire one.
2. **Search before writing; do not read silence as absence.** Find the existing
   owner of the topic and its lineage — a duplicate divides relevance instead of
   strengthening knowledge. An empty result answers "no row matches this
   phrasing", never "this initiative owes nothing": a pending is worded in the
   terms of the problem as it looked when it opened, not in the terms of the
   thing that just resolved it, so step 1's sweep is what finds it.
3. **Choose the home.** Prefer brief, plan, task, project context, event, or
   episode when one owns the fact. Continue only for genuinely curated value.
4. **Adjudicate the change.** The orchestrator chooses scope, create/append/
   correct/transition/link, role, lifecycle, initiative, and verification.
   Preserve lineage when knowledge replaces or graduates from earlier work.
5. **Show exact values and obtain consent.** The user may correct the proposed
   values before durable persistence. A request such as “reflexionemos y
   guardemos” authorizes writing only after the proposal has been shown.
   `checkpoint` is one atomic operation and remains all-or-nothing.
6. **Verify the durable result.** Read back the affected rows, lifecycle, scope,
   and links. Report partial batch failures per operation.

## When curated memory earns attention

- **Decision:** it was accepted, is not already captured canonically, and will
  constrain a future choice.
- **Project milestone:** it closes a meaningful arc. A routine session close is
  not a milestone and does not automatically create a checkpoint.
- **Live handoff:** one unresolved concern has no structured work object and
  must resurface. Keep one concern per thread.
- **Learning or dead end:** it prevents repeated investigation or error.
- **Gaia improvement:** a concrete symptom, component, evidence, and
  reproduction deserves visible follow-up. After consent, persist it as a
  `feedback` live thread in initiative `gaia_system`, carried forward until
  closed or graduated.

## When curated memory loses it

Attention is finite and a live thread spends it every session. Two exits, and
neither is deletion:

- **Closed** — the concern was resolved or stopped being relevant.
  `reclassify --status=closed` retires it from the worklist and keeps the row.
- **Graduated** — the work finished and left knowledge worth holding.
  `reclassify --status=graduated`, or `--class=anchor` when future dispatches
  should carry the knowledge; `link --kind=graduated_to` preserves the lineage
  from the thread to the anchor it became.

Both verbs are non-mutative, so cost is never the reason a thread stays open.
Whoever observes the resolution owns the exit: a session that resolves a thread
and does not close it has moved that thread's cost onto every session after it.

## Ownership

- The **user** is the authority for durable personal and project knowledge.
- The **orchestrator** resolves ownership, searches, adjudicates, presents the
  proposal, and verifies it.
- Other specialists only propose (`memory_delta`, `memorialize_suggestions`);
  they never write curated memory directly.
- The runtime enforces the writer boundary and core data invariants. The skill
  supplies curation judgment; it does not duplicate enforcement details.

## Handoffs

- `session-reflection` recovers decisions, live work, learnings, Gaia
  improvements, and the closures its sweep found, then produces the curation
  proposal consumed here.
- `gaia-compact` runs after durable persistence and carries only transient
  continuity plus references to what was saved.
- `reference.md` contains exact CLI forms, enums, scope rules, retrieval,
  checkpoint payloads, history coverage, and graph mechanics.
- `examples.md` contains worked create/update/batch/checkpoint cases.

## Anti-patterns

- **Writing without knowing what the initiative owes:** an empty search means
  no row matched your phrasing, not that nothing is pending; the write adds a
  parallel note and leaves open the pending it duplicates, unseen.
- **Copying what already has a home:** briefs, plans, tasks, project state,
  events, and episodes already retain it; a copy is a stale rival source.
- **Treating every close as a milestone:** turns long-term memory into session
  summaries and consumes retrieval attention.
- **Bundling live work:** one status cannot honestly represent several
  independent concerns; use one thread per concern.
- **Claiming perfect history:** ordinary updates are audited, but hard deletion
  and workspace removal can destroy records. Exact tracked fields are defined
  by the current schema and migration, not by prose.
