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

## Every row answers three questions

- **Whose it is.** The user (true of him in any project, so it has no
  workspace), a project (true of that codebase or initiative), or Gaia itself (a
  symptom of, or a decision about, the host). A row is written where its owner's
  readers look. A workspace is a container, not an owner: a row that names only
  a container reaches no reader who is looking for it.
- **Which role it serves.** Durable knowledge (facts, accepted decisions,
  useful dead ends, preferences, milestones), one live thread that must
  reappear, or a log kept for audit and never reinjected.
- **What kind of claim it makes.** The first word of its description says so,
  so a listing reads as a set of claims rather than titles, and a user's fact
  is told from a user's preference without opening the body.

The enums, sentinels, thresholds and claim vocabulary are the CLI's;
`reference.md` anchors each to the symbol that reads it.

For Gaia itself, choose from how the fact was produced, not from the current
directory: Gaia observed failing or rubbing in use is `initiative=gaia_system`,
host-scoped by `gaia/store/writer.py::apply_host_scope` (a project anchor is
refused); a decision to build or change Gaia is project-scoped
`initiative=gaia`.

## Other home first

Before saving, ask: **does this already have a canonical home?** Work in flight
belongs in a brief, plan, or task; domain state in project context or the owning
system; raw execution detail in events, episodes, or the transcript.

Do not copy a fact into memory merely because it matters. A second source of
truth becomes stale; a durable reference to the canonical object is enough.
That reference is soft: the row names the brief, plan or task in its text, and
nothing links the two. No reader walks such a link, so it would cost a write
and add nothing; the name in the text is what a later search finds.

What remains for memory is placed by three questions, asked in order, because
the answer decides who will ever read it:

- **Would it still hold in a project the user has never touched?** Then it is
  the user's, with no project, and it reaches every session and every agent.
- **Is it a short, fixed value an agent working one project must obey without
  reading anything else** -- how that project integrates, where it releases?
  Then it is a declared fact of that project, not memory: the `workflow` key of
  its `project_identity` entry, which every specialist dispatched to that
  project receives in its kernel. A rule an agent has to go looking for is a
  rule it will not follow.
- **Is it history, reasons or something still owed about one project?** Then
  it is that project's memory, read when that project is worked on.

