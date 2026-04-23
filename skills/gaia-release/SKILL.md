---
name: gaia-release
description: Use when testing, validating, or publishing Gaia releases (live testing, dry-run, beta, stable)
metadata:
  user-invocable: false
  type: technique
---

# Gaia Release

Each mode tests a different surface. Live tests your code changes. Dry-run tests the install pipeline. Beta and release test the distribution channel. Skipping a layer means discovering its bugs in production -- a broken symlink in live mode does not predict a missing file in `package.json`'s `files` array, and a clean dry-run does not prove npm registry delivery works.

## Decision Tree

```
"I want to test Gaia"
├─ Quick iteration on code? -> live (LOCAL)
├─ Validate before publishing? -> dry-run (LOCAL)
├─ Share pre-release with testers? -> beta (PIPELINE)
└─ Ship to all users? -> release (PIPELINE)
```

## Mode: live

Symlinks point directly to source -- edits are instant, no build step.

**When:** "test here", "try this out", "put it in live mode"

1. Detect current state: where does `.claude/hooks` point?
2. Create symlinks from target `.claude/` to `gaia-dev/`: agents, hooks, skills, config, tools, commands
3. Verify: `npx gaia-doctor`
4. Tell user to restart Claude Code

**Default path:** Current project (cwd). If user says "here" -> cwd. If user specifies a project -> that path.

**Revert:** `npm install @jaguilar87/gaia` restores release symlinks.

Live mode does not test build output, package contents, or install pipeline. A file present in source but missing from `package.json` files array will work in live and break in dry-run.

## Mode: dry-run

Validates the full install flow without publishing. Tests exactly what `npm publish` would ship.

**When:** "test the install", "dry-run", "validate before release"

Core sequence: build plugins -> validate build -> `npm pack` -> install .tgz in clean `/tmp/` project -> run `gaia-doctor` + `gaia-status` -> test both plugin modes (ops and security).

For step-by-step commands, see `reference.md`.

Test both modes: default (ops) validates orchestration and delegation. Security mode (`GAIA_PLUGIN_MODE=security`) validates the stripped-down path with no agents and native T3 dialog. A change that works in one mode can break the other because they load different skill sets and hook configurations.

## Mode: beta

Pre-release published to npm via GitHub Actions. Install with `@beta` tag.

**When:** "publish beta", "beta release", "pre-release"

Dry-run must pass first. Then: bump version with beta pre-release tag -> merge PR to `main` -> create GitHub Release with beta version tag -> `publish.yml` triggers automatically.

For version bump details and verification steps, see `reference.md`.

## Mode: release

Stable release published to npm via GitHub Actions. Install with `@latest` tag.

**When:** "publish release", "stable release", "ship it"

Same flow as beta with a stable version bump. The pipeline owns publishing -- `NPM_TOKEN` is in GitHub Secrets.

For step-by-step commands, see `reference.md`.

## Pipeline: publish.yml

Triggered by GitHub Release events. Builds plugins, validates artifacts, auto-detects npm tag from version string (`-beta.` -> beta, `-rc.` -> rc, else -> latest), and publishes. Details in `reference.md`.

## Anti-Patterns

- **Live-only testing** -- live bypasses the build and pack pipeline entirely; dry-run catches what live cannot.
- **Local npm publish** -- the pipeline owns publishing; local publish bypasses build verification.
- **Single-mode testing** -- ops and security load different configurations; one can break independently.
- **Stale dist/** -- forgetting `npm run build:plugins` before pack means validating old code.
- **Missing restart** -- the process caches skills at startup; mode switches require restart.
