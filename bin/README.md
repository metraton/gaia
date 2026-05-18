# Bin

The `bin/` directory holds the command-line surface of Gaia. There is one user-facing binary -- `gaia` -- and every operation is reached through a subcommand of it. The subcommands are not separate scripts you maintain individually; they are Python modules in `bin/cli/` that the dispatcher discovers at runtime.

The diagnostic model to learn first is `gaia doctor`. Every subcommand follows the same pattern -- parse args, resolve paths, run checks, exit with a status code -- so reading `bin/cli/doctor.py` once tells you how every other subcommand here works.

## Cuándo se activa

```
User runs: gaia <subcommand> [args]
        |
bin/gaia (Python entry point) loads the dispatcher
        |
bin/cli/__init__.py imports every module in bin/cli/ that defines register()
        |
Each module's register(subparsers) attaches its argparse + cmd_<name>() handler
        |
Dispatcher routes to the matched handler, which exits with a status code
```

The npm lifecycle scripts in `package.json` invoke specific subcommands rather than separate binaries:

```
npm install @jaguilar87/gaia
        |
postinstall script -> python3 bin/gaia install --postinstall
        |
Bootstraps the database, merges permissions/hooks, recreates symlinks
```

```
npm uninstall @jaguilar87/gaia
        |
preuninstall script -> python3 bin/gaia uninstall --preuninstall
        |
Cleans temporary caches, old logs, __pycache__, preserves project-context.json
```

No Claude Code session is involved in either case. The subcommands run in a normal Python process and interact with the filesystem directly.

## Qué hay aquí

```
bin/
├── gaia                       # Python entry point — dispatches to bin/cli/<name>
├── pre-publish-validate.js    # Pre-publish gate for the release pipeline
├── python-detect.js           # Python runtime detection helper for npm lifecycles
├── validate-sandbox.sh        # End-to-end consumer-install verification harness
├── README.md
└── cli/                       # Subcommand modules (one file per subcommand)
    ├── __init__.py            # Discovery: imports every sibling that defines register()
    ├── _install_helpers.py    # Shared helpers for install/update (private, leading _)
    ├── approvals.py           # gaia approvals  — list/show/reject/clean/stats T3 grants
    ├── brief.py               # gaia brief      — feature briefs / specs lifecycle
    ├── cleanup.py             # gaia cleanup    — preuninstall: caches, logs, __pycache__
    ├── context.py             # gaia context    — show / scan / diff project-context.json
    ├── doctor.py              # gaia doctor     — system health check (the model to learn)
    ├── history.py             # gaia history    — recent agent sessions
    ├── install.py             # gaia install    — postinstall: bootstrap DB, settings, symlinks
    ├── memory.py              # gaia memory     — episodic memory: stats, search, show
    ├── metrics.py             # gaia metrics    — usage analytics (tier, agent, anomalies)
    ├── paths.py               # Shared path resolution helpers
    ├── plans.py               # gaia plans      — list/show feature plans
    ├── workspace.py           # gaia workspace  — workspace identity / consolidate operations
    ├── scan.py                # gaia scan       — project scanner; refreshes project-context.json
    ├── status.py              # gaia status     — quick installation snapshot
    ├── uninstall.py           # gaia uninstall  — full or preuninstall removal
    └── update.py              # gaia update     — re-sync after npm install bumped the version
```

## Convenciones

**Subcommand contract:** Every file in `bin/cli/` that exposes a subcommand defines two functions:

```python
def register(subparsers) -> None:
    """Attach this subcommand's argparse parser. Called once at startup
    by bin/cli/__init__.py."""
    p = subparsers.add_parser("<name>", help="...")
    p.add_argument(...)
    p.set_defaults(func=cmd_<name>)

def cmd_<name>(args) -> int:
    """Handler. Receives parsed argparse Namespace, returns exit code."""
```

Modules whose name starts with `_` (e.g. `_install_helpers.py`) are private helpers, never registered as subcommands. Files like `paths.py` that expose only utilities and no `register()` are also skipped by the dispatcher.

**Lifecycle binding:** Only `gaia install` (postinstall) and `gaia uninstall` (preuninstall) are wired to npm events via `package.json` `scripts`. The lifecycle calls pass `--postinstall` / `--preuninstall` so the subcommand can apply the more conservative install-time policy.

**Path resolution:** Subcommands resolve paths through symlinks to the source package using `Path.resolve()`. The pattern is visible in `cli/doctor.py`.

**Exit codes:** `0` on success, `1` on warnings, `2` on errors. The release pipeline's sandbox harness relies on these -- do not print a success line and exit non-zero, or vice versa.

**Preserved on cleanup:** `project-context.json` and `.claude/` symlinks are never touched by `gaia cleanup`. Anything the user relies on across reinstalls must be on that preservation list, which lives in `cli/cleanup.py`.

**`package.json` `bin` field:**

```json
{
  "bin": {
    "gaia": "bin/gaia"
  }
}
```

A single binary; subcommands are discovered, not registered.

## Ver también

- [`package.json`](../package.json) -- exposes `bin/gaia`; `scripts.postinstall` / `scripts.preuninstall` wire the lifecycle subcommands
- [`INSTALL.md`](../INSTALL.md) -- installation workflow that calls `gaia scan` and `gaia install`
- [`templates/README.md`](../templates/README.md) -- `gaia install` and `gaia scan` consume templates from here
- [`hooks/README.md`](../hooks/README.md) -- `gaia doctor` verifies the hook registrations are valid
- [`bin/validate-sandbox.sh`](./validate-sandbox.sh) -- end-to-end harness that drives `gaia` subcommands against a fresh tarball install
