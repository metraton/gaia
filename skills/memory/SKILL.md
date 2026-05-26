---
name: memory
description: Use when reading, searching, saving, or curating Gaia memory — atoms, decisions, negative space, project/user/feedback notes — or when triaging the SessionStart Workspace Memory block
metadata:
  user-invocable: false
  type: technique
---

# Memory

Gaia's curated memory lives in the `memory` table of the substrate DB
(`~/.gaia/gaia.db`), with a parallel `memory_links` table that lets
notes reference each other like a Zettelkasten. This skill covers the
full flow: read what is already in your context, query when more is
needed, propose a save when a discovery is closed, and curate when
the set drifts.

## Mental model

Two ortogonal axes describe every memory row:

| Axis | Values | What it captures |
|------|--------|------------------|
| `class` (rol) | `anchor` / `thread` / `log` | The role the note plays in the session. Anchors are stable knowledge that survives across sessions; threads are work-in-progress that needs handoff; logs are append-only history kept for traceability. |
| `type` (forma) | `atom` / `decision` / `negative` / `project` / `user` / `feedback` | The discipline of the body -- how the row is shaped and validated. Internal taxonomy: never surface `atom`/`decision`/`negative` to the user as the way to think about memory. |

`status` only applies when `class=thread`. Its values form the thread
lifecycle: `open` (in progress) -> `carry_forward` (must reach the
next session) -> `graduated` (promoted to an anchor or otherwise
resolved) -> `closed` (no longer relevant). The writer auto-clears
`status` when `class` moves away from `thread` unless `--status` is
explicit; pass `--status=null` to clear deliberately.

`memory_links` connects two rows by `kind` ∈ `{relates_to,
supersedes, derived_from, graduated_to}`. Links are how a network of
notes forms over time; supersedes is how an obsolete note is retired
without losing its history.

### User-facing labels

The taxonomy above is internal vocabulary. When presenting memory to
the user, translate to plain labels that describe the role:

| Internal | Surface as |
|----------|------------|
| `class=thread`, `status=open` | "Hilos abiertos" |
| `class=thread`, `status=carry_forward` | "Para esta sesión" |
| `class=anchor` (user/identity body) | "Sobre ti" |
| `class=anchor` (project/system body) | "Lo que sabemos del proyecto" |
| `class=log` | "Bitácora" |

The user sees what the note *is for*, not which bucket it lives in.

## Who writes

Only the orchestrator and `gaia-operator` mutate memory directly via
the CLI. Subagents dispatched into a task do **not** call
`gaia memory add` -- the writer hook rejects mutation from a dispatch
context. Subagents instead propose new memory by emitting a
`memorialize_suggestions` block in their `json:contract`; the
orchestrator presents the proposal to the user and persists on
confirmation.

`memory_fts` is the FTS5 mirror; triggers keep it in sync after any
write.

## Read flow

### Trust the injected block first

At SessionStart the orchestrator receives a `## Workspace Memory`
block listing the top notes for the current workspace
(`gaia memory get-relevant`, bounded). When the user's question can
be answered from what is already in your context, do **not** re-query
-- the injected block is already the relevance-ranked top, with
`carry_forward` threads sorted first.

### Query for depth

Reach for the CLI when:

- The user names a topic you do not see in the injected block.
- The injected block has a stub but the user wants the full body.
- You are about to write a new memory and need to check for duplicates.

| Need | Command |
|------|---------|
| FTS5 search (curated + episodes) | `gaia memory search "<query>" [--limit N]` |
| Curated only | `gaia memory search "<query>" --scope=memory` |
| Episodes only | `gaia memory search "<query>" --scope=episodes` |
| Show one curated row | `gaia memory show <slug>` |
| Show one episode | `gaia memory episode-show <episode_id>` |
| List curated rows (filter by type) | `gaia memory list --type=atom` |
| List by workspace | `gaia memory list --workspace=<ws>` |
| Health stats | `gaia memory stats` |
| Pairwise contradiction scan | `gaia memory conflicts [--threshold F]` |
| Get the injection block (debug) | `gaia memory get-relevant --workspace=<ws>` |

Always pass `--json` when output feeds a follow-up step.

