BEGIN TRANSACTION;
CREATE TABLE "acceptance_criteria" (
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
CREATE TABLE agent_contract_handoff_approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id  INTEGER NOT NULL,                -- FK -> agent_contract_handoffs.id
    approval_id TEXT NOT NULL,                   -- FK -> approval_grants.approval_id
    decision    TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'EXPIRED', 'REVOKED')),
    decided_at  TEXT NOT NULL,
    FOREIGN KEY (handoff_id)  REFERENCES agent_contract_handoffs(id) ON DELETE CASCADE,
    FOREIGN KEY (approval_id) REFERENCES approval_grants(approval_id)
);
CREATE TABLE "agent_contract_handoffs" (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id      TEXT,
    agent_id         TEXT NOT NULL,
    session_id       TEXT,
    workspace        TEXT NOT NULL,
    brief_id         INTEGER,
    task_status      TEXT NOT NULL
                     CHECK (task_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT')),
    raw_handoff_json TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (brief_id)  REFERENCES briefs(id)
);
CREATE TABLE agent_contract_permissions (
    agent_name    TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    can_read      INTEGER NOT NULL DEFAULT 0,
    can_write     INTEGER NOT NULL DEFAULT 0,
    cloud_scope   TEXT,             -- NULL = all providers; 'gcp', 'aws', etc. for overlays
    PRIMARY KEY (agent_name, contract_name, cloud_scope)
);
CREATE TABLE agent_permissions (
    table_name  TEXT NOT NULL,   -- name of the target table
    agent_name  TEXT NOT NULL,   -- agent identifier (e.g. 'developer', 'platform-architect')
    allow_write INTEGER NOT NULL DEFAULT 0,  -- 1 = allow, 0 = deny (BOOLEAN)
    PRIMARY KEY (table_name, agent_name)
);
INSERT INTO "agent_permissions" VALUES('apps','developer',1);
INSERT INTO "agent_permissions" VALUES('clusters','cloud-troubleshooter',1);
INSERT INTO "agent_permissions" VALUES('features','developer',1);
INSERT INTO "agent_permissions" VALUES('libraries','developer',1);
INSERT INTO "agent_permissions" VALUES('services','developer',1);
INSERT INTO "agent_permissions" VALUES('gaia_installations','gaia-system',1);
INSERT INTO "agent_permissions" VALUES('integrations','gaia-system',1);
INSERT INTO "agent_permissions" VALUES('clusters_defined','gitops-operator',1);
INSERT INTO "agent_permissions" VALUES('releases','gitops-operator',1);
INSERT INTO "agent_permissions" VALUES('workloads','gitops-operator',1);
INSERT INTO "agent_permissions" VALUES('clusters','platform-architect',1);
INSERT INTO "agent_permissions" VALUES('tf_live','platform-architect',1);
INSERT INTO "agent_permissions" VALUES('tf_modules','platform-architect',1);
CREATE TABLE approval_events (
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
CREATE TABLE approval_grants (
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
CREATE TABLE approvals (
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
CREATE TABLE apps (
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
PRAGMA writable_schema=ON;
INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql)VALUES('table','apps_fts','apps_fts',0,'CREATE VIRTUAL TABLE apps_fts USING fts5(
    name,
    description,
    topic_key,
    content=''apps'',
    content_rowid=''rowid''
)');
CREATE TABLE 'apps_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
INSERT INTO "apps_fts_config" VALUES('version',4);
CREATE TABLE 'apps_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
INSERT INTO "apps_fts_data" VALUES(1,X'');
INSERT INTO "apps_fts_data" VALUES(10,X'00000000000000');
CREATE TABLE 'apps_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE 'apps_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE brief_dependencies (
    brief_id          INTEGER NOT NULL,
    depends_on_id     INTEGER NOT NULL,
    PRIMARY KEY (brief_id, depends_on_id),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_id) REFERENCES briefs(id) ON DELETE CASCADE
);
CREATE TABLE briefs (
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
INSERT INTO "briefs" VALUES(1,'fixture-ws','fixture-brief','draft',NULL,'Fixture brief','representative row',NULL,NULL,NULL,NULL,'2026-09-28T20:28:02Z','2026-09-28T20:28:02Z');
INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql)VALUES('table','briefs_fts','briefs_fts',0,'CREATE VIRTUAL TABLE briefs_fts USING fts5(
    objective,
    context,
    approach,
    content=''briefs'',
    content_rowid=''id''
)');
INSERT INTO "briefs_fts" VALUES('representative row',NULL,NULL);
CREATE TABLE 'briefs_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
INSERT INTO "briefs_fts_config" VALUES('version',4);
CREATE TABLE 'briefs_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
INSERT INTO "briefs_fts_data" VALUES(1,X'01020000');
INSERT INTO "briefs_fts_data" VALUES(10,X'000000000101010001010101');
INSERT INTO "briefs_fts_data" VALUES(137438953473,X'0000001E0F30726570726573656E74617469766501020202026F770102030413');
CREATE TABLE 'briefs_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
INSERT INTO "briefs_fts_docsize" VALUES(1,X'020000');
CREATE TABLE 'briefs_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
INSERT INTO "briefs_fts_idx" VALUES(1,X'',2);
CREATE TABLE clusters (
    workspace  TEXT NOT NULL,   -- FK -> workspaces.name
    name       TEXT NOT NULL,   -- cluster name; scanner-owned
    provider   TEXT,            -- 'gke', 'eks', 'aks'; scanner-owned
    region     TEXT,            -- cloud region; scanner-owned
    attributes TEXT,            -- JSON blob for flexible extra attributes; agent-owned
    scanner_ts TEXT,            -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE clusters_defined (
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
CREATE TABLE episode_anomalies (
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
CREATE TABLE episodes (
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
INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql)VALUES('table','episodes_fts','episodes_fts',0,'CREATE VIRTUAL TABLE episodes_fts USING fts5(
    episode_id UNINDEXED,
    prompt,
    enriched_prompt,
    tags,
    title,
    content=''episodes'',
    content_rowid=''rowid''
)');
CREATE TABLE 'episodes_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
INSERT INTO "episodes_fts_config" VALUES('version',4);
CREATE TABLE 'episodes_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
INSERT INTO "episodes_fts_data" VALUES(1,X'');
INSERT INTO "episodes_fts_data" VALUES(10,X'00000000000000');
CREATE TABLE 'episodes_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE 'episodes_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE evidence (
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
CREATE TABLE features (
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
CREATE TABLE gaia_installations (
    workspace    TEXT NOT NULL,  -- FK -> workspaces.name
    machine      TEXT NOT NULL,  -- machine name or tailscale hostname; scanner-owned
    version      TEXT,           -- installed Gaia version; scanner-owned
    install_mode TEXT,           -- 'npm-global', 'local', 'dev'; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, machine),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE harness_events (
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
CREATE TABLE integrations (
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
CREATE TABLE libraries (
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
CREATE TABLE machines (
    workspace    TEXT NOT NULL,  -- FK -> workspaces.name
    name         TEXT NOT NULL,  -- machine hostname; scanner-owned
    os           TEXT,           -- 'windows', 'linux', 'macos'; scanner-owned
    arch         TEXT,           -- 'amd64', 'arm64'; scanner-owned
    tailscale_ip TEXT,           -- Tailscale MagicDNS or IP; scanner-owned
    scanner_ts   TEXT,           -- ISO8601 timestamp of last scan; scanner-owned
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE memory (
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
    initiative        TEXT,  -- canonical project/initiative grouping key (clean, vantage-independent). Distinct from project_ref (the git-common-dir path): initiative is the human-facing key that unifies git projects (basename of project_ref sans .git -> 'gaia', 'balance') AND logical initiatives that are NOT git repos ('branchkinect', 'buildwiz', 'axisio', ...). NULL when no initiative can be resolved without guessing. Populated at write time (upsert_memory / gaia memory add: --project -> basename(project_ref); --initiative -> normalized key). Column added v32; existing rows backfilled by scripts/migrations/v31_to_v32.sql.
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
INSERT INTO "memory" VALUES('fixture-ws','fixture_memory_1','project','fixture','representative row',NULL,NULL,'log',NULL,NULL,NULL,NULL);
INSERT INTO "memory" VALUES('fixture-ws','fixture_memory_2','feedback','fixture','representative row',NULL,NULL,'log',NULL,NULL,NULL,NULL);
INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql)VALUES('table','memory_fts','memory_fts',0,'CREATE VIRTUAL TABLE memory_fts USING fts5(
    workspace UNINDEXED,
    name UNINDEXED,
    description,
    body,
    content=''memory'',
    content_rowid=''rowid''
)');
INSERT INTO "memory_fts" VALUES('fixture-ws','fixture_memory_1','fixture','representative row');
INSERT INTO "memory_fts" VALUES('fixture-ws','fixture_memory_2','fixture','representative row');
CREATE TABLE 'memory_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
INSERT INTO "memory_fts_config" VALUES('version',4);
CREATE TABLE 'memory_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
INSERT INTO "memory_fts_data" VALUES(1,X'0200000204');
INSERT INTO "memory_fts_data" VALUES(10,X'000000000102020002010101020101');
INSERT INTO "memory_fts_data" VALUES(137438953473,X'000000300830666978747572650106010202010E726570726573656E746174697665010601030202026F770106010303040E15');
INSERT INTO "memory_fts_data" VALUES(274877906945,X'000000300830666978747572650206010202010E726570726573656E746174697665020601030202026F770206010303040E15');
CREATE TABLE 'memory_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
INSERT INTO "memory_fts_docsize" VALUES(1,X'00000102');
INSERT INTO "memory_fts_docsize" VALUES(2,X'00000102');
CREATE TABLE 'memory_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
INSERT INTO "memory_fts_idx" VALUES(1,X'',2);
INSERT INTO "memory_fts_idx" VALUES(2,X'',2);
CREATE TABLE "memory_history" (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace          TEXT NOT NULL,
    name               TEXT NOT NULL,
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
    changed_by_agent   TEXT,
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE memory_links (
    workspace  TEXT NOT NULL,  -- FK -> workspaces.name
    src_name   TEXT NOT NULL,
    dst_name   TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('relates_to', 'supersedes', 'derived_from', 'graduated_to')),
    created_at TEXT,
    PRIMARY KEY (workspace, src_name, dst_name, kind),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE milestones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id    INTEGER NOT NULL,
    order_num   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'done', 'blocked')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);
CREATE TABLE plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id   INTEGER NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'draft'
               CHECK (status IN ('draft', 'active', 'closed')),
    content    TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);
CREATE TABLE project_context_contracts (
    workspace     TEXT NOT NULL,  -- FK -> workspaces.name
    contract_name TEXT NOT NULL,
    payload       TEXT NOT NULL,
    metadata      TEXT,
    updated_at    TEXT,
    PRIMARY KEY (workspace, contract_name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE "project_context_contracts_history" (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_key        TEXT NOT NULL,
    workspace           TEXT NOT NULL,
    before_payload_json TEXT,
    after_payload_json  TEXT NOT NULL,
    changed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    changed_by_agent    TEXT,
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE project_facets (
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
CREATE TABLE "project_history" (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    before_path       TEXT,
    after_path        TEXT,
    before_workspace  TEXT,
    after_workspace   TEXT,
    before_name       TEXT,
    after_name        TEXT,
    before_status     TEXT,
    after_status      TEXT,
    changed_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);
CREATE TABLE projects (
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
INSERT INTO "projects" VALUES('fixture-ws','fixture-project','library',NULL,NULL,NULL,NULL,NULL,NULL,'/fixture/project','active',NULL,NULL,NULL,NULL);
INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql)VALUES('table','projects_fts','projects_fts',0,'CREATE VIRTUAL TABLE projects_fts USING fts5(
    name,
    role,
    primary_language,
    content=''projects'',
    content_rowid=''rowid''
)');
INSERT INTO "projects_fts" VALUES('fixture-project','library',NULL);
CREATE TABLE 'projects_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
INSERT INTO "projects_fts_config" VALUES('version',4);
CREATE TABLE 'projects_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
INSERT INTO "projects_fts_data" VALUES(1,X'01020100');
INSERT INTO "projects_fts_data" VALUES(10,X'000000000101010001010101');
INSERT INTO "projects_fts_data" VALUES(137438953473,X'0000002A08306669787475726501020201076C6962726172790106010102010770726F6A656374010203040C0E');
CREATE TABLE 'projects_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
INSERT INTO "projects_fts_docsize" VALUES(1,X'020100');
CREATE TABLE 'projects_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
INSERT INTO "projects_fts_idx" VALUES(1,X'',2);
CREATE TABLE releases (
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
CREATE TABLE scheduled_task_machines (
    task_id      INTEGER NOT NULL,           -- FK -> scheduled_tasks.id
    machine_name TEXT NOT NULL,              -- target machine hostname (= platform.node())
    PRIMARY KEY (task_id, machine_name),
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);
CREATE TABLE scheduled_task_state (
    task_id        INTEGER NOT NULL,         -- FK -> scheduled_tasks.id
    machine_name   TEXT NOT NULL,            -- machine this state is for (= platform.node())
    backend        TEXT,                     -- 'cron' | 'launchd' | 'schtasks'
    installed      INTEGER NOT NULL DEFAULT 0, -- 1 = materialized in the scheduler (BOOLEAN)
    last_synced_at TEXT,                      -- ISO8601 of the last successful sync
    PRIMARY KEY (task_id, machine_name),
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);
CREATE TABLE scheduled_tasks (
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
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT
);
INSERT INTO "schema_version" VALUES(18,'2026-09-28T20:28:02Z','baseline floor: schema.sql at v18');
INSERT INTO "schema_version" VALUES(19,'2026-09-28T20:28:02Z','applied migration v18_to_v19.sql');
INSERT INTO "schema_version" VALUES(20,'2026-09-28T20:28:02Z','applied migration v19_to_v20.sql');
INSERT INTO "schema_version" VALUES(21,'2026-09-28T20:28:02Z','applied migration v20_to_v21.sql');
INSERT INTO "schema_version" VALUES(22,'2026-09-28T20:28:02Z','applied migration v21_to_v22.sql');
INSERT INTO "schema_version" VALUES(23,'2026-09-28T20:28:02Z','applied migration v22_to_v23.sql');
INSERT INTO "schema_version" VALUES(24,'2026-09-28T20:28:02Z','applied migration v23_to_v24.sql');
INSERT INTO "schema_version" VALUES(25,'2026-09-28T20:28:02Z','applied migration v24_to_v25.sql');
INSERT INTO "schema_version" VALUES(26,'2026-09-28T20:28:02Z','applied migration v25_to_v26.sql');
INSERT INTO "schema_version" VALUES(27,'2026-09-28T20:28:02Z','applied migration v26_to_v27.sql');
INSERT INTO "schema_version" VALUES(28,'2026-09-28T20:28:02Z','applied migration v27_to_v28.sql');
INSERT INTO "schema_version" VALUES(29,'2026-09-28T20:28:02Z','applied migration v28_to_v29.sql');
INSERT INTO "schema_version" VALUES(30,'2026-09-28T20:28:02Z','applied migration v29_to_v30.sql');
INSERT INTO "schema_version" VALUES(31,'2026-09-28T20:28:02Z','applied migration v30_to_v31.sql');
INSERT INTO "schema_version" VALUES(32,'2026-09-28T20:28:02Z','applied migration v31_to_v32.sql');
INSERT INTO "schema_version" VALUES(33,'2026-09-28T20:28:02Z','applied migration v32_to_v33.sql');
CREATE TABLE services (
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
INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql)VALUES('table','services_fts','services_fts',0,'CREATE VIRTUAL TABLE services_fts USING fts5(
    name,
    description,
    topic_key,
    content=''services'',
    content_rowid=''rowid''
)');
CREATE TABLE 'services_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
INSERT INTO "services_fts_config" VALUES('version',4);
CREATE TABLE 'services_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
INSERT INTO "services_fts_data" VALUES(1,X'');
INSERT INTO "services_fts_data" VALUES(10,X'00000000000000');
CREATE TABLE 'services_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE 'services_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE surface_routing (
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
CREATE TABLE task_notifications (
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
CREATE TABLE tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER NOT NULL,
    order_num     INTEGER NOT NULL,
    goal          TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'done', 'skipped')),
    evidence_path TEXT,
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);
CREATE TABLE tf_live (
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
CREATE TABLE tf_modules (
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
CREATE TABLE workloads (
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
CREATE TABLE workspaces (
    name          TEXT NOT NULL PRIMARY KEY,  -- workspace name (canonical: host/owner/repo or directory basename)
    identity      TEXT,                       -- identity: for git-bearing workspace = git remote URL normalized lowercase; for organizational workspace = name; scanner-owned
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),  -- scanner-owned
    last_scan_at  TEXT,                       -- ISO8601 timestamp of last successful `gaia scan` run; NULL = never scanned; v7
    status        TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'missing'; scanner-owned (soft-delete). 'missing' = the Gaia install footprint disappeared (workspace demoted); v17
    missing_since TEXT                         -- ISO8601 timestamp when status set to 'missing'; NULL if active; scanner-owned; v17
);
INSERT INTO "workspaces" VALUES('fixture-ws','fixture-ws','2026-09-28T20:28:02Z',NULL,'active',NULL);
CREATE INDEX idx_workspaces_identity ON workspaces(identity);
CREATE INDEX idx_projects_workspace ON projects(workspace);
CREATE INDEX idx_projects_topic_key ON projects(topic_key);
CREATE INDEX idx_apps_workspace ON apps(workspace);
CREATE INDEX idx_apps_status ON apps(status);
CREATE INDEX idx_apps_topic_key ON apps(topic_key);
CREATE INDEX idx_libraries_workspace ON libraries(workspace);
CREATE INDEX idx_services_workspace ON services(workspace);
CREATE INDEX idx_services_status ON services(status);
CREATE INDEX idx_services_topic_key ON services(topic_key);
CREATE INDEX idx_features_workspace ON features(workspace);
CREATE INDEX idx_features_status ON features(status);
CREATE INDEX idx_features_topic_key ON features(topic_key);
CREATE INDEX idx_project_facets_workspace ON project_facets(workspace);
CREATE INDEX idx_project_facets_scope ON project_facets(scope);
CREATE INDEX idx_tf_modules_workspace ON tf_modules(workspace);
CREATE INDEX idx_tf_modules_topic_key ON tf_modules(topic_key);
CREATE INDEX idx_tf_live_workspace ON tf_live(workspace);
CREATE INDEX idx_releases_workspace ON releases(workspace);
CREATE INDEX idx_workloads_workspace ON workloads(workspace);
CREATE INDEX idx_workloads_cluster ON workloads(cluster);
CREATE INDEX idx_clusters_defined_workspace ON clusters_defined(workspace);
CREATE INDEX idx_clusters_workspace ON clusters(workspace);
CREATE INDEX idx_integrations_workspace ON integrations(workspace);
CREATE INDEX idx_integrations_topic_key ON integrations(topic_key);
CREATE INDEX idx_gaia_installations_workspace ON gaia_installations(workspace);
CREATE INDEX idx_machines_workspace ON machines(workspace);
CREATE TRIGGER projects_fts_insert AFTER INSERT ON projects BEGIN
    INSERT INTO projects_fts(rowid, name, role, primary_language)
    VALUES (new.rowid, new.name, new.role, new.primary_language);
END;
CREATE TRIGGER projects_fts_delete AFTER DELETE ON projects BEGIN
    INSERT INTO projects_fts(projects_fts, rowid, name, role, primary_language)
    VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
END;
CREATE TRIGGER projects_fts_update AFTER UPDATE ON projects BEGIN
    INSERT INTO projects_fts(projects_fts, rowid, name, role, primary_language)
    VALUES ('delete', old.rowid, old.name, old.role, old.primary_language);
    INSERT INTO projects_fts(rowid, name, role, primary_language)
    VALUES (new.rowid, new.name, new.role, new.primary_language);
END;
CREATE TRIGGER apps_fts_insert AFTER INSERT ON apps BEGIN
    INSERT INTO apps_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;
CREATE TRIGGER apps_fts_delete AFTER DELETE ON apps BEGIN
    INSERT INTO apps_fts(apps_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
END;
CREATE TRIGGER apps_fts_update AFTER UPDATE ON apps BEGIN
    INSERT INTO apps_fts(apps_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
    INSERT INTO apps_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;
CREATE TRIGGER services_fts_insert AFTER INSERT ON services BEGIN
    INSERT INTO services_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;
CREATE TRIGGER services_fts_delete AFTER DELETE ON services BEGIN
    INSERT INTO services_fts(services_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
END;
CREATE TRIGGER services_fts_update AFTER UPDATE ON services BEGIN
    INSERT INTO services_fts(services_fts, rowid, name, description, topic_key)
    VALUES ('delete', old.rowid, old.name, old.description, old.topic_key);
    INSERT INTO services_fts(rowid, name, description, topic_key)
    VALUES (new.rowid, new.name, new.description, new.topic_key);
END;
CREATE INDEX idx_briefs_workspace ON briefs(workspace);
CREATE INDEX idx_briefs_status ON briefs(status);
CREATE INDEX idx_briefs_topic_key ON briefs(topic_key);
CREATE INDEX idx_milestones_brief ON milestones(brief_id);
CREATE INDEX idx_tasks_plan ON tasks(plan_id);
CREATE INDEX idx_evidence_brief ON evidence(brief_id);
CREATE INDEX idx_evidence_ac ON evidence(brief_id, ac_id);
CREATE TRIGGER briefs_ai AFTER INSERT ON briefs BEGIN
    INSERT INTO briefs_fts(rowid, objective, context, approach)
    VALUES (new.id, new.objective, new.context, new.approach);
END;
CREATE TRIGGER briefs_ad AFTER DELETE ON briefs BEGIN
    INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
    VALUES ('delete', old.id, old.objective, old.context, old.approach);
END;
CREATE TRIGGER briefs_au AFTER UPDATE ON briefs BEGIN
    INSERT INTO briefs_fts(briefs_fts, rowid, objective, context, approach)
    VALUES ('delete', old.id, old.objective, old.context, old.approach);
    INSERT INTO briefs_fts(rowid, objective, context, approach)
    VALUES (new.id, new.objective, new.context, new.approach);
END;
CREATE INDEX idx_episodes_workspace_timestamp ON episodes(workspace, timestamp DESC);
CREATE INDEX idx_episodes_session ON episodes(session_id);
CREATE TRIGGER episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
END;
CREATE TRIGGER episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
END;
CREATE TRIGGER episodes_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
    INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
END;
CREATE INDEX idx_episode_anomalies_type      ON episode_anomalies(type);
CREATE INDEX idx_episode_anomalies_workspace  ON episode_anomalies(workspace, timestamp DESC);
CREATE INDEX idx_episode_anomalies_episode    ON episode_anomalies(episode_id);
CREATE INDEX idx_memory_workspace ON memory(workspace);
CREATE INDEX idx_memory_type ON memory(type);
CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;
CREATE TRIGGER memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
END;
CREATE TRIGGER memory_au AFTER UPDATE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;
CREATE INDEX memory_links_src ON memory_links(workspace, src_name);
CREATE INDEX idx_memory_links_dst_kind ON memory_links(workspace, dst_name, kind);
CREATE TRIGGER trg_memory_history
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
CREATE INDEX idx_project_context_contracts_workspace ON project_context_contracts(workspace);
CREATE INDEX idx_agent_contract_perms_agent ON agent_contract_permissions(agent_name);
CREATE INDEX idx_harness_events_workspace_ts ON harness_events(workspace, ts DESC);
CREATE INDEX idx_harness_events_type ON harness_events(type);
CREATE INDEX idx_task_notifications_unread ON task_notifications(unread, created_at DESC);
CREATE INDEX idx_scheduled_tasks_workspace ON scheduled_tasks(workspace, enabled);
CREATE INDEX idx_approval_grants_agent   ON approval_grants(agent_id);
CREATE INDEX idx_approval_grants_session ON approval_grants(session_id);
CREATE INDEX idx_approval_grants_status  ON approval_grants(status);
CREATE INDEX idx_agent_contract_handoff_approvals_handoff ON agent_contract_handoff_approvals(handoff_id);
CREATE TRIGGER trg_pcc_history
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
CREATE INDEX idx_approvals_status     ON approvals(status);
CREATE INDEX idx_approvals_agent      ON approvals(agent_id);
CREATE INDEX idx_approvals_session    ON approvals(session_id);
CREATE INDEX idx_approval_events_approval  ON approval_events(approval_id, id);
CREATE INDEX idx_approval_events_type      ON approval_events(event_type);
CREATE INDEX idx_approval_events_session   ON approval_events(session_id);
CREATE TRIGGER ai_approval_events_hash
AFTER INSERT ON approval_events
BEGIN
    SELECT 1;
END;
CREATE TRIGGER bu_approval_events_immutable
BEFORE UPDATE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval_events is append-only');
END;
CREATE TRIGGER bd_approval_events_immutable
BEFORE DELETE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval_events is append-only');
END;
CREATE TRIGGER bu_approvals_status_has_event
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
CREATE TRIGGER trg_project_history
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
CREATE INDEX idx_acceptance_criteria_brief
    ON acceptance_criteria(brief_id);
CREATE INDEX idx_memory_history_workspace_name ON memory_history(workspace, name);
CREATE INDEX idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
CREATE INDEX idx_pcc_history_contract ON project_context_contracts_history(contract_key);
CREATE INDEX idx_project_history_workspace_name ON project_history(workspace, name);
PRAGMA writable_schema=OFF;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('acceptance_criteria',0);
INSERT INTO "sqlite_sequence" VALUES('memory_history',0);
INSERT INTO "sqlite_sequence" VALUES('agent_contract_handoffs',0);
INSERT INTO "sqlite_sequence" VALUES('project_context_contracts_history',0);
INSERT INTO "sqlite_sequence" VALUES('project_history',0);
INSERT INTO "sqlite_sequence" VALUES('briefs',1);
COMMIT;
