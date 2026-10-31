---
name: memory
description: Use before the first memory verb — read or write — whenever the orchestrator is about to touch memory as this turn's subject: deciding what persists, searching, curating, or triaging what a session injected at start.
---

# Memory

Memory is the curation technique that turns selected experience into continuity:
durable knowledge kept small, live work kept visible, and raw operational
history left on the automatic event floors where it belongs. Every curated row
is one claim of a closed kind, held by one owner, and Gaia delivers it to the
readers that kind and owner reach.

## What memory is for

Gaia's harness -- its identity, skills, hooks, CLI and the Environment every
session is born with -- is what Gaia does for anyone. Memory holds only the
difference this user, one of his workspaces, groups or projects makes to it.
That one idea decides both whether something is kept and how it is written:

- **What Gaia already knows is no difference.** Kept again, it is a second
  source that goes stale.
- **What would hold for any user is no difference either.** It is a missing
  standard or a defect, fixed in the harness and never patched into his rows.
- **A difference lives only where the harness leaves a choice.** A default
  bends to him once he has agreed to the bend. A wall never does -- consent,
  security, the contract every agent closes -- because a wall memory could move
  would not be a wall. Signing every PR as JLA is a difference; accepting every
  destroy is nothing memory can hold.
- **Whose difference it is decides its owner,** so he never has to name one.
- **A row carries the difference alone,** never the context Gaia already has,
  which is what keeps it short.
- **A decision goes to its home when it is made;** the conversation is only a
  cache of it (orchestrator identity, principle 7).

## The three floors

| Floor | Purpose | Lifecycle |
|---|---|---|
| Events | Commands, dispatches, session events, and other operational facts | Automatic and short-lived |
| Episodes | Searchable agent-turn outcomes and anomalies | Automatic, retained for diagnosis |
| Curated memory | User-governed knowledge and work that must affect future decisions | Deliberate and long-lived |

Events and episodes are evidence, not durable truth.

## Every row answers three questions

**What it claims -- its kind.** `--kind` is required on every `gaia memory
add`, and the list is closed; a missing or unknown kind is refused with the
list.

| Kind | What it claims |
|---|---|
| `fact` | something true; it commands nothing |
| `preference` | a rule every output on a surface obeys; its title is the rule |
| `decision` | one choice among alternatives, with its why |
| `lesson` | something learned, or a dead end |
| `pending` | work owed |
| `defect` | Gaia failing in use; a bug in a product is that project's `pending` |
| `tool` | a program built for the user; its title says how to use it |

**Whose it is -- its owner.** Five owners, each holding only some kinds. The
scope flags place the row; a kind its owner cannot hold is refused, and the
refusal names the right home.

| Owner | Kinds it holds | Placed by |
|---|---|---|
| user | fact, preference, tool | `--type=user`, no scope flag |
| workspace | fact, preference, decision | `--workspace=<ws>` alone |
| group | fact, preference, decision, lesson, pending | `--initiative=<group key> --workspace=<ws>` |
| project | fact, preference, decision, lesson, pending | `--project=<name> --workspace=<ws>` |
| gaia | lesson, defect | `--initiative=gaia_system --workspace=<ws>` |

**Which role it plays -- its class.** An `anchor` is durable and reaches its
readers; a `thread` is one live concern that must reappear until it is closed;
a `log` is kept for audit and never reinjected.

Both tables are what `gaia map memory` and `gaia memory add --help` print, and
both render from `gaia/store/memory_placement.py::KINDS_BY_OWNER` and
`gaia/store/memory_placement.py::KIND_MEANING`; where this page and the CLI
differ, the CLI is right and this page is the drift. `--type` is still the
CLI's row family, which a curated slug's prefix must match (`atom_`,
`decision_`, `negative_`); the kind is what a reader decides on.

For Gaia itself, choose from how the fact was produced, not from the current
directory: Gaia observed failing or rubbing in use belongs to owner gaia, a
`defect` or a `lesson` under `--initiative=gaia_system`, host-scoped by
`gaia/store/writer.py::apply_host_scope` (a project anchor is refused); a
decision to build or change Gaia belongs to the project `gaia`.

**When owners disagree, the most specific wins:** Gaia < user < workspace <
group < project (`gaia/store/memory_placement.py::PRECEDENCE`). A project
receives its group's and its workspace's rows. Consent, security and the
contract every agent closes never move, whatever a row says.

