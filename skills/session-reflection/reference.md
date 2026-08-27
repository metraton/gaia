# Session Reflection — Reference

Recovery and reconciliation mechanics for dense sessions. `SKILL.md` carries the
process and the output shape; this holds the sweep detail, the objective-state
checks, the Gaia-improvement body, and the milestone test.

## Recovery pass

Walk the transcript from its first turn and collect four independent streams:

1. explicit acceptance, rejection, correction, deferral, and closure;
2. user reactions to specialist results;
3. durable work objects mentioned or changed;
4. contract findings from `cross_layer_impacts`, `open_gaps`, and
   `failure_report`.

Keep settled and open disjoint. A topic discussed without affirmative or
objective closure remains open. A specialist finding can enter "What Gaia should
improve" without entering "What we settled".

The transcript is only half the input. It shows what the session produced; it
cannot show what the session silently resolved, because a pending opened three
sessions ago is not mentioned by the turn that finally makes it moot. That half
comes from the sweep.

## The reverse sweep

For every initiative the harvest touched, read that initiative's whole live
corpus and triage it row by row:

```bash
gaia memory get-relevant --initiative=<key> --json
```

The mode is deliberately uncapped — the whole live-pending set with `body`
projected and `description` verbatim — because it answers a question the caller
asked rather than feeding the SessionStart budget. `memory/reference.md`
("Retrieval is cwd-INDEPENDENT") owns that contract; do not re-derive it here.

Three mechanics decide whether the sweep is complete:

- **The key is the stored initiative**, normalized at write time by
  `normalize_initiative`. Pass it as stored, not as the project's display name.
- **Rows with no initiative are their own bucket.** `--initiative=otros` targets
  the NULL-initiative rows; skipping it leaves the least-owned pendings — the
  ones most likely to be stale — permanently unswept.
- **A project's own initiative is workspace-scoped.** A pending filed under
  another workspace is not in the answer, and its absence is not evidence it
  does not exist.
- **`gaia_system` is host-scoped, and complete from any vantage.** Every
  `gaia_system` row is pinned to the sentinel workspace by the writer
  (`gaia/store/writer.py::apply_host_scope`, `HOST_SCOPED_INITIATIVES`), and
  every read of it unions that sentinel in regardless of the caller's own
  workspace (`bin/cli/memory.py::_reader_workspaces`). So an empty sweep of
  `gaia_system` from any workspace is a complete answer, not a scoping gap —
  unlike a project initiative, there is no "wrong vantage" to worry about.

Ask of each returned row: did this session close it, advance it, invalidate it,
or leave it untouched? Only the last verdict produces no row in the reflection.

| Verdict | Operation | Lifecycle result |
|---|---|---|
| resolved, or no longer relevant | TRANSITION | `status=closed` |
| finished and left knowledge worth holding | TRANSITION | `status=graduated`, or `class=anchor` plus a `graduated_to` link from the thread |
| advanced but still open | APPEND | body grows, status unchanged |
| contradicted by what the session learned | TRANSITION + LINK | close or supersede, with the lineage recorded |
| untouched | none | stays as it is |

`memory/reference.md` ("Move a note through its lifecycle") holds the exact
`reclassify` and `link` forms. Both verbs are non-mutative, so the only thing
that keeps a resolved thread open is failing to propose the row.

## Objective-state reconciliation

A `SKIP` is a claim about another system, so verify it against that system
before writing it:

- draft/open briefs and plans;
- task and gate status;
- blocked or needs-input contracts;
- approvals still in flight;
- existing curated memory on the same topic.

An item genuinely represented by one of these takes `SKIP` with the identifier
named. An item whose supposed owner turns out to be closed, missing, or about
something else is open work that has been hiding behind a reference.

Closing a brief through `set-status` is a bare status write and does not run
its own consistency check; `close` does (`bin/cli/brief.py::_cmd_close` calls
`verify_brief` as a non-blocking advisory), but the orchestrator's `gaia` CLI
lane admits only `set-status` (`hooks/modules/security/gaia_cli_only_guard.py::check`).
So an objectively-verifiable brief closure runs `gaia brief verify` by hand
first, and reads its result, before `set-status` — otherwise the closure
carries none of the check the free verb would have supplied.

## Gaia improvement shape

A persistable improvement has four recoverable fields:

- Symptom: observable behavior, not diagnosis.
- Component: one precise owner.
- Evidence: observed proof.
- Reproduction: exact repeatable route, or `unknown`.

All four are shown in the reflection, not summarized into it. A defect whose
evidence was never displayed was consented to as a slug.

It is materialized as a `feedback` live thread with `initiative=gaia_system`
and carry-forward lifecycle — `add` on a `feedback` row is autonomous under
the exception boundary in `memory/SKILL.md`. Those two values are what
make the corpus retrievable in one read —
`gaia memory get-relevant --initiative=gaia_system` — so a defect carrying the
project's own initiative, or another type, is absent from that answer. The exact
slug, CLI flags, and body headings are defined in `memory/reference.md`.

## Milestone test

Use `checkpoint` only when the session completed a durable arc that a future
reader would recognize as a project milestone. Number of turns, number of
dispatches, or merely reaching session end are not evidence of a milestone.

## Anti-patterns

- **Scoping the pass by something other than the whole arc:** a recent-turns
  window drops early decisions that were never revisited, and scoping it to
  what the next context needs drops precisely the items only memory can
  hold. Either way the user cannot tell an omission from a judgment.
- **Confusing findings with agreements:** specialist evidence can justify a
  Gaia improvement, but presenting it as a settled decision fabricates
  consent.
- **Asserting a home instead of verifying it:** `SKIP — already tracked`,
  written without opening the brief, plan, or task, reads as filed and is
  indistinguishable from lost. A named home is a claim; check it before
  making it.
- **Curating in one direction only:** a reflection that only opens rows grows
  the corpus monotonically until every pending looks equally live and the
  worklist stops being read. Closing is half the work.
- **Duplicating canonical work:** memory created so the reflection reads
  self-contained becomes a second source of truth that goes stale.
- **Checkpoint by ritual:** only a durable arc a future reader would
  recognize as a milestone earns one; turn count and session end are not
  evidence.
- **Curating past the witness:** asserting a closure or a settled fact the
  session itself does not support. Autonomous curation removed the pre-write
  review that used to catch this; nothing else does. Every row in the report
  still needs the same evidence a reviewer would have demanded — the sweep
  result, the verified object state, the transcript agreement — not the
  confidence that it is probably fine.
- **Assumed cascade:** treating one object's status as evidence for a sibling
  object's closure — a plan marked `active` does not tell you anything about
  its tasks' individual status, and a closed task does not tell you its plan
  is done. There is no mechanical cascade between brief, plan, and task
  status; each is checked on its own. Measured: 38 of 60 plans in the
  substrate sit open while carrying no signal from their own task state that
  would resolve that on inspection alone.