## Write flow (curated)

Memory growth requires deliberation. Not every observation is worth
persisting -- a bounded curation forces prioritisation, and an
overstuffed memory loses signal to noise. Propose a save **only when
one of these triggers fires**:

| Trigger | Type | Class default | Slug shape |
|---------|------|---------------|------------|
| A decision was closed (with rationale) | `decision` | `anchor` | `decision_<topic>` |
| A reusable fact was discovered | `atom` | `anchor` | `atom_<topic>` |
| A path was tried, abandoned, and should not recur | `negative` | `anchor` | `negative_<topic>` |
| Cross-cutting repo / system knowledge | `project` | `anchor` | `project_<topic>` |
| User preference or identity update | `user` | `anchor` | `user_<topic>` |
| Post-mortem / correction the system must remember | `feedback` | `log` | `feedback_<topic>` |
| Work-in-progress that must survive the session | any | `thread` (`--status=carry_forward`) | `<type>_<topic>` |

The CLI enforces `^<type>_[a-z0-9_]+$` for `atom`, `decision`, and
`negative` slugs; the legacy types (`project`, `user`, `feedback`)
keep historical naming freedom.

The flow:

1. Propose to the user: "Discovery worth saving as `atom_<slug>` -- ok?"
2. On confirmation, persist:

```bash
# Primary path -- use --body-file for any body longer than one line or
# containing special characters, code blocks, or template syntax:
gaia memory add \
  --name="<slug matching the pattern>" \
  --type="atom|decision|negative|project|user|feedback" \
  --class="anchor|thread|log" \
  [--status="open|carry_forward|graduated|closed"] \
  --body-file=/tmp/body.md \
  --description="<one-line summary>" \
  --workspace="<ws>"

# Heredoc to stdin (no temp file needed):
cat <<'EOF' | gaia memory add --name="atom_x" --type=atom \
  --description="..." --body-file=-
The body content here, with any markdown, code blocks, or
special characters, safely passed through stdin.
EOF

# Inline --body only for single-line bodies without special characters:
gaia memory add --name="atom_x" --type=atom \
  --body="Single-line plain text fact." --description="..."
```

`add` is UPSERT semantics: same `--name` + `--workspace` overwrites
in place. There is no separate `update` command.

### Body shape per type

- **`atom_*`**: one fact, 1-3 sentences. If it sprawls, it is two atoms.
- **`decision_*`**: state the decision in the first line, then 2-5
  lines of rationale -- "considered X, chose Y because". Include the
  date.
- **`negative_*`**: state the closed path in the first line, then why
  it failed and what was used instead. The point is *do not retry this*.
- **`project_*` / `user_*` / `feedback_*`**: free-form markdown.
  Treat as anchors unless the content is a running thread.

## Curate flow

Run periodically (or when `gaia memory stats` shows conflicts > 0,
or when memory size feels unwieldy).

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

1. `gaia memory search "<topic>" --scope=memory` to find overlaps.
2. Read both bodies; identify the broader scope.
3. UPSERT the merged content into the broader slug.
4. Link the narrower to the broader with `--kind=supersedes`, then
   `gaia memory delete <narrower-slug> --yes` if it adds no value to
   keep. Prefer `supersedes` over delete when the historical
   reasoning is worth preserving.

### Conflict resolution

`gaia memory conflicts` flags pairs whose bodies overlap above a
Jaccard threshold. For each pair:

- If they are duplicates, merge and supersede (see Deduplication).
- If they contradict, the newer one usually wins -- but ask the user
  before overwriting a `decision_*` row.

### Pruning stale entries

1. Identify rows referencing retired projects, deprecated tooling,
   or resolved decisions whose outcome no longer needs justification.
2. Prefer `reclassify --class=log` over deletion when the history
   has audit value; delete only when the row was always noise.
3. Confirm with the user before removing.
4. `gaia memory delete <slug> --yes`.

### Splitting overgrown bodies

When a body exceeds ~100 lines, split into focused subtopics:

1. Identify natural section boundaries.
2. `gaia memory add` one row per subtopic with a tightly scoped slug.
3. Link the new rows back to the original with `--kind=derived_from`.
4. Replace the original body with a brief index, or
   `--kind=supersedes` it from a new umbrella note.

