---
name: gaia-release
description: Use when testing, validating, or publishing Gaia releases (live testing, dry-run, beta, stable)
metadata:
  user-invocable: false
  type: technique
---

# Gaia Release

Each mode tests a different surface. Live tests your code changes in your real workspace. Dry-run tests the install pipeline in an ephemeral sandbox. Beta and release test the distribution channel. Skipping a layer means discovering its bugs in production -- a live install over an existing workspace does not predict a missing file in `package.json`'s `files` array on a clean project, and a clean dry-run does not prove npm registry delivery works.

## Decision Tree

```
"I want to test Gaia"
├─ Quick iteration on code? -> live (LOCAL)
├─ Validate before publishing? -> dry-run (LOCAL)
├─ Share pre-release with testers? -> beta (PIPELINE)
└─ Ship to all users? -> release (PIPELINE)
```

## Mode: live

Fresh tarball install over the current workspace -- packs the working tree and installs it like a real consumer would, but into the user's `.claude/` so restarts pick it up.

**When:** "test here", "try this out", "put it in live mode"

1. From the gaia-ops-dev repo root, run: `npm run gaia:install-local`
   - Runs `npm pack` to build the tarball from the working tree
   - Invokes `bin/validate-sandbox.sh --target local` which detects the workspace (`$HOME/ws/me/` or the first `.claude/` parent walking up from cwd), installs the tarball there, and runs the 8-check harness (settings-preservation check is skipped -- no pre-snapshot possible for a real workspace).
2. Tell user: "Gaia fresh-installed locally from dev working tree. Restart Claude Code to activate."

**Default path:** Detected by the harness. `$HOME/ws/me/` if present, otherwise nearest `.claude/` ancestor.

**Revert:** `npm install @jaguilar87/gaia@rc` (or `@latest`) over the same workspace -- the next install wins.

Live mode now uses tarball install (no symlinks) to avoid approval flood when editing hooks/skills during development. Editing a symlinked file under `.claude/hooks/` triggers a per-path approval prompt on every subsequent hook invocation, which compounds rapidly across a session. A fresh tarball install gives a stable working tree for the session; re-run `npm run gaia:install-local` when you want to pick up new edits.

Live mode still does not test build output's consumer path end-to-end in a clean project -- dry-run (`gaia:verify-install:local` -> sandbox in `/tmp/`) does.

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

- **Live-only testing** -- live tests the tarball on your actual workspace but with your accumulated state; an ephemeral sandbox (`gaia:verify-install:local`) is still needed to prove a clean-install works.
- **Local npm publish** -- the pipeline owns publishing; local publish bypasses build verification.
- **Single-mode testing** -- ops and security load different configurations; one can break independently.
- **Stale dist/** -- forgetting `npm run build:plugins` before pack means validating old code.
- **Missing restart** -- the process caches skills at startup; mode switches require restart.
- **Symlink-based live mode** -- deprecated. Editing a symlinked file under `.claude/hooks/` or `.claude/skills/` triggers per-path approval prompts on every hook invocation. Fresh-install flow avoids this.