## Who reads what

Gaia delivers by kind and owner; no row is marked for a reader.

| Where | Who reads it | What it carries |
|---|---|---|
| `## User memory`, at birth | the orchestrator only | the user's live facts and preferences, grouped by kind, each line its title, `until=` when it has one, and `ref=memory:user/<slug>` |
| `## Environment`, at birth and in every kernel | everyone | one `tool=<binary>` line per user tool whose binary is on PATH, with `replaces=` and its ref; a kernel carries at most five |
| `# Scope memory`, in a kernel | a dispatched agent | the preferences, facts and lessons of its workspace > group > project chain, each line marked `scope=` |
| The project card, `gaia context project <name>` | the orchestrator, once the project is named | the project's rows indexed by kind |
| `# Standing notes`, in a kernel | a dispatched agent | the dispatch project's anchors, slug and one line |

A user's row never reaches an agent's kernel. The user's preferences that touch
an agent's output travel written in its goal, where a verifier can check them;
that is the orchestrator's rule, in its identity.

Only the title travels in an index, so a title is written to be obeyed alone. A
preference's title is the rule, imperative, as `<Where it applies>: <what to
do>`. A tool's title is how to invoke it, at most 80 characters. The why, the
origin, the date and the examples go in the body, opened with
`gaia memory show <slug>`.

## Other home first

Before saving, ask: **does this already have a canonical home?** The harness is
the first one: what an agent's identity, a skill, a hook, the CLI's help or the
Environment block already carries is no difference, so it gets no row. Work in
flight belongs in a brief, plan, or task; domain state in project context or the
owning system; raw execution detail in events, episodes, or the transcript.

Do not copy a fact into memory merely because it matters. A second source of
truth becomes stale; a durable reference to the canonical object is enough.
That reference is soft: the row names the brief, plan or task in its text, and
nothing links the two. No reader walks such a link, so it would cost a write
and add nothing; the name in the text is what a later search finds.

What remains for memory is placed by whose difference it is, which also decides
who will ever read it. Three questions, asked in order:

- **Is it his difference in any project, one another user would not share?**
  Then the user owns it, and it reaches every session he opens.
- **Is it a short, fixed value an agent working one project must obey without
  reading anything else** -- how that project integrates, where it releases?
  Then it is a declared fact of that project, not memory: the `workflow` key of
  its `project_identity` entry, which every specialist dispatched to that
  project receives in its kernel. A rule an agent has to go looking for is a
  rule it will not follow.
- **Is it history, reasons, a rule or something still owed about one scope?**
  Then that scope owns it: the project when it is true of one codebase, the
  group when it is true of all its projects, the workspace when it is true of
  everything there and is a fact, a preference or a decision.

A rule the user sets for one project splits along those lines: the habit
("follow the workflow each repository declares") is his, the value ("this one
integrates straight to main") belongs to the project. Filed as a user row, the
value follows him into every other project and contradicts the next one.

Ask of any rule for how Gaia should behave: **would it hold for any user?** Then
it is not his difference: it is a missing standard or a defect, filed under
owner gaia where anyone can list and fix it. Saved as his preference instead,
he carries the patch forever and nobody sees the bug. Only if he wants the bend
before the fix lands is a preference kept meanwhile, written with
`--until memory:gaia/<defect slug>` so it stops being injected the day that
defect closes. The harness holds behaviour; memory holds his difference from it.

## Where it goes when the user just says "save this"

The user names the thing, never its home; the kind and the owner are mine, and
the first question is whether it is a difference at all. What Gaia already
carries gets no row. What any user would want is Gaia work, a defect or a
missing standard that step 1 routes. The first yes wins:

1. **Work someone must do?**
   - Part of a version or feature with a brief or plan: add it there, as an AC,
     a task through `gaia-planner`, or a `brief decision`. "So the agent on X
     does it" means X's brief, because that is what that agent reads. The
     version comes from the project's `workflow` (its accumulating branch or PR)
     and `gaia brief list`; with no brief yet, append to the version's scope
     thread.
   - New work that needs its own scope: propose a brief (`brief-spec`).
   - A defect in Gaia: `--kind=defect`, `--type=feedback`, `class=thread`,
     `status=open`, `--initiative=gaia_system`.
   - A bug in a product we own: that project's `pending`.
   - A defect in a product whose repo we don't own (Claude Code, OpenCode, a
     vendor CLI): propose an issue on that product's upstream repo, drafted with
     `ticket-writing`; creating it needs a signature. Add a gaia `lesson` or
     `defect` only if Gaia must work around it.
   - A repo whose declared workflow says issues: an issue in that repo.
   - Otherwise, one concern with no work object: a `pending` thread in that
     project's or group's initiative.
2. **A short fixed rule an agent on one project must obey** (how it integrates,
   reviews, releases): written into the `workflow` value of that project's
   `project_identity` entry, which every kernel dispatched to it carries; not
   memory.
3. **His difference, true in any project:** `--type=user`, kind `fact` or
   `preference`. A preference that bends a harness default is written once he
   has agreed to the bend. One meant to hold for a while carries `--until
   <date>`, or `--until memory:<ws>/<slug>` to hold while that row is live.
4. **A decision, fact, lesson, rule or dead end about one scope:** an anchor of
   that project, group or workspace, by the matrix. Building Gaia goes under the
   project `gaia`; Gaia failing in use under owner gaia.
5. **Raw history:** nothing to save.

**Something I built for him** -- a script, a wrapper, a launcher -- is a
`tool`, owned by the user whichever project it serves. Gaia ships one tool, its
CLI; everything else on his machine is his installation's:

```bash
gaia memory add --name=user_tool_<binary> --type=user --kind=tool \
  --binary=<binary> [--replaces=<cli>] \
  --description='<how to invoke it, when>' --body='<what it is and why>'
