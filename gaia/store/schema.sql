-- Gaia SQLite substrate schema
-- Version: 2.0 (workspace/project rename: workspaces=organizational container, projects=git-bearing project)
--
-- Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
-- No runtime dependency on engram; patterns lifted with attribution (see NOTICE.md).
--
-- Vocabulary:
--   workspaces -- organizational containers (e.g. "me", "bildwiz", "qxo"). May contain
--                 0..N projects. The workspace root usually does NOT have its own .git.
--   projects   -- git-bearing source repositories within a workspace (formerly "repos").
--                 Each project belongs to exactly one workspace.
--
-- All child tables segmented by `workspace` (FK -> workspaces.name). Project-scoped
-- child tables also carry a `project` column (FK -> projects(workspace, name)).
-- ON DELETE CASCADE propagates workspace deletion to all child rows.
--
-- Ownership annotations per column:
--   -- scanner-owned: written by the reconciler/scanner on each scan cycle
--   -- agent-owned:   written by domain agents (developer, platform-architect, etc.)

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- workspaces: organizational containers (formerly `projects` in v1 schema).
-- A workspace may contain zero or more git-bearing projects.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspaces (
    name          TEXT NOT NULL PRIMARY KEY,  -- workspace name (canonical: host/owner/repo or directory basename)
    identity      TEXT,                       -- identity: for git-bearing workspace = git remote URL normalized lowercase; for organizational workspace = name; scanner-owned
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),  -- scanner-owned
    last_scan_at  TEXT,                       -- ISO8601 timestamp of last successful `gaia scan` run; NULL = never scanned; v7
    status        TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'missing'; scanner-owned (soft-delete). 'missing' = the Gaia install footprint disappeared (workspace demoted); v17
    missing_since TEXT                         -- ISO8601 timestamp when status set to 'missing'; NULL if active; scanner-owned; v17
);

CREATE INDEX IF NOT EXISTS idx_workspaces_identity ON workspaces(identity);

-- ---------------------------------------------------------------------------
-- projects: git-bearing source projects within a workspace (formerly `repos`).
-- A project is the unit of code -- it has a git remote, primary language, etc.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    workspace        TEXT NOT NULL,  -- FK -> workspaces.name
    name             TEXT NOT NULL,  -- project name (basename); scanner-owned
    role             TEXT,           -- e.g. 'backend', 'frontend', 'library', 'infra'; agent-owned
    remote_url       TEXT,           -- git remote URL (raw, unnormalized); scanner-owned
    platform         TEXT,           -- 'github', 'bitbucket', 'gitlab', etc.; scanner-owned
    primary_language TEXT,           -- detected primary language; scanner-owned
    scanner_ts       TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    topic_key        TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    group_name       TEXT,           -- optional group/team within the workspace (workspace->group->repo, AC-2); scanner-owned
    path             TEXT,           -- absolute path on disk to the project root; scanner-owned (findability: project -> path + workspace)
    status           TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'missing'; scanner-owned (soft-delete)
    missing_since    TEXT,           -- ISO8601 timestamp when status set to 'missing'; NULL if active; scanner-owned
    project_identity TEXT,           -- stable, vantage-independent project identity (git-common-dir realpath > normalized remote > realpath path); scanner-owned. NULL allowed for legacy/uninitialized rows. The partial unique index idx_projects_identity collapses the SAME physical repo scanned from different workspaces/roots into ONE row. See workspace-identity brief M1-T2.
    description      TEXT,           -- human-authored summary/purpose of the project; agent-owned. Never written by the scan path (gaia.store.writer._PROJECTS_AGENT_OWNED); survives any number of scanner rescans unchanged. Added v23 (workspace-identity brief M3-T9).
    superseded_by    TEXT,           -- points to the successor project_identity after a 'movido' adjudication; NULL until then. Column added v25 (scan-v2 SV1); populated in SV4.
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace);
CREATE INDEX IF NOT EXISTS idx_projects_topic_key ON projects(topic_key);
-- Note: idx_projects_identity (partial UNIQUE on project_identity) is NOT
-- declared here. It references the project_identity column, which on an
-- existing (pre-v18) DB does not yet exist when this CREATE TABLE IF NOT EXISTS
-- short-circuits -- declaring the index here would parse-fail with "no such
-- column: project_identity" during bootstrap of a legacy DB. The index is
-- created by scripts/migrations/v17_to_v18.sql (existing DBs, after the ALTER)
-- and by v17_to_v18_fresh.sql (fresh installs, after schema.sql added the
-- column). Same convention as idx_memory_class_status (see L669) and the
-- episodes tier indexes (L579).