A rule the user sets for one project splits along those lines: the habit
("follow the workflow each repository declares") is his, the value ("this one
integrates straight to main") belongs to the project. Filed as a user row, the
value follows him into every other project and contradicts the next one.

Then ask of any rule for how Gaia should behave: **would it still make sense if
Gaia worked perfectly?** If not, it is not knowledge about anyone -- it is a
defect, filed under `gaia_system` where anyone can list and fix it. Saved as a
user preference instead, the user carries the patch forever and nobody sees the
bug. What the user wants meanwhile is a preference that names the bug it covers
and retires the day that bug closes. Behaviour belongs to the harness; memory
holds what is true.

## Process

1. **Know what a session is born with, then sweep what you are about to
   touch.** Every session opens with four sections: the projects, each with its
   live-pending count; the environment it stands in; the user; and the user's
   preferences. The last two are the user's standing rows, whole; every
   dispatched subagent receives them too, minus the rows whose audience is the
   orchestrator alone, so a row about how the orchestrator should report spends
   no specialist's attention. Project memory — anchors, threads, their bodies —
   never loads at birth and never reaches a specialist's kernel: a count is a
   signal to ask, not the corpus, and what a dispatched turn needs of it travels
   as a reference in its goal. It arrives when that project is worked on: before
   writing into an initiative, or whenever the question is what it still owes,
   read its whole live-pending set with
   `gaia memory get-relevant --initiative=<key>`, uncapped and with bodies, and
   its standing notes with `--sections anchor` added. A reader who only sees the
   counts can add rows and never retire one. `gaia session preview` shows the birth block without
   recording anything.
2. **Search before writing; do not read silence as absence.** Find the topic's
   existing owner and its lineage — a duplicate divides relevance instead of
   strengthening knowledge. An empty result answers "no row matches this
   phrasing", never "this initiative owes nothing": a pending is worded as the
   problem looked when it opened, not as what just resolved it, so step 1's
   sweep is what finds it. An empty
   `gaia memory get-relevant --initiative` is the complete answer for that
   project: it reads every workspace by the canonical project key
   (`gaia.store.reader::pending_threads_by_project`). Without `--workspace`, a
   slug verb also reaches a project's row stored under another workspace
   (`bin/cli/memory.py::_workspace_holding`, refusing with `ambiguous_slug`
   when two other workspaces hold it); a named `--workspace` is never swapped
   for another, and `delete` never makes that jump -- it refuses with
   `workspace_not_named` and names the `--workspace` to sign. A search stays scoped to the
   caller's workspace plus `_gaia_host` and `_gaia_user`
   (`bin/cli/memory.py::_reader_workspaces`), so an empty search or a missing
   row without a project is a scoping hypothesis — confirm with the right
   `--workspace` before trusting it.
3. **Choose the home.** Run *Other home first*, and continue only for genuinely
   curated value.
4. **Write one thing per row, in the shape a reader decides on.** The
   orchestrator answers the three questions above, then writes a description
   that is one sentence naming its kind of claim and a body that carries the
   content, why it holds, where it came from and when -- a measured fact says
   when and how it was measured, or it is an opinion. Knowledge changes by a new
   row that supersedes the old one, the arrow pointing from the new row to the
   old, which then leaves every injection; rewriting the old row in place
   erases the change itself, so memory is append-only: there is no `edit`, an
   `add` over an existing name is refused, and `add --replace` (signed) is only
   for correcting an error. Only a log or a live thread grows by `append`. Graduation keeps its
   lineage the same way. A row about the user stands from the moment it is
   written -- every session and every dispatched agent carries it -- so the
   first question of *Other home first* is what earns it that reach. The CLI
   checks length, owner and the arrow and prints
   the vocabulary as the row is written; it cannot judge whether the row
   deserved to exist.
5. **Curate against the exception boundary, then report.** Curation is
   delegated: the orchestrator adjudicates and executes directly, inside the
   boundary below, and reports what changed afterward — it does not show a
   proposal and wait for it to be confirmed.

   | Operation | Handling |
   |---|---|
   | `add`/`append`/`reclassify`/`link` (creating an edge) on rows not about the user or a user decision | autonomous, brief report |
   | `type=user` rows (about the user) | autonomous, flagged above the report for veto (convention — no mechanical backstop). They have no workspace: `add` from any workspace writes to the user scope, and a name already there is reported, never overwritten — a changed preference is a new row that supersedes it, which a link reaches from any workspace |
   | contradicting or superseding a user `decision_*` row | ask first |
   | `add --replace`/`delete`/`link --delete` | T3 approval flow; delegate to a specialist and never autoexecute. `memory_links` keeps no history, so removing an edge is as unrecoverable as a delete |
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

Attention is finite and a live thread spends it every session. Three exits, and
none is deletion:

- **Closed** — the concern was resolved or stopped being relevant.
  `reclassify --status=closed` retires it from the worklist and keeps the row.
- **Graduated** — the work finished and left knowledge worth holding.
  `reclassify --status=graduated`, or `--class=anchor` when future dispatches
  should carry the knowledge; `link --kind=graduated_to` preserves the lineage
  from the thread to the anchor it became.
- **Superseded** — a newer row holds the truth now; the supersedes link from it
  retires this one from every injection. A workaround preference is superseded
  or closed the day its bug closes.

These verbs are non-mutative, so cost is never the reason a row stays live.
Whoever observes the resolution owns the exit: a session that resolves a thread
and does not close it has moved that thread's cost onto every session after it.

## Curating inconsistencies

Curation looks for two kinds of inconsistency, and the CLI resolves neither.

- **Rows that contradict each other.** Two live rows of one owner that answer
  the same question differently leave every reader to guess, and whichever it
  happens to read first wins. `gaia memory conflicts` lists pairs that share
  wording as candidates; only reading both says whether they disagree. What
  holds is written as the newest row and supersedes the other -- the same arrow
  as any change, from the new row to the old.
- **A preference that collides with a system rule.** A preference governs a
  default: anything Gaia would otherwise choose on the user's behalf. It never
  governs an invariant -- consent, security, the contract every agent closes --
  because those are what make everything else trustworthy, and a preference
  able to lift one would turn every preference into a bypass. Such a preference
  is not saved as written: the collision is named to the user, and what is kept
  is the part that governs a default.

## Who writes

- The **user** governs durable knowledge and every "ask first"/"veto" exception.
- The **orchestrator** reads deliberately, resolves ownership, adjudicates,
  curates within the exception boundary, and reports.
- `gaia-operator` materializes already-adjudicated instructions exactly.
- Other specialists only propose (`memory_delta`, `memorialize_suggestions`);
  they never write curated memory directly. They can read `gaia memory add
  --help` and `gaia memory link --help`, so a proposed row arrives in the
  shape it will be written in.
- The runtime enforces the writer boundary and core data invariants. The skill
  supplies curation judgment; it does not duplicate enforcement details.

## Handoffs

- `session-reflection` recovers decisions, live work, learnings, Gaia
  improvements and closures, then hands off the curation this skill governs.
- `gaia-compact` runs after durable persistence and carries only transient
  continuity plus references to what was saved.
- The CLI is the other teaching surface: `gaia memory add --help` carries the
  claim vocabulary and the three owners, and every write warns on the form and
  closes with a pointer back here.
- `reference.md` contains the write path (scope rules, enums, warnings) and
  retrieval; three siblings hold the rest, loaded by subject:
  `reference-curation.md` (lifecycle, links, checkpoint payloads, history
  coverage, graph), `reference-defect-promotion.md` (the `gaia_system` defect
  shape) and `reference-access-telemetry.md` (the read counters).
- `examples.md` contains worked create, change, bug-as-rule and checkpoint cases.

## Anti-patterns
- **Digest or search as corpus:** either can miss an initiative or old wording;
  only the deliberate whole-initiative sweep supports closure.
- **Wrong-scope convenience:** cwd does not decide where knowledge belongs.
- **A bug saved as a preference:** the user carries the patch forever and nobody
  lists the defect.
- **The supersedes arrow pointed backwards:** the new row leaves every injection
  and the stale one stays, so the corpus reads as if the change never happened.
- **A resolved thread nobody closes:** it taxes every later session.
- **Stale update metadata:** `append` changes only the body, while repeating
  `add` without `--description` clears that description; verify both.
- **Curating against an old snapshot:** re-read affected rows before closure;
  concurrent sessions and hooks can change the corpus.
- **Claiming perfect history:** only schema and migrations define what survives.
