---
name: gaia-devmode
description: Use when testing Gaia changes locally, preparing a pre-release validation, or publishing a beta version
metadata:
  user-invocable: false
  type: technique
---

# Gaia Dev Mode

Each mode tests a different surface. Live tests your code changes. Dry-run tests the install pipeline. Beta tests the distribution channel. Skipping a layer means discovering its bugs in production -- a broken symlink in live mode doesn't predict a missing file in `package.json`'s `files` array, and a clean dry-run doesn't prove npm registry delivery works.

## Mode: live

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

## Mode: dry-run

Validates the full install flow without publishing. Tests exactly what `npm publish` would ship.

**When:** "test the install", "dry-run", "validate before release"

The core sequence: build plugins -> validate build -> `npm pack` -> install .tgz in clean `/tmp/` project -> run `gaia-doctor` + `gaia-status` -> test both plugin modes (ops and security).

For detailed step-by-step commands, read `reference.md` in this directory.

**Critical:** Always test both modes. Default (ops) validates orchestration and delegation. Security mode (`GAIA_PLUGIN_MODE=security`) validates the stripped-down path with no agents and native T3 dialog. A change that works in one mode can break the other because they load different skill sets and hook configurations.

## Mode: beta

Publish a tagged pre-release to npm. Real users install with `@beta` tag.

**When:** "publish beta", "beta release", "pre-release"

All dry-run steps must pass first -- beta adds npm registry delivery on top of dry-run validation. For version bumping, publishing, and verification commands, read `reference.md`.

## Decision Tree

```
"I want to test Gaia"
├─ Quick iteration on code? -> live
├─ Validate before publishing? -> dry-run
└─ Share with others for testing? -> beta

"Which plugin mode?"
├─ Default -> gaia-ops (full orchestration)
├─ GAIA_PLUGIN_MODE=security -> gaia-security
└─ Both -> test each sequentially
```

## Anti-Patterns

- Testing only in live mode and assuming release works -- live bypasses the build and pack pipeline entirely
- Publishing without running dry-run first -- npm pack uses the `files` array, not your filesystem
- Skipping dual-mode testing -- ops and security load different configurations; one can break independently
- Forgetting `npm run build:plugins` before pack or publish -- stale dist/ means shipping old code
- Not telling user to restart Claude Code after mode switch -- the process caches skills at startup
