-- Migration v23 -> v24: add the scanner-owned `project_facets` table (M3/T8, AC-6).
--
-- Persists the per-project stack fingerprint (languages, frameworks with
-- version, build tools, detected infrastructure/deployment/orchestration
-- aspects) as homogeneous facet rows keyed by (workspace, project, scope, key)
-- rather than as ad-hoc columns on `projects`. `scope` is a generic,
-- extensible vocabulary so a new aspect needs no schema change. The table is
-- 100% scanner-owned: every `gaia scan` refreshes the fingerprint by upserting
-- the current facets and pruning the stale ones for the project.
--
-- The DDL mirrors gaia/store/schema.sql (floor model: schema.sql already
-- carries this table, so on a fresh install this CREATE targets an existing
-- object). CREATE TABLE / CREATE INDEX IF NOT EXISTS are idempotent, so this
-- migration is safe to replay on a fresh install (floor+1..EXPECTED) as well
-- as to apply in-place on a DB at v23.

CREATE TABLE IF NOT EXISTS project_facets (
    workspace  TEXT NOT NULL,  -- FK -> workspaces.name
    project    TEXT NOT NULL,  -- FK -> projects.name within the same workspace
    scope      TEXT NOT NULL,  -- generic facet scope (language|framework|build|infrastructure|deployment|orchestration|ci_cd|...); scanner-owned
    key        TEXT NOT NULL,  -- detected name within the scope (e.g. 'python', 'nestjs', 'terraform'); scanner-owned
    value      TEXT,           -- detail/version for the facet (e.g. framework version, manifest path); scanner-owned
    scanner_ts TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, scope, key),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_project_facets_workspace ON project_facets(workspace);
CREATE INDEX IF NOT EXISTS idx_project_facets_scope ON project_facets(scope);
