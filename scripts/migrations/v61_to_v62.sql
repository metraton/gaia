-- gaia-compat: backward
-- Migration v61 -> v62: reminders and routines as notifications.
--
-- One notification concept: a report, a one-shot reminder or a recurring
-- routine, due when a read finds `due_at` at or before now, optionally pointing
-- at a skill, a memory or a project. Reminders and routines keep unread = 0 and
-- are open while closed_at IS NULL, so older code, which reads this table as an
-- unread inbox of reports, never counts, lists as unread or acknowledges them.
--
-- Backward: defaulted and nullable columns older code neither reads nor has to
-- write; every existing row becomes a report with no due_at, due as before.

ALTER TABLE task_notifications ADD COLUMN kind TEXT NOT NULL DEFAULT 'report'
    CHECK (kind IN ('report', 'reminder', 'routine'));
ALTER TABLE task_notifications ADD COLUMN due_at TEXT;
ALTER TABLE task_notifications ADD COLUMN recurrence TEXT;
ALTER TABLE task_notifications ADD COLUMN pointer_kind TEXT
    CHECK (pointer_kind IN ('skill', 'memory', 'project'));
ALTER TABLE task_notifications ADD COLUMN pointer_ref TEXT;
ALTER TABLE task_notifications ADD COLUMN pointer_workspace TEXT;
ALTER TABLE task_notifications ADD COLUMN closed_at TEXT;
