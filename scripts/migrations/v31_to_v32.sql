-- Migration v31 -> v32: add memory.initiative (canonical project/initiative
-- grouping key) and backfill it on existing rows.
--
-- WHY
--   `memory.project_ref` (the git-common-dir path, e.g.
--   `/home/jorge/ws/me/gaia/.git`) is populated only on rows that belong to a
--   git project -- ~16% of rows in practice. The other ~84% carry NULL
--   project_ref and encode their real initiative in the slug prefix of `name`
--   (`decision_branchkinect_...`, `atom_aos_...`, `bildwiz_...`). Deriving the
--   project from that slug ad hoc produces garbage (first-token guessing).
--   `initiative` is the clean, vantage-independent key that unifies BOTH git
--   projects and logical (non-repo) initiatives, so downstream grouping
--   (memory injection / get-relevant redesign, Task B) has one honest column
--   to group by instead of re-parsing slugs.
--
-- DESIGN
--   New nullable column; project_ref is UNTOUCHED (it remains the git-path
--   anchor). Low blast radius, no rebuild of the memory table.
--
-- IDEMPOTENCY (floor model, replayed on every fresh install)
--   * ADD COLUMN: schema.sql already declares `memory.initiative` for fresh
--     installs, so on a fresh DB the column exists before this file runs.
--     SQLite has no `ADD COLUMN IF NOT EXISTS`; the bootstrap runner guard
--     (_filter_add_column_idempotent, Section 3c) neutralises the ALTER when
--     the column already exists, and Section 1.5 adds it pre-schema on an
--     existing DB. One `ALTER TABLE ... ADD COLUMN ...` per line, as the
--     runner's matcher requires.
--   * BACKFILL: `WHERE initiative IS NULL` makes the UPDATE a deterministic
--     no-op on re-run and on fresh installs (the memory table is empty there).
--     A row that legitimately resolves to NULL stays NULL and is harmlessly
--     re-evaluated on replay.
--
-- BACKFILL PRIORITY (first matching CASE branch wins, top to bottom)
--   (a) git project_ref present   -> repo basename without `.git`
--                                    (`/home/jorge/ws/me/gaia/.git` -> `gaia`).
--   (b) slug token matches the EXPLICIT allow-list of known initiatives
--       -> that initiative. The token test wraps the slug in underscores and
--       uses `LIKE '%\_tok\_%' ESCAPE '\'` so only WHOLE `_`-delimited tokens
--       match (never a substring). Branch order encodes leftmost-token-wins
--       for the only real co-occurrences in the corpus:
--         `..branchkinect..century..` -> branchkinect (branchkinect first)
--         `..century..diagram_builder..` -> century   (century before diagram_builder)
--   (c) gaia-internal token (contract/scan/release/approval/security/memory/
--       t3/mutation/hook) and no allow-list hit -> 'gaia'.
--   (d) nothing matches -> NULL. NEVER a first-token guess.

ALTER TABLE memory ADD COLUMN initiative TEXT;

UPDATE memory
SET initiative = CASE
    -- (a) git-anchored rows: initiative = repo basename without `.git`.
    WHEN project_ref IS NOT NULL AND project_ref != '' THEN
        (
            -- basename( strip trailing '/.git' or '.git' from project_ref )
            WITH stripped(p) AS (
                SELECT CASE
                    WHEN project_ref LIKE '%/.git'
                        THEN substr(project_ref, 1, length(project_ref) - 5)
                    WHEN project_ref LIKE '%.git'
                        THEN substr(project_ref, 1, length(project_ref) - 4)
                    ELSE project_ref
                END
            )
            SELECT replace(p, rtrim(p, replace(p, '/', '')), '') FROM stripped
        )

    -- (b) explicit allow-list of known initiatives (whole-token match).
    --     Ordered so leftmost-token-wins holds for real co-occurrences.
    WHEN ('_' || name || '_') LIKE '%\_branchkinect\_%' ESCAPE '\' THEN 'branchkinect'
    WHEN ('_' || name || '_') LIKE '%\_buildwiz\_%' ESCAPE '\' THEN 'buildwiz'
    WHEN ('_' || name || '_') LIKE '%\_bildwiz\_%' ESCAPE '\' THEN 'bildwiz'
    WHEN ('_' || name || '_') LIKE '%\_axisio\_%' ESCAPE '\' THEN 'axisio'
    WHEN ('_' || name || '_') LIKE '%\_newsletter\_%' ESCAPE '\' THEN 'newsletter'
    WHEN ('_' || name || '_') LIKE '%\_century\_%' ESCAPE '\' THEN 'century'
    WHEN ('_' || name || '_') LIKE '%\_diagram\_builder\_%' ESCAPE '\' THEN 'diagram_builder'
    WHEN ('_' || name || '_') LIKE '%\_balance\_%' ESCAPE '\' THEN 'balance'
    WHEN ('_' || name || '_') LIKE '%\_gaia\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_aos\_%' ESCAPE '\' THEN 'aos'
    WHEN ('_' || name || '_') LIKE '%\_nfi\_%' ESCAPE '\' THEN 'nfi'
    WHEN ('_' || name || '_') LIKE '%\_qxo\_%' ESCAPE '\' THEN 'qxo'
    WHEN ('_' || name || '_') LIKE '%\_rnd\_%' ESCAPE '\' THEN 'rnd'

    -- (c) gaia-internal tokens -> 'gaia'.
    WHEN ('_' || name || '_') LIKE '%\_contract\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_scan\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_release\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_approval\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_security\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_memory\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_t3\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_mutation\_%' ESCAPE '\' THEN 'gaia'
    WHEN ('_' || name || '_') LIKE '%\_hook\_%' ESCAPE '\' THEN 'gaia'

    -- (d) unknown -> NULL (never a first-token guess).
    ELSE NULL
END
WHERE initiative IS NULL;
