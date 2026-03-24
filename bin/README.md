# Gaia-Ops Utility Scripts

Utility scripts to install, update, diagnose, and manage the gaia-ops package.

## Purpose

Automate common package management tasks, providing a friendly interface for operations that would otherwise require complex manual steps.

## How It Works

```
User executes bin/script
        |
[Script] detects current state
        |
    Executes actions
    |               |
[Installation]   [Cleanup]
    |               |
Configure symlinks  Remove files
```

## Available Scripts

### Installation and Setup

| Script | Description |
|--------|-------------|
| `gaia-scan` | Project scanner and installer (Python) |
| `gaia-update.js` | Configuration updater (postinstall hook) — updates hooks template, merges permissions into settings.local.json, ensures plugin-registry |

### Diagnostics and Monitoring

| Script | Description |
|--------|-------------|
| `gaia-doctor.js` | System health check |
| `gaia-skills-diagnose.js` | Diagnoses skills, injection wiring, and contract gaps |
| `gaia-status.js` | Current system status |
| `gaia-metrics.js` | Metrics and usage statistics |
| `gaia-history.js` | Operation history viewer |
| `gaia-review.js` | Review engine interface |

### Cleanup and Uninstall

| Script | Description |
|--------|-------------|
| `gaia-cleanup.js` | Cleans temporary files (preuninstall hook) |
| `gaia-uninstall.js` | Complete uninstall |

### Validation

| Script | Description |
|--------|-------------|
| `pre-publish-validate.js` | Pre-publish validation |

## npm Binaries

Defined in `package.json`:

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

## Common Usage

### First Installation

```bash
npm install @jaguilar87/gaia-ops
npx gaia-scan
claude
```

### Update

```bash
npm update @jaguilar87/gaia-ops
# Postinstall hook updates automatically
```

### Diagnostics

```bash
# System health check
npx gaia-doctor

# Skills diagnosis (structure + wiring + known gaps)
npx gaia-skills-diagnose

# Include focused pytest probe for skills/injection
npx gaia-skills-diagnose --run-tests

# JSON output for CI
npx gaia-skills-diagnose --json --strict

# View metrics
npx gaia-metrics

# View operation history
npx gaia-history
```

### Uninstall

```bash
npx gaia-uninstall
npm uninstall @jaguilar87/gaia-ops
```

## gaia-cleanup.js

**What it cleans:**
- Temporary caches
- Old logs (>30 days)
- __pycache__ directories

**What it preserves:**
- `project-context.json`
- `.claude/` symlinks

## Environment Variables

```bash
export CLAUDE_GITOPS_DIR="./my-gitops"
export CLAUDE_PROJECT_ID="my-gcp-project"
npx gaia-scan --non-interactive
```

## References

- [INSTALL.md](../INSTALL.md) - Installation guide
- [README.md](../README.md) - Package overview

---

**Version:** 4.5.0 | **Updated:** 2026-03-24 | **Scripts:** 11