```

The binary must be on PATH when the row is written, and one live row describes
each binary. Environment shows the line while the binary is detected.
`--replaces=<cli>` makes the hooks judge the binary as that CLI, so it meets
every gate the CLI meets; it is refused for a CLI the classifier does not
model. The decision that led to building it stays a decision, linked
`derived_from`. Written as a fact or a preference instead, the tool never
reaches Environment and the hooks never learn what it wraps.

Split a mixed item: the decision goes to its anchor, the work to its brief. Ask
only when two live projects could own it, when a new brief or issue would be
created, or when it contradicts a user decision row; never ask which table. Tell
the user one line: "Saved as <kind> for <owner>, because <who will read it
there>."

## When to save

At the moment it appears: a row written at the close of a session is written
from a memory of the conversation, and the parts that did not seem important
then are the parts lost. These earn a curated row:

- **A decision:** accepted, with no canonical home, and it binds a later choice.
- **A preference the user voices or a fact about him:** saved the turn he says
  it, on the user.
- **A tool built for him:** its row written in the same turn it is built.
- **A project milestone:** it closes a meaningful arc. A routine session close
  is not one; minting a checkpoint for it turns memory into session summaries.
- **A live handoff:** one unresolved concern has no structured work object and
  must resurface, as a `pending` thread. One concern per thread -- a single
  status cannot honestly represent several.
- **A lesson or dead end:** it prevents repeated investigation or error.
- **A Gaia defect:** a concrete symptom, component, evidence and reproduction
  deserves visible follow-up, as a `defect` thread under `gaia_system`, carried
  forward until closed or graduated.

## Process

1. **Know what a session is born with, then sweep what you are about to
   touch.** A session opens with `## User memory`, `## Due`, `## Environment`
   (its tool lines included) and `## Workspaces I manage`, each project beside
   its live-pending count. A dispatched specialist gets `# Scope memory`, its
   project's standing notes and one Environment block, so a machine fact needs
   no row. Project memory bodies never load at birth: a count is a signal to
   ask, not the corpus. Anything else a dispatched turn needs travels as a
   reference in its goal. It arrives when that project is worked on: before
   writing into an initiative, or whenever the question is what it still owes,
   read its whole live-pending set with
   `gaia memory get-relevant --initiative=<key>`, uncapped and with bodies, and
   its standing notes with `--sections anchor` added. A reader who only sees the
   counts can add rows and never retire one. For a project the user names, the
   memory index on its `gaia context project` card is the first memory read;
   bodies open when the request is about what it owes or decided.
   `gaia session preview` shows the birth block without recording anything.
