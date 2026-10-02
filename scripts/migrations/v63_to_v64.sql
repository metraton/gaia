-- gaia-compat: breaking
-- Migration v63 -> v64: a project is named by its normalized remote.
--
-- projects.project_identity becomes the normalized `origin` remote
-- (`host/owner/repo`, the form of gaia.project._normalize_remote) for every row
-- that records a remote; a row without one keeps its git-common-dir or path.
-- Rows whose remotes normalize alike are clones of one project and would
-- collide on idx_projects_identity, so they fold into one row: the active row
-- whose folder is named like the remote, else the oldest, survives; every other
-- active clone becomes a `copy` facet of it (the scan fills in its branch), its
-- description passes to a survivor that has none, and its row and scanner-owned
-- dependents are deleted. memory.project_ref and projects.superseded_by hold
-- identities, so they are rewritten to the new value, and memory.initiative
-- with them where it was the key derived from the old anchor
-- (gaia.store.writer.initiative_from_project_ref); memory keeps its project.
--
-- Breaking: code older than v64 writes git-common-dir identities, which would
-- split each project back into one row per clone.

CREATE TEMP TABLE identity_remap AS
WITH unprefixed AS (
    SELECT rid, workspace, name, path, status, project_identity AS old_identity,
           CASE
               WHEN u LIKE 'https://%' THEN substr(u, 9)
               WHEN u LIKE 'http://%' THEN substr(u, 8)
               WHEN u LIKE 'ssh://%' THEN substr(u, 7)
               WHEN u LIKE 'git+ssh://%' THEN substr(u, 11)
               WHEN u LIKE 'git+https://%' THEN substr(u, 13)
               ELSE u
           END AS s
    FROM (SELECT rowid AS rid, *, lower(trim(remote_url)) AS u FROM projects)
    WHERE project_identity IS NOT NULL AND u <> ''
), scp_form AS (
    SELECT *,
           CASE
               WHEN s LIKE 'git@%' AND instr(substr(s, 5), ':') > 0
                   THEN substr(s, 5, instr(substr(s, 5), ':') - 1) || '/'
                        || substr(s, 5 + instr(substr(s, 5), ':'))
               WHEN s LIKE 'git@%' THEN substr(s, 5)
               ELSE s
           END AS h
    FROM unprefixed
)
SELECT rid, workspace, name, path, status, old_identity,
       rtrim(CASE WHEN h GLOB '*.git' THEN substr(h, 1, length(h) - 4) ELSE h END, '/')
           AS new_identity
FROM scp_form;

DELETE FROM identity_remap WHERE new_identity = '';

INSERT INTO identity_remap (rid, workspace, name, path, status, old_identity, new_identity)
SELECT rowid, workspace, name, path, status, project_identity, project_identity
FROM projects
WHERE project_identity IN (SELECT new_identity FROM identity_remap)
  AND rowid NOT IN (SELECT rid FROM identity_remap);

CREATE TEMP TABLE identity_survivor AS
SELECT new_identity, rid FROM (
    SELECT new_identity, rid,
           row_number() OVER (
               PARTITION BY new_identity
               ORDER BY status = 'active' DESC,
                        lower(name) = substr(new_identity,
                            length(rtrim(new_identity, replace(new_identity, '/', ''))) + 1) DESC,
                        rid
           ) AS rank
    FROM identity_remap
)
WHERE rank = 1;

CREATE TEMP TABLE identity_loser AS
SELECT l.rid, l.workspace, l.name, l.path, l.status, s.workspace AS keep_workspace,
       s.name AS keep_name, s.path AS keep_path
FROM identity_remap l
JOIN identity_survivor sv ON sv.new_identity = l.new_identity AND sv.rid <> l.rid
JOIN identity_remap s ON s.rid = sv.rid;

CREATE TEMP TABLE identity_key AS
WITH RECURSIVE refs(ref, trimmed) AS (
    SELECT ref,
           CASE
               WHEN r GLOB '*/.git' THEN substr(r, 1, length(r) - 5)
               WHEN r GLOB '*.git' THEN rtrim(substr(r, 1, length(r) - 4), '/')
               ELSE r
           END
    FROM (
        SELECT ref, rtrim(trim(ref), '/') AS r FROM (
            SELECT old_identity AS ref FROM identity_remap
            UNION SELECT new_identity FROM identity_remap
        )
    )
), walk(ref, rest, out) AS (
    SELECT ref, lower(substr(trimmed, length(rtrim(trimmed, replace(trimmed, '/', ''))) + 1)), ''
    FROM refs
    UNION ALL
    SELECT ref, substr(rest, 2),
           out || CASE
               WHEN substr(rest, 1, 1) GLOB '[a-z0-9]' THEN substr(rest, 1, 1)
               WHEN out = '' OR substr(out, -1) = '_' THEN ''
               ELSE '_'
           END
    FROM walk WHERE rest <> ''
)
SELECT ref, NULLIF(rtrim(out, '_'), '') AS project_key FROM walk WHERE rest = '';

INSERT OR IGNORE INTO project_facets (workspace, project, scope, key, value, scanner_ts)
SELECT keep_workspace, keep_name, 'copy', path, NULL, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM identity_loser
WHERE status = 'active' AND path IS NOT NULL AND path IS NOT keep_path;

UPDATE projects SET description = (
    SELECT p.description FROM identity_loser l JOIN projects p ON p.rowid = l.rid
    WHERE l.keep_workspace = projects.workspace AND l.keep_name = projects.name
      AND p.description IS NOT NULL
    ORDER BY l.rid LIMIT 1
)
WHERE description IS NULL
  AND rowid IN (SELECT rid FROM identity_survivor);

UPDATE memory SET
    initiative = CASE
        WHEN initiative IS (
            SELECT k.project_key FROM identity_key k WHERE k.ref = memory.project_ref)
        THEN (
            SELECT k.project_key FROM identity_remap r JOIN identity_key k ON k.ref = r.new_identity
            WHERE r.old_identity = memory.project_ref)
        ELSE initiative
    END,
    project_ref = (
        SELECT new_identity FROM identity_remap WHERE old_identity = memory.project_ref)
WHERE project_ref IN (
    SELECT old_identity FROM identity_remap WHERE old_identity <> new_identity);

UPDATE projects SET superseded_by = (
    SELECT new_identity FROM identity_remap WHERE old_identity = projects.superseded_by)
WHERE superseded_by IN (
    SELECT old_identity FROM identity_remap WHERE old_identity <> new_identity);

DELETE FROM apps WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM libraries WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM services WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM features WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM project_facets WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM tf_modules WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM tf_live WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM releases WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM workloads WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM clusters_defined WHERE (workspace, project) IN (SELECT workspace, name FROM identity_loser);
DELETE FROM projects WHERE rowid IN (SELECT rid FROM identity_loser);

UPDATE projects SET project_identity = (
    SELECT r.new_identity FROM identity_remap r WHERE r.rid = projects.rowid)
WHERE rowid IN (SELECT rid FROM identity_survivor)
  AND project_identity IS NOT (
    SELECT r.new_identity FROM identity_remap r WHERE r.rid = projects.rowid);

DROP TABLE identity_key;
DROP TABLE identity_loser;
DROP TABLE identity_survivor;
DROP TABLE identity_remap;
