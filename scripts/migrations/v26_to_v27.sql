-- Migration v26 -> v27: DB-backed surface routing.
--
-- Retires config/surface-routing.json as the routing source of truth and moves
-- it to each agent's `routing:` frontmatter block, seeded into this table at
-- install time by tools/scan/seed_surface_routing.py (a mirror of
-- seed_contract_permissions.py). The matcher tools/context/surface_router.py
-- now reads this table instead of the JSON file.
--
-- The DDL mirrors gaia/store/schema.sql (floor model: schema.sql already
-- carries this object, so on a fresh install the CREATE below targets a table
-- that already exists). CREATE TABLE IF NOT EXISTS is idempotent by
-- construction, so this migration is a safe no-op replay on a fresh install and
-- an additive create on any DB behind v27. Seeding of rows is NOT done here --
-- it is an install-time step (seed_surface_routing.py), exactly as
-- agent_contract_permissions rows are seeded by seed_contract_permissions.py
-- rather than by a migration.

CREATE TABLE IF NOT EXISTS surface_routing (
    surface                TEXT NOT NULL PRIMARY KEY,
    primary_agent          TEXT NOT NULL,
    adjacent_surfaces_json TEXT NOT NULL DEFAULT '[]',
    contract_sections_json TEXT NOT NULL DEFAULT '[]',
    required_checks_json   TEXT NOT NULL DEFAULT '[]',
    keywords_json          TEXT NOT NULL DEFAULT '[]',
    commands_json          TEXT NOT NULL DEFAULT '[]',
    artifacts_json         TEXT NOT NULL DEFAULT '[]',
    sub_surfaces_json      TEXT
);
