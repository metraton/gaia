---
name: memory
description: Use when reading, searching, saving, or curating Gaia memory — atoms, decisions, negative space, project/user/feedback notes — or when triaging the SessionStart Workspace Memory block
metadata:
  user-invocable: false
  type: technique
---

# Memory

Gaia's curated memory lives in the `memory` table of the substrate DB
(`~/.gaia/gaia.db`) and is shaped by **six slug types**. Three are the
legacy mixed-purpose notes; three are the new bounded-curation taxonomy
that the orchestrator sees at SessionStart. This skill covers the full
flow: read what is already in your context, query when more is needed,
write when a discovery is closed, and curate when the set drifts.

## The six slug types

| Type        | Slug pattern              | Purpose                                                | Example                          |
|-------------|---------------------------|--------------------------------------------------------|----------------------------------|
| `atom`      | `atom_<topic>`            | Stable knowledge atom -- one fact, recall on demand    | `atom_qxo_uses_node_20`          |
| `decision`  | `decision_<topic>`        | Decision provenance -- the choice and the rationale    | `decision_terraform_vs_pulumi`   |
| `negative`  | `negative_<topic>`        | Negative space -- closed path, do not repeat           | `negative_helm_inline_charts`    |
| `project`   | `project_<topic>` (free)  | Legacy: repo/system knowledge, often mixed             | `project_gaia_v5`                |
| `user`      | `user_<topic>` (free)     | Legacy: personal preferences and identity              | `user_jorge`                     |
| `feedback`  | `feedback_<topic>` (free) | Legacy: corrections, learnings, post-mortems           | `feedback_release_learnings`     |

The three curated types (`atom`, `decision`, `negative`) enforce the
slug pattern `^<type>_[a-z0-9_]+$` at the CLI -- inserting an invalid
slug is rejected. Legacy types keep their historical naming freedom.

`memory_fts` is the FTS5 mirror; triggers keep it in sync.

## Read flow

### Trust the injected block first

At SessionStart the orchestrator receives a `## Workspace Memory` block
listing the top atoms / decisions / negatives for the current workspace
(`gaia memory get-relevant`, bounded to ~800 chars). When the user's
question can be answered from what is already in your context, do **not**
re-query -- the injected block is already the relevance-ranked top.

### Query for depth

Reach for the CLI when:

- The user names a topic you do not see in the injected block.
- The injected block has a stub but the user wants the full body.
- You are about to write a new memory and need to check for duplicates.

| Need                                    | Command                                                  |
|-----------------------------------------|----------------------------------------------------------|
| FTS5 search (curated + episodes)        | `gaia memory search "<query>" [--limit N]`               |
| Curated only                            | `gaia memory search "<query>" --scope=memory`            |
| Episodes only                           | `gaia memory search "<query>" --scope=episodes`          |
| Show one curated row                    | `gaia memory show <slug>`                                |
| Show one episode                        | `gaia memory episode-show <episode_id>`                  |
| List curated rows (filter by type)      | `gaia memory list --type=atom`                           |
| Health stats                            | `gaia memory stats`                                      |
| Pairwise contradiction scan             | `gaia memory conflicts [--threshold F]`                  |
| Get the injection block (debug)         | `gaia memory get-relevant --workspace=<ws>`              |

Always pass `--json` when output feeds a follow-up step.

## Write flow

Memory growth requires deliberation. Not every observation is worth
persisting -- a bounded curation forces prioritisation, and an overstuffed
memory loses signal to noise. Propose a save **only when one of these
triggers fires**:

| Trigger                                            | Type        | Slug shape                          |
|----------------------------------------------------|-------------|-------------------------------------|
| A decision was closed (with rationale)             | `decision`  | `decision_<topic>`                  |
| A discovery was made and will be reused            | `atom`      | `atom_<topic>`                      |
| A path was tried, abandoned, and should not recur  | `negative`  | `negative_<topic>`                  |
| Cross-cutting repo / system knowledge              | `project`   | `project_<topic>`                   |
| User preference or identity update                 | `user`      | `user_<topic>`                      |
| Post-mortem / correction the system must remember  | `feedback`  | `feedback_<topic>`                  |

The flow:

1. Propose to the user: "Discovery worth saving as `atom_<slug>` -- ok?"
2. On confirmation, dispatch a subagent (or run yourself if you hold the
   tool) with:

```bash
gaia memory add \
  --name="<slug matching the pattern>" \
  --type="atom|decision|negative|project|user|feedback" \
  --body="<markdown body, no frontmatter>" \
  --description="<one-line summary>" \
  --workspace="<ws>"
```

`add` is UPSERT semantics: same `--name` + `--workspace` overwrites in
place. There is no separate `update` command.

### Body shape per curated type

- **`atom_*`**: one fact, 1-3 sentences. If it sprawls, it is two atoms.
- **`decision_*`**: state the decision in the first line, then 2-5 lines
  of rationale (what was considered, why this won). Include the date.
- **`negative_*`**: state the closed path in the first line, then why it
  failed and what was used instead. The point is *do not retry this*.

## Curate flow

Run periodically (or when `gaia memory stats` shows conflicts > 0,
or when memory size feels unwieldy).

### Deduplication

1. `gaia memory search "<topic>" --scope=memory` to find overlaps.
2. Read both bodies; identify the broader scope.
3. UPSERT the merged content into the broader slug.
4. `gaia memory delete <narrower-slug> --yes` to drop the redundant row.

### Conflict resolution

`gaia memory conflicts` flags pairs whose bodies overlap above a Jaccard
threshold. For each pair:

- If they are duplicates, merge and delete one (see Deduplication).
- If they contradict, the newer one usually wins -- but ask the user
  before overwriting a `decision_*` row.

### Pruning stale entries

1. Identify rows referencing retired projects, deprecated tooling, or
   resolved decisions whose outcome no longer needs justification.
2. Confirm with the user before removing.
3. `gaia memory delete <slug> --yes`.

### Splitting overgrown bodies

When a body exceeds ~100 lines, split into focused subtopics:

1. Identify natural section boundaries.
2. `gaia memory add` one row per subtopic with a tightly scoped slug.
3. Replace the original body with a brief index pointing to the new
   slugs, or delete it entirely.

### Editing a single field

`gaia memory edit --name=<slug> --field=<description|body> --content="..."`
patches one column. Pass `--append` to append (separator `\n\n`) rather
than overwrite.

## Rules

| Rule                                                | Reason                                                                                  |
|-----------------------------------------------------|------------------------------------------------------------------------------------------|
| One topic per row                                   | Slug names a single concern; split if a row outgrows its scope.                          |
| `description` is required for new rows              | Listings, injection block, and search results lean on description, not body.             |
| Confirm before pruning                              | Report what will be removed and get user confirmation.                                   |
| Read before overwriting                             | `add` is UPSERT -- the prior body is gone. Always `show` first when re-using a slug.    |
| Use UPSERT, not delete-then-insert                  | Preserves `origin_session_id` provenance and avoids FTS5 churn.                          |
| Curated slugs must match `^<type>_[a-z0-9_]+$`      | The CLI rejects mismatched slugs; the convention is what makes the taxonomy queryable.  |

## Anti-patterns

- **Saving trivial observations** -- "tested locally and it worked" is
  not memory, it is conversational filler. Memory is the bounded set of
  facts, decisions, and closed paths that anchor *future* sessions.
- **Slug discipline violations on curated types** -- `atom_*`,
  `decision_*`, `negative_*` slugs that do not match the pattern get
  rejected at the CLI. The pattern is not bureaucracy; it is what makes
  the taxonomy queryable as a set.
- **Overwriting without reading** -- `add` is UPSERT; the prior body is
  gone. Read with `gaia memory show <slug>` first when reusing a slug.
- **Treating delete as the way to update** -- UPSERT (re-running `add`)
  is the canonical update path; deletion is reserved for genuinely
  abandoned entries.
- **Writing `.md` files to disk** -- the legacy
  `~/.claude/projects/.../memory/` directory is read-only-for-humans.
  The DB row IS the memory.

## Drift fixes (read once)

- `gaia memory delete <slug> [--yes]` is **shipped**; older docs that
  say it is "upcoming" are wrong.
- `gaia memory show <slug>` operates on the **curated** row. The
  separate `gaia memory episode-show <episode_id>` is the episode
  inspector -- different surface.
- `gaia memory search --scope=curated` was renamed `--scope=memory`;
  `curated` still works as a deprecated alias.
- The skill formerly called `memory-search` has been folded into this
  skill; references should point at `Skill('memory')`.