2. **Search before writing; do not read silence as absence.** Find the
   subject's existing owner and its lineage — a duplicate divides relevance
   instead of strengthening knowledge. An empty result answers "no row matches
   this phrasing", never "this initiative owes nothing": a pending is worded as
   the problem looked when it opened, not as what just resolved it, so step 1's
   sweep is what finds it. An empty
   `gaia memory get-relevant --initiative` is the complete answer for that
   project: it reads every workspace by the canonical project key
   (`gaia/store/reader.py::pending_threads_by_project`). Without `--workspace`, a
   slug verb also reaches a project's row stored under another workspace
   (`bin/cli/memory.py::_workspace_holding`, refusing with `ambiguous_slug`
   when two other workspaces hold it); a named `--workspace` is never swapped
   for another, and `delete` never makes that jump -- it refuses with
   `workspace_not_named` and names the `--workspace` to sign. A search stays
   scoped to the caller's workspace plus `_gaia_host` and `_gaia_user`
   (`bin/cli/memory.py::_reader_workspaces`), so an empty search or a missing
   row without a project is a scoping hypothesis — confirm with the right
   `--workspace` before trusting it. Before writing a user preference, search
   across owners -- his rows and the scope's decisions -- and link a match
   with `relates_to` instead of copying it. A preference he restates is a
   delivery failure first: read the existing row's kind, owner, class and
   `last_injected_at`, and correct where it sits before adding a new row.
3. **Choose the kind and the owner.** Run *Other home first* and *Where it
   goes*, check the pair against the matrix, and continue only for genuinely
   curated value.
4. **Write one thing per row, in the shape a reader decides on.** `--kind`
   names the claim; the description is the title, written for its kind; the
   body carries the difference, why it holds, where it came from and when -- a
   measured fact says when and how it was measured, or it is an opinion.
   Context Gaia already has is not repeated in it. Knowledge changes by a new
   row that supersedes the old one, the arrow pointing from the new row to the
   old, which then leaves every injection. **Supersedes stays within one owner
   and one kind:** a more specific owner already overrides a broader one when
   it is read, and a kind that changes is linked `graduated_to` or
   `derived_from`. Rewriting the old row in place erases the change itself, so
   memory is append-only: there is no `edit`, an `add` over an existing name is
   refused, and `add --replace` (signed) is only for correcting an error. Only
   a log or a live thread grows by `append`. The CLI refuses a kind its owner
   cannot hold, `--until` on anything but a preference, a tool whose binary is
   not on PATH, and a supersedes across owners or kinds; it cannot judge
   whether the row deserved to exist.
5. **Curate against the exception boundary, then report.** Curation is
   delegated: the orchestrator adjudicates and executes directly, inside the
   boundary below, and reports what changed afterward — it does not show a
   proposal and wait for it to be confirmed.

   | Operation | Handling |
   |---|---|
   | `add`/`append`/`reclassify`/`link` (creating an edge) on rows not owned by the user and not a user decision | autonomous, brief report |
   | a user preference that bends a harness default | ask first: the bend is his to agree to |
   | other user rows (facts, preferences, tools) | autonomous, flagged above the report for veto (convention — no mechanical backstop). They have no workspace: `add` from any workspace writes to the user, and a name already there is reported, never overwritten — a changed preference is a new row that supersedes it, which a link reaches from any workspace |
   | contradicting or superseding a user `decision_*` row | ask first |
   | `add --replace`/`delete`/`link --delete` | T3 approval flow; delegate to a specialist and never autoexecute. `memory_links` keeps no history, so removing an edge is as unrecoverable as a delete |
   | `checkpoint` | autonomous after the milestone test; it is one atomic operation and remains all-or-nothing |
   | closing an objectively verifiable brief/plan | autonomous, report; run `gaia brief verify` by hand before `set-status` — `close` (which runs verification for free, `bin/cli/brief.py::_cmd_close`) is not on the orchestrator's `gaia` CLI lane, only `set-status` is |
   | promoting a TASK | never direct — dispatch `gaia-verifier` |
   | approvals | read/report only |

6. **Verify the durable result.** Read back the affected rows -- kind, owner,
   lifecycle, links -- and report partial batch failures per operation.

## When curated memory loses it

Attention is finite and a live thread spends it every session. Four exits, and
none is deletion:

- **Closed** — the concern was resolved or stopped being relevant.
  `reclassify --status=closed` retires it from the worklist and keeps the row.