### Editing a single field

```bash
# Preferred for multi-line or markdown-rich bodies:
gaia memory edit --name=<slug> --field=body --body-file=/tmp/new_body.md

# Stdin variant:
cat new_body.md | gaia memory edit --name=<slug> --field=body --body-file=-

# Inline for short plain-text changes:
gaia memory edit --name=<slug> --field=<description|body> --content="..."
```

Patches one column. Pass `--append` to append (separator `\n\n`)
rather than overwrite. Use `edit` for corrections; use `reclassify`
to change `class`/`status`; use `link` to wire the graph.

## Carry-forward / handoff

To make a note land first in the next session's injected block:

```bash
gaia memory reclassify <slug> --class=thread --status=carry_forward
```

The SessionStart injector sorts threads in `carry_forward` ahead of
everything else when building the Workspace Memory block, so the next
orchestrator instance sees it immediately. When the work concludes,
move the same row to `graduated` (or `closed`), or promote it to
`class=anchor` if it became stable knowledge.

## Knowledge graph (future)

`memory_links` is the foundation for treating Gaia memory as a
navigable graph. Today, links power supersedes / derived_from /
graduated_to traversals at query time and keep retired notes
reachable for audit. A future brief will export the graph to
Obsidian (or similar) so the network of anchors, threads, and
decisions can be navigated visually outside the CLI.

## Rules

| Rule | Reason |
|------|--------|
| One topic per row | The slug names a single concern; split if a row outgrows its scope. |
| `description` is required for new rows | Listings, the injection block, and search results lean on description, not body. |
| Rich bodies require `--description` | The CLI enforces this: when the body contains code blocks, headers, or 3+ blank lines, `--description` is mandatory. SessionStart falls back to `body[:60]` when description is absent, which destroys code-block semantics. |
| Confirm before pruning | Report what will be removed and get user confirmation. |
| Read before overwriting | `add` is UPSERT -- the prior body is gone. Always `show` first when re-using a slug. |
| Use UPSERT, not delete-then-insert | Preserves `origin_session_id` provenance and avoids FTS5 churn. |
| Curated slugs must match `^<type>_[a-z0-9_]+$` | The CLI rejects mismatched slugs; the convention is what makes the taxonomy queryable. |
| Subagents propose, the orchestrator persists | Direct `gaia memory add` from a dispatch is rejected -- use `memorialize_suggestions` instead. |

## Anti-patterns

- **Surfacing `atom`/`decision`/`negative` to the user as the way to
  think about memory** -- `type` is internal discipline of the body;
  the user reasons in terms of "for this session", "about me", "open
  threads", "log". Present roles, not buckets.
- **Writing memory from inside a subagent dispatch** -- the writer
  enforcement layer rejects it. Propose via `memorialize_suggestions`
  in the contract and let the orchestrator persist.
- **Changing `class` without thinking about `status`** -- moving away
  from `class=thread` auto-clears `status`. That is usually what you
  want, but pass `--status` explicitly when the new state matters for
  audit (e.g. `--status=null` on a promotion to anchor).
- **Deleting a superseded note instead of linking** -- a
  `supersedes` link keeps the historical reasoning reachable; delete
  only when the row was always noise.
- **Saving trivial observations** -- "tested locally and it worked"
  is conversational filler, not memory. Memory is the bounded set of
  facts, decisions, and closed paths that anchor *future* sessions.
- **Slug discipline violations on curated types** -- `atom_*`,
  `decision_*`, `negative_*` slugs that do not match the pattern get
  rejected at the CLI. The pattern is not bureaucracy; it is what
  makes the taxonomy queryable as a set.
- **Treating delete as the way to update** -- UPSERT (re-running
  `add`) is the canonical update path; `reclassify` moves the row
  through its lifecycle; `link` wires the graph. Deletion is
  reserved for genuinely abandoned entries.
- **Writing `.md` files to disk as memory** -- the legacy
  `~/.claude/projects/.../memory/` directory is read-only-for-humans.
  The DB row IS the memory.
