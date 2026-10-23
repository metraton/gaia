# gaia.paths

Path resolver and directory-layout manager for the Gaia storage substrate.

This is a sub-package of [`gaia`](../README.md). For full API and CLI
documentation, see the parent README.

## Quick reference

```python
from gaia.paths import (
    data_dir, db_path, snapshot_dir, state_dir,
    workspaces_dir, logs_dir, events_dir, cache_dir,
    scratch_dir, evidence_dir, worktrees_dir, tmp_dir, rejected_turns_dir,
    ensure_layout, workspace_id,
    create_snapshot, enforce_retention, latest_snapshot_age_seconds,
)

ensure_layout()                  # mkdir -p mode 0700, idempotent
print(db_path())                 # ~/.gaia/gaia.db
create_snapshot(db_path(), snapshot_dir(), retain=5, prefix="uninstall")
```

## Modules

| Module      | Purpose                                                        |
|-------------|----------------------------------------------------------------|
| `resolver`  | Pure path-resolution functions (no I/O). Reads `GAIA_DATA_DIR`; `db_path()` also honors `GAIA_DB`, which outranks it for the database file alone. |
| `layout`    | `ensure_layout()` -- materializes the directory tree on first use. |
| `snapshot`  | Shared "copy DB to gzip snapshot + rotate to last N" helper. One implementation used by both `gaia uninstall` (backup-by-default) and the SessionStart auto-backup (throttled 24h). Copy-based -- never moves or deletes the live DB. |
| `backup`    | Every backup of the live database goes through `write_verified_gzip`: SQLite's Online Backup API copies one committed state, `integrity_check` proves it, gzip compresses it, and an atomic rename publishes it. A copy that fails is deleted and never replaces or prunes an existing backup. |
| `__init__`  | Re-exports the public API and the `workspace_id` alias.        |

Retention reads the timestamp in the file name, never the clock. Session-start snapshots keep 7 daily, 4 weekly and 3 monthly (`prune_gfs`; `uninstall` snapshots are exempt). Pre-migration and workspace backups keep the newest 3 of each kind (`prune_newest`); plain `.db` backups from earlier versions are left alone. The outcome of the last session-start run is written to `state/db-backup-status.json`, which `backup_health` turns into the verdict `gaia doctor` shows.

## Attribution

Patterns inspired by [engram](https://github.com/koaning/engram) (MIT).
No runtime dependency on engram.
