---
name: memory-curation
description: Use when reorganizing, deduplicating, pruning, or saving curated memory entries (project_*, user_*, feedback_*)
metadata:
  user-invocable: false
  type: technique
---

# Memory Curation

Curate the long-lived memory notes that the orchestrator and specialists draw
on across sessions. This skill covers two flows: **saving** new memory and
**maintaining** the existing set (deduplicate, prune, merge, split).

## DB is the source of truth (read this first)

Curated memory lives in the `memory` table of the Gaia substrate database
(`~/.gaia/gaia.db`). Rows are created and mutated through the `gaia memory`
CLI -- never by writing `.md` files into `~/.claude/projects/.../memory/`.
That filesystem layout is **legacy** and is being removed in the
`legacy-cleanup` brief.

If a previous version of this skill or a stale doc tells you to write
`{type}_{topic}.md` to `~/.claude/projects/-home-jorge-ws-me/memory/` and
update `MEMORY.md`, ignore it. The migration to DB-canonical is in progress
(session 2026-05-06, brief `gaia-state-machines`). When in doubt: there is
no file to write -- there is a CLI command to run.

The 28 legacy `.md` files under `~/.claude/projects/-home-jorge-ws-me/memory/`
have already been imported into the `memory` table by `migration_02`. Do not
re-import, re-export, or edit them in place.

## Memory Schema

The `memory` table has primary key `(project, name)` and these columns:

| Column              | Purpose                                                |
|---------------------|--------------------------------------------------------|
| `project`           | Workspace identity (FK to `projects.name`)             |
| `name`              | Slug, e.g. `project_gaia_v5`, `user_jorge`             |
| `type`              | One of `project`, `user`, `feedback` (CHECK)           |
| `description`       | One-line summary (mirrors legacy frontmatter)          |
| `body`              | Markdown body without frontmatter                      |
| `origin_session_id` | Session that produced or last touched the row         |
| `updated_at`        | ISO8601 timestamp; refreshed on every UPSERT           |

`memory_fts` is the FTS5 mirror; triggers (`memory_ai`, `memory_au`,
`memory_ad`) keep it in sync automatically. Do not write to it directly.

| Type       | Purpose                                          | Example slug                  |
|------------|--------------------------------------------------|-------------------------------|
| `project`  | Repo / system knowledge                          | `project_gaia_v5`             |
| `user`     | Personal preferences and identity                | `user_jorge`                  |
| `feedback` | Corrections, learnings, post-mortems             | `feedback_release_learnings`  |

## Save a memory entry (DB-only)

Run:

```bash
gaia memory add \
  --name="<type>_<topic>" \
  --type="<project|user|feedback>" \
  --body="<markdown body, no frontmatter>" \
  --description="<one-line summary>" \
  --workspace="<ws>"
```

The CLI writes a row to `memory` and prints back the slug, type, and a body
preview. **Do not write any file under `~/.claude/projects/.../memory/`.**
No `MEMORY.md` row to update, no `.md` to create, no frontmatter on disk.
The DB row IS the memory.

UPSERT semantics: re-running with the same `--name` and `--workspace`
overwrites the row in place (single row per PK). This is the canonical way
to update an existing entry -- there is no separate `gaia memory update`
yet; `add` covers both flows.

| Need                           | Command                                                        |
|--------------------------------|----------------------------------------------------------------|
| Insert a new entry             | `gaia memory add --name=... --type=... --body=...`             |
| Update an existing entry       | Same command -- the second call UPSERTs                        |
| Add a one-line summary         | Pass `--description="..."`                                     |
| Save into a non-current ws     | Pass `--workspace=<ws>`                                        |
| JSON output (scripting)        | Add `--json`                                                   |

`--workspace` defaults to `gaia.project.current()` (the workspace inferred
from cwd). Pass it explicitly when running outside the workspace tree.

## Read curated memory

| Need                | Command                                              |
|---------------------|------------------------------------------------------|
| FTS5 search         | `gaia memory search <query> [--limit N]`             |
| Health stats        | `gaia memory stats`                                  |
| Show one episode    | `gaia memory show <episode_id>` (episodic, not curated) |
| Conflict scan       | `gaia memory conflicts [--threshold F]`              |

