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

gaia workspace current          # Print resolved workspace identity
gaia workspace info             # Structured info: identity, cwd, paths
gaia workspace merge FROM TO    # Preview a merge of workspace files
gaia workspace merge FROM TO --confirm  # Execute the merge
gaia workspace retire SRC --into DST --dry-run  # Report what folding SRC's gaia.db rows into DST moves
gaia workspace retire SRC --into DST --yes      # Fold them (backup + undo ledger; T3)
gaia workspace retire --undo LEDGER --yes       # Put back what that retire changed
```

`workspace retire` re-keys what SRC owns (projects, briefs, contracts,
integrations, schedules, memory) and leaves its history rows keyed to SRC,
recording the alias `SRC -> DST` that readers of DST follow
(`gaia/store/workspace_retire.py`).

`gaia paths` always invokes `ensure_layout()` before printing so that the
directory tree under `~/.gaia/` (or `$GAIA_DATA_DIR`) is materialized on
first use with mode 0700.

## Environment variables

| Variable        | Default        | Purpose                                |
|-----------------|----------------|----------------------------------------|
| `GAIA_DATA_DIR` | `~/.gaia`      | Override the root data directory (ROOT-scoped: moves the database and every sibling directory) |
| `GAIA_DB`       | `<root>/gaia.db` | Override the database file alone (FILE-scoped: leaves scratch, evidence and logs under the root) |

Precedence for the database file, highest first: **`GAIA_DB`**, then
**`GAIA_DATA_DIR`**, then `~/.gaia/gaia.db`. `GAIA_DB` outranks `GAIA_DATA_DIR`
because it is the more specific of the two, and because every other
database-path resolver in the tree already ranks it that way
(`scripts/bootstrap_database.py`, where the variable originated;
`bin/cli/doctor.py`; `bin/cli/_converge.py`; `bin/gaia`;
`bin/validate-sandbox.sh`; and the CI workflow, which sets `GAIA_DB` alone).
Setting both at *different* places prints a warning to stderr naming which one
won; setting both at the same file is the established isolation idiom and stays
silent. The ladder is pinned by `tests/paths/test_db_path_precedence.py`.

### Declaring a wrapper as another CLI (a tool row)

The security hooks judge a command by what it does and where it lands, not by
the binary that runs it.

Opening or editing your own pull request runs free for any forge CLI. Commenting,
approving, closing, merging or retargeting asks for your signature. So does
anything that changes production or a protected branch. A force push asks too;
it is never refused outright.

Some gates are still tied to one CLI's name: the permanent blocks
(`gh repo delete`) and a few consent anchors, such as `gh workflow run`. A
wrapper you installed around a CLI (a launcher that pins an account, for
example) runs the same subcommands under another name, so those gates do not
recognize it on their own.

A user tool row in memory points them at the wrapper. Gaia ships no wrapper
names; declare yours once, with the wrapper on PATH:

```bash
gaia memory add --name=user_tool_mywrapper --type=user --kind=tool \
  --binary=mywrapper --replaces=gh \
  --description='mywrapper pr create ...' --body='what it is and why'
```

The hooks read every live tool row's binary and `replaces` once per process,
matched against the command's base name. With no tool row there is no alias.

A declared wrapper is also read as the CLI it wraps, and the stricter verdict
stands. It is blocked wherever that CLI is blocked and asks wherever that CLI
asks. A declared name the classifier already models, such as `kubectl=gh`,
`ssh=gh` or `shred=gh`, keeps its own gates as well. Only a wrapper name the
classifier knows nothing about also takes the wrapped CLI's free verdicts.
The declaration belongs to you: Gaia's installer does not write it, so set it
in your shell profile or in the `env` block of the host settings you own (for
Claude Code, `~/.claude/settings.json`). A leading `-C <dir>` on a declared `gh` wrapper is skipped
before the publish guard reads the subcommand. Malformed pairs are ignored. The
hooks read the variable at start-up, so restart the host after changing it.
Implemented in `hooks/modules/security/cli_aliases.py`; pinned by
`tests/hooks/modules/security/test_cli_alias_from_config.py`.

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
