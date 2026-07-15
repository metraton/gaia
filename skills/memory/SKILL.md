---
name: memory
description: Use when reading, searching, saving, or curating Gaia memory — atoms, decisions, negative space, project/user/feedback notes — or when triaging the SessionStart Workspace Memory block
---

# Memory

Gaia's curated memory lives in the `memory` table of the substrate DB
(`~/.gaia/gaia.db`), with a parallel `memory_links` table that lets
notes reference each other like a Zettelkasten. This skill covers the
full flow: read what is already in your context, query when more is
needed, propose a save when a discovery is closed, and curate when
the set drifts. Deep mechanics — project-scoped anchoring internals,
the periodic curate operations, and the knowledge-graph roadmap — live
in `reference.md`, loaded on demand.

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
| `class` | `anchor` / `thread` / `log` | The role the note plays in the session. `anchor` = stable knowledge available in any session, surfaced in `## Memory — About you / What I know` -- it is background knowledge, not a pending-item mechanism. `thread` = actionable work-in-progress that needs handoff; a `thread` with `status=carry_forward` is the one class/status pair that resurfaces at the top of the *next* session's opening block, `## Memory — For this session`. `log` = append-only history kept for traceability, never re-injected. |
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
| Show one curated row (incl. class + status) | `gaia memory show <slug>` |
| Show a row's in/out links | `gaia memory show <slug> --links` |
| Show a row's version history | `gaia memory show <slug> --history` |
| Narrate a row's lineage timeline | `gaia memory story <slug> [--max-depth N]` |
| Show one episode | `gaia memory episode-show <episode_id>` |
| List curated rows (filter by type) | `gaia memory list --type=atom` |
| List by workspace | `gaia memory list --workspace=<ws>` |
| Health stats | `gaia memory stats` |
| Pairwise contradiction scan | `gaia memory conflicts [--threshold F]` |
| Get the injection block (debug) | `gaia memory get-relevant --workspace=<ws>` |

`show --json` now emits `class` and `status`. `story` resolves the
`memory_links` lineage around a slug (BFS both directions, cycle-safe,
depth-bounded) and fuses every node's `memory_history` into one
chronological timeline -- approximate birth (the `memory` table has no
`created_at`, so birth is the first observable trace and is flagged as
such), body appends/edits, status transitions, and link creation --
closing with a final-state table. `--json` yields
`{nodes, edges, timeline, final_states}`.

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
| A row that contradicts the new fact | Resolve before writing -- see Conflict resolution in `reference.md`. Writing both leaves the substrate self-contradictory. |

Skipping the search is how the substrate accumulates near-duplicates
that each carry a fraction of the weight one consolidated row would.
The search is always-on; the deeper consolidation it can trigger is
proportional -- a trivial new fact with no overlap writes directly,
and only an overlap pulls in dedup or supersede work.

### Decide from intention first, then apply the type defaults

Before consulting the defaults table below, ask one question: **does this
item need to appear at the top of the NEXT session's opening block?** That
answer -- not the row's type -- is the primary criterion for `class`/`status`:

- **If YES** -- this is a pending for the next session, it must land in
  `## Memory — For this session` -- the row MUST be `class=thread
  --status=carry_forward`, regardless of what type/slug prefix it uses.
  `atom_*`, `decision_*`, `project_*`, `user_*` all support this: the slug
  prefix still picks the *content shape*, but the class/status override
  expresses the *intent to carry forward*, not the type default.
- **If NO** -- it is durable background knowledge, not a pending item -- the
  type's default `class=anchor` is correct, and the row is expected to
  surface only in `## Memory — About you / What I know`.

Getting this backwards is the most common curated-memory mistake: a
`class=anchor` row -- which is the DEFAULT for `project_*`, `user_*`,
`atom_*`, and `decision_*` in the table below -- never appears in
`## Memory — For this session`, no matter how urgent its content. It
surfaces only in `About you / What I know`, a section that is capped at a
small quota and is trimmed BEFORE `carry_forward` under char-budget
pressure (see "Trim order and quotas" under Carry-forward / handoff below).
A "pendiente para la próxima sesión" saved as an anchor does not fail
loudly -- it is simply never presented as a pending, and can be the first
thing dropped when the budget is tight.

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

| Body shape | Slug prefix → type | Class default | Resurfaces in "For this session"? |
|------------|-------------------|---------------|:---:|
| A closed decision with its rationale | `decision_<topic>` → `--type=decision` | `anchor` | No -- surfaces in "About you / What I know" |
| A stable reusable fact | `atom_<topic>` → `--type=atom` | `anchor` | No -- surfaces in "About you / What I know" |
| A closed path that should not recur | `negative_<topic>` → `--type=negative` | `anchor` | No -- surfaces in "About you / What I know" |
| Cross-cutting repo / system knowledge | `project_<topic>` → `--type=project` | `anchor` | No -- surfaces in "About you / What I know" |
| User preference or identity | `user_<topic>` → `--type=user` | `anchor` | No -- surfaces in "About you / What I know" |
| Post-mortem / correction the system must remember | `feedback_<topic>` → `--type=feedback` | `log` | No -- `log` is never injected |
| Work-in-progress that must survive the session | `<type>_<topic>` → `--type=<type>` | `thread` (`--status=carry_forward`) | **Yes** -- the only row that lands in "For this session" |

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

### Project-scoped memory (summary)

`gaia memory add` requires **at least one** explicit scope flag --
`--project` (preferred) or `--workspace` -- and never infers scope from
the cwd on the write side. `--project=<name>` anchors the row to the
project's durable `project_identity` in `memory.project_ref`, so the
note stays correctly attributed even after the project moves workspace;
`--workspace=<ws>` alone is the explicit degraded lane (`project_ref`
NULL, exit 0) for a note about the workspace as a whole. Anchoring is
forward-only. For the full mechanics -- the structured error codes
(`missing_scope`, `project_unresolved`, `project_workspace_mismatch`,
`project_no_identity`), the coalesce-or-omit `upsert_memory()`
discipline, and the read-side project-aware retrieval in
`get-relevant` -- see `reference.md`.

