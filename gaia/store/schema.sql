-- Gaia SQLite substrate schema
-- Version: 1.0 (B1 - workspace-container-in-context-schema)
--
-- Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
-- No runtime dependency on engram; patterns lifted with attribution (see NOTICE.md).
--
-- All tables segmented by `project` (workspace identity, canonical form: host/owner/repo).
-- `projects.name` is the FK target for all child tables.
-- ON DELETE CASCADE propagates workspace deletion to all child rows (reconciliation pattern).
--
-- Ownership annotations per column:
--   -- scanner-owned: written by the reconciler/scanner on each scan cycle
--   -- agent-owned:   written by domain agents (developer, terraform-architect, etc.)
--
-- Live-state fields (retired per live-state-audit.json from B1 M1.a):
--   GCP: artifact_registry, cloud_sql, memorystore, cloud_storage, secret_manager,
--        pubsub, cloud_nat, workload_identity, static_ips
--   AWS: vpc_mapping, load_balancers, api_gateway, irsa_bindings
--   These will be populated by scanners via the store API in B2+, not stored as columns.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- projects: workspace identity registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    name        TEXT NOT NULL PRIMARY KEY,  -- workspace identity (canonical: host/owner/repo or dirname)
    identity    TEXT,                       -- git remote URL normalized lowercase (fallback: name); scanner-owned
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))  -- scanner-owned
);

CREATE INDEX IF NOT EXISTS idx_projects_identity ON projects(identity);