- **Graduated** — the work finished and left knowledge worth holding.
  `reclassify --status=graduated`, or `--class=anchor` when future dispatches
  should carry the knowledge; `link --kind=graduated_to` preserves the lineage
  from the thread to the anchor it became.
- **Superseded** — a newer row of the same owner and kind holds the truth now;
  the supersedes link from it retires this one from every injection.
- **Expired** — a preference whose `until` date passed, or whose `until` row
  closed, stops being injected by itself. A workaround preference is written
  that way, so the day its defect closes it leaves.

These verbs are non-mutative, so cost is never the reason a row stays live.
Whoever observes the resolution owns the exit: a session that resolves a thread
and does not close it has moved that thread's cost onto every session after it.

## Curating inconsistencies

Curation looks for two kinds of inconsistency, and the CLI resolves neither.

- **Rows that contradict each other.** Two live rows of one owner and kind that
  answer the same question differently leave every reader to guess, and
  whichever it happens to read first wins. `gaia memory conflicts` lists pairs
  that share wording as candidates; only reading both says whether they
  disagree. What holds is written as the newest row and supersedes the other --
  the same arrow as any change, from the new row to the old. Rows of different
  owners do not contradict: the more specific one wins where both apply.
- **A preference that collides with a wall.** This is the wall *What memory is
  for* names, met after the fact. A preference governs a default: anything Gaia
  would otherwise choose on the user's behalf. It changes the route, never
  consent, security or the contract every agent closes, because those are what
  make everything else trustworthy, and a preference able to lift one would
  turn every preference into a bypass. Such a preference is not saved as
  written: the collision is named to the user, and what is kept is the part
  that governs a default.

## Who writes

- The **user** governs durable knowledge and every "ask first"/"veto" exception.
- The **orchestrator** reads deliberately, resolves kind and owner, adjudicates,
  curates within the exception boundary, and reports.
- `gaia-operator` materializes already-adjudicated instructions exactly.
- Other specialists only propose, in their contract's `memory_delta`;
  they never write curated memory directly. They can read `gaia memory add
  --help` and `gaia map memory`, so a proposed row arrives with its kind and
  owner already chosen.
- The runtime enforces the writer boundary, the kind-per-owner matrix and the
  supersedes rule. The skill supplies curation judgment; it does not duplicate
  enforcement details.

## Handoffs

- `session-reflection` recovers decisions, live work, lessons, Gaia defects
  and closures, then hands off the curation this skill governs.
- A compaction, the last step of `session-reflection`, runs after durable
  persistence and carries only transient continuity plus references to what
  was saved.
- The CLI is the other teaching surface: `gaia map memory` is the tour of the
  model -- kinds, owners, precedence, delivery, supersession, `until` -- and
  `gaia memory add --help` carries the kind list and the owner matrix for one
  write; every write warns on the form and closes with a pointer back here.
- `reference.md` contains the write path (scope, kinds and owners, tool rows,
  warnings) and retrieval; three siblings hold the rest, loaded by subject:
  `reference-curation.md` (lifecycle, links, checkpoint payloads, history
  coverage, graph), `reference-defect-promotion.md` (the gaia defect shape)
  and `reference-access-telemetry.md` (the read counters).
- `examples.md` contains worked create, change, tool, bug-as-rule and
  checkpoint cases.

## Anti-patterns

- **Digest or search as corpus:** either can miss an initiative or old wording;
  only the deliberate whole-initiative sweep supports closure.
- **Wrong-scope convenience:** cwd does not decide which owner holds a row.
- **A standard or a bug saved as his preference:** what any user would want,
  kept in his rows -- he carries the patch forever and nobody lists the defect.
- **A user row as a way to reach an agent:** it never reaches a kernel; the
  rule an agent must apply is written into its goal.
- **A tool saved as a fact or a preference:** Environment never shows it and
  the hooks never judge it as the CLI it wraps.
- **The supersedes arrow pointed backwards, or across owners or kinds:** the
  new row leaves every injection and the stale one stays, or the link is
  refused; a broader owner is overridden by reading, not by superseding.
- **A resolved thread nobody closes:** it taxes every later session.
- **Stale update metadata:** `append` changes only the body, while repeating
  `add` without `--description` clears that description; verify both.
- **Curating against an old snapshot:** re-read affected rows before closure;
  concurrent sessions and hooks can change the corpus.
- **Claiming perfect history:** only schema and migrations define what survives.
