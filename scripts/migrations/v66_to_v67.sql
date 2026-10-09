-- gaia-compat: backward
-- Migration v66 -> v67: a measured fact says when and how it was measured.
--
-- memory.measured_at and memory.method are written by `gaia memory add
-- --measured-at --method` and shown by `memory show` and `memory get-relevant
-- --initiative`. Both are nullable and forward-only: a row written before v67
-- has no knowable measurement date, so nothing is backfilled.
--
-- Backward: older code selects memory columns by name and ignores these two.

ALTER TABLE memory ADD COLUMN measured_at TEXT;
ALTER TABLE memory ADD COLUMN method TEXT;
