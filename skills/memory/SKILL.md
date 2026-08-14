---
name: memory
description: Use when reading, searching, saving, or curating Gaia memory, deciding whether a session finding should persist, or triaging injected memory at session start
---

# Memory

Memory is the curation technique that turns selected experience into continuity:
durable knowledge kept small, live work kept visible, and raw operational
history left on the automatic event floors where it belongs.

## The three floors

| Floor | Purpose | Lifecycle |
|---|---|---|
| Events | Commands, dispatches, session events, and other operational facts | Automatic and short-lived |
| Episodes | Searchable agent-turn outcomes and anomalies | Automatic, retained for diagnosis |
| Curated memory | User-governed knowledge and work that must affect future decisions | Deliberate and long-lived |

Events and episodes are evidence, not durable truth.

## Think in roles, not storage vocabulary

Every curated item serves one human-facing role:

- **Durable knowledge**: stable facts, accepted decisions, useful dead ends,
  user preferences, and meaningful milestones.
- **Live thread**: one actionable concern that must reappear in a later session.
- **Historical log**: append-only context useful for audit but not reinjection.

The internal `class`, `status`, `type`, slug, and link enums implement these
roles. Consult `reference.md` only when materializing or debugging them.

## Other home first

Before saving, ask: **does this already have a canonical home?** Work in flight
belongs in a brief, plan, or task; domain state in project context or the owning
system; raw execution detail in events, episodes, or the transcript.

Do not copy a fact into memory merely because it matters. A second source of
truth becomes stale; a durable reference to the canonical object is enough.

## Process

1. **Read the injected memory, then sweep what you are about to touch.** The
   digest and anchors in context are a worklist under a SessionStart budget, not
   the corpus. Before writing into an initiative — or whenever the question is
   what it still owes — read its whole live-pending set with
   `gaia memory get-relevant --initiative=<key>`, uncapped and with bodies. A
   reader who only sees the digest can add rows and never retire one.
2. **Search before writing; do not read silence as absence.** Find the topic's
   existing owner and its lineage — a duplicate divides relevance instead of
   strengthening knowledge. An empty result answers "no row matches this
   phrasing", never "this initiative owes nothing": a pending is worded as the
   problem looked when it opened, not as what just resolved it, so step 1's
   sweep is what finds it.
3. **Choose the home.** Run *Other home first*, and continue only for genuinely
   curated value.
4. **Adjudicate the change.** The orchestrator chooses scope, create/append/
   correct/transition/link, role, lifecycle, initiative, and verification.
   Preserve lineage when knowledge replaces or graduates from earlier work.
5. **Show exact values and obtain consent.** The user may correct the proposed
   values before durable persistence. A request such as “reflexionemos y
   guardemos” authorizes writing only after the proposal has been shown.
   `checkpoint` is one atomic operation and remains all-or-nothing.
6. **Verify the durable result.** Read back the affected rows, lifecycle, scope,
   and links. Report partial batch failures per operation.

## Reading a row leaves a trace

Three counters bump as a side effect of being read, and two questions classify
any surface, including one not built yet. **Did the caller identify these rows,
or describe a window and take whatever fell in?** A slug identifies them; a
named initiative identifies them; a filter, a search term, a date range or a
dump of the table identifies nothing, however much of each row it prints.
Identified, so reaching them was the point — **deliberate** (`show`, `story`,
`get-relevant --initiative`). **When they were not: did the rows answer the
question their caller asked, or were they assembled into somebody's context?**
Answered — **neither** (`search`, `list`, `gaia query`): what fell in reflects
the phrasing, not the row. Assembled — automatic, and split once more by what
the block reaches: one fixed corpus every time it fires, the same rows whatever
the occasion is about, is **kernel** (today, the dispatch block); a window
picked for this occasion is **injection** (`get-relevant`, however launched).
Rendering is what counts: a row trimmed out of a block was never reached.

Never blend two into one number. A count is worth reading only if a higher one
means the row was worth more, and each axis fires at a rate set by something
other than the row: the kernel's corpus rides on every dispatch, so folded into
injection it heads any ranking by construction and measures dispatch volume, not
usefulness; injection folded into deliberate lets a row pushed at people read as
demand. A surface built tomorrow earns its own axis by that test. None of this
changes what gets injected — selection still orders by `updated_at` and reads no
counter.

## When curated memory earns attention

- **Decision:** accepted, with no canonical home, and it binds a later choice.
- **Project milestone:** it closes a meaningful arc. A routine session close is
  not one; minting a checkpoint for it turns memory into session summaries.
- **Live handoff:** one unresolved concern has no structured work object and
  must resurface. One concern per thread — a single status cannot honestly
  represent several.
- **Learning or dead end:** it prevents repeated investigation or error.
- **Gaia improvement:** a concrete symptom, component, evidence and reproduction
  deserves visible follow-up. After consent, persist it as a `feedback` live
  thread in initiative `gaia_system`, carried forward until closed or graduated.

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
  improvements and closures, then produces the curation proposal consumed here.
- `gaia-compact` runs after durable persistence and carries only transient
  continuity plus references to what was saved.
- `reference.md` contains exact CLI forms, enums, scope rules, retrieval,
  checkpoint payloads, the access-telemetry columns and call sites, history
  coverage, and graph mechanics.
- `examples.md` contains worked create/update/batch/checkpoint cases.

## Anti-patterns

- **Claiming perfect history:** ordinary updates are audited, but hard deletion
  and workspace removal can destroy records. Exact tracked fields are defined by
  the current schema and migration, not by prose.
