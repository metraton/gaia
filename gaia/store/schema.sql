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
--   -- agent-owned:   written by domain agents (developer, terraform-architect, etc.)

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- workspaces: organizational containers (formerly `projects` in v1 schema).
-- A workspace may contain zero or more git-bearing projects.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspaces (
    name        TEXT NOT NULL PRIMARY KEY,  -- workspace name (canonical: host/owner/repo or directory basename)
    identity    TEXT,                       -- identity: for git-bearing workspace = git remote URL normalized lowercase; for organizational workspace = name; scanner-owned
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))  -- scanner-owned
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
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace);
CREATE INDEX IF NOT EXISTS idx_projects_topic_key ON projects(topic_key);

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
    agent_name  TEXT NOT NULL,   -- agent identifier (e.g. 'developer', 'terraform-architect')
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
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_acceptance_criteria_brief ON acceptance_criteria(brief_id);

CREATE TABLE IF NOT EXISTS milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id    INTEGER NOT NULL,
    order_num   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
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
    CHECK (plan_status IS NULL OR plan_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_episodes_workspace_timestamp ON episodes(workspace, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);

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
-- memory: curated memory documents (project_*, user_*, feedback_* markdown notes)
-- Note: name prefix "project_" is a memory category name, unrelated to projects table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory (
    workspace         TEXT NOT NULL,  -- FK -> workspaces.name
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback')),
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_workspace ON memory(workspace);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(type);

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
-- context_contracts: project-context.json reconstructed as (workspace, section) rows
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_contracts (
    workspace    TEXT NOT NULL,  -- FK -> workspaces.name
    section_name TEXT NOT NULL,
    payload      TEXT NOT NULL,
    metadata     TEXT,
    updated_at   TEXT,
    PRIMARY KEY (workspace, section_name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_context_contracts_workspace ON context_contracts(workspace);

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
