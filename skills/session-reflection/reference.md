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
- **The mode is workspace-scoped.** A pending filed under another workspace is
  not in the answer, and its absence is not evidence it does not exist.

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

## Gaia improvement shape

A persistable improvement has four recoverable fields:

- Symptom: observable behavior, not diagnosis.
- Component: one precise owner.
- Evidence: observed proof.
- Reproduction: exact repeatable route, or `unknown`.

All four are shown in the reflection, not summarized into it. The review is the
user's only chance to correct them before they become a durable thread, and a
defect whose evidence was never displayed was consented to as a slug.

After user confirmation it is materialized as a `feedback` live thread with
`initiative=gaia_system` and carry-forward lifecycle. Those two values are what
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