-- ---------------------------------------------------------------------------
-- repos: source code repositories in the workspace
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repos (
    project          TEXT NOT NULL,  -- FK -> projects.name
    name             TEXT NOT NULL,  -- repo name (basename); scanner-owned
    role             TEXT,           -- e.g. 'backend', 'frontend', 'library', 'infra'; agent-owned
    remote_url       TEXT,           -- git remote URL (raw, unnormalized); scanner-owned
    platform         TEXT,           -- 'github', 'bitbucket', 'gitlab', etc.; scanner-owned
    primary_language TEXT,           -- detected primary language; scanner-owned
    scanner_ts       TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    topic_key        TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    PRIMARY KEY (project, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_repos_project ON repos(project);
CREATE INDEX IF NOT EXISTS idx_repos_topic_key ON repos(topic_key);

-- ---------------------------------------------------------------------------
-- apps: deployed applications (services, jobs, functions, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS apps (
    project     TEXT NOT NULL,  -- FK -> projects.name
    repo        TEXT NOT NULL,  -- FK -> repos.name within the same project
    name        TEXT NOT NULL,  -- app/service name; scanner-owned
    kind        TEXT,           -- 'service', 'job', 'function', 'cronjob'; scanner-owned
    description TEXT,           -- human description; agent-owned
    status      TEXT,           -- 'active', 'deprecated', 'planned'; agent-owned
    topic_key   TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts  TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_apps_project ON apps(project);
CREATE INDEX IF NOT EXISTS idx_apps_status ON apps(status);
CREATE INDEX IF NOT EXISTS idx_apps_topic_key ON apps(topic_key);

-- ---------------------------------------------------------------------------
-- libraries: shared library packages within the workspace
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS libraries (
    project    TEXT NOT NULL,  -- FK -> projects.name
    repo       TEXT NOT NULL,  -- FK -> repos.name within the same project
    name       TEXT NOT NULL,  -- library/package name; scanner-owned
    version    TEXT,           -- current version; scanner-owned
    language   TEXT,           -- primary language; scanner-owned
    scanner_ts TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_libraries_project ON libraries(project);

-- ---------------------------------------------------------------------------
-- services: infrastructure-level services (APIs, databases, queues, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS services (
    project     TEXT NOT NULL,  -- FK -> projects.name
    repo        TEXT NOT NULL,  -- FK -> repos.name within the same project
    name        TEXT NOT NULL,  -- service name; scanner-owned
    kind        TEXT,           -- 'api', 'database', 'queue', 'cache', 'storage'; scanner-owned
    description TEXT,           -- human description; agent-owned
    status      TEXT,           -- 'active', 'deprecated', 'planned'; agent-owned
    topic_key   TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts  TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_services_project ON services(project);
CREATE INDEX IF NOT EXISTS idx_services_status ON services(status);
CREATE INDEX IF NOT EXISTS idx_services_topic_key ON services(topic_key);

-- ---------------------------------------------------------------------------
-- features: feature flags and feature-level metadata
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS features (
    project     TEXT NOT NULL,  -- FK -> projects.name
    repo        TEXT NOT NULL,  -- FK -> repos.name within the same project
    name        TEXT NOT NULL,  -- feature name / flag key; scanner-owned
    status      TEXT,           -- 'active', 'deprecated', 'planned'; agent-owned
    description TEXT,           -- human description; agent-owned
    topic_key   TEXT,           -- optional dimension key for upsert disambiguation; agent-owned
    scanner_ts  TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_features_project ON features(project);
CREATE INDEX IF NOT EXISTS idx_features_status ON features(status);
CREATE INDEX IF NOT EXISTS idx_features_topic_key ON features(topic_key);

-- ---------------------------------------------------------------------------
-- tf_modules: Terraform module definitions tracked in the workspace
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tf_modules (
    project    TEXT NOT NULL,  -- FK -> projects.name
    repo       TEXT NOT NULL,  -- FK -> repos.name within the same project
    name       TEXT NOT NULL,  -- module name; scanner-owned
    source     TEXT,           -- module source path or registry reference; scanner-owned
    version    TEXT,           -- pinned version; scanner-owned
    topic_key  TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tf_modules_project ON tf_modules(project);
CREATE INDEX IF NOT EXISTS idx_tf_modules_topic_key ON tf_modules(topic_key);

-- ---------------------------------------------------------------------------
-- tf_live: live Terraform state (applied infrastructure resources)
-- Note: live-state fields retired per audit (vpc_mapping, load_balancers, etc.
-- are scanner-populated via store API, not stored as typed columns here).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tf_live (
    project    TEXT NOT NULL,   -- FK -> projects.name
    repo       TEXT NOT NULL,   -- FK -> repos.name within the same project
    name       TEXT NOT NULL,   -- resource name; scanner-owned
    kind       TEXT,            -- resource type (e.g. 'aws_instance', 'google_sql_database_instance'); scanner-owned
    attributes TEXT,            -- JSON blob of selected attributes; scanner-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tf_live_project ON tf_live(project);

-- ---------------------------------------------------------------------------
-- releases: release/tag history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS releases (
    project    TEXT NOT NULL,   -- FK -> projects.name
    repo       TEXT NOT NULL,   -- FK -> repos.name within the same project
    name       TEXT NOT NULL,   -- release tag or version string; scanner-owned
    released_at TEXT,           -- ISO8601 release date; scanner-owned
    notes      TEXT,            -- release notes summary; agent-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_releases_project ON releases(project);

-- ---------------------------------------------------------------------------
-- workloads: Kubernetes workloads / compute workloads tracked per repo
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workloads (
    project    TEXT NOT NULL,   -- FK -> projects.name
    repo       TEXT NOT NULL,   -- FK -> repos.name within the same project
    name       TEXT NOT NULL,   -- workload name; scanner-owned
    kind       TEXT,            -- 'Deployment', 'StatefulSet', 'DaemonSet', 'Job', etc.; scanner-owned
    namespace  TEXT,            -- Kubernetes namespace; scanner-owned
    cluster    TEXT,            -- cluster name this runs on; scanner-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workloads_project ON workloads(project);
CREATE INDEX IF NOT EXISTS idx_workloads_cluster ON workloads(cluster);

-- ---------------------------------------------------------------------------
-- clusters_defined: cluster definitions declared in the codebase (Terraform, Helm, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clusters_defined (
    project    TEXT NOT NULL,   -- FK -> projects.name
    repo       TEXT NOT NULL,   -- FK -> repos.name within the same project
    name       TEXT NOT NULL,   -- cluster name; scanner-owned
    provider   TEXT,            -- 'gke', 'eks', 'aks', etc.; scanner-owned
    region     TEXT,            -- cloud region; scanner-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, repo, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (project, repo) REFERENCES repos(project, name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_clusters_defined_project ON clusters_defined(project);

-- ---------------------------------------------------------------------------
-- clusters: live cluster instances (project-level, not repo-scoped)
-- (static metadata kept per audit; live state retired to B2+ scanners)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clusters (
    project    TEXT NOT NULL,   -- FK -> projects.name
    name       TEXT NOT NULL,   -- cluster name; scanner-owned
    provider   TEXT,            -- 'gke', 'eks', 'aks'; scanner-owned
    region     TEXT,            -- cloud region; scanner-owned
    attributes TEXT,            -- JSON blob for flexible extra attributes; agent-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_clusters_project ON clusters(project);

-- ---------------------------------------------------------------------------
-- integrations: third-party integrations and tools installed in the project
-- (e.g. DataDog, Sentry, PagerDuty, Tailscale, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integrations (
    project      TEXT NOT NULL,  -- FK -> projects.name
    name         TEXT NOT NULL,  -- integration name; scanner-owned
    kind         TEXT,           -- 'monitoring', 'alerting', 'security', 'network'; agent-owned
    version      TEXT,           -- installed version; scanner-owned
    install_path TEXT,           -- file path where the integration config lives; scanner-owned
    topic_key    TEXT,           -- optional dimension key for upsert disambiguation; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_integrations_project ON integrations(project);
CREATE INDEX IF NOT EXISTS idx_integrations_topic_key ON integrations(topic_key);

-- ---------------------------------------------------------------------------
-- gaia_installations: Gaia CLI installation records per machine
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gaia_installations (
    project      TEXT NOT NULL,  -- FK -> projects.name
    machine      TEXT NOT NULL,  -- machine name or tailscale hostname; scanner-owned
    version      TEXT,           -- installed Gaia version; scanner-owned
    install_mode TEXT,           -- 'npm-global', 'local', 'dev'; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, machine),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gaia_installations_project ON gaia_installations(project);

-- ---------------------------------------------------------------------------
-- machines: machines participating in this workspace (Tailscale network, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS machines (
    project      TEXT NOT NULL,  -- FK -> projects.name
    name         TEXT NOT NULL,  -- machine hostname; scanner-owned
    os           TEXT,           -- 'windows', 'linux', 'macos'; scanner-owned
    arch         TEXT,           -- 'amd64', 'arm64'; scanner-owned
    tailscale_ip TEXT,           -- Tailscale MagicDNS or IP; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (project, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_machines_project ON machines(project);

-- ---------------------------------------------------------------------------
-- agent_permissions: per-table per-agent write authorization
--
-- NOTE: B1 inserts schema + 1 example row for tests.
-- B3 M2 inserts the full mapping for all 5 domain agents.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_permissions (
    table_name  TEXT NOT NULL,   -- name of the target table
    agent_name  TEXT NOT NULL,   -- agent identifier (e.g. 'developer', 'terraform-architect')
    allow_write INTEGER NOT NULL DEFAULT 0,  -- 1 = allow, 0 = deny (BOOLEAN)
    PRIMARY KEY (table_name, agent_name)
);

-- Example row for tests (B1 M2 requirement: 1 row for developer->apps=allow)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write)
VALUES ('apps', 'developer', 1);

-- ---------------------------------------------------------------------------
-- FTS5 mirror tables for full-text search (repos, apps, services)
-- Used by `gaia ... search` (B7+). Triggers keep mirrors in sync.
-- ---------------------------------------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS repos_fts USING fts5(
    name,
    role,
    primary_language,
    content='repos',
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

CREATE TRIGGER IF NOT EXISTS repos_fts_insert AFTER INSERT ON repos BEGIN
    INSERT INTO repos_fts(rowid, name, role, primary_language)
    VALUES (new.rowid, new.name, new.role, new.primary_language);
END;

CREATE TRIGGER IF NOT EXISTS repos_fts_delete AFTER DELETE ON repos BEGIN
    INSERT INTO repos_fts(repos_fts, rowid, name, role, primary_language)
    VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
END;

CREATE TRIGGER IF NOT EXISTS repos_fts_update AFTER UPDATE ON repos BEGIN
    INSERT INTO repos_fts(repos_fts, rowid, name, role, primary_language)
    VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
    INSERT INTO repos_fts(rowid, name, role, primary_language)
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

-- briefs: one row per brief (agent-owned, written by `gaia brief` CLI)
CREATE TABLE IF NOT EXISTS briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project      TEXT NOT NULL,        -- FK -> projects.name (workspace identity)
    name         TEXT NOT NULL,        -- unique bare name within project (e.g. 'paths-and-identity-foundations')
    status       TEXT NOT NULL DEFAULT 'draft',  -- 'draft', 'open', 'in-progress', 'closed', 'archived'
    surface_type TEXT,                 -- 'cli', 'api', 'infra', etc. (from frontmatter)
    title        TEXT,                 -- human title (# heading)
    objective    TEXT,                 -- ## Objective section
    context      TEXT,                 -- ## Context section
    approach     TEXT,                 -- ## Approach section
    out_of_scope TEXT,                 -- ## Out of Scope section
    topic_key    TEXT,                 -- optional dimension key
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (project, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_briefs_project ON briefs(project);
CREATE INDEX IF NOT EXISTS idx_briefs_status ON briefs(status);
CREATE INDEX IF NOT EXISTS idx_briefs_topic_key ON briefs(topic_key);

-- acceptance_criteria: structured ACs per brief
CREATE TABLE IF NOT EXISTS acceptance_criteria (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id       INTEGER NOT NULL,   -- FK -> briefs.id
    ac_id          TEXT NOT NULL,      -- e.g. 'AC-1', 'AC-2'
    description    TEXT,
    evidence_type  TEXT,               -- 'command', 'pytest', 'manual'
    evidence_shape TEXT,               -- JSON blob: {"run": "...", "expect": "..."}
    artifact_path  TEXT,               -- relative path to evidence artifact file
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_acceptance_criteria_brief ON acceptance_criteria(brief_id);

-- milestones: ordered milestones per brief
CREATE TABLE IF NOT EXISTS milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id    INTEGER NOT NULL,   -- FK -> briefs.id
    order_num   INTEGER NOT NULL,   -- display order (1-based)
    name        TEXT NOT NULL,      -- milestone label (e.g. 'M1', 'M2')
    description TEXT,               -- milestone description
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_milestones_brief ON milestones(brief_id);

-- brief_dependencies: DAG edges between briefs (within the same project)
CREATE TABLE IF NOT EXISTS brief_dependencies (
    brief_id          INTEGER NOT NULL,  -- FK -> briefs.id (the dependent)
    depends_on_id     INTEGER NOT NULL,  -- FK -> briefs.id (the dependency)
    PRIMARY KEY (brief_id, depends_on_id),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_id) REFERENCES briefs(id) ON DELETE CASCADE
);

-- plans: one plan per brief (may be extended in future briefs)
CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id   INTEGER NOT NULL UNIQUE,  -- FK -> briefs.id (one plan per brief)
    status     TEXT NOT NULL DEFAULT 'draft',  -- 'draft', 'active', 'closed'
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

-- tasks: ordered tasks within a plan
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER NOT NULL,   -- FK -> plans.id
    order_num     INTEGER NOT NULL,   -- execution order (1-based)
    goal          TEXT,               -- task goal description
    status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'done', 'skipped'
    evidence_path TEXT,               -- path to evidence artifact
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_plan ON tasks(plan_id);

-- ---------------------------------------------------------------------------
-- FTS5 mirror for briefs (objective / context / approach)
-- Used by `gaia brief search`. Triggers keep mirror in sync.
-- ---------------------------------------------------------------------------

CREATE VIRTUAL TABLE IF NOT EXISTS briefs_fts USING fts5(
    objective,
    context,
    approach,
    content='briefs',
    content_rowid='id'
);

-- Triggers to maintain briefs_fts in sync with briefs base table

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
