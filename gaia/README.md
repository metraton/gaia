# gaia (Python package)

Canonical storage substrate for Gaia state — paths, workspace identity, and
directory layout — provided as a reusable Python library and CLI.

This package is the foundation of **Gaia substrate v6** (brief B0). All other
storage components (B1 SQLite schema, B9 MCP exporters, future briefs) build
on top of `gaia.paths` and `gaia.project`.

## Why

Before B0, every piece of mutable Gaia state lived inside the user's repo
under `.claude/`. That mixed package code with project state and made
"installing Gaia" leave traces in every repo it touched. B0 establishes
a single canonical location — `~/.gaia/` (overridable with `GAIA_DATA_DIR`)
— and a deterministic workspace-identity model so that no Gaia state is
written into the user's repository.

## Public API

### Paths

```python
from gaia.paths import (
    data_dir,         # ~/.gaia (or $GAIA_DATA_DIR)
    db_path,          # data_dir() / "gaia.db"
    snapshot_dir,     # data_dir() / "snapshot"
    state_dir,        # data_dir() / "state"
    workspaces_dir,   # data_dir() / "workspaces"
    logs_dir,         # data_dir() / "logs"
    events_dir,       # data_dir() / "events"
    cache_dir,        # data_dir() / "cache"
    ensure_layout,    # mkdir -p with mode 0700, idempotent
    workspace_id,     # alias for gaia.project.current()
)

# Resolve paths (no I/O)
print(db_path())                # PosixPath('/home/user/.gaia/gaia.db')

# Materialize the layout on first use (mode 0700)
ensure_layout()
```

The resolver functions read `GAIA_DATA_DIR` from the environment on every
call (no caching) so that tests using `monkeypatch.setenv` work as expected.

### Workspace identity

```python
from gaia.project import current, merge, list_known, MergeResult

# Resolve workspace identity from the current directory.
# Three-level fallback:
#   1. Git remote URL normalized to canonical form host/owner/repo
#   2. Directory basename in lowercase
#   3. Literal "global" (no git, no identifiable directory)
print(current())                # 'github.com/metraton/gaia'

# Preview a workspace merge (no files moved)
result = merge("github.com/owner/old-repo", "github.com/owner/new-repo")
for rel, size in result.preview:
    print(f"would move {rel} ({size} bytes)")
for rel in result.conflicts:
    print(f"conflict on {rel}")

# Execute the merge (non-conflicting files are moved; conflicts left in place)
merge("github.com/owner/old-repo", "github.com/owner/new-repo", confirm=True)
```

### Identity normalization rules

The canonical form is `host/owner/repo`:
- All lowercase
- No protocol prefix (`https://`, `git@`, etc.)
- No `.git` suffix
- SSH separator `:` between host and path is normalized to `/`

| Input                                                  | Canonical form                            |
|--------------------------------------------------------|--------------------------------------------|
| `git@github.com:Metraton/Gaia.git`                     | `github.com/metraton/gaia`                 |
| `https://github.com/Metraton/Gaia.git`                 | `github.com/metraton/gaia`                 |
| `https://bitbucket.org/aaxisdigital/bildwiz.git`       | `bitbucket.org/aaxisdigital/bildwiz`       |

When no git remote is configured, the lowercase directory basename is used.
When neither git remote nor identifiable directory is available (e.g. a
global install where `cwd` is `/`), the literal string `"global"` is returned.

## CLI

The package exposes two subcommands of the existing `gaia` CLI:

```sh
gaia paths                      # Print all resolved paths (key=value)
gaia paths data                 # Print only data_dir()
gaia paths db                   # Print only db_path()

gaia project current            # Print resolved workspace identity
gaia project info               # Structured info: identity, cwd, paths
gaia project merge FROM TO      # Preview a workspace merge
gaia project merge FROM TO --confirm   # Execute the merge
```

`gaia paths` always invokes `ensure_layout()` before printing so that the
directory tree under `~/.gaia/` (or `$GAIA_DATA_DIR`) is materialized on
first use with mode 0700.

## Environment variables

| Variable        | Default        | Purpose                                |
|-----------------|----------------|----------------------------------------|
| `GAIA_DATA_DIR` | `~/.gaia`      | Override the root data directory        |

## Standalone use

`gaia.paths` is designed to be importable by external consumers without
pulling in any Claude-Code hook runtime. The package depends only on the
Python standard library (`os`, `pathlib`, `subprocess`, `shutil`, `dataclasses`).

```python
# In a downstream project:
from gaia.paths import db_path
import sqlite3

conn = sqlite3.connect(db_path())
```

## Attribution

`gaia.paths` is inspired by patterns from
[engram](https://github.com/koaning/engram) (MIT License). No engram code
is imported or executed at runtime; the patterns (path resolver with env
override, on-first-use directory creation) were lifted with attribution.

## License

MIT. See repository root `LICENSE`.
