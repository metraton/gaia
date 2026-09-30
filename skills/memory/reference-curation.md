# Memory — Curation mechanics

The exact forms behind the curation verbs: lifecycle moves, links,
deduplication, pruning, splitting, the `append` / `add --replace` / `checkpoint` worked
examples, history coverage, and graph behavior. Load this when executing or
auditing a curate operation. The verb-selection judgment and the exception
boundary are in `SKILL.md`; the write path, scope errors and warnings are in
`reference.md`.

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

# Retire an obsolete decision without losing the history: NEW first, OLD second
gaia memory link decision_new decision_old --kind=supersedes

# Drop a link that turned out wrong
gaia memory link a b --kind=relates_to --delete
```

Both endpoints must exist as curated rows. The command is
idempotent: re-running the same link is a no-op. The four kinds map
to the four reasons one note refers to another -- a generic
relationship (`relates_to`), an obsolescence (`supersedes`), a
derivation (`derived_from`), and a thread-to-anchor promotion path
(`graduated_to`).

`supersedes` has one direction: src is the row that holds now, dst the one
it replaces. Every injection drops the dst
(`gaia/store/reader.py::not_superseded`), and `gaia memory story` labels it
`superseded` from the src and the src `successor` from the dst
(`gaia/store/reader.py::_role_for_edge`). On success `link` prints
`<src> reemplaza a <dst>`, and warns `supersedes_reversed` when the dst is
the newer row by birth (`created_at`, or `updated_at` before v50) -- a
warning, since a corrected old row can legitimately be newer.

Each end is found in `--workspace`, in the user and host scopes, or in the
workspace holding it as a project row (`bin/cli/memory.py::_workspace_holding`,
`ambiguous_slug` when two other workspaces do), so the two can have different
owners: a user row in `_gaia_user` supersedes its predecessor still under a
project workspace. The link is stored under the src's workspace with the
dst's in `memory_links.dst_workspace` (v61; NULL when both share one), which
`gaia/store/writer.py::insert_memory_link` validates like the src, and
`gaia workspace retire` carries along when it moves the dst row.

### Deduplication

Trigger this only when a search (or `gaia memory conflicts`) reveals an
actual overlap -- it is not a step every save runs. Consolidation is
**additive**: you merge forward and link, you do not erase.

1. `gaia memory search "<topic>" --scope=memory` to find overlaps.
2. Read both bodies; identify the broader scope.
3. UPSERT the merged content into the broader slug.
4. Link the broader to the narrower it absorbed:
   `gaia memory link <broader> <narrower> --kind=supersedes`. The
   `supersedes` link retires the obsolete row while keeping its
   reasoning reachable -- that is the additive path. Delete the
   narrower slug only when it was always pure noise with no history
   worth preserving; superseding is the default, deletion the
   exception.

For periodic sweeps rather than per-save checks, run
`gaia memory conflicts` to surface overlapping pairs across the whole
set at once, then resolve each as above.

### Conflict resolution

`gaia memory conflicts` (`tools/memory/conflict_detector.py::detect_conflicts`)
lists candidate pairs from the curated memory in `gaia.db`: live rows no
`supersedes` link retired, paired only inside one owner -- the user's rows
across every workspace, or the rows of one initiative key
(`gaia/store/reader.py::live_owned_memory_rows`) -- whose word stems of
name, description and body overlap at or above `--threshold`
(`DEFAULT_THRESHOLD`, Jaccard; stems are the first `_STEM_LENGTH`
characters, so English and Spanish inflections meet). Every class is
included, so a forgotten `log` row that still contradicts a standing anchor
shows up. `gaia memory stats` counts the same set. The score says the pair
shares wording, never that it disagrees. For each pair, read both bodies:

- If they are duplicates, merge and supersede (see Deduplication).
- If they contradict, write or pick the row that stands and link it
  `--kind=supersedes` to the one it replaces; the newer one usually wins,
  but ask the user before contradicting a `decision_*` row.
- If they only share vocabulary, leave both.

The detector does not look for a preference that clashes with a system
rule: that is the curator's reading, taught in `SKILL.md`.

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

### Verb detail: `append`, supersede and `add --replace` worked examples

`SKILL.md` carries the curation judgment — which role an item serves, when it
earns curated attention, and how it exits ("When curated memory loses it").
These are the worked examples and the history guarantee behind the verbs it
names.

**Grow a log or a live thread -- `append` (non-mutative):**

```bash
gaia memory append <slug> --body="One more finding: ..."

# Markdown-rich or multi-line text uses an explicit body file:
gaia memory append <slug> --body-file=/tmp/more.md
```

`append` concatenates onto the current body (separator `\n\n`) and never
overwrites. It is classified **non-mutative (T0)** — appending only grows
the record, so it needs no approval. This is what you want for a
carry-forward thread or running log that accumulates. Knowledge that
changed is not appended to: it is a new row that supersedes the old one.

**A changed agreement -- a new row that supersedes:**

```bash
gaia memory add --name=<new_slug> --type=<type> --project=<p> --body-file=/tmp/new.md
gaia memory link <new_slug> <old_slug> --kind=supersedes
```

Memory is append-only: nobody edits a row to record that an agreement
changed. `gaia memory edit` is retired and exits naming this path.

**Correct an error -- `add --replace` (T3):**

```bash
gaia memory add --name=<slug> --type=<type> --project=<p> --body-file=/tmp/corrected.md --replace
```

The one in-place rewrite left, for a row that is wrong rather than
outdated: without `--replace` an `add` over an existing name is refused
with `name_exists`. It needs a signature for every caller because it
changes what future reads see; the prior value stays in `memory_history`.
It also corrects `--project`/`--project-ref` and `--audience`. Use
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
errors -- see the error table in `reference.md`) and the same subagent-dispatch gate (only
the orchestrator/operator pair may write). If the record body reads like
it hides a pending (`TODO`, `pendiente`, `next step`, `- [ ]`) while
`pendientes` is empty, it emits a non-blocking **warning** (exit 0). This
is the mechanism `session-reflection` uses when a closing arc passes the
milestone test -- one command instead of an `add` per row plus a `link` per
thread. An ordinary session close does not require a checkpoint.

**Ordinary updates are audited.** Any UPDATE to `name`, `body`, `description`,
`type`, `class`, `status`, `workspace`, `project_ref`, `initiative`, or
`deleted_at` fires `trg_memory_history`, which archives the tracked before/after
values. This covers `append`, `add --replace`, and lifecycle/scope
transitions. It is a recovery aid, not an immortality guarantee: explicit hard
deletion and workspace cascade can remove the row and its history.

## Knowledge graph (future)

`memory_links` is the foundation for treating Gaia memory as a
navigable graph. Today, links power supersedes / derived_from /
graduated_to traversals at query time and keep retired notes
reachable for audit. A future brief will export the graph to
Obsidian (or similar) so the network of anchors, threads, and
decisions can be navigated visually outside the CLI.
