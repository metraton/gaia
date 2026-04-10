---
name: gaia-release
description: Use when testing, validating, or publishing Gaia releases (live testing, dry-run, beta, stable)
metadata:
  user-invocable: false
  type: technique
---

# Gaia Release

Each mode tests a different surface. Live tests your code changes. Dry-run tests the install pipeline. Beta and release test the distribution channel. Skipping a layer means discovering its bugs in production -- a broken symlink in live mode doesn't predict a missing file in `package.json`'s `files` array, and a clean dry-run doesn't prove npm registry delivery works.

## Mode: live (LOCAL)

Symlinks point directly to source -- edits are instant, no build step.

**When:** "test here", "try this out", "put it in live mode"

1. Detect current state: where does `.claude/hooks` point?
2. Create symlinks from target `.claude/` to `gaia-ops-dev/`:
   agents, hooks, skills, config, tools, commands
3. Verify: `npx gaia-doctor`
4. Tell user to restart Claude Code

**Default path:** Current project (cwd). If user says "here" -> cwd.
If user specifies a project -> that path.

**Revert:** `npm install @jaguilar87/gaia-ops` restores release symlinks.

**What live does NOT test:** Build output, package contents, install pipeline. The symlink bypasses all of these -- a file that exists in source but is missing from `package.json` files array will work in live and break in dry-run.

## Mode: dry-run (LOCAL)

Validates the full install flow without publishing. Tests exactly what `npm publish` would ship.

**When:** "test the install", "dry-run", "validate before release"

The core sequence: build plugins -> validate build -> `npm pack` -> install .tgz in clean `/tmp/` project -> run `gaia-doctor` + `gaia-status` -> test both plugin modes (ops and security).

For detailed step-by-step commands, read `reference.md` in this directory.

**Critical:** Always test both modes. Default (ops) validates orchestration and delegation. Security mode (`GAIA_PLUGIN_MODE=security`) validates the stripped-down path with no agents and native T3 dialog. A change that works in one mode can break the other because they load different skill sets and hook configurations.

## Mode: beta (PIPELINE)

Pre-release published to npm via GitHub Actions. Real users install with `@beta` tag.

**When:** "publish beta", "beta release", "pre-release"

All dry-run steps must pass locally first. Then:

1. Bump version with beta pre-release tag (e.g., `npm version preminor --preid=beta`)
2. Merge PR to `main`
3. Create a GitHub Release with the beta version tag (e.g., `v5.3.0-beta.0`)
4. `publish.yml` triggers on the release event -- it builds, validates, and publishes with `--tag beta` automatically

You do not run `npm publish` locally. The pipeline owns the build-validate-publish sequence. For version bump details, see `reference.md`.

## Mode: release (PIPELINE)

Stable release published to npm via GitHub Actions. Users install with `@latest` tag.

**When:** "publish release", "stable release", "ship it"

All dry-run steps must pass locally first. Then:

1. Bump version to stable (e.g., `npm version minor`)
2. Merge PR to `main`
3. Create a GitHub Release with the version tag (e.g., `v5.3.0`)
4. `publish.yml` triggers on the release event -- it builds, validates, and publishes with `--tag latest` automatically

Same as beta: the pipeline owns publishing. `NPM_TOKEN` is stored in GitHub Secrets -- never publish from a local machine.

## Pipeline: publish.yml

The `publish.yml` workflow is the single path to npm for both beta and release. Triggered by GitHub Release events, it:

1. Checks out the tagged commit
2. Runs `npm ci` + `npm run build:plugins`
3. Verifies all plugin artifacts exist in `dist/`
4. Commits built artifacts back to `main` (if changed)
5. Runs `npm run pre-publish:validate`
6. Detects the npm tag from the version string (`-beta.` -> beta, `-rc.` -> rc, else -> latest)
7. Publishes with `npm publish --access public --tag <detected>`

## Decision Tree

```
"I want to test Gaia"
├─ Quick iteration on code? -> live (LOCAL)
├─ Validate before publishing? -> dry-run (LOCAL)
├─ Share pre-release with testers? -> beta (PIPELINE)
└─ Ship to all users? -> release (PIPELINE)
```

## Anti-Patterns

- Testing only in live mode and assuming release works -- live bypasses the build and pack pipeline entirely
- Running `npm publish` locally -- the pipeline owns publishing; local publish bypasses build verification
- Publishing without running dry-run first -- npm pack uses the `files` array, not your filesystem
- Skipping dual-mode testing -- ops and security load different configurations; one can break independently
- Forgetting `npm run build:plugins` before local pack -- stale dist/ means validating old code
- Not telling user to restart Claude Code after mode switch -- the process caches skills at startup
