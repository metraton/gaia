# NOTICE — gaia.store

This module incorporates design patterns lifted from the **engram** project.

## Source

- **engram** — https://github.com/koaning/engram
- **License**: MIT License

## Patterns lifted (with attribution, no runtime dependency)

The following design ideas are adapted from engram into `gaia.store` and
its companion modules `gaia.paths` and `gaia.project`:

1. **Identity from git remote URL, normalized lowercase** —
   `projects.identity` is populated from the git remote URL canonicalized to
   `host/owner/repo` form (lowercase, no protocol, no `.git` suffix), with
   fallback to the workspace name. See `gaia.project._normalize_remote` and
   `gaia.store.writer._resolve_identity`.

2. **`topic_key` as an optional dimension of upsert** —
   Tables holding long-text or multi-dimensional records (`projects`, `apps`,
   `services`, `features`, `tf_modules`) include an optional `topic_key`
   column used to disambiguate upserts beyond the primary key. See
   `schema.sql` and the `topic_key` parameter on `upsert_project` /
   `upsert_app`.

3. **FTS5 mirror tables for text search** —
   `projects_fts`, `apps_fts`, `services_fts` are FTS5 virtual tables that
   shadow their base tables and stay in sync via INSERT/UPDATE/DELETE
   triggers.

4. **Snapshot and merge flow for drifted identities** —
   The `gaia workspace merge` flow for handling drifted workspace identities
   is inspired by engram's snapshot model. See `gaia.paths.snapshot_dir`
   (database snapshots under the Gaia data directory) and `gaia.project.merge`.

## Statement

There is **no runtime dependency** on engram. Patterns are lifted as design
references; all code in `gaia/store/`, `gaia/paths/`, and `gaia/project.py`
is original and licensed under Gaia's own MIT terms (see top-level
`LICENSE`).

We acknowledge engram (MIT License) for the conceptual contributions
listed above. Their license requires preservation of copyright and
permission notices, which this NOTICE file fulfills for the patterns
referenced here.
