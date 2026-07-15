# Memory — Reference

Deep mechanics for the `memory` skill: project-scoped anchoring
internals, the periodic curate flow, and the knowledge-graph roadmap.
Load this on demand when `SKILL.md` points you here — day-to-day read
and write operations do not need it. The core mental model, read flow,
write flow, carry-forward discipline, rules, and anti-patterns all live
in `SKILL.md`.

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

**Project-aware retrieval:** `gaia memory get-relevant` (the SessionStart
injection path, `_cmd_get_relevant`) is no longer purely workspace-scoped.
It resolves the active project from the cwd via the same shared resolver,
and when that resolves, restricts and reorders results to prioritize rows
anchored to the active project (`project_ref = active`) while still
including unanchored workspace-wide notes (`project_ref IS NULL`) --
rows anchored to a *different* project in the same workspace drop out.
When the cwd does not resolve to a single project (e.g. at a workspace
root), retrieval falls back to the previous behavior: workspace-scoped,
all rows.

This cwd inference is **read-only and deliberate**: on retrieval a wrong
guess only re-ranks what is shown (cheap, reversible), so inferring scope
from the cwd is safe. It is **not** mirrored on the write side, where a wrong
guess would persist a bad `project_ref` -- hence `add` demands explicit
scope and refuses to infer (see "Required scope" above).

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

`SKILL.md` carries the verb-selection table (add / append / reclassify /
checkpoint / edit / delete with tiers). These are the worked examples and
the history guarantee behind it.

**Add to a note -- `append` (the primary additive verb, non-mutative):**

```bash
gaia memory append <slug> --body="One more finding: ..."

# Markdown-rich or multi-line text:
gaia memory append <slug> --body-file=/tmp/more.md
cat more.md | gaia memory append <slug> --body-file=-
```

`append` concatenates onto the current body (separator `\n\n`) and never
overwrites. It is classified **non-mutative (T0)** — appending only grows
the record, so it needs no approval. This is what you want for a
carry-forward thread or running log that accumulates.

**Correct a note -- `edit` (supersede-with-history):**

```bash
# Fix a body that is WRONG (overwrites the live column):
gaia memory edit --name=<slug> --field=body --body-file=/tmp/corrected.md
cat corrected.md | gaia memory edit --name=<slug> --field=body --body-file=-
gaia memory edit --name=<slug> --field=<description|body> --content="..."
```

`edit` is the **correction** verb: use it when the existing content is
wrong and must be replaced. It is classified **T3 (needs approval)**
because it changes what future reads see. It is non-destructive under the
hood — the `--append` flag still exists and delegates to the same path as
`append` — but for adding text, reach for `append` first. Use
`reclassify` to change `class`/`status`; use `link` to wire the graph.

**Persist a whole session close -- `checkpoint` (atomic, non-mutative):**

```bash
gaia memory checkpoint --file /tmp/session_checkpoint.json \
  --project=<project> --workspace=<ws>
cat payload.json | gaia memory checkpoint --file - --workspace=<ws>
```

`checkpoint` writes a session-close reflection as ONE transaction: the
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
is the mechanism `session-reflection` Step 6 uses to save a closing
session -- one command instead of an `add` per row plus a `link` per
thread.

**Nothing is ever truly lost.** Any UPDATE to `body`, `description`,
`type`, `status`, `workspace`, or `deleted_at` fires the
`trg_memory_history` trigger, which archives the before/after into the
`memory_history` table — this covers `append`, `edit`, and `add`'s UPSERT
alike. Every version is recoverable from `memory_history` (a DB-layer
safety net, queryable directly; no `gaia memory` subcommand browses it
yet).

## Knowledge graph (future)

`memory_links` is the foundation for treating Gaia memory as a
navigable graph. Today, links power supersedes / derived_from /
graduated_to traversals at query time and keep retired notes
reachable for audit. A future brief will export the graph to
Obsidian (or similar) so the network of anchors, threads, and
decisions can be navigated visually outside the CLI.
