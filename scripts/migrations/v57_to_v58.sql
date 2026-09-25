-- Migration v57 -> v58: token usage per API message, and the change history of
-- a brief.
--
-- * token_usage: one row per (session_id, message_id), filled by
--   `gaia usage ingest` from Claude Code transcripts. Structure only here: the
--   migration does not read transcripts.
-- * brief_events + triggers: every later AC edit, decision, brief edit and
--   replaced plan version is recorded as it happens (source='recorded').
-- * Backfill, source='reconstructed': what existing rows still prove -- each
--   brief's creation, each decision, each replaced plan version. Each INSERT
--   skips an event already present, so a replay adds nothing. AC edits made
--   before v58 left no trace and are not invented.

CREATE TABLE IF NOT EXISTS brief_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id    INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    subject     TEXT,
    before      TEXT,
    after       TEXT,
    source      TEXT NOT NULL DEFAULT 'recorded'
                CHECK (source IN ('recorded', 'reconstructed')),
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_brief_events_brief ON brief_events(brief_id, occurred_at);

CREATE TRIGGER IF NOT EXISTS brief_events_brief_created
AFTER INSERT ON briefs
BEGIN
    INSERT INTO brief_events (brief_id, kind, subject, after)
    VALUES (NEW.id, 'brief_created', NEW.name,
            json_object('title', NEW.title, 'status', NEW.status));
END;

CREATE TRIGGER IF NOT EXISTS brief_events_brief_edited
AFTER UPDATE OF title, objective, context, approach, out_of_scope, status ON briefs
WHEN OLD.title IS NOT NEW.title OR OLD.objective IS NOT NEW.objective
  OR OLD.context IS NOT NEW.context OR OLD.approach IS NOT NEW.approach
  OR OLD.out_of_scope IS NOT NEW.out_of_scope OR OLD.status IS NOT NEW.status
BEGIN
    INSERT INTO brief_events (brief_id, kind, subject, before, after)
    VALUES (NEW.id, 'brief_edited', NEW.name,
            json_object('title', OLD.title, 'objective', OLD.objective, 'context', OLD.context,
                        'approach', OLD.approach, 'out_of_scope', OLD.out_of_scope, 'status', OLD.status),
            json_object('title', NEW.title, 'objective', NEW.objective, 'context', NEW.context,
                        'approach', NEW.approach, 'out_of_scope', NEW.out_of_scope, 'status', NEW.status));
END;

CREATE TRIGGER IF NOT EXISTS brief_events_ac_added
AFTER INSERT ON acceptance_criteria
BEGIN
    INSERT INTO brief_events (brief_id, kind, subject, after)
    VALUES (NEW.brief_id, 'ac_added', NEW.ac_id,
            json_object('description', NEW.description, 'evidence_type', NEW.evidence_type,
                        'evidence_shape', NEW.evidence_shape, 'status', NEW.status));
    UPDATE briefs SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = NEW.brief_id;
END;

CREATE TRIGGER IF NOT EXISTS brief_events_ac_edited
AFTER UPDATE ON acceptance_criteria
WHEN OLD.description IS NOT NEW.description OR OLD.evidence_type IS NOT NEW.evidence_type
  OR OLD.evidence_shape IS NOT NEW.evidence_shape OR OLD.artifact_path IS NOT NEW.artifact_path
  OR OLD.status IS NOT NEW.status OR OLD.ac_id IS NOT NEW.ac_id
BEGIN
    INSERT INTO brief_events (brief_id, kind, subject, before, after)
    VALUES (NEW.brief_id, 'ac_edited', NEW.ac_id,
            json_object('description', OLD.description, 'evidence_type', OLD.evidence_type,
                        'evidence_shape', OLD.evidence_shape, 'artifact_path', OLD.artifact_path,
                        'status', OLD.status),
            json_object('description', NEW.description, 'evidence_type', NEW.evidence_type,
                        'evidence_shape', NEW.evidence_shape, 'artifact_path', NEW.artifact_path,
                        'status', NEW.status));
    UPDATE briefs SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = NEW.brief_id;
