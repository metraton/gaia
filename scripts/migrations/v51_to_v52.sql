-- Migration v51 -> v52: durable provenance for the current task-close epoch.
--
-- harness_events remains the queryable audit channel, but its retention policy
-- cannot answer whether an old override still carries the task's current done
-- state. These nullable ids live on the task state they qualify. Every later
-- status transition clears or replaces them, so historical override events do
-- not become current provenance and repeated verdict evaluation remains
-- idempotent even after old harness events are pruned.

ALTER TABLE tasks ADD COLUMN close_override_event_id INTEGER;
ALTER TABLE tasks ADD COLUMN close_override_divergence_event_id INTEGER;