## Curate flow

Run periodically (or when `gaia memory stats` shows conflicts > 0, or
when memory size feels unwieldy). The mechanics of each operation --
`reclassify` lifecycle moves, `link` edges, deduplication, conflict
resolution, pruning, and splitting overgrown bodies -- live in
`reference.md`; reach for them when a sweep is warranted. What stays
here is the decision that governs all of them: which verb to reach for.

### The operation vocabulary: aggregate and reclassify, don't mutate

Memory is **aggregated** and **reclassified**, not mutated. The verbs
reflect that model -- reach for them in this order:

| Verb | Use it to | Tier | Note |
|------|-----------|:----:|------|
| `append` | **ADD** text to an existing body (the primary "sum something" verb) | **T0** — no approval | Additive; concatenates with `\n\n`; prior body kept in `memory_history` |
| `add` | Create a NEW note (UPSERT by `--name`) | T0 | Distinct from `append` — `add` makes a row, `append` grows one |
| `reclassify` | Change a note's `class` / `status` (lifecycle) | T0 | Canonical way to retire a note without losing it |
| `checkpoint` | Persist a whole session-close reflection **atomically** (record anchor + N carry-forward threads + N `derived_from` links) | T0 | One transaction, all-or-nothing; `--file <payload.json\|->`. The session-close save path — see `session-reflection` Step 6 |
| `edit` | **CORRECT** existing text (overwrite/supersede-with-history) | **T3** — needs approval | Reserved for fixing what is *wrong*; changes what reads see |
| `delete` | Remove a note | T3 | **Discouraged by convention** — prefer `reclassify --status` |

`append` (T0) is the primary additive verb -- it grows a body without
approval, which is what a carry-forward thread or running log wants.
`add` (UPSERT) creates or overwrites a row in place. `edit` (T3) is the
correction verb, reserved for fixing what is *wrong*, and needs approval
because it changes what future reads see. **Nothing is ever truly
lost** -- every UPDATE archives the prior version into `memory_history`
via `trg_memory_history`. See `reference.md` for the worked
`append`/`edit` examples, the `reclassify`/`link` mechanics, and the
history guarantee.

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

### Trim order and quotas (why an anchor can vanish, but a carry_forward usually survives)

`class=anchor` and `class=thread status=carry_forward` are NOT equally
durable in the SessionStart injection block -- this asymmetry is exactly
why "pendientes para la próxima sesión" belong in `carry_forward`, never in
`anchor`. Both a per-section quota and a char-budget trim order favor
`carry_forward` over `anchor`:

- **Per-section quota.** `## Memory — About you / What I know` (`anchor`)
  is capped at a small fixed quota (`_RELEVANT_PER_CLASS_QUOTA["anchor"]`)
  at query time -- only the top few anchors (identity anchors pinned first,
  then most-recently-updated) are even selected as candidates. `## Memory —
  For this session` (`carry_forward`) gets its own, larger recency sub-cap
  (`_RELEVANT_CARRY_FORWARD_CAP`) and is selected first, ahead of anchors.
- **Char-budget trim order.** `gaia memory get-relevant` enforces a hard
  character cap on the whole block. When the rendered block overflows that
  cap, sections are trimmed one bullet at a time in this fixed order:
  `thread_open` → `anchor` → `carry_forward`. `carry_forward` is the LAST
  resort -- it is trimmed only once `thread_open` and `anchor` have both
  been fully emptied and the block is still over budget. In practice this
  means: under budget pressure, `anchor` rows are cut before a single
  `carry_forward` row is touched.

The practical consequence: a pending saved as `class=anchor` is disadvantaged
twice over -- it competes for a smaller quota, AND it is one of the first
sections trimmed when the budget is tight -- on top of never appearing in
`For this session` at all (see "Decide from intention first" above). A
`carry_forward` thread is not literally exempt from trimming forever -- it
IS in the trim-order list, as the last target -- but it is the row the
mechanism protects hardest, exactly the property a "pendiente" needs.

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

**Session close applies this same decomposition.** When
`session-reflection` persists a closing session, it saves the arc as
*one* **record** anchor plus *one* **carry-forward thread per open
pending**, each linked `derived_from` the record -- never a single
anchor whose body buries the pendings (that body is never re-injected,
so a buried pending goes invisible). The record carries what happened;
the threads carry what must resurface. That whole decomposition is
written in ONE atomic call with `gaia memory checkpoint` (a JSON payload
of `resumen` + `pendientes`), not an `add` per row -- all-or-nothing, so
a session never ends with a half-written save. See
`session-reflection/SKILL.md` (Step 6) for that flow -- it is this "One
thread = one note" rule applied at the session boundary.

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

- **Saving a "pendiente para la próxima sesión" as `class=anchor`** --
  including via `project_*` or `user_*`, which default to `anchor`. It
  lands in `## Memory — About you / What I know`, competes for that
  section's small quota, is trimmed BEFORE `carry_forward` under
  char-budget pressure (see "Trim order and quotas" under Carry-forward /
  handoff), and never appears as a pending in `## Memory — For this
  session` no matter how urgent its content. Pendings are threads: use
  `class=thread --status=carry_forward`, regardless of the slug's type
  prefix (see "Decide from intention first" in Write flow).
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
  `reclassify`, never by hand-editing text. This is the same failure a
  session save hits when it packs the whole arc into one anchor -- see
  Carry-forward / handoff and `session-reflection/SKILL.md`.
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
