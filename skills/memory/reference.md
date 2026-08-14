# Memory — Reference

Exact mechanics for the `memory` technique: storage enums, CLI operations,
scope and retrieval rules, checkpoint payloads, history coverage, and graph
behavior. Load this when materializing, debugging, or auditing memory; the
judgment and ownership flow stays in `SKILL.md`.

## Project-scoped memory: reference `project_ref`, not the workspace

Memory rows are keyed by `(workspace, name)`, but a workspace is a
container that can be renamed, split, or hold a project that later
moves elsewhere (scan-v2: a project row re-keyed by a `movido`
adjudication carries `superseded_by`, but the memory row stays under
its original `workspace` key unless a human runs `move-memory`). A
`project_*` note that means "this is true of project X" should record
that fact durably rather than only implicitly through the workspace it
happens to live in today.

`memory.project_ref` (schema v25, scan-v2 SV1) is the stable anchor for
this: it should hold the project's `project_identity` -- the same
vantage-independent identity scan writes onto `projects.project_identity`
(git-common-dir realpath > normalized remote > realpath path). A note keyed
this way remains correctly attributed even after the project physically
moves workspace -- the `project_ref` value does not change on a move, only
the `projects` row's `(workspace, name)` does. A `project_*` note about the
workspace as a whole (not a single project within it) legitimately leaves
`project_ref` NULL.

**Required scope (deterministic, no guessing).** `gaia memory add` requires
**at least one** explicit scope flag -- `--project` (preferred) or
`--workspace`. It never writes with project and workspace both empty: that
would leave `project_ref` NULL purely for lack of input. The function does
**not** infer scope from the cwd and does **not** fall back silently. Scope
inference from natural language ("the century project") is the
**orchestrator's** job, not the function's -- the function only accepts
explicit, resolvable scope.

- `--project=<name>` resolves the name within `--workspace` to that project's
  `projects.project_identity` and persists it as `memory.project_ref`:

  ```bash
  gaia memory add --name=project_x_status --type=project \
    --project=x --workspace=me --body="..."
  ```

- `--project-ref=<identity>` anchors directly to a known identity string
  (scripting across workspaces); mutually exclusive with `--project`.
- `--workspace=<ws>` alone (no project flag) is the **explicit degraded
  lane**: a legitimate workspace-scoped note with `project_ref` NULL and
  exit 0. A `project_*` note about the workspace as a whole lives here.

**Errors are structured and machine-parseable** so the orchestrator can run
the command, read the failure, and *manage* it deterministically instead of
guessing. Every failure exits non-zero (1) and, with `--json`, prints
`{"error": "...", "code": "<code>", ...}` (text mode prints
`Error [<code>]: ...` to stderr). On any of them the row is **not** written --
there is no partial or silent-NULL write:

| `code` | Cause | How the orchestrator manages it |
|--------|-------|---------------------------------|
| `missing_scope` | Neither `--project` nor `--workspace` given | Re-run with `--workspace` (degraded lane), or resolve a project and re-run with `--project`. |
| `project_unresolved` | `--project=<name>` does not exist in the workspace | Ask the user which project, or list `projects` and retry. |
| `project_workspace_mismatch` | `--project` exists, but under a different workspace (see `found_in`) | Re-run with a workspace from `found_in`, or correct the project name. |
| `project_no_identity` | Project exists but has no `project_identity` yet | `gaia scan` first, then retry. |

When `--project` resolves, the note is anchored: `memory.project_ref` = the
project's durable identity.

Anchoring is **forward-only, by design**. Rows written before this
mechanism existed stay `project_ref IS NULL` -- the memory-row-to-project
mapping is genuinely ambiguous whenever a workspace hosts more than one
project, so no backfill can guess it. A `project_*` row gets anchored only
by an explicit `--project` / `--project-ref` at write time.
`upsert_memory()` treats `project_ref` with coalesce-or-omit discipline:
omitting it on a later update never clobbers a previously-set anchor back to
NULL (a later `add` that re-supplies only `--workspace` keeps the prior
anchor).

