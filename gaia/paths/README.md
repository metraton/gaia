# gaia.paths

Path resolver and directory-layout manager for the Gaia storage substrate.

This is a sub-package of [`gaia`](../README.md). For full API and CLI
documentation, see the parent README.

## Quick reference

```python
from gaia.paths import (
    data_dir, db_path, snapshot_dir, state_dir,
    workspaces_dir, logs_dir, events_dir, cache_dir,
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
| `resolver`  | Pure path-resolution functions (no I/O). Reads `GAIA_DATA_DIR`.|
| `layout`    | `ensure_layout()` -- materializes the directory tree on first use. |
| `snapshot`  | Shared "copy DB to gzip snapshot + rotate to last N" helper. One implementation used by both `gaia uninstall` (backup-by-default) and the SessionStart auto-backup (throttled 24h). Copy-based -- never moves or deletes the live DB. |
| `__init__`  | Re-exports the public API and the `workspace_id` alias.        |

## Attribution

Patterns inspired by [engram](https://github.com/koaning/engram) (MIT).
No runtime dependency on engram.
