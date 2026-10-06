-- gaia-compat: backward
-- Migration v62 -> v63: each enabled scheduled task becomes one routine.
--
-- `gaia schedule` and the scheduler that started the host unattended are
-- retired. A scheduled task was recurring work the user wanted done; its
-- successor is a routine notification, due when a read finds it so, pointing
-- at the skill of the same name. Each ENABLED task becomes one open routine,
-- due at once (no `due_at`): the user meets it the next time Gaia is used and
-- `gaia notifications ack` moves it to its next occurrence. A disabled task
-- produces nothing. SQL cannot see the skills directory, so the pointer is
-- always the name; the routine's body says what to do when no such skill exists.
--
-- The four scheduler tables keep every row and are no longer read or written.
-- Their neutral schedule is rewritten into the recurrence shape routines use:
-- calendar fields become lists (sorted, 7 read as Sunday 0) and an interval's
-- `every_seconds` becomes `seconds`.
--
-- Backward: only inserts. A task that already has a routine, open or closed, is
-- not converted again, so running this twice changes nothing.

INSERT INTO task_notifications
    (workspace, task_name, headline, body, unread, kind, due_at, recurrence,
     pointer_kind, pointer_ref)
SELECT
    t.workspace,
    t.name,
    'Run ' || t.name,
    'Converted from a scheduled task when the scheduler was retired. It points at '
        || 'the skill of the same name; if there is none, cancel this routine.',
    0,
    'routine',
    NULL,
    CASE json_extract(t.schedule_spec, '$.kind')
        WHEN 'interval' THEN json_object(
            'kind', 'interval',
            'seconds', json_extract(t.schedule_spec, '$.every_seconds'))
        ELSE json_object(
            'kind', 'calendar',
            'minute', json(CASE json_type(t.schedule_spec, '$.minute')
                WHEN 'array' THEN (SELECT json_group_array(v) FROM (
                    SELECT DISTINCT value AS v FROM json_each(t.schedule_spec, '$.minute') ORDER BY v))
                WHEN 'integer' THEN json_array(json_extract(t.schedule_spec, '$.minute'))
            END),
            'hour', json(CASE json_type(t.schedule_spec, '$.hour')
                WHEN 'array' THEN (SELECT json_group_array(v) FROM (
                    SELECT DISTINCT value AS v FROM json_each(t.schedule_spec, '$.hour') ORDER BY v))
                WHEN 'integer' THEN json_array(json_extract(t.schedule_spec, '$.hour'))
            END),
            'day_of_month', json(CASE json_type(t.schedule_spec, '$.day_of_month')
                WHEN 'array' THEN (SELECT json_group_array(v) FROM (
                    SELECT DISTINCT value AS v FROM json_each(t.schedule_spec, '$.day_of_month') ORDER BY v))
                WHEN 'integer' THEN json_array(json_extract(t.schedule_spec, '$.day_of_month'))
            END),
            'month', json(CASE json_type(t.schedule_spec, '$.month')
                WHEN 'array' THEN (SELECT json_group_array(v) FROM (
                    SELECT DISTINCT value AS v FROM json_each(t.schedule_spec, '$.month') ORDER BY v))
                WHEN 'integer' THEN json_array(json_extract(t.schedule_spec, '$.month'))
            END),
            'day_of_week', json(CASE json_type(t.schedule_spec, '$.day_of_week')
                WHEN 'array' THEN (SELECT json_group_array(v) FROM (
                    SELECT DISTINCT CASE WHEN value = 7 THEN 0 ELSE value END AS v
                    FROM json_each(t.schedule_spec, '$.day_of_week') ORDER BY v))
                WHEN 'integer' THEN json_array(
                    CASE WHEN json_extract(t.schedule_spec, '$.day_of_week') = 7
                         THEN 0 ELSE json_extract(t.schedule_spec, '$.day_of_week') END)
            END))
    END,
    'skill',
    t.name
FROM scheduled_tasks t
WHERE t.enabled = 1
  AND json_valid(t.schedule_spec)
  AND json_extract(t.schedule_spec, '$.kind') IN ('calendar', 'interval')
  AND NOT EXISTS (
      SELECT 1 FROM task_notifications n
      WHERE n.kind = 'routine' AND n.task_name = t.name AND n.workspace IS t.workspace);