**Retrieval is cwd-INDEPENDENT (schema v32, commit `d2fba1c`).** An earlier
form of `gaia memory get-relevant` (`_cmd_get_relevant`) resolved an "active
project" from the launch directory and used it to restrict/reorder results
(`project_ref = active` prioritized, a *different* project's rows dropped
out). That cwd-based project inference has been **removed** -- the launch
directory no longer filters, restricts, or reorders anything. `_cmd_get_relevant`
now dispatches on explicit flags only, workspace-scoped, never per-project by
cwd:

- **(no flag, the default)** -- the TRANSVERSAL DIGEST (`_render_digest`): a
  cross-project worklist of live-pending threads (`class=thread`, `status` in
  `carry_forward`/`open`), grouped by the canonical `memory.initiative` key,
  identical regardless of the directory the session started in. This is the
  orchestrator's SessionStart view.
- **`--sections=carry_forward,anchor,thread_open`** -- the class/status
  SECTION renderer (`_render_sections`); this is the subagent-dispatch path
  (`--sections=anchor` gives a dispatched subagent only the durable "About
  you / What I know" anchors). Workspace-scoped, never filtered or
  prioritized by the launch directory.
- **`--initiative=X`** -- PROJECT MODE (`_render_project_mode`): the explicit
  replacement for the old implicit cwd guess. Rather than inferring which
  project is "active" from where the command was launched, the caller names
  the initiative directly (normalized the same way the write side stores it,
  via `normalize_initiative`); the special value `otros` targets the
  NULL-initiative bucket. Returns the WHOLE live-pending corpus of that one
  initiative -- no top-N cap, no overflow footer, `body` projected alongside
  `class`/`status`, and `description` verbatim (uncapped, no ellipsis).
  `--max-chars` is accepted and ignored here. The attention cap that governs
  the digest and section renderers exists because THEY feed an unrequested
  SessionStart block, where the budget is scarce; applying the same cap to a
  corpus the caller explicitly asked for, for triage, would silently
  withhold part of the answer it was asked to return. This is deliberate --
  do not reintroduce the cap here to make the two modes "consistent."
- **`--types=...`** -- the legacy per-type flow (`_cmd_get_relevant_by_type`),
  kept verbatim for back-compat; also workspace-scoped only.

The *workspace* itself (not the project within it) may still be cwd-inferred
when `--workspace` is omitted -- `_resolve_workspace` falls back to
`gaia.project.current()`, same as the write side's default -- but that is
workspace identity, not project-level filtering/reordering, and it was never
the "active project" mechanism this note is about.

This closes an asymmetry the earlier form had: read-side cwd inference used
to be justified as "read-only and deliberate... a wrong guess only re-ranks
what is shown," in contrast with the write side, which has always refused to
infer project scope from the cwd (see "Required scope" above). With the cwd
guess removed from retrieval too, both sides now share the same discipline:
neither infers project scope from the launch directory -- `add` demands an
explicit `--project`/`--project-ref`/`--workspace`, and `get-relevant` demands
an explicit `--initiative` to narrow to one project's pending work.

## Digest and anchor budgets: query mechanics

`class=anchor` and `class=thread status=carry_forward` surface through
two separate SessionStart queries, each with its own budget. `SKILL.md`
keeps the practical consequence -- an anchor never reaches the digest;
this section holds the query mechanics behind it.

- **The digest (`carry_forward`/`open`) never carries anchors at all.**
  Its query filters `class='thread' AND status IN ('carry_forward',
  'open')`, so an `anchor` row is invisible to it regardless of budget.
  Its own overflow mechanism trims whole initiatives from the tail
  (top-K initiatives, with a global "+N más" and a per-initiative "+N
  más en X" hint) when the ~1500-char cap is exceeded -- it never
  competes with anchors for that budget.
- **The anchor call (`sections=["anchor"]`) never carries pendings at
  all.** Its query filters `class='anchor'` only, and is additionally
  capped at a small fixed quota (`_RELEVANT_PER_CLASS_QUOTA["anchor"]`,
  identity anchors pinned first, then most-recently-updated) before it
  is even rendered -- independent of and much tighter than the
  digest's own worklist budget.
- **A three-way, single-call trim order also exists in the CLI** --
  `gaia memory get-relevant --sections=carry_forward,anchor,thread_open`
  (one call, all three sections) trims one bullet at a time in the
  fixed order `thread_open` → `anchor` → `carry_forward` when the
  combined render overflows the char cap. No live caller requests that
  three-section combination today; it is reachable only by an explicit
  manual invocation.

## Access telemetry: exact columns and call sites

The `memory` table carries three independent counter/timestamp pairs --
`injection_count` / `last_injected_at`, `deliberate_count` /
`last_deliberate_at`, and `kernel_count` / `last_kernel_at` (v50) -- bumped
by one shared helper, `gaia.store.writer.record_memory_access(workspace,
name, kind, *, db_path=None)`; `kind` is exactly `"injection"`,
`"deliberate"`, or `"kernel"`, anything else raises `ValueError`. The
UPDATE touches only the counter and its timestamp, never `updated_at` or
`body`, so it fires no `memory_history` row and never reorders the digest,
which still sorts by `updated_at` alone. It is best-effort: every failure
is swallowed and reported as `False`, so a locked or unreachable DB never
breaks the read it instruments.

`SKILL.md`'s "Reading a row leaves a trace" carries the property that decides
deliberate from automatic -- whether the CALLER identified the rows, never
whether the answer carried the body. That property alone reaches only two
buckets; it does not by itself split the automatic bucket further. A second,
purely mechanical test does that: an automatic surface is `kernel` when it
renders a FIXED corpus on every subagent dispatch (the same `type=user AND
audience=executor` rows, regardless of what the dispatch is about), and
`injection` when the rendered rows vary with what the caller's window
actually selects (`get-relevant`'s digest/sections/types). Splitting on that
axis is what keeps the dispatch-kernel's fixed rows from dominating a
demand ranking by construction. These are the places code applies both tests
today, and a new surface is classified by them rather than added to this
list:

| Call site | Symbol | Kind |
|---|---|---|
| `get-relevant` (no flag), `--sections=`, `--types=` | `_bump_injection_telemetry` (`bin/cli/memory.py`) | injection |
| Subagent kernel's "How the user works" block | `_record_kernel_telemetry` (`hooks/modules/context/kernel_builder.py`) | kernel |
| `memory show <slug>`, every mode including `--links`/`--history` | `_cmd_curated_show` (`bin/cli/memory.py`) | deliberate |
| `memory story <slug>`, the seed row alone | `_cmd_story` (`bin/cli/memory_story.py`) | deliberate |
| `get-relevant --initiative=<key>`, text and JSON alike | `_render_project_mode` (`bin/cli/memory.py`) | deliberate |

Whatever a window returns writes nothing, whatever it renders of each row:
`search`, `list`, `stats`, `conflicts`, and `gaia query` in every one of its
modes -- table, `--json`, `--count`, `--group-by`. `gaia query` is the
substrate's event reader, auditing memory, episodes and the hook log in one
call; it never names a row, so no shape of its output is a read of one. The
lineage a `story` BFS discovers around its seed is the same case: the caller
named the seed, not what the walk reached from it.

`tests/integration/test_memory_access_telemetry_surfaces.py` pins every CLI
row of that table by running the real command and measuring the counters it
moved, discovering the surface set from the argument parser rather than from
a list -- so a new subcommand or a new flag on a read subcommand fails it
until classified. The kernel row has no subcommand to run: it is context
assembly, not a `gaia memory` verb, so it is pinned instead by two dedicated
recipes that invoke `build_memory_block` directly --
`test_kernel_memory_block_counts_the_rows_it_renders` and
`test_kernel_dispatch_and_context_digest_move_disjoint_axes_on_the_same_row`
(the latter proves the kernel and injection axes move independently on one
row both can reach) -- plus
`test_every_bump_call_site_belongs_to_a_classified_surface`, which scans the
source for `record_memory_access` call sites so a bump wired into an
unclassified module fails too.

Two surfaces read the counters back, and only two of the three pairs:
`gaia memory show <slug>` prints the injection and deliberate pairs on their
own lines (`injection_count`/`last_injected_at`,
`deliberate_count`/`last_deliberate_at`); `gaia memory list` prints an `INJ`
and a `DELIB` column and takes `--sort=injection` or `--sort=deliberate`
(`_cmd_list` -> `gaia.store.writer.list_memory`, `_MEMORY_LIST_ORDERS`), with
`--order=asc|desc` choosing the direction -- default `desc` on a counter,
`asc` on `--sort=name` (`_MEMORY_LIST_DEFAULT_DIRECTIONS`), ties always broken
by name ascending. `--order=asc` on a counter is how "which rows are barely
used" is asked without reading the tail of an untopped list. Neither surface
ever combines two counters into one number or one sort key -- the same
never-merge rule as the write side. `kernel_count`/`last_kernel_at` are
written (see the call-site table above) but neither surface projects them --
`get_memory()` and `list_memory()`'s own `SELECT`s name only the injection
and deliberate columns -- so today the kernel pair is readable only by a
direct query against the `memory` table, never through `show` or `list`.

Reading them back is their only consumer, for the two pairs that are read
back at all. Automatic SessionStart selection -- `get-relevant`'s digest,
`--sections=`, `--types=`, and the kernel's "How the user works" block --
still ignores all three counters and orders by `updated_at` alone,
unchanged. Wiring injection SELECTION itself to any counter remains a
separate, undecided step.

## Promoted defect: the `gaia_system` initiative shape

`episode_anomalies` is the raw defect floor. It is written unrequested at
SubagentStop and it prunes itself to 90 days by cascade from `episodes`,
because most of what lands there is noise with an expiry date. A defect
that must outlive that window is **promoted** into curated memory under
`initiative='gaia_system'`, where one read
(`gaia memory get-relevant --initiative=gaia_system --json`) returns the
whole corpus for triage without reopening the sessions that produced it.

Promotion is a curation act: the orchestrator decides what earns it and
`gaia-operator` executes the write, with the user's consent. A dispatched
subagent never promotes -- `subagent_memory_write_guard` rejects
`gaia memory add` from any dispatch other than `gaia-operator`,
categorically and unapprovably. Its participation is to PROPOSE (a
`failure_report` in its contract, or a `memorialize_suggestions` entry).

### The row: existing flags only, no new schema

| Flag | Value | Why it is fixed |
|------|-------|-----------------|
| `--name` / `--type` | `feedback_<component>_<symptom>` / `feedback` | The slug prefix IS the type. `feedback` is the type for a post-mortem the system must remember, and it is one of `MEMORIALIZE_VALID_TYPES`, so a subagent can propose it. |
| `--class` | `thread` | A defect is open work, not background knowledge. Only `class=thread` is visible to the initiative digest's query. |
| `--status` | `carry_forward` | The digest selects `status IN ('carry_forward','open')`; `carry_forward` states the defect must reach the next session, overriding the `log` default that `feedback_*` would otherwise take. |
| `--initiative` | `gaia_system` | The one grouping key that makes the corpus retrievable in a single query. Normalized to lowercase_snake by the writer. |
| scope | `--workspace=<ws>` | The corpus is local to an installation and never travels. Do NOT pass `--project`: with a git project anchored, `initiative` is derived from the repo basename, and the corpus must not split into per-repo keys. |
| `--description` | one line, the symptom in the reader's words | Listings and the digest render `description`, not the body. |

Nothing here is new schema. `initiative`, `class`, and `status` are columns
that already exist and flags `gaia memory add` already accepts. A dedicated
`type=defect` was rejected deliberately: it would require a migration and a
change to the `memory.type` CHECK, while `initiative='gaia_system'` costs
neither.

### The body: four named fields, fixed order

The body is structure, not prose. Exactly four second-level headings, spelled
and ordered as below:

```markdown
## Symptom
What was observed, stated as the observable fact -- not the diagnosis.

## Component
The owner of the defect, as `file + symbol`, a CLI verb, or a hook module.
One target, named precisely enough to open.

## Evidence
The observed proof, verbatim: command output, a query result, an anomaly id.
One bullet per item; do not paraphrase what was captured.

## Reproduction
The exact command or sequence that makes the defect appear again, with
absolute paths.
```

Four rules make that a shape rather than a suggestion:

- All four headings are present, spelled exactly, in that order.
- A field that is genuinely unknown carries the single word `unknown`. An
  omitted heading is a malformed defect, not an empty one -- a reader
  splitting the body cannot tell absence from oversight.
- No other `##` heading appears in the body. Deeper structure inside a
  field uses bullets or `###`.
- Recovery is therefore a split of the body on `^## `, yielding the four
  fields in a fixed order. That is what "recoverable without reopening the
  session" means, and it is why the headings are not negotiable per author.

### The promotion path

Two sources feed the corpus, and both converge on the SAME row because the
flags above are constants of the path, not choices of the promoter.

**Source A -- a raw floor anomaly.** An `episode_anomalies` row of type
`agent_reported_defect` (`hooks/modules/agents/defect_capture.py`,
`DEFECT_ANOMALY_TYPE`), whose payload carries `agent`, `attempted`,
`symptom`, `component`, `evidence` and a severity. Map it:

| Body field | Comes from |
|------------|-----------|
| `## Symptom` | `attempted` + `symptom`, as "attempted X; Y broke" |
| `## Component` | `component`, which is optional in the report -- `unknown` when null |
| `## Evidence` | `evidence[]`, one bullet per item, verbatim |
| `## Reproduction` | **Not carried by the raw floor.** Supplied at promotion. |

That last row is why promotion is a deliberate act and not a copy job: the
capture channel records what an agent observed, and nobody can re-run a
defect from that alone. Adding the reproduction is the work promotion does.
The anomaly's `severity` does not become a body field -- it informs whether
the defect is worth promoting at all.

**Source B -- a subagent proposal.** A `memorialize_suggestions` entry with
`type: "feedback"` whose `body` already carries the four headings. The
suggestion block carries `slug`, `type`, `class`, `description`, `body` and
`rationale` -- it carries neither `status` nor `initiative`
(`MEMORIALIZE_VALID_TYPES` and the copied keys in
`hooks/modules/agents/response_contract.py`). Those two are supplied by the
writer from the table above, which is exactly what keeps two promotions from
two different agents landing in the same shape.

The write, identical for both sources:

```bash
gaia memory add \
  --name="feedback_<component>_<symptom>" \
  --type=feedback \
  --class=thread \
  --status=carry_forward \
  --initiative=gaia_system \
  --workspace="<ws>" \
  --description="<one-line symptom>" \
  --body-file=-
```

Read back with `gaia memory show <slug> --json` (single row) or
`gaia memory get-relevant --initiative=gaia_system --json` (the corpus).

### Promotion is one-way

The two floors have opposite retention. `episode_anomalies` is pruned at 90
days by cascade from `episodes`; curated memory has no retention at all --
only a soft delete, with `trg_memory_history` archiving every prior version.
So a promoted defect is permanent by construction, and there is no demotion:
nothing carries a curated row back down to a floor that expires, and deleting
it is discouraged by the same convention that governs every other row.

The consequences to hold before promoting:

- **Promote what deserves permanence**, not everything that failed. The raw
  floor already keeps the noise for 90 days and `gaia metrics` already
  aggregates it; promotion is for the defect a future session must be able
  to pick up as work.
- **A resolved defect ends by `status`, never by deletion** --
  `gaia memory reclassify <slug> --status=closed` (no longer relevant) or
  `--status=graduated` (fixed, or promoted to an anchor). It leaves the
  digest and stays in the corpus.
- **Re-promoting the same defect UPSERTs** -- `add` is keyed by
  `(name, workspace)`, so reusing the slug overwrites in place rather than
  splitting one defect across two rows. Search before promoting.

## Curate flow

Run periodically (or when `gaia memory stats` shows conflicts > 0,
or when memory size feels unwieldy). `SKILL.md` keeps the verb-selection
decision (the operation vocabulary); this section holds the mechanics of
each curate operation.

### Move a note through its lifecycle

`gaia memory reclassify` is the canonical way to change `class` or
`status` without touching the body:

```bash
# Mark a thread to carry into the next session
gaia memory reclassify thread_handoff --class=thread --status=carry_forward

# Promote a graduated thread into a stable anchor
gaia memory reclassify thread_promoted --class=anchor --status=null

# Just close a thread
gaia memory reclassify thread_old --status=closed
```

When `class` moves away from `thread` without `--status`, the writer
auto-clears `status` so the row remains consistent. Use
`--status=null` only when you want the clear to be explicit in the
audit trail.

### Connect notes (Zettelkasten edges)

`gaia memory link` creates or deletes a row in `memory_links`:

```bash
# Two anchors that inform each other
gaia memory link atom_node_20 anchor_routing --kind=relates_to

# Retire an obsolete decision without losing the history
gaia memory link decision_old decision_new --kind=supersedes

# Drop a link that turned out wrong
gaia memory link a b --kind=relates_to --delete
```

Both endpoints must exist as curated rows. The command is
idempotent: re-running the same link is a no-op. The four kinds map
to the four reasons one note refers to another -- a generic
relationship (`relates_to`), an obsolescence (`supersedes`), a
derivation (`derived_from`), and a thread-to-anchor promotion path
(`graduated_to`).

### Deduplication

Trigger this only when a search (or `gaia memory conflicts`) reveals an
actual overlap -- it is not a step every save runs. Consolidation is
**additive**: you merge forward and link, you do not erase.

1. `gaia memory search "<topic>" --scope=memory` to find overlaps.
2. Read both bodies; identify the broader scope.
3. UPSERT the merged content into the broader slug.
4. Link the narrower to the broader with `--kind=supersedes`. The
   `supersedes` link retires the obsolete row while keeping its
   reasoning reachable -- that is the additive path. Delete the
   narrower slug only when it was always pure noise with no history
   worth preserving; superseding is the default, deletion the
   exception.

For periodic sweeps rather than per-save checks, run
`gaia memory conflicts` to surface overlapping pairs across the whole
set at once, then resolve each as above.

### Conflict resolution

`gaia memory conflicts` flags pairs whose bodies overlap above a
Jaccard threshold. For each pair:

- If they are duplicates, merge and supersede (see Deduplication).
- If they contradict, the newer one usually wins -- but ask the user
  before overwriting a `decision_*` row.

### Pruning stale entries

1. Identify rows referencing retired projects, deprecated tooling,
   or resolved decisions whose outcome no longer needs justification.
2. **Prefer `reclassify` over deletion.** `reclassify --class=log` or
   `reclassify --status=closed|graduated` retires a note while keeping
   it — memory is meant to be aggregated and reclassified, not deleted.
   Deletion is discouraged by convention; reach for it only when a row
   was always pure noise.
3. If you must delete: confirm with the user first, then
   `gaia memory delete <slug> --yes` (soft-delete/tombstone by default —
   recoverable; it stays T3). `--hard` physically destroys the row and
   its history and is strongly discouraged.

### Splitting overgrown bodies

When a body exceeds ~100 lines, split into focused subtopics:

1. Identify natural section boundaries.
2. `gaia memory add` one row per subtopic with a tightly scoped slug.
3. Link the new rows back to the original with `--kind=derived_from`.
4. Replace the original body with a brief index, or
   `--kind=supersedes` it from a new umbrella note.

### Verb detail: `append` and `edit` worked examples

`SKILL.md` carries the curation judgment — which role an item serves, when it
earns curated attention, and how it exits ("When curated memory loses it").
These are the worked examples and the history guarantee behind the verbs it
names.

**Add to a note -- `append` (the primary additive verb, non-mutative):**

```bash
gaia memory append <slug> --body="One more finding: ..."

# Markdown-rich or multi-line text uses an explicit body file:
gaia memory append <slug> --body-file=/tmp/more.md
```

`append` concatenates onto the current body (separator `\n\n`) and never
overwrites. It is classified **non-mutative (T0)** — appending only grows
the record, so it needs no approval. This is what you want for a
carry-forward thread or running log that accumulates.

**Correct a note -- `edit` (supersede-with-history):**

```bash
# Fix a body that is WRONG (overwrites the live column):
gaia memory edit --name=<slug> --field=body --body-file=/tmp/corrected.md
gaia memory edit --name=<slug> --field=<description|body> --content="..."
```

`edit` is the **correction** verb: use it when the existing content is
wrong and must be replaced. It is classified **T3 (needs approval)**
because it changes what future reads see. It is non-destructive under the
hood — the `--append` flag still exists and delegates to the same path as
`append` — but for adding text, reach for `append` first. Use
`reclassify` to change `class`/`status`; use `link` to wire the graph.

**Persist a meaningful milestone -- `checkpoint` (atomic, non-mutative):**

```bash
gaia memory checkpoint --file /tmp/session_checkpoint.json \
  --project=<project> --workspace=<ws>
```

`checkpoint` writes a confirmed milestone as ONE transaction: the
`resumen` object becomes the record anchor (`class=anchor`), each
`pendientes[]` entry becomes a `class=thread status=carry_forward` row
(inheriting the record's `type`), and each thread is linked
`derived_from` the record. It is **all-or-nothing** -- an invalid or
malformed payload writes *zero* rows -- and **idempotent** (the
fecha-stamped `project_session_<date>_<topic>` slug makes re-runs UPSERT
rather than duplicate). Payload shape:

```json
{
  "resumen":   {"name", "type", "description", "body"},
  "pendientes": [{"name", "description", "body"}, ...]
}
```

It reuses the same scope contract as `add` (structured `missing_scope` /
`project_unresolved` / `project_workspace_mismatch` / `project_no_identity`
errors -- see the table above) and the same subagent-dispatch gate (only
the orchestrator/operator pair may write). If the record body reads like
it hides a pending (`TODO`, `pendiente`, `next step`, `- [ ]`) while
`pendientes` is empty, it emits a non-blocking **warning** (exit 0). This
is the mechanism `session-reflection` uses when a closing arc passes the
milestone test -- one command instead of an `add` per row plus a `link` per
thread. An ordinary session close does not require a checkpoint.

**Ordinary updates are audited.** Any UPDATE to `name`, `body`, `description`,
`type`, `class`, `status`, `workspace`, `project_ref`, `initiative`, or
`deleted_at` fires `trg_memory_history`, which archives the tracked before/after
values. This covers `append`, `edit`, lifecycle/scope transitions, and `add`'s
UPSERT. It is a recovery aid, not an immortality guarantee: explicit hard
deletion and workspace cascade can remove the row and its history.

## Knowledge graph (future)

`memory_links` is the foundation for treating Gaia memory as a
navigable graph. Today, links power supersedes / derived_from /
graduated_to traversals at query time and keep retired notes
reachable for audit. A future brief will export the graph to
Obsidian (or similar) so the network of anchors, threads, and
decisions can be navigated visually outside the CLI.
