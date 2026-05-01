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
)

ensure_layout()                  # mkdir -p mode 0700, idempotent
print(db_path())                 # ~/.gaia/gaia.db
```

## Modules

| Module      | Purpose                                                        |
|-------------|----------------------------------------------------------------|
| `resolver`  | Pure path-resolution functions (no I/O). Reads `GAIA_DATA_DIR`.|
| `layout`    | `ensure_layout()` -- materializes the directory tree on first use. |
| `__init__`  | Re-exports the public API and the `workspace_id` alias.        |

## Attribution

Patterns inspired by [engram](https://github.com/koaning/engram) (MIT).
No runtime dependency on engram.