END;

CREATE TRIGGER IF NOT EXISTS brief_events_ac_removed
AFTER DELETE ON acceptance_criteria
WHEN EXISTS (SELECT 1 FROM briefs WHERE id = OLD.brief_id)
BEGIN
    INSERT INTO brief_events (brief_id, kind, subject, before)
    VALUES (OLD.brief_id, 'ac_removed', OLD.ac_id,
            json_object('description', OLD.description, 'status', OLD.status));
    UPDATE briefs SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = OLD.brief_id;
END;

CREATE TRIGGER IF NOT EXISTS brief_events_decision_added
AFTER INSERT ON brief_decisions
BEGIN
    INSERT INTO brief_events (brief_id, kind, subject, after)
    VALUES (NEW.brief_id, 'decision_added', 'D' || NEW.id,
            json_object('decision', NEW.decision, 'rationale', NEW.rationale,
                        'supersedes_id', NEW.supersedes_id));
END;

CREATE TRIGGER IF NOT EXISTS brief_events_plan_version_replaced
AFTER INSERT ON plan_versions
BEGIN
    INSERT INTO brief_events (brief_id, kind, subject, after)
    SELECT p.brief_id, 'plan_version_replaced', 'plan ' || NEW.plan_id || ' v' || NEW.version,
           json_object('reason', NEW.reason, 'status', NEW.status, 'change_id', NEW.change_id,
                       'chars', length(NEW.content))
    FROM plans p WHERE p.id = NEW.plan_id;
END;

CREATE TABLE IF NOT EXISTS token_usage (
    session_id            TEXT NOT NULL,
    message_id            TEXT NOT NULL,
    harness_agent_id      TEXT,
    agent_type            TEXT NOT NULL,
    model                 TEXT,
    timestamp             TEXT NOT NULL,
    input_tokens          INTEGER NOT NULL DEFAULT 0,
    output_tokens         INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
    transcript_path       TEXT NOT NULL,
    PRIMARY KEY (session_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_token_usage_agent ON token_usage(harness_agent_id) WHERE harness_agent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_token_usage_timestamp ON token_usage(timestamp);

INSERT INTO brief_events (brief_id, kind, subject, after, source, occurred_at)
SELECT b.id, 'brief_created', b.name, json_object('title', b.title, 'status', b.status),
       'reconstructed', b.created_at
FROM briefs b
WHERE NOT EXISTS (SELECT 1 FROM brief_events e
                  WHERE e.brief_id = b.id AND e.kind = 'brief_created');

INSERT INTO brief_events (brief_id, kind, subject, after, source, occurred_at)
SELECT d.brief_id, 'decision_added', 'D' || d.id,
       json_object('decision', d.decision, 'rationale', d.rationale, 'supersedes_id', d.supersedes_id),
       'reconstructed', d.created_at
FROM brief_decisions d
WHERE NOT EXISTS (SELECT 1 FROM brief_events e
                  WHERE e.brief_id = d.brief_id AND e.kind = 'decision_added'
                    AND e.subject = 'D' || d.id);

INSERT INTO brief_events (brief_id, kind, subject, after, source, occurred_at)
SELECT p.brief_id, 'plan_version_replaced', 'plan ' || v.plan_id || ' v' || v.version,
       json_object('reason', v.reason, 'status', v.status, 'change_id', v.change_id,
                   'chars', length(v.content)),
       'reconstructed', v.saved_at
FROM plan_versions v JOIN plans p ON p.id = v.plan_id
WHERE NOT EXISTS (SELECT 1 FROM brief_events e
                  WHERE e.brief_id = p.brief_id AND e.kind = 'plan_version_replaced'
                    AND e.subject = 'plan ' || v.plan_id || ' v' || v.version);
