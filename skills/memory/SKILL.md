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

## The one-line test: which initiative

A fact about Gaia itself splits by how it was produced, and that split decides
where it can physically land — not as a style choice, but because the writer
enforces it:

- **Observed the system fail or rub in use** — a symptom, with evidence,
  noticed while working on something else — is `initiative=gaia_system`.
  `gaia_system` is host-scoped (`gaia/store/writer.py::HOST_SCOPED_INITIATIVES`):
  `apply_host_scope` (`gaia/store/writer.py::apply_host_scope`) forces the row
  into the sentinel workspace `_gaia_host` regardless of whatever
  `--workspace`/env/cwd resolved, and refuses a project anchor outright —
  passing `project_ref` raises `MemoryHostScopeError`
  (`gaia/store/writer.py::MemoryHostScopeError`, `code=host_scope_no_project`).
- **Decided to build or change Gaia** — a design choice, a completed change, a
  plan for how a component should work — is `initiative=gaia` (or whatever the
  repo's own basename normalizes to), project-scoped exactly like any other
  project's memory: `--workspace`/`--project` apply normally and the row lives
  wherever that project's memory already lives.

This is the only judgment the writer still leaves to you. Once the initiative
is named, the physical destination is not a choice: mis-picking the initiative
is the only way to mis-file the row, because nothing downstream corrects a
scope chosen wrong at the source.

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
   | `edit`/`delete` | T3 approval flow, and also categorically denied on the orchestrator's own `gaia` CLI lane (`hooks/modules/security/gaia_cli_only_guard.py::check`) — a dispatched specialist runs it under approval, the orchestrator's bare CLI never does |
   | `checkpoint` | autonomous when it passes the milestone test (`session-reflection/reference.md`) |
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

- The **user** is the authority for durable personal and project knowledge, and
  for the exceptions the process table marks "ask first" or "veto".
- The **orchestrator** resolves ownership, searches, adjudicates, executes
  within the exception boundary above, and reports.
- `gaia-operator` executes what the orchestrator has already adjudicated,
  without loading this skill itself (`agents/gaia-operator.md` carries no
  `memory` entry in its `skills:` list) — a convention of responsibility split
  between the two roles, not a technical inability for either to run the
  other's step.
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

- **Claiming perfect history:** ordinary updates are audited, but hard deletion
  and workspace removal can destroy records. Exact tracked fields are defined by
  the current schema and migration, not by prose.
- **Digest as corpus:** treating the SessionStart digest (`bin/cli/memory.py::_render_digest`)
  as the whole state of an initiative. It is a char-budgeted worklist that trims
  whole initiatives from the tail — an initiative absent from it may still carry
  live-pending rows; only `get-relevant --initiative=<key>` returns the corpus.
- **Empty read as absence:** a search or digest miss answers only "not under
  this phrasing", never "nothing is owed" (see Process step 2). This is now
  mechanically ruled out for a host-scoped initiative specifically: every read
  of `gaia_system` unions the sentinel workspace into the query
  (`bin/cli/memory.py::_reader_workspaces`), so an empty result there really is
  empty, from any vantage — it can no longer be explained away as "wrong
  workspace".
- **Writing where you're standing:** before host-scope existed, a `gaia_system`
  row landed in whatever workspace the session happened to be in, scattering
  "how Gaia itself is doing" across every workspace ever used until no single
  read could see the whole corpus. For a host-scoped initiative this is now
  mechanically impossible — `apply_host_scope` forces the sentinel regardless
  of cwd. The lesson still generalizes to any non-host-scoped write: the
  workspace you happen to be standing in is not evidence of the right scope
  for a fact that is really about something broader — run *The one-line test*.
- **A resolved thread nobody closes:** fixing what a thread describes without
  reclassifying it moves that thread's cost onto every later session that has
  to re-read and re-triage it, even though closing it costs nothing (both
  lifecycle verbs are non-mutative).
- **Append leaves a stale description:** `append` (`bin/cli/memory.py::_cmd_append`)
  concatenates onto `body` only — it never touches `description`. A listing or
  digest renders `description`, not `body`, so a note that grows entirely
  through appends can carry a description that no longer matches what the
  body now says.
- **`add` without `--description` erases it:** `upsert_memory`
  (`gaia/store/writer.py::upsert_memory`) writes `description` straight from
  the call's argument on every UPSERT (`description = excluded.description`,
  not coalesced against the existing value) — unlike `project_ref`/`initiative`/
  `audience`, which are deliberately coalesce-or-omit. Re-running `add` on an
  existing row without repeating `--description` silently NULLs a previously
  set one.
- **Curating in the cold without a snapshot:** the database keeps moving while
  a curation or audit pass runs — other sessions and hooks are still writing.
  A conclusion drawn from an early read and asserted later, without re-reading
  the rows it depends on, can state something as current that changed
  underneath it while the pass was still running.
