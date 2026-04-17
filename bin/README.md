# Bin

The `bin/` directory holds the command-line utilities that surround Gaia — the install helpers, the diagnostics, the status reporters, and the cleanup scripts. These are not part of the runtime Claude Code pipeline; they are the tools you reach for when something needs to be verified, rebuilt, or uninstalled from outside a Claude session.

Each script here is registered in `package.json` under the `bin` field, which makes it callable through `npx` (e.g., `npx gaia-doctor`) once the package is installed. Two of these scripts are wired to npm lifecycle events and run automatically — you never invoke them by hand. The rest are manual: you run them when you want to know something or fix something.

The diagnostic model to learn first is `gaia-doctor`. Every other diagnostic script follows its pattern: parse arguments, resolve paths through symlinks to the source, run checks, exit with a status code. Reading `gaia-doctor.js` once will tell you how every other script here works.

## Cuándo se activa

The scripts in this directory split into two categories based on how they get triggered.

**Category A — npm lifecycle scripts (automatic):**

```
User runs: npm install @jaguilar87/gaia-ops
        |
npm fires postinstall lifecycle event
        |
bin/gaia-update.js runs automatically
        |
Updates hooks template, merges permissions into settings.local.json,
ensures plugin-registry entry
```

```
User runs: npm uninstall @jaguilar87/gaia-ops
        |
npm fires preuninstall lifecycle event
        |
bin/gaia-cleanup.js runs automatically
        |
Cleans temporary caches, old logs (>30 days), __pycache__ directories
Preserves project-context.json and .claude/ symlinks
```

**Category B — manual invocation (on-demand):**

```
User runs: npx gaia-doctor  (or gaia-status, gaia-scan, etc.)
        |
npm/npx resolves the bin entry in package.json
        |
Executes the script
        |
Exits with status code
```

No Claude Code session is involved in either category. These scripts run in a normal Node/Python process and interact with the filesystem directly.

## Qué hay aquí

```
bin/
├── gaia                       # Wrapper for convenience (shell)
├── gaia-scan                  # Project scanner (Python entry)
├── gaia-scan.py               # Python implementation of gaia-scan
├── gaia-update.js             # npm postinstall: updates hooks template, merges permissions
├── gaia-cleanup.js            # npm preuninstall: cleans caches, old logs, __pycache__
├── gaia-doctor.js             # System health check — the diagnostic model to learn first
├── gaia-status.js             # Current system status
├── gaia-skills-diagnose.js    # Skills injection wiring diagnosis
├── gaia-metrics.js            # Metrics and usage statistics
├── gaia-history.js            # Operation history viewer
├── gaia-review.js             # Review engine interface
├── gaia-uninstall.js          # Complete uninstall (manual)
├── pre-publish-validate.js    # Pre-publish validation gate (used by release pipeline)
├── python-detect.js           # Python runtime detection helper
└── cli/                       # Shared CLI utilities
```

## Convenciones

**Lifecycle binding:** Only `gaia-update.js` (postinstall) and `gaia-cleanup.js` (preuninstall) are wired to npm events via `package.json` `scripts`. Every other script is manual.

**npx-invocable list** (from `package.json` `bin`):

```json
{
  "bin": {
    "gaia-scan": "bin/gaia-scan",
    "gaia-doctor": "bin/gaia-doctor.js",
    "gaia-skills-diagnose": "bin/gaia-skills-diagnose.js",
    "gaia-cleanup": "bin/gaia-cleanup.js",
    "gaia-uninstall": "bin/gaia-uninstall.js",
    "gaia-metrics": "bin/gaia-metrics.js",
    "gaia-review": "bin/gaia-review.js",
    "gaia-status": "bin/gaia-status.js",
    "gaia-history": "bin/gaia-history.js",
    "gaia-update": "bin/gaia-update.js"
  }
}
```

**Path resolution:** Scripts must resolve paths through symlinks to the source package. The pattern is visible in `gaia-doctor.js` — use `fs.realpathSync` on the symlink target before running checks.

**Exit codes:** `0` on success, non-zero on failure. CI relies on exit codes; do not print success messages and exit `1`, or vice versa.

**Preserved on cleanup:** `project-context.json` and `.claude/` symlinks are never touched by `gaia-cleanup.js`. Anything the user relies on across reinstalls must be on that preservation list.

## Ver también

- [`package.json`](../package.json) — `bin` field registers these scripts; `scripts.postinstall` / `scripts.preuninstall` wire the lifecycle scripts
- [`INSTALL.md`](../INSTALL.md) — installation workflow that calls these scripts
- [`templates/README.md`](../templates/README.md) — `gaia-update.js` and `gaia-scan.py` consume templates from here
- [`hooks/README.md`](../hooks/README.md) — `gaia-doctor.js` verifies the hook registrations are valid