Note: `gaia memory show` operates on the *episodic* memory log, not on the
curated `memory` table. To inspect a single curated row, run:

```bash
sqlite3 -readonly ~/.gaia/gaia.db \
  "SELECT name, type, description, updated_at FROM memory WHERE name='<slug>'"
```

A dedicated `gaia memory get <name>` is on the follow-up brief.

## Curation Operations

### Deduplication

1. `gaia memory search <topic>` -- find rows that overlap.
2. Read both bodies; identify the file with the broader scope.
3. Merge the content into one body (in your head or a scratch buffer).
4. `gaia memory add --name=<broader-slug> --body="<merged>" ...` to upsert
   the merged row.
5. (Once `gaia memory delete` ships) delete the narrower row. Until then,
   leave a one-line stub pointing at the merged slug.

### Pruning Stale Entries

1. Search for rows referencing deprecated tools, retired projects, or
   resolved decisions.
2. Confirm with the user before removing.
3. Soft-prune by upserting a body that says `superseded by <slug>` until a
   delete subcommand exists.

### Merging Overlapping Topics

1. Same as deduplication, but the two rows are adjacent (not duplicate).
2. Choose the slug that best represents the combined topic.
3. Update `description` to reflect the merged scope.
4. UPSERT the merged row; soft-prune the redundant one.

### Splitting Overgrown Bodies

When a body exceeds ~100 lines, split into focused subtopics:

1. Identify natural section boundaries.
2. UPSERT one row per subtopic with a clearly scoped slug.
3. Replace the original body with a brief index pointing to the new slugs.

### Health Check

Run `gaia memory stats` and `gaia memory conflicts` periodically. The
conflict scan surfaces rows whose bodies overlap above a similarity
threshold -- candidates for dedup or merge.

## Rules

| Rule | Reason |
|------|--------|
| One topic per row | Slug names a single concern; split if a row outgrows its scope. |
| `description` is required for new rows | Listings and indexes show description, not body. |
| Confirm before pruning | Report what will be removed and get user confirmation. |
| Never edit the legacy `.md` files | They are read-only relics; the DB ignores them. |
| Use UPSERT, not delete-then-insert | Preserves `origin_session_id` provenance and avoids FTS5 churn. |

## Filesystem behavior (DEPRECATED)

The legacy directory `~/.claude/projects/-home-jorge-ws-me/memory/` with
`MEMORY.md` plus `{type}_{topic}.md` files is **legacy** and will be removed
in the `legacy-cleanup` brief. Reasons it is being retired:

- Two writers (filesystem + DB) drift apart silently; only one can be the
  source of truth.
- `MEMORY.md` had to be hand-curated to reflect the directory; mismatches
  were common. The DB is self-describing -- there is no separate index to
  drift.
- FTS5 search and conflict detection require structured columns, not free-
  form markdown frontmatter.
- Cascade deletes across related artifacts (briefs, plans, tasks) require
  FK semantics, which a flat directory cannot provide.

If you find code, docs, or skills that still describe the directory + index
convention, flag them in `cross_layer_impacts` -- do not edit them as a side
effect of a memory task.

## Anti-Patterns

- **Writing `{type}_{topic}.md` to disk** -- the DB is the source of truth;
  any file on disk is either a legacy import or stale.
- **Updating `MEMORY.md`** -- there is no index file in the DB-canonical
  flow. The `memory` table is its own catalog (query by `type` or `name`).
- **Using `sqlite3 INSERT` directly** -- bypass `gaia memory add` and you
  lose the validated type CHECK, the timestamp refresh, and the workspace
  resolution. Always go through the CLI.
- **Skipping `--description`** -- listings and search results lean on
  `description`; an empty description makes a row hard to find.
- **Treating "delete" as the way to update** -- UPSERT (re-running `add`)
  is the canonical update path; deletion is reserved for genuinely
  abandoned entries.
