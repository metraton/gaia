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

Memory is a **convention, not a harness**. Nothing below is enforced by
the runtime the way a security tier or a protected path is -- these are
project-specific agreements that *guide* the orchestrator and
`gaia-operator`; they oblige no one. Read "do X" as "the project agreed
to X because the alternative drifts", not "the harness will stop you".
The store enforces only two mechanical guarantees -- slug↔type matching
and the `memory_history` audit trail; everything else is discipline you
choose to keep.

## Mental model

Two orthogonal axes describe every memory row:

| Axis | Values | What it captures |
|------|--------|------------------|
| `class` | `anchor` / `thread` / `log` | The role the note plays in the session. Anchors are stable knowledge that survives across sessions; threads are work-in-progress that needs handoff; logs are append-only history kept for traceability. |
| `type` | `atom` / `decision` / `negative` / `project` / `user` / `feedback` | The discipline of the body -- how the row is shaped and validated. Internal taxonomy: never surface `atom`/`decision`/`negative` to the user as the way to think about memory. |

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
| `class=thread`, `status=open` | "Open threads" |
| `class=thread`, `status=carry_forward` | "For this session" |
| `class=anchor` (user/identity body) | "About you" |
| `class=anchor` (project/system body) | "What we know about the project" |
| `class=log` | "Log" |

The user sees what the note *is for*, not which bucket it lives in.

## Who writes

Only the orchestrator and `gaia-operator` mutate memory directly via
the CLI. Subagents dispatched into a task do **not** call
`gaia memory add` -- the writer hook rejects mutation from a dispatch
context. Subagents instead propose new memory by emitting a
`memorialize_suggestions` block in their `agent_contract_handoff`; the
orchestrator presents the proposal to the user and persists on
confirmation.

Subagents do receive memory -- a **read-only copy** of the curated
block is appended to their dispatch context, the same sections the
orchestrator saw at SessionStart. They read it to ground their work in
prior knowledge; they cannot write it back. The split is deliberate:
every curated entry enters the substrate as a named, user-confirmed
choice, never as a side effect of an investigation that happened to
touch a topic.

`memory_fts` is the FTS5 mirror; triggers keep it in sync after any
write.

## Read flow

### Trust the injected block first

At SessionStart the orchestrator receives the curated memory block
listing the top notes for the current workspace
(`gaia memory get-relevant`, bounded). It is emitted as up to three
sections: `## Memory — For this session` (carry-forward threads),
`## Memory — About you / What I know` (anchors), and
`## Memory — Open threads` (open threads). When the user's question
can be answered from what is already in your context, do **not**
re-query -- the injected block is already the relevance-ranked top,
with `carry_forward` threads sorted first.

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

This flow runs once a save is already warranted -- the orchestrator
decides *what* earns a place in memory; this skill defines *how* the
row is shaped, named, normalized, and persisted. Every write begins by
searching for what already exists, because the substrate is a set, not
an append log: a new row that duplicates an old one splits the signal
across two slugs instead of strengthening one.

### What earns a place in curated memory

Before deciding *how* to save, decide *whether* curated memory is the
right home at all. The test: **does this fact already have a home?**
Work-in-flight belongs in a brief or plan; domain state (infra
inventory, routing, cluster details) belongs in project-context;
conversational detail belongs in the transcript. Curated memory is the
home only for cross-cutting facts, closed decisions, closed dead-ends,
and threads that must survive a session *and have no other structured
home*. If it fits a brief, a plan, or a project-context table, save it
there -- a copy in memory is just a second source of truth that drifts.

Two roles split the work: the **orchestrator decides what** earns a
place (it applies this test and the value judgment), and
**`gaia-operator` executes the save** (it applies the shaping rules
below). A subagent does neither -- it proposes via
`memorialize_suggestions` and the orchestrator disposes.

### Search before you write

Before any `add`, run `gaia memory search "<topic>" --scope=memory`.
Three outcomes, three actions:

| Search reveals | Action |
|----------------|--------|
| Nothing related | Write the new row -- it is genuinely new knowledge. |
| A row on the same topic, narrower or stale | UPSERT the broader content into the existing slug; do not create a parallel slug. |
| A row that contradicts the new fact | Resolve before writing -- see Conflict resolution. Writing both leaves the substrate self-contradictory. |

Skipping the search is how the substrate accumulates near-duplicates
that each carry a fraction of the weight one consolidated row would.
The search is always-on; the deeper consolidation it can trigger is
proportional -- a trivial new fact with no overlap writes directly,
and only an overlap pulls in dedup or supersede work.

