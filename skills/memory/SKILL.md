---
name: memory
description: Use before the first memory verb — read or write — whenever the orchestrator is about to touch memory as this turn's subject: deciding what persists, searching, curating, or triaging what a session injected at start.
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

## The one-line initiative test

- **Observed Gaia fail or rub in use:** `initiative=gaia_system`, host-scoped
  by `gaia/store/writer.py::apply_host_scope`; a project anchor is refused.
- **Decided to build or change Gaia:** project-scoped `initiative=gaia`.

Choose from how the fact was produced, not the current directory. The writer
enforces the destination after that choice; `reference.md` owns its mechanics.

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
4. **Adjudicate the change.** The orchestrator chooses scope (see *The one-line
   test* for a host-scoped initiative), create/append/correct/transition/link,
   role, lifecycle, and verification. Preserve lineage when knowledge replaces
   or graduates from earlier work.
5. **Curate against the exception boundary, then report.** Curation is
   delegated: the orchestrator adjudicates and executes directly, inside the
   boundary below, and reports what changed afterward — it does not show a
   proposal and wait for it to be confirmed.

   | Operation | Handling |
   |---|---|
   | `add`/`append`/`reclassify`/`link` on `project`/`feedback`/`atom`/`negative` rows | autonomous, brief report |
   | `type=user` rows (about the user) | autonomous, flagged above the report for veto (convention — no mechanical backstop) |
   | contradicting or superseding a user `decision_*` row | ask first |
   | `edit`/`delete` | T3 approval flow; delegate to a specialist and never autoexecute |
   | `checkpoint` | autonomous after the milestone test; it is one atomic operation and remains all-or-nothing |
   | closing an objectively verifiable brief/plan | autonomous, report; run `gaia brief verify` by hand before `set-status` — `close` (which runs verification for free, `bin/cli/brief.py::_cmd_close`) is not on the orchestrator's `gaia` CLI lane, only `set-status` is |
   | promoting a TASK | never direct — dispatch `gaia-verifier` |
   | approvals | read/report only |

6. **Verify the durable result.** Read back the affected rows, lifecycle, scope,
   and links. Report partial batch failures per operation.

## When curated memory earns attention

- **Decision:** accepted, with no canonical home, and it binds a later choice.
- **Project milestone:** it closes a meaningful arc. A routine session close is
  not one; minting a checkpoint for it turns memory into session summaries.
- **Live handoff:** one unresolved concern has no structured work object and
  must resurface. One concern per thread — a single status cannot honestly
  represent several.
- **Learning or dead end:** it prevents repeated investigation or error.
- **Gaia improvement:** a concrete symptom, component, evidence and reproduction
  deserves visible follow-up. Persist it as a `feedback` live thread in
  initiative `gaia_system`, carried forward until closed or graduated.

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

- The **user** governs durable knowledge and every "ask first"/"veto" exception.
- The **orchestrator** reads deliberately, resolves ownership, adjudicates,
  curates within the exception boundary, and reports.
- `gaia-operator` materializes already-adjudicated instructions exactly.
- Other specialists only propose (`memory_delta`, `memorialize_suggestions`);
  they never write curated memory directly.
- The runtime enforces the writer boundary and core data invariants. The skill
  supplies curation judgment; it does not duplicate enforcement details.

## Handoffs

- `session-reflection` recovers decisions, live work, learnings, Gaia
  improvements and closures, then hands off the curation this skill governs.
- `gaia-compact` runs after durable persistence and carries only transient
  continuity plus references to what was saved.
- `reference.md` contains exact CLI forms, enums, scope rules, retrieval,
  checkpoint payloads, access telemetry, history coverage, and graph mechanics.
- `examples.md` contains worked create/update/batch/checkpoint cases.

## Anti-patterns
- **Digest or search as corpus:** either can miss an initiative or old wording;
  only the deliberate whole-initiative sweep supports closure.
- **Wrong-scope convenience:** cwd does not decide where knowledge belongs.
- **A resolved thread nobody closes:** it taxes every later session.
- **Stale update metadata:** `append` changes only the body, while repeating
  `add` without `--description` clears that description; verify both.
- **Curating against an old snapshot:** re-read affected rows before closure;
  concurrent sessions and hooks can change the corpus.
- **Claiming perfect history:** only schema and migrations define what survives.
