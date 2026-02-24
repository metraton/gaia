# Gaia-Ops Utility Scripts

**[Version en espanol](README.md)**

Utility scripts to install, update and manage the gaia-ops package.

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

| Script | Lines | Description |
|--------|-------|-------------|
| `gaia-init.js` | ~1000 | Main installer |
| `gaia-update.js` | ~300 | Configuration updater |

### Cleanup and Uninstall

| Script | Lines | Description |
|--------|-------|-------------|
| `gaia-cleanup.js` | ~200 | Cleans temporary files |
| `gaia-uninstall.js` | ~250 | Complete uninstall |

### Validation

| Script | Lines | Description |
|--------|-------|-------------|
| `pre-publish-validate.js` | ~400 | Pre-publish validation |
| `gaia-skills-diagnose.js` | ~700 | Diagnoses skills, injection wiring, and contract gaps |

## Common Usage

### First Installation

```bash
npm install @jaguilar87/gaia-ops
npx gaia-init
claude-code
```

### Update

```bash
npm update @jaguilar87/gaia-ops
# Postinstall hook updates automatically
```

### Uninstall

```bash
node bin/gaia-uninstall.js
npm uninstall @jaguilar87/gaia-ops
```

## gaia-cleanup.js

**What it cleans:**
- Temporary caches
- Old logs (>30 days)
- __pycache__ directories

**What it preserves:**
- `project-context.json`
- `CLAUDE.md`
- `.claude/` symlinks

## npm Binaries

Defined in `package.json`:

```json
{
  "bin": {
    "gaia-init": "bin/gaia-init.js",
    "gaia-skills-diagnose": "bin/gaia-skills-diagnose.js",
    "gaia-cleanup": "bin/gaia-cleanup.js",
    "gaia-uninstall": "bin/gaia-uninstall.js"
  }
}
```

### Skills and Injection Diagnosis

```bash
# Fast diagnosis (structure + wiring + known gaps)
npx gaia-skills-diagnose

# Include focused pytest probe for skills/injection
npx gaia-skills-diagnose --run-tests

# JSON output for CI
npx gaia-skills-diagnose --json --strict
```

## Environment Variables

```bash
export CLAUDE_GITOPS_DIR="./my-gitops"
export CLAUDE_PROJECT_ID="my-gcp-project"
npx gaia-init --non-interactive
```

## References

- [INSTALL.md](../INSTALL.md) - Installation guide
- [README.md](../README.md) - Package overview

---

**Version:** 1.1.0 | **Updated:** 2026-02-24 | **Scripts:** 6