### The slug is the single source of truth for type

**The slug prefix and the type are the same thing.** When you pick a
slug, you have picked the type -- there is no independent `--type` to
choose separately. The CLI and the store both enforce this: a
`(slug, type)` pair that disagrees is always an error, never silently
reclassified. Choose the slug first; derive `--type` from its prefix.

Once a save is warranted, the *shape* of what you are saving picks the
slug prefix, and the prefix is the type. This table maps the shape of
the body to its slug -- it is the form a row takes, not a checklist of
when to write:

| Body shape | Slug prefix → type | Class default |
|------------|-------------------|---------------|
| A closed decision with its rationale | `decision_<topic>` → `--type=decision` | `anchor` |
| A stable reusable fact | `atom_<topic>` → `--type=atom` | `anchor` |
| A closed path that should not recur | `negative_<topic>` → `--type=negative` | `anchor` |
| Cross-cutting repo / system knowledge | `project_<topic>` → `--type=project` | `anchor` |
| User preference or identity | `user_<topic>` → `--type=user` | `anchor` |
| Post-mortem / correction the system must remember | `feedback_<topic>` → `--type=feedback` | `log` |
| Work-in-progress that must survive the session | `<type>_<topic>` → `--type=<type>` | `thread` (`--status=carry_forward`) |

The CLI enforces `^{type}_[a-z0-9_]+$` with type-specific matching: a
`decision_*` slug is only valid with `--type=decision`, not with
`--type=atom`. The store also rejects legacy-type calls
(`--type=project`) that use a curated slug prefix (`atom_*`,
`decision_*`, `negative_*`). Both directions of mismatch fail loudly.

The flow:

1. Match the body shape above → pick the slug `<prefix>_<topic>`.
2. Derive `--type` directly from the slug prefix (they are the same thing).
3. Search first (see Search before you write); if a related row exists,
   UPSERT into it rather than minting a new slug.
4. Persist:

```bash
# Primary path -- use --body-file for any body longer than one line or
# containing special characters, code blocks, or template syntax.
# --type is derived from the slug prefix; they must match:
gaia memory add \
  --name="decision_my_topic" \
  --type=decision \
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

### Project-scoped memory: reference `project_ref`, not the workspace

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

**CLI-enforced (N3, forward-only):** `gaia memory add` exposes `--project=<name>`,
resolved within `--workspace` to that project's `projects.project_identity`
and persisted as `memory.project_ref`:

```bash
gaia memory add --name=project_x_status --type=project \
  --project=x --workspace=me --body="..."
```

If you already hold the stable identity string (e.g. scripting across
workspaces), pass it directly with `--project-ref=<identity>` instead --
mutually exclusive with `--project`. Neither flag guesses: an unknown
project name, or a project row that has no `project_identity` yet (legacy
row, or not yet scanned), is a clear error, not a silent NULL.

Anchoring is **forward-only, by design**. Rows written before this
mechanism existed stay `project_ref IS NULL` -- the memory-row-to-project
mapping is genuinely ambiguous whenever a workspace hosts more than one
project, so no backfill can guess it. Only whoever writes a `project_*`
row -- the one who knows which project it is about -- anchors it, via
`--project` at write time. `upsert_memory()` treats `project_ref` with
coalesce-or-omit discipline: omitting it on a later update never clobbers
a previously-set anchor back to NULL.

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

### The operation vocabulary: aggregate and reclassify, don't mutate

Memory is **aggregated** and **reclassified**, not mutated. The verbs
reflect that model -- reach for them in this order:

| Verb | Use it to | Tier | Note |
|------|-----------|:----:|------|
| `append` | **ADD** text to an existing body (the primary "sum something" verb) | **T0** — no approval | Additive; concatenates with `\n\n`; prior body kept in `memory_history` |
| `add` | Create a NEW note (UPSERT by `--name`) | T0 | Distinct from `append` — `add` makes a row, `append` grows one |
| `reclassify` | Change a note's `class` / `status` (lifecycle) | T0 | Canonical way to retire a note without losing it |
| `edit` | **CORRECT** existing text (overwrite/supersede-with-history) | **T3** — needs approval | Reserved for fixing what is *wrong*; changes what reads see |
| `delete` | Remove a note | T3 | **Discouraged by convention** — prefer `reclassify --status` |

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

**Nothing is ever truly lost.** Any UPDATE to `body`, `description`,
`type`, `status`, `workspace`, or `deleted_at` fires the
`trg_memory_history` trigger, which archives the before/after into the
`memory_history` table — this covers `append`, `edit`, and `add`'s UPSERT
alike. Every version is recoverable from `memory_history` (a DB-layer
safety net, queryable directly; no `gaia memory` subcommand browses it
yet).

## Carry-forward / handoff

A carry-forward is a single thread that must reach the next session. Its
lifecycle is a state machine on the `status` column, not prose in the
body:

1. **Born** -- `gaia memory reclassify <slug> --class=thread --status=carry_forward`.
2. **Surfaced** -- the SessionStart injector sorts `carry_forward`
   threads ahead of everything else into `## Memory — For this session`,
   so the next orchestrator instance sees it first.