-- ---------------------------------------------------------------------------
-- apps: deployed applications (services, jobs, functions, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS apps (
    workspace   TEXT NOT NULL,  -- FK -> workspaces.name
    project     TEXT NOT NULL,  -- FK -> projects.name within the same workspace
    name        TEXT NOT NULL,  -- app/service name; scanner-owned
    kind        TEXT,           -- 'service', 'job', 'function', 'cronjob'; scanner-owned
    description TEXT,           -- human description; agent-owned
    status      TEXT,           -- 'active', 'deprecated', 'planned'; agent-owned
    topic_key   TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts  TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_apps_workspace ON apps(workspace);
CREATE INDEX IF NOT EXISTS idx_apps_status ON apps(status);
CREATE INDEX IF NOT EXISTS idx_apps_topic_key ON apps(topic_key);

-- ---------------------------------------------------------------------------
-- libraries: shared library packages within the workspace
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS libraries (
    workspace  TEXT NOT NULL,  -- FK -> workspaces.name
    project    TEXT NOT NULL,  -- FK -> projects.name within the same workspace
    name       TEXT NOT NULL,  -- library/package name; scanner-owned
    version    TEXT,           -- current version; scanner-owned
    language   TEXT,           -- primary language; scanner-owned
    scanner_ts TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_libraries_workspace ON libraries(workspace);

-- ---------------------------------------------------------------------------
-- services: infrastructure-level services (APIs, databases, queues, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS services (
    workspace   TEXT NOT NULL,  -- FK -> workspaces.name
    project     TEXT NOT NULL,  -- FK -> projects.name within the same workspace
    name        TEXT NOT NULL,  -- service name; scanner-owned
    kind        TEXT,           -- 'api', 'database', 'queue', 'cache', 'storage'; scanner-owned
    description TEXT,           -- human description; agent-owned
    status      TEXT,           -- 'active', 'deprecated', 'planned'; agent-owned
    topic_key   TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts  TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_services_workspace ON services(workspace);
CREATE INDEX IF NOT EXISTS idx_services_status ON services(status);
CREATE INDEX IF NOT EXISTS idx_services_topic_key ON services(topic_key);

-- ---------------------------------------------------------------------------
-- features: feature flags and feature-level metadata
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS features (
    workspace   TEXT NOT NULL,  -- FK -> workspaces.name
    project     TEXT NOT NULL,  -- FK -> projects.name within the same workspace
    name        TEXT NOT NULL,  -- feature name / flag key; scanner-owned
    status      TEXT,           -- 'active', 'deprecated', 'planned'; agent-owned
    description TEXT,           -- human description; agent-owned
    topic_key   TEXT,           -- optional dimension key for upsert disambiguation; agent-owned
    scanner_ts  TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_features_workspace ON features(workspace);
CREATE INDEX IF NOT EXISTS idx_features_status ON features(status);
CREATE INDEX IF NOT EXISTS idx_features_topic_key ON features(topic_key);

-- ---------------------------------------------------------------------------
-- project_facets: the per-project stack fingerprint (M3/T8, AC-6).
--
-- Homogeneous rows + discriminator (the memory-type pattern, a stepping stone
-- toward a future graph): the languages, frameworks (with version), build
-- tools, and detected infrastructure/deployment/orchestration aspects the
-- scanners derive for a repo are persisted as facet rows here rather than as
-- ad-hoc columns on `projects`. `scope` is a generic, extensible vocabulary
-- (language, framework, build, infrastructure, deployment, orchestration,
-- ci_cd, ...) so a new aspect (e.g. documentation, data/ml) needs no schema
-- change -- only a new scope value. `key` is the detected name (e.g. "python",
-- "nestjs", "terraform") and `value` its detail/version (e.g. a framework
-- version, a manifest path, an IaC base path) or NULL when there is no detail.
--
-- 100% scanner-owned: there are no agent-owned columns here. Every `gaia scan`
-- run refreshes the fingerprint by upserting the current facets (keyed on
-- (workspace, project, scope, key), coalesce-safe) and pruning the stale ones
-- for the project -- a rescan REFRESHES without duplicating.
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- tf_modules: Terraform module definitions tracked in the workspace
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tf_modules (
    workspace  TEXT NOT NULL,  -- FK -> workspaces.name
    project    TEXT NOT NULL,  -- FK -> projects.name within the same workspace
    name       TEXT NOT NULL,  -- module name; scanner-owned
    source     TEXT,           -- module source path or registry reference; scanner-owned
    version    TEXT,           -- pinned version; scanner-owned
    topic_key  TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tf_modules_workspace ON tf_modules(workspace);
CREATE INDEX IF NOT EXISTS idx_tf_modules_topic_key ON tf_modules(topic_key);

-- ---------------------------------------------------------------------------
-- tf_live: live Terraform state (applied infrastructure resources)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tf_live (
    workspace  TEXT NOT NULL,   -- FK -> workspaces.name
    project    TEXT NOT NULL,   -- FK -> projects.name within the same workspace
    name       TEXT NOT NULL,   -- resource name; scanner-owned
    kind       TEXT,            -- resource type (e.g. 'aws_instance', 'google_sql_database_instance'); scanner-owned
    attributes TEXT,            -- JSON blob of selected attributes; scanner-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tf_live_workspace ON tf_live(workspace);

-- ---------------------------------------------------------------------------
-- releases: release/tag history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS releases (
    workspace  TEXT NOT NULL,   -- FK -> workspaces.name
    project    TEXT NOT NULL,   -- FK -> projects.name within the same workspace
    name       TEXT NOT NULL,   -- release tag or version string; scanner-owned
    released_at TEXT,           -- ISO8601 release date; scanner-owned
    notes      TEXT,            -- release notes summary; agent-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_releases_workspace ON releases(workspace);

-- ---------------------------------------------------------------------------
-- workloads: Kubernetes workloads / compute workloads tracked per project
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workloads (
    workspace  TEXT NOT NULL,   -- FK -> workspaces.name
    project    TEXT NOT NULL,   -- FK -> projects.name within the same workspace
    name       TEXT NOT NULL,   -- workload name; scanner-owned
    kind       TEXT,            -- 'Deployment', 'StatefulSet', 'DaemonSet', 'Job', etc.; scanner-owned
    namespace  TEXT,            -- Kubernetes namespace; scanner-owned
    cluster    TEXT,            -- cluster name this runs on; scanner-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workloads_workspace ON workloads(workspace);
CREATE INDEX IF NOT EXISTS idx_workloads_cluster ON workloads(cluster);

-- ---------------------------------------------------------------------------
-- clusters_defined: cluster definitions declared in the codebase (Terraform, Helm, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clusters_defined (
    workspace  TEXT NOT NULL,   -- FK -> workspaces.name
    project    TEXT NOT NULL,   -- FK -> projects.name within the same workspace
    name       TEXT NOT NULL,   -- cluster name; scanner-owned
    provider   TEXT,            -- 'gke', 'eks', 'aks', etc.; scanner-owned
    region     TEXT,            -- cloud region; scanner-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, project, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (workspace, project) REFERENCES projects(workspace, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_clusters_defined_workspace ON clusters_defined(workspace);

-- ---------------------------------------------------------------------------
-- clusters: live cluster instances (workspace-level, not project-scoped)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clusters (
    workspace  TEXT NOT NULL,   -- FK -> workspaces.name
    name       TEXT NOT NULL,   -- cluster name; scanner-owned
    provider   TEXT,            -- 'gke', 'eks', 'aks'; scanner-owned
    region     TEXT,            -- cloud region; scanner-owned
    attributes TEXT,            -- JSON blob for flexible extra attributes; agent-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_clusters_workspace ON clusters(workspace);

-- ---------------------------------------------------------------------------
-- integrations: third-party integrations and tools installed in the workspace
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integrations (
    workspace    TEXT NOT NULL,  -- FK -> workspaces.name
    name         TEXT NOT NULL,  -- integration name; scanner-owned
    kind         TEXT,           -- 'monitoring', 'alerting', 'security', 'network'; agent-owned
    version      TEXT,           -- installed version; scanner-owned
    install_path TEXT,           -- file path where the integration config lives; scanner-owned
    topic_key    TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_integrations_workspace ON integrations(workspace);
CREATE INDEX IF NOT EXISTS idx_integrations_topic_key ON integrations(topic_key);

-- ---------------------------------------------------------------------------
-- gaia_installations: Gaia CLI installation records per machine
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gaia_installations (
    workspace    TEXT NOT NULL,  -- FK -> workspaces.name
    machine      TEXT NOT NULL,  -- machine name or tailscale hostname; scanner-owned
    version      TEXT,           -- installed Gaia version; scanner-owned
    install_mode TEXT,           -- 'npm-global', 'local', 'dev'; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, machine),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gaia_installations_workspace ON gaia_installations(workspace);

-- ---------------------------------------------------------------------------
-- machines: machines participating in this workspace (Tailscale network, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS machines (
    workspace    TEXT NOT NULL,  -- FK -> workspaces.name
    name         TEXT NOT NULL,  -- machine hostname; scanner-owned
    os           TEXT,           -- 'windows', 'linux', 'macos'; scanner-owned
    arch         TEXT,           -- 'amd64', 'arm64'; scanner-owned
    tailscale_ip TEXT,           -- Tailscale MagicDNS or IP; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_machines_workspace ON machines(workspace);

-- ---------------------------------------------------------------------------
-- agent_permissions: per-table per-agent write authorization
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_permissions (
    table_name  TEXT NOT NULL,   -- name of the target table
    agent_name  TEXT NOT NULL,   -- agent identifier (e.g. 'developer', 'platform-architect')
    allow_write INTEGER NOT NULL DEFAULT 0,  -- 1 = allow, 0 = deny (BOOLEAN)
    PRIMARY KEY (table_name, agent_name)
);

-- Example row for tests (1 row for developer->apps=allow)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write)
VALUES ('apps', 'developer', 1);

-- ---------------------------------------------------------------------------
-- FTS5 mirror tables for full-text search (projects, apps, services)
-- ---------------------------------------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS projects_fts USING fts5(
    name,
    role,
    primary_language,
    content='projects',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS apps_fts USING fts5(
    name,
    description,
    topic_key,
    content='apps',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE IF NOT EXISTS services_fts USING fts5(
    name,
    description,
    topic_key,
    content='services',
    content_rowid='rowid'
);

-- Triggers to keep FTS5 mirrors in sync with base tables

CREATE TRIGGER IF NOT EXISTS projects_fts_insert AFTER INSERT ON projects BEGIN
    INSERT INTO projects_fts(rowid, name, role, primary_language)
    VALUES (new.rowid, new.name, new.role, new.primary_language);
END;

CREATE TRIGGER IF NOT EXISTS projects_fts_delete AFTER DELETE ON projects BEGIN
    INSERT INTO projects_fts(projects_fts, rowid, name, role, primary_language)
    VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
END;

CREATE TRIGGER IF NOT EXISTS projects_fts_update AFTER UPDATE ON projects BEGIN
    INSERT INTO projects_fts(projects_fts, rowid, name, role, primary_language)
    VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
    INSERT INTO projects_fts(rowid, name, role, primary_language)
    VALUES (new.rowid, new.name, new.role, new.primary_language);
END;

CREATE TRIGGER IF NOT EXISTS apps_fts_insert AFTER INSERT ON apps BEGIN
    INSERT INTO apps_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;

CREATE TRIGGER IF NOT EXISTS apps_fts_delete AFTER DELETE ON apps BEGIN
    INSERT INTO apps_fts(apps_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
END;

CREATE TRIGGER IF NOT EXISTS apps_fts_update AFTER UPDATE ON apps BEGIN
    INSERT INTO apps_fts(apps_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
    INSERT INTO apps_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;

CREATE TRIGGER IF NOT EXISTS services_fts_insert AFTER INSERT ON services BEGIN
    INSERT INTO services_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;

CREATE TRIGGER IF NOT EXISTS services_fts_delete AFTER DELETE ON services BEGIN
    INSERT INTO services_fts(services_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
END;

CREATE TRIGGER IF NOT EXISTS services_fts_update AFTER UPDATE ON services BEGIN
    INSERT INTO services_fts(services_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
    INSERT INTO services_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;

-- ---------------------------------------------------------------------------
-- B8: briefs / plans / dependencies -- project management tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace    TEXT NOT NULL,        -- FK -> workspaces.name
    name         TEXT NOT NULL,        -- unique bare name within workspace (e.g. 'paths-and-identity-foundations')
    status       TEXT NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft', 'open', 'in-progress', 'closed', 'archived')),
    surface_type TEXT,                 -- 'cli', 'api', 'infra', etc. (from frontmatter)
    title        TEXT,                 -- human title (# heading)
    objective    TEXT,                 -- ## Objective section
    context      TEXT,                 -- ## Context section
    approach     TEXT,                 -- ## Approach section
    out_of_scope TEXT,                 -- ## Out of Scope section
    topic_key    TEXT,                 -- optional dimension key
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_briefs_workspace ON briefs(workspace);
CREATE INDEX IF NOT EXISTS idx_briefs_status ON briefs(status);
CREATE INDEX IF NOT EXISTS idx_briefs_topic_key ON briefs(topic_key);

CREATE TABLE IF NOT EXISTS acceptance_criteria (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id       INTEGER NOT NULL,
    ac_id          TEXT NOT NULL,
    description    TEXT,
    evidence_type  TEXT,
    evidence_shape TEXT,
    artifact_path  TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'done', 'blocked', 'descoped')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);
-- 'descoped' (v21) is a HARD-TERMINAL status: an AC deliberately removed from
-- scope. Unlike a task's reopenable 'skipped', there is NO transition OUT of
-- 'descoped' (see gaia.state.transitions.AC_LIFECYCLE_TRANSITIONS). It is part
-- of the TERMINAL set {done, descoped} that verify_brief treats as resolved.

CREATE INDEX IF NOT EXISTS idx_acceptance_criteria_brief ON acceptance_criteria(brief_id);

CREATE TABLE IF NOT EXISTS milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id    INTEGER NOT NULL,
    order_num   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'done', 'blocked')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_milestones_brief ON milestones(brief_id);

CREATE TABLE IF NOT EXISTS brief_dependencies (
    brief_id          INTEGER NOT NULL,
    depends_on_id     INTEGER NOT NULL,
    PRIMARY KEY (brief_id, depends_on_id),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id   INTEGER NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft', 'active', 'closed')),
    content    TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER NOT NULL,
    order_num     INTEGER NOT NULL,
    goal          TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'done', 'skipped')),
    evidence_path TEXT,
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(plan_id);

-- ---------------------------------------------------------------------------
-- evidence (three-tier storage model)
-- ---------------------------------------------------------------------------
-- Per-AC evidence rows. Two storage modes:
--   inline: text IS NOT NULL, artifact_path IS NULL (payload <= 4096 bytes)
--   blob:   text IS NULL, artifact_path IS NOT NULL (payload stored in FS)
-- type CHECK enforces the evidence taxonomy. brief_id CASCADE cleans up rows.

CREATE TABLE IF NOT EXISTS evidence (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id         INTEGER NOT NULL,
    ac_id            TEXT NOT NULL,
    task_id          TEXT,
    type             TEXT NOT NULL CHECK (type IN ('text', 'file', 'command_output', 'url', 'screenshot')),
    text             TEXT,
    artifact_path    TEXT,
    size_bytes       INTEGER,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_by_agent TEXT,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_evidence_brief ON evidence(brief_id);
CREATE INDEX IF NOT EXISTS idx_evidence_ac ON evidence(brief_id, ac_id);

-- ---------------------------------------------------------------------------
-- FTS5 mirror for briefs (objective / context / approach)
-- ---------------------------------------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS briefs_fts USING fts5(
    objective,
    context,
    approach,
    content='briefs',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS briefs_ai AFTER INSERT ON briefs BEGIN
    INSERT INTO briefs_fts(rowid, objective, context, approach)
    VALUES (new.id, new.objective, new.context, new.approach);
END;

CREATE TRIGGER IF NOT EXISTS briefs_ad AFTER DELETE ON briefs BEGIN
    INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
    VALUES ('delete', old.id, old.objective, old.context, old.approach);
END;

CREATE TRIGGER IF NOT EXISTS briefs_au AFTER UPDATE ON briefs BEGIN
    INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
    VALUES ('delete', old.id, old.objective, old.context, old.approach);
    INSERT INTO briefs_fts(rowid, objective, context, approach)
    VALUES (new.id, new.objective, new.context, new.approach);
END;

-- ===========================================================================
-- === Local data migration tables (added 2026-05-05) ===
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- episodes: episodic memory entries (one row per agent turn / task outcome)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episodes (
    episode_id            TEXT NOT NULL PRIMARY KEY,
    workspace             TEXT NOT NULL,              -- FK -> workspaces.name
    timestamp             TEXT NOT NULL,
    session_id            TEXT,
    task_id               TEXT,
    agent                 TEXT,
    type                  TEXT,
    title                 TEXT,
    prompt                TEXT,
    enriched_prompt       TEXT,
    wf_prompt             TEXT,
    clarifications        TEXT,
    keywords              TEXT,
    tags                  TEXT,
    commands_executed     TEXT,
    context_metrics       TEXT,
    relevance_score       REAL,
    outcome               TEXT,
    duration_seconds      REAL,
    exit_code             INTEGER,
    plan_status           TEXT,
    output_length         INTEGER,
    output_tokens_approx  INTEGER,
    tier                  TEXT,                         -- security tier (T0/T1/T2/T3); v10 addition
    CHECK (plan_status IS NULL OR plan_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_episodes_workspace_timestamp ON episodes(workspace, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
-- idx_episodes_tier and idx_episodes_tier_outcome are created by the migration on
-- existing DBs (v9_to_v10.sql) and by the fresh-install variant (v9_to_v10_fresh.sql)
-- on clean installs. They cannot be declared here because schema.sql runs before
-- migrations, and existing DBs do not yet have the tier column at that point.

CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    episode_id UNINDEXED,
    prompt,
    enriched_prompt,
    tags,
    title,
    content='episodes',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
END;

CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
END;

CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
    INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
END;

-- ---------------------------------------------------------------------------
-- episode_anomalies: structured anomaly records extracted from episodes
-- (v10 addition: episodic-workflow-to-db AC-3)
-- ---------------------------------------------------------------------------
-- Each row is one anomaly extracted from an episode's context_metrics blob.
-- Provides efficient type-filtered, time-windowed, and workspace-scoped
-- anomaly queries without full-table JSON parsing.
-- The payload column preserves the full original JSON for forward compat.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episode_anomalies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  TEXT NOT NULL,              -- FK -> episodes.episode_id
    workspace   TEXT NOT NULL,              -- denormalized for partition queries without JOIN
    timestamp   TEXT NOT NULL,              -- denormalized from parent episode for time-range queries
    type        TEXT NOT NULL,              -- e.g. "investigation_skip", "no_tool_use"
    severity    TEXT,                       -- e.g. "warning", "error", "info"
    message     TEXT,                       -- human-readable description
    payload     TEXT,                       -- full JSON object (forward-compat for extra keys)
    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_episode_anomalies_type      ON episode_anomalies(type);
CREATE INDEX IF NOT EXISTS idx_episode_anomalies_workspace  ON episode_anomalies(workspace, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_episode_anomalies_episode    ON episode_anomalies(episode_id);

-- ---------------------------------------------------------------------------
-- memory: curated memory documents (project_*, user_*, feedback_* markdown notes)
-- Note: name prefix "project_" is a memory category name, unrelated to projects table.
-- ---------------------------------------------------------------------------
--
-- Schema v4 (added 2026-05-22): two new nullable columns plus the memory_links
-- table for graph primitives.
-- Schema v11 (2026-05-26): memory.class promoted to NOT NULL with CHECK
--   constraint. All pre-v4 NULL rows were reclassified by task #2 before
--   the v10->v11 migration ran the table rebuild. Writer-side enforcement
--   remains but DDL now also enforces the invariant.
--
--   class   -- semantic role of the memory document. NOT NULL since v11.
--              Allowed values: 'anchor', 'thread', 'log'.
--   status  -- lifecycle marker for class=thread rows ({open,carry_forward,
--              graduated,closed}). NULL for class=anchor/log rows.
--
CREATE TABLE IF NOT EXISTS memory (
    workspace         TEXT NOT NULL,  -- FK -> workspaces.name
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative')),
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    class             TEXT NOT NULL DEFAULT 'log' CHECK (class IN ('anchor', 'thread', 'log')),  -- v4/v11
    status            TEXT,  -- v4: lifecycle for class=thread (open|carry_forward|graduated|closed)
    project_ref       TEXT,  -- remote-stable project anchor for project-scoped memory (projects.project_identity); NULL until populated. Column added v25 (scan-v2 SV1); populated/used in SV3.
    deleted_at        TEXT,  -- tombstone marker (scan-v2 SV3). NULL = live row; non-NULL ISO8601 = soft-deleted. delete_memory() sets this instead of DELETE so the row + body survive; hard DELETE is reserved for explicit human curation (delete_memory(hard=True)). All read paths filter `deleted_at IS NULL`. Column added v26.
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_workspace ON memory(workspace);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(type);
-- Note: idx_memory_class_status is NOT declared here. It is created by
-- scripts/migrations/v3_to_v4.sql after the columns exist on the live DB.
-- Declaring it here would parse-fail on v3 DBs during the schema.sql replay
-- because the index references columns that schema.sql declares but
-- `CREATE TABLE IF NOT EXISTS` does not add to pre-existing tables.

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    workspace UNINDEXED,
    name UNINDEXED,
    description,
    body,
    content='memory',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;

CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
END;

CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;

-- ---------------------------------------------------------------------------
-- memory_links (v4): graph primitives between curated memory rows.
-- kind enum enforced via CHECK because it is a fresh table -- no rebuild risk.
--   relates_to     -- general association
--   supersedes     -- src replaces dst; injector excludes rows that are
--                     dst of an active supersedes edge
--   derived_from   -- src is a refinement / instance of dst
--   graduated_to   -- thread row graduated into an anchor row
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_links (
    workspace  TEXT NOT NULL,  -- FK -> workspaces.name
    src_name   TEXT NOT NULL,
    dst_name   TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('relates_to', 'supersedes', 'derived_from', 'graduated_to')),
    created_at TEXT,
    PRIMARY KEY (workspace, src_name, dst_name, kind),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS memory_links_src ON memory_links(workspace, src_name);
CREATE INDEX IF NOT EXISTS idx_memory_links_dst_kind ON memory_links(workspace, dst_name, kind);

-- ---------------------------------------------------------------------------
-- memory_history: provenance / version audit trail for `memory` rows
-- (scan-v2 SV3). trg_memory_history fires AFTER UPDATE on `memory` to capture
-- before/after of the columns that carry data or lineage -- the same pattern
-- as trg_pcc_history / trg_project_history above, applied to `memory`.
--
-- This single trigger blinds three memory-loss vectors at the SQL layer, so
-- no code path can bypass it:
--   * archive-on-upsert: upsert_memory()'s ON CONFLICT DO UPDATE fires this
--     trigger, so the PREVIOUS body is archived under before_body before it is
--     overwritten -- the body is never lost, every version is recoverable.
--   * tombstone-on-delete: delete_memory() soft-deletes by setting deleted_at;
--     that UPDATE lands a history row (before_deleted_at NULL -> after non-NULL).
--   * relocate origin trace: relocate_memory() re-keys workspace; the trigger
--     records before_workspace -> after_workspace, preserving the move origin.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace          TEXT NOT NULL,  -- FK -> workspaces.name (current workspace at time of change)
    name               TEXT NOT NULL,  -- memory slug (current name at time of change)
    before_workspace   TEXT,
    after_workspace    TEXT,
    before_body        TEXT,
    after_body         TEXT,
    before_type        TEXT,
    after_type         TEXT,
    before_description TEXT,
    after_description  TEXT,
    before_status      TEXT,
    after_status       TEXT,
    before_deleted_at  TEXT,
    after_deleted_at   TEXT,
    changed_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    changed_by_agent   TEXT,  -- optional: GAIA_DISPATCH_AGENT at write time (NULL when trigger-populated)
    FOREIGN KEY (workspace) REFERENCES workspaces(name)
);

CREATE INDEX IF NOT EXISTS idx_memory_history_workspace_name ON memory_history(workspace, name);

-- trg_memory_history: fires AFTER UPDATE on `memory` whenever body, workspace,
-- type, description, status, or deleted_at changes. Uses `IS NOT` (not `!=`)
-- so a transition to/from NULL (e.g. deleted_at NULL -> timestamp on tombstone,
-- or description cleared) is still detected -- SQL `!=` against NULL is NULL
-- (falsy) and would silently miss it. Runs independently of the memory_au
-- trigger (that one only re-indexes memory_fts and is unaffected by this
-- trigger's columns).
CREATE TRIGGER IF NOT EXISTS trg_memory_history
AFTER UPDATE ON memory
WHEN OLD.body IS NOT NEW.body
   OR OLD.workspace IS NOT NEW.workspace
   OR OLD.type IS NOT NEW.type
   OR OLD.description IS NOT NEW.description
   OR OLD.status IS NOT NEW.status
   OR OLD.deleted_at IS NOT NEW.deleted_at
BEGIN
    INSERT INTO memory_history (
        workspace, name,
        before_workspace, after_workspace,
        before_body, after_body,
        before_type, after_type,
        before_description, after_description,
        before_status, after_status,
        before_deleted_at, after_deleted_at,
        changed_at
    ) VALUES (
        NEW.workspace, NEW.name,
        OLD.workspace, NEW.workspace,
        OLD.body, NEW.body,
        OLD.type, NEW.type,
        OLD.description, NEW.description,
        OLD.status, NEW.status,
        OLD.deleted_at, NEW.deleted_at,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;

-- ---------------------------------------------------------------------------
-- project_context_contracts: project-context.json reconstructed as (workspace, contract) rows
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_context_contracts (
    workspace     TEXT NOT NULL,  -- FK -> workspaces.name
    contract_name TEXT NOT NULL,
    payload       TEXT NOT NULL,
    metadata      TEXT,
    updated_at    TEXT,
    PRIMARY KEY (workspace, contract_name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_project_context_contracts_workspace ON project_context_contracts(workspace);

-- ---------------------------------------------------------------------------
-- agent_contract_permissions: per-contract per-agent read/write authorization
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_contract_permissions (
    agent_name    TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    can_read      INTEGER NOT NULL DEFAULT 0,
    can_write     INTEGER NOT NULL DEFAULT 0,
    cloud_scope   TEXT,             -- NULL = all providers; 'gcp', 'aws', etc. for overlays
    PRIMARY KEY (agent_name, contract_name, cloud_scope)
);

CREATE INDEX IF NOT EXISTS idx_agent_contract_perms_agent ON agent_contract_permissions(agent_name);

-- ---------------------------------------------------------------------------
-- surface_routing: intent-to-agent routing table.
-- Source of truth is each agent's `routing:` frontmatter block; seeded at
-- install time by tools/scan/seed_surface_routing.py (mirror of
-- seed_contract_permissions.py). The matcher tools/context/surface_router.py
-- reads this table instead of the retired config/surface-routing.json.
-- One row per surface. The *_json columns hold JSON-encoded arrays.
-- contract_sections mirrors the surface's primary agent's
-- project_context_contracts.read (single source of truth); sub_surfaces is
-- NULL except where a surface splits by sub-surface owner (e.g. planning_specs
-- -> brief owned by the orchestrator via the brief-spec skill, plan owned by
-- gaia-planner).
-- keywords_json is DEPRECATED: the matcher scores surfaces from commands_json
-- and artifacts_json only. No agent frontmatter declares `keywords` anymore.
-- The column is kept (not dropped) for backward compatibility with any
-- un-migrated install; its DEFAULT '[]' means a routing block that omits
-- `keywords` seeds cleanly with no crash.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS surface_routing (
    surface                TEXT NOT NULL PRIMARY KEY,
    primary_agent          TEXT NOT NULL,
    adjacent_surfaces_json TEXT NOT NULL DEFAULT '[]',
    contract_sections_json TEXT NOT NULL DEFAULT '[]',
    required_checks_json   TEXT NOT NULL DEFAULT '[]',
    keywords_json          TEXT NOT NULL DEFAULT '[]',  -- deprecated; unused by the matcher
    commands_json          TEXT NOT NULL DEFAULT '[]',
    artifacts_json         TEXT NOT NULL DEFAULT '[]',
    sub_surfaces_json      TEXT
);

-- ---------------------------------------------------------------------------
-- harness_events: append-only mirror of events.jsonl
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace TEXT,             -- workspace name; NULL for global events
    ts        TEXT NOT NULL,
    type      TEXT NOT NULL,
    source    TEXT,
    agent     TEXT,
    result    TEXT,
    severity  TEXT,
    payload   TEXT
);

CREATE INDEX IF NOT EXISTS idx_harness_events_workspace_ts ON harness_events(workspace, ts DESC);
CREATE INDEX IF NOT EXISTS idx_harness_events_type ON harness_events(type);

-- ---------------------------------------------------------------------------
-- task_notifications: reports a headless scheduled task leaves for the user.
-- ---------------------------------------------------------------------------
-- A headless scheduled task (see the scheduled-task skill) runs unattended and
-- cannot ask the user anything mid-run. When it finishes it writes ONE row here
-- with a generic, PII-free summary of what it did plus any approval_ids it had
-- to accumulate. The row carries the resumable Claude session_id so the user
-- can `claude --resume <session_id>` on demand to grant the pending T3s.
--
-- Distinct from harness_events (append-only audit mirror, no mutable state):
-- these rows carry a MUTABLE `unread` flag that `gaia notifications ack` clears,
-- because the whole point is a lightweight unread inbox surfaced at SessionStart
-- and as a per-prompt counter. Not curated memory, so -- like harness_events --
-- it is written without an agent_permissions gate.
CREATE TABLE IF NOT EXISTS task_notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace  TEXT,                      -- workspace name; NULL for global
    task_name  TEXT NOT NULL,             -- name of the scheduled task that reported
    headline   TEXT NOT NULL,             -- short one-line summary (the title)
    body       TEXT,                      -- full detail message (generic, no PII)
    session_id TEXT,                      -- resumable Claude session id (claude --resume)
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unread     INTEGER NOT NULL DEFAULT 1, -- 1 = not yet acknowledged (BOOLEAN)
    acked_at   TEXT                       -- ISO8601 when marked seen; NULL while unread
);

CREATE INDEX IF NOT EXISTS idx_task_notifications_unread ON task_notifications(unread, created_at DESC);

-- ---------------------------------------------------------------------------
-- scheduled_tasks: OS-agnostic DESIRED STATE for recurring headless tasks.
-- ---------------------------------------------------------------------------
-- The desired-state registry that lets a scheduled task stop living only in one
-- machine's crontab and instead live in gaia.db, so any machine sharing the DB
-- can materialize it. The SCHEDULE is stored NEUTRAL as a JSON `schedule_spec`
-- (a tagged union: {"kind":"calendar", minute/hour/day_of_month/month/
-- day_of_week} or {"kind":"interval","every_seconds":N}) -- NOT a raw cron
-- string -- so a per-platform backend (cron today; launchd/schtasks later) can
-- translate it to its native form. `schedule_hint` is a human-readable render
-- (e.g. "07:30 L-V"), derived, never authoritative.
--
-- `prompt_body` is the CANONICAL prompt content (portable across machines on a
-- shared DB); `prompt_path` is the machine-local file a sync materializes it to.
-- `project_dir` is machine-local (a path that may differ per machine). Writing
-- desired state (register/enable/disable) is reversible local bookkeeping (T0,
-- like briefs/plans/task_notifications); only MATERIALIZING it into the machine
-- scheduler (`gaia schedule sync`) is a consented mutation (T3). The hook only
-- DETECTS drift at SessionStart; it never writes the scheduler in silence.
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace     TEXT,                      -- workspace name; NULL for global
    name          TEXT NOT NULL,             -- stable task name (unique per workspace)
    schedule_spec TEXT NOT NULL,             -- NEUTRAL schedule as JSON (calendar|interval)
    schedule_hint TEXT,                       -- human render of the schedule (derived)
    prompt_body   TEXT,                       -- canonical prompt content (portable)
    prompt_path   TEXT,                       -- machine-local file the prompt materializes to
    project_dir   TEXT,                       -- cwd for the wrapper (machine-local)
    wrapper_kind  TEXT DEFAULT 'headless-claude', -- which wrapper template to generate
    enabled       INTEGER NOT NULL DEFAULT 1, -- 1 = should be installed; 0 = disabled (BOOLEAN)
    machine_scope TEXT NOT NULL DEFAULT 'all', -- 'all' | 'named' (see scheduled_task_machines)
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (workspace, name)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_workspace ON scheduled_tasks(workspace, enabled);

-- Machine scoping (only populated when scheduled_tasks.machine_scope = 'named').
-- machine_name matches machines.name (= platform.node()); no hard FK to machines
-- because a task may target a machine the scanner has not indexed yet.
CREATE TABLE IF NOT EXISTS scheduled_task_machines (
    task_id      INTEGER NOT NULL,           -- FK -> scheduled_tasks.id
    machine_name TEXT NOT NULL,              -- target machine hostname (= platform.node())
    PRIMARY KEY (task_id, machine_name),
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);

-- Per-machine MATERIALIZATION state. The crontab is local to each machine, so
-- whether a desired task is actually installed is tracked per (task, machine).
-- `gaia schedule sync` writes this on a successful install; `status` and the
-- SessionStart reconciliation block read it.
CREATE TABLE IF NOT EXISTS scheduled_task_state (
    task_id        INTEGER NOT NULL,         -- FK -> scheduled_tasks.id
    machine_name   TEXT NOT NULL,            -- machine this state is for (= platform.node())
    backend        TEXT,                     -- 'cron' | 'launchd' | 'schtasks'
    installed      INTEGER NOT NULL DEFAULT 0, -- 1 = materialized in the scheduler (BOOLEAN)
    last_synced_at TEXT,                      -- ISO8601 of the last successful sync
    PRIMARY KEY (task_id, machine_name),
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- approval_grants: DB-backed store for command_set approval grants (v7 / M3)
-- Replaces the filesystem JSON store (.claude/cache/approvals/).
-- Per D5/D10: no TTL column (enforced at query time via created_at + 10 min);
-- byte-for-byte command match per command_set item; each item is single-use.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_grants (
    approval_id          TEXT PRIMARY KEY,           -- nonce, e.g. 32-char hex
    agent_id             TEXT,                       -- agent that initiated the request
    session_id           TEXT,                       -- CLAUDE_SESSION_ID at grant time
    command_set_json     TEXT NOT NULL,              -- JSON array of {command, rationale}
    scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',  -- grant scope type
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at           TEXT,                       -- ISO8601 or NULL (TTL enforced at query time)
    status               TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|CONSUMED|REVOKED|EXPIRED
    consumed_indexes_json TEXT,                      -- JSON array of consumed command_set indexes
    consumed_at          TEXT,                       -- ISO8601 when all items consumed
    revoked_at           TEXT,                       -- ISO8601 when explicitly revoked
    multi_use            INTEGER NOT NULL DEFAULT 0, -- 1 = multi-use grant, 0 = single-use (BOOLEAN)
    confirmed            INTEGER NOT NULL DEFAULT 0  -- 1 = grant confirmed by user, 0 = pending (BOOLEAN)
);

CREATE INDEX IF NOT EXISTS idx_approval_grants_agent   ON approval_grants(agent_id);
CREATE INDEX IF NOT EXISTS idx_approval_grants_session ON approval_grants(session_id);
CREATE INDEX IF NOT EXISTS idx_approval_grants_status  ON approval_grants(status);

-- ---------------------------------------------------------------------------
-- agent_contract_handoffs: persisted SubagentStop contract envelopes (v9/M4)
-- Each row captures one agent session's closing contract envelope.
-- brief_id is NULLABLE -- agents without a brief context still produce a row.
-- EXTENSION_POINT: state-machine-completion can query WHERE brief_id=N.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_contract_handoffs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id      TEXT,                        -- v28/T7: CLI-minted contract/draft id
                     -- (gaia.contract.drafts.mint_draft_id, "{agent_id}.{token}").
                     -- THE idempotency key: gaia.store.writer.finalize_agent_contract_handoff
                     -- INSERTs ... ON CONFLICT(contract_id) DO NOTHING, so the first writer
                     -- to commit for a given contract_id establishes the row and every
                     -- subsequent write for the SAME contract_id (a retried finalize, or
                     -- -- T9 -- a racing hook backstop) is a genuine no-op. NULLABLE: legacy
                     -- rows written before T7 (and any writer that does not carry a contract
                     -- id) have no value here and are exempt from the uniqueness constraint
                     -- (SQLite's UNIQUE index permits any number of NULLs).
    agent_id         TEXT NOT NULL,               -- e.g. "a1b2c3d4e5"
    session_id       TEXT,                        -- CLAUDE_SESSION_ID at SubagentStop time
    workspace        TEXT NOT NULL,               -- FK -> workspaces.name
    brief_id         INTEGER,                     -- NULLABLE FK -> briefs.id; EXTENSION_POINT
    task_status      TEXT NOT NULL               -- resolved plan_status from contract envelope
                     -- v22: CHECK mirrors the episodes.plan_status enum (the
                     -- canonical plan_status values -- see agent-protocol
                     -- SKILL.md and handoff_persister.py, which writes
                     -- envelope["agent_status"]["plan_status"] verbatim here).
                     CHECK (task_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT')),
    raw_handoff_json TEXT NOT NULL,               -- full contract envelope serialized
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name),
    FOREIGN KEY (brief_id)  REFERENCES briefs(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
-- v28/T7: UNIQUE (not just an index) is what makes ON CONFLICT(contract_id) a
-- real constraint-backed idempotent UPSERT rather than an application-level
-- convention -- see the contract_id column comment above.
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);

-- ---------------------------------------------------------------------------
-- agent_contract_handoff_approvals: approval decisions linked to handoffs (v9/M4)
-- CASCADE-deletes when the parent handoff row is removed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_contract_handoff_approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id  INTEGER NOT NULL,                -- FK -> agent_contract_handoffs.id
    approval_id TEXT NOT NULL,                   -- FK -> approval_grants.approval_id
    decision    TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'EXPIRED', 'REVOKED')),
    decided_at  TEXT NOT NULL,
    FOREIGN KEY (handoff_id)  REFERENCES agent_contract_handoffs(id) ON DELETE CASCADE,
    FOREIGN KEY (approval_id) REFERENCES approval_grants(approval_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_contract_handoff_approvals_handoff ON agent_contract_handoff_approvals(handoff_id);

-- ---------------------------------------------------------------------------
-- project_context_contracts_history: audit trail for PCC mutations (v9/M4)
-- trg_pcc_history fires AFTER UPDATE on project_context_contracts to capture
-- before/after payloads at the SQL layer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_context_contracts_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_key        TEXT NOT NULL,            -- stores project_context_contracts.contract_name value
    workspace           TEXT NOT NULL,            -- FK -> workspaces.name
    before_payload_json TEXT,                     -- NULL on first insert (no prior value)
    after_payload_json  TEXT NOT NULL,            -- new payload value
    changed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    changed_by_agent    TEXT,                     -- optional: GAIA_DISPATCH_AGENT at write time
    FOREIGN KEY (workspace) REFERENCES workspaces(name)
);

CREATE INDEX IF NOT EXISTS idx_pcc_history_contract ON project_context_contracts_history(contract_key);

-- trg_pcc_history: fires AFTER UPDATE on project_context_contracts to capture
-- before/after payloads at the SQL layer.
-- Fixed in v11: OLD.contract_key -> OLD.contract_name (PCC PK column name),
--               OLD/NEW.payload_json -> OLD/NEW.payload (PCC payload column name).
CREATE TRIGGER IF NOT EXISTS trg_pcc_history
AFTER UPDATE ON project_context_contracts
BEGIN
    INSERT INTO project_context_contracts_history (
        contract_key, workspace, before_payload_json, after_payload_json, changed_at
    ) VALUES (
        OLD.contract_name,
        OLD.workspace,
        OLD.payload,
        NEW.payload,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;

-- ---------------------------------------------------------------------------
-- approvals: durable approval lifecycle records (v12 / approval-model-redesign)
-- One row per approval request. Survives session close; queryable cross-session.
-- id carries a P-{uuid4} prefix so it is readable in denial messages and logs.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY,           -- P-{uuid4} prefixed identifier
    agent_id     TEXT,                       -- agent that initiated the request
    session_id   TEXT,                       -- CLAUDE_SESSION_ID at request time
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected', 'revoked', 'expired')),
    fingerprint  TEXT,                       -- SHA-256 hex of canonical sealed_payload_json
    payload_json TEXT,                       -- canonical-JSON sealed_payload at REQUESTED time
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_at   TEXT                        -- ISO-8601 UTC when approved/rejected/revoked
);

CREATE INDEX IF NOT EXISTS idx_approvals_status     ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_agent      ON approvals(agent_id);
CREATE INDEX IF NOT EXISTS idx_approvals_session    ON approvals(session_id);

-- ---------------------------------------------------------------------------
-- approval_events: append-only hash-chained audit log (v12 / approval-model-redesign)
-- Column inventory from plan D15. this_hash is computed by the AFTER INSERT
-- trigger ai_approval_events_hash via the gaia_sha256 scalar function registered
-- at connection time in gaia.store.writer._connect().
-- prev_hash IS NULL for the genesis row (row 0 in the chain per approval).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id   TEXT NOT NULL,                     -- FK -> approvals.id
    event_type    TEXT NOT NULL CHECK (event_type IN (
                      'REQUESTED',
                      'SHOWN',
                      'APPROVED',
                      'REJECTED',
                      'EXECUTED',
                      'FAILED',
                      'NOOP',
                      'REVOKED',
                      'REVERTED'
                  )),
    agent_id      TEXT,
    session_id    TEXT,
    payload_json  TEXT,
    fingerprint   TEXT,
    prev_hash     TEXT,                              -- NULL for genesis row
    this_hash     TEXT,                              -- computed by trigger
    metadata_json TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (approval_id) REFERENCES approvals(id)
);

CREATE INDEX IF NOT EXISTS idx_approval_events_approval  ON approval_events(approval_id, id);
CREATE INDEX IF NOT EXISTS idx_approval_events_type      ON approval_events(event_type);
CREATE INDEX IF NOT EXISTS idx_approval_events_session   ON approval_events(session_id);

-- AFTER INSERT trigger: named placeholder for schema introspection consistency.
-- this_hash is computed by the application layer (gaia.approvals.chain.insert_event)
-- before each INSERT; the trigger is a no-op SELECT that exists so that `gaia doctor`
-- can assert all three expected triggers are present.
-- Note: a real AFTER INSERT + UPDATE-on-same-row conflicts with the BEFORE UPDATE
-- immutability trigger in SQLite; application-layer computation resolves this.
CREATE TRIGGER IF NOT EXISTS ai_approval_events_hash
AFTER INSERT ON approval_events
BEGIN
    SELECT 1;
END;

-- BEFORE UPDATE trigger: enforce append-only invariant.
CREATE TRIGGER IF NOT EXISTS bu_approval_events_immutable
BEFORE UPDATE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval_events is append-only');
END;

-- BEFORE DELETE trigger: enforce append-only invariant.
CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
BEFORE DELETE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval_events is append-only');
END;

-- BEFORE UPDATE trigger: enforce that every approvals.status transition has a
-- preceding event in the append-only approval_events chain (Task B audit-
-- immutability gap closure).
--
-- Fires when status changes TO one of the three user-visible terminal statuses
-- (approved / rejected / revoked). For each new status it checks that an event
-- row with the matching event_type exists for this approval_id. Because the
-- canonical write path (store.transition) inserts the event FIRST and then
-- UPDATEs status, the event row is already in the transaction-visible table by
-- the time this trigger fires -- and the check passes. A direct UPDATE that
-- bypasses the write path (no preceding insert_event call) will find COUNT=0
-- and RAISE(ABORT), rolling back the update.
--
-- 'expired' is intentionally excluded: it is a cleanup-layer status (TTL
-- sweep) with no corresponding event_type in the approval_events schema. All
-- other status values ('pending') are only ever written by INSERT in
-- insert_requested(), not by UPDATE, so they are not reachable here.
CREATE TRIGGER IF NOT EXISTS bu_approvals_status_has_event
BEFORE UPDATE OF status ON approvals
WHEN NEW.status != OLD.status AND NEW.status IN ('approved', 'rejected', 'revoked')
BEGIN
    SELECT CASE
        WHEN (
            SELECT COUNT(*) FROM approval_events
             WHERE approval_id = NEW.id
               AND event_type = CASE NEW.status
                                    WHEN 'approved' THEN 'APPROVED'
                                    WHEN 'rejected' THEN 'REJECTED'
                                    WHEN 'revoked'  THEN 'REVOKED'
                                END
        ) = 0
        THEN RAISE(ABORT, 'approvals: status change requires a preceding event in approval_events')
    END;
END;

-- ---------------------------------------------------------------------------
-- project_history: provenance/lineage audit trail for `projects` rows
-- (scan-v2 SV1). trg_project_history fires AFTER UPDATE on `projects` to
-- capture before/after path/workspace/name/status at the SQL layer -- the
-- same pattern as trg_pcc_history / project_context_contracts_history above,
-- applied to `projects` instead of `project_context_contracts`.
--
-- This gives a connected timeline for both move (path/workspace/name change)
-- and soft-delete (status -> 'missing'): every mutation that scan-v2 cares
-- about lands here without the scanner needing to write history explicitly.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace         TEXT NOT NULL,  -- FK -> workspaces.name (current workspace at time of change)
    name              TEXT NOT NULL,  -- FK -> projects.name within that workspace (current name at time of change)
    before_path       TEXT,
    after_path        TEXT,
    before_workspace  TEXT,
    after_workspace   TEXT,
    before_name       TEXT,
    after_name        TEXT,
    before_status     TEXT,
    after_status      TEXT,
    changed_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name)
);

CREATE INDEX IF NOT EXISTS idx_project_history_workspace_name ON project_history(workspace, name);

-- trg_project_history: fires AFTER UPDATE on `projects` whenever path,
-- workspace, name, or status changes (move or soft-delete). Uses `IS NOT`
-- (not `!=`) so a transition to/from NULL (e.g. path cleared) is still
-- detected -- SQL `!=` against NULL is NULL (falsy) and would silently miss
-- it. Runs independently of the projects_fts_update trigger (that one only
-- re-indexes name/role/primary_language into projects_fts and is unaffected
-- by this trigger's columns).
CREATE TRIGGER IF NOT EXISTS trg_project_history
AFTER UPDATE ON projects
WHEN OLD.path IS NOT NEW.path
   OR OLD.workspace IS NOT NEW.workspace
   OR OLD.name IS NOT NEW.name
   OR OLD.status IS NOT NEW.status
BEGIN
    INSERT INTO project_history (
        workspace, name,
        before_path, after_path,
        before_workspace, after_workspace,
        before_name, after_name,
        before_status, after_status,
        changed_at
    ) VALUES (
        NEW.workspace, NEW.name,
        OLD.path, NEW.path,
        OLD.workspace, NEW.workspace,
        OLD.name, NEW.name,
        OLD.status, NEW.status,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;

-- ---------------------------------------------------------------------------
-- schema_version: migration ledger.
-- One row per applied schema migration; the highest version is the current
-- live schema. `gaia doctor` reads MAX(version) and compares against the
-- EXPECTED_SCHEMA_VERSION constant baked into the CLI for the running build.
-- Bootstrap inserts row (1, ..., 'initial schema') -- future schema bumps
-- must add their own INSERT OR IGNORE in bootstrap_database.sh.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