3. **Closed** -- when the work concludes, `reclassify --status=closed`
   (no longer relevant) or `--status=graduated`, or promote to
   `class=anchor` if it became stable knowledge.

**One thread = one note.** A carry-forward captures *one* concern with
*one* `status`. Do not pack independent items into a single body with
`## PENDIENTE` / `## CERRADO` sections: the `status` column then means
nothing (the row reads "open" while half its items are done), the
`description` becomes a manual rollup that drifts from the body, and
closing one item degenerates into editing prose the state machine never
sees. When a handoff carries N items, write N notes and group them with
`--kind=derived_from` under one umbrella note. The graph groups them;
the body never bundles them.

**Close whole, never piece by piece.** A thread ends by moving its
`status`, not by hand-editing its body to shift a line into a "done"
section. If you find yourself editing body text to record partial
progress, that note was bundling threads that should have been separate
rows -- split it first, then close each with `reclassify`.

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
| `description` is an honest summary of one thread | Keep it a one-line summary of the single concern the row holds -- never a manual rollup of multiple items. With one thread per note there is nothing to roll up and nothing to drift. |
| One thread = one note | A carry-forward or thread holds one concern with one `status`. Multi-item handoffs are N notes linked with `derived_from`, not one bundled body. Close a thread by its `status` (`reclassify --status=closed`), never by editing body prose. |
| Rich bodies require `--description` | The CLI enforces this: when the body contains code blocks, headers, or 3+ blank lines, `--description` is mandatory. SessionStart falls back to `body[:60]` when description is absent, which destroys code-block semantics. |
| Confirm before pruning | Report what will be removed and get user confirmation. |
| Add with `append`, correct with `edit` | To grow a note, use `append` (T0, additive, no approval) -- it is the primary "add something" verb. Reserve `edit` (T3) for CORRECTING content that is wrong: `add` (UPSERT) and `edit` both replace the live row in place, so `show` first to overwrite deliberately. The prior version is never lost -- `trg_memory_history` archives it into `memory_history` -- but the live row then shows only the new text. |
| Use UPSERT, not delete-then-insert | Preserves `origin_session_id` provenance and avoids FTS5 churn. |
| The slug prefix IS the type -- they are the same thing | Do not choose a slug and then pick `--type` independently. Pick the slug; derive `--type` from its prefix. A `(slug, type)` pair that disagrees is always an error -- the CLI and store reject it in both directions (curated-slug-with-wrong-type and curated-prefix-with-legacy-type). |
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
- **Bundling several threads into one note** -- a body with
  `## PENDIENTE` / `## CERRADO` sections gives many concerns one
  `status`, so neither the state machine nor the injector can tell what
  is open, and progress degenerates into editing body prose while the
  `description` rollup drifts from the body. One thread = one note; group
  multi-item handoffs with `derived_from` and close each with
  `reclassify`, never by hand-editing text.
- **Deleting a superseded note instead of linking** -- a
  `supersedes` link keeps the historical reasoning reachable; delete
  only when the row was always noise.
- **Saving trivial observations** -- "tested locally and it worked"
  is conversational filler, not memory. Memory is the bounded set of
  facts, decisions, and closed paths that anchor *future* sessions.
- **Treating `--type` and `--name` as two independent parameters** --
  they are the same thing expressed twice. Choose the slug; read
  `--type` from its prefix. Passing `--name=decision_foo --type=atom`
  or `--name=atom_foo --type=project` both produce an error from the
  CLI -- the store rejects mismatches in both directions. If you find
  yourself wondering which type to pass for a given slug, the answer
  is always the prefix of the slug.
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
