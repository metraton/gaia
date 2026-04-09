---
name: gaia-devmode
description: Use when testing Gaia changes locally, preparing a pre-release validation, or publishing a beta version
metadata:
  user-invocable: false
  type: technique
---

# Gaia Dev Mode

Three modes for testing Gaia, from fastest iteration to full release validation.

## Mode: live

Test changes in real-time. Symlinks point directly to source — edits are instant.

**When:** "test here", "try this out", "put it in live mode"

1. Detect current state: where does `.claude/hooks` point?
2. Create symlinks from target `.claude/` to `gaia-ops-dev/`:
   agents, hooks, skills, config, tools, commands
3. Verify: `npx gaia-doctor`
4. Tell user to restart Claude Code

**Default path:** Current project (cwd). If user says "here" → cwd.
If user specifies a project → that path.

**Revert:** `npm install @jaguilar87/gaia-ops` restores release symlinks.

**Does NOT test:** Install pipeline, build output, package contents.

## Mode: dry-run

Validate the full install flow without publishing. Tests exactly what `npm publish` would ship.

**When:** "test the install", "dry-run", "validate before release"

1. Build both plugins:
   `npm run build:plugins`
2. Validate build:
   `npm run pre-publish:validate`
3. Pack the package:
   `npm pack` → creates `.tgz` (uses `files` array from package.json)
4. Install in clean project:
   ```
   mkdir /tmp/gaia-dry-run-YYYYMMDD && cd $_
   npm init -y
   npm install /path/to/jaguilar87-gaia-ops-X.Y.Z.tgz
   ```
5. Validate installation:
   ```
   npx gaia-doctor
   npx gaia-status
   npx gaia-skills-diagnose
   ```
6. Test BOTH modes:
   - Default (ops): start `claude`, verify orchestrator, delegation, T3 nonce approval
   - Security: `GAIA_PLUGIN_MODE=security claude`, verify no agents, native T3 dialog
7. Test plugin channel (if applicable):
   `claude --plugin-dir /path/to/gaia-ops-dev/dist/gaia-ops`
8. Run test pyramid:
   - L1: `npm test` (from gaia-ops-dev, not test project)
   - Routing: `python3 tools/gaia_simulator/cli.py "<test prompt>"`

**Default path:** `/tmp/gaia-dry-run-YYYYMMDD/`.
If user specifies a project → that path.

**Cleanup:** Delete `/tmp/gaia-dry-run-*/` when done.

## Mode: beta

Publish a tagged pre-release to npm. Real users can install with `@beta` tag.

**When:** "publish beta", "beta release", "pre-release"

1. All dry-run steps must pass first
2. Version bump with pre-release tag:
   `npm version preminor --preid=beta` (or premajor for breaking changes)
3. Build: `npm run build:plugins`
4. Validate: `npm run pre-publish:validate`
5. Publish with tag:
   `npm publish --tag beta`
6. Verify from npm:
   ```
   mkdir /tmp/gaia-beta-verify && cd $_
   npm init -y
   npm install @jaguilar87/gaia-ops@beta
   npx gaia-doctor
   npx gaia-status
   ```

**To promote beta to latest:** `npm dist-tag add @jaguilar87/gaia-ops@X.Y.Z latest`

## Path Defaults

| User says | Path used |
|-----------|-----------|
| "here" / "this session" / "this project" | Current working directory |
| "in project X" / specific path | That path |
| Nothing specified (dry-run/beta) | `/tmp/gaia-{mode}-YYYYMMDD/` |

## Decision Tree

```
"I want to test Gaia"
├─ Quick iteration on code? → live
├─ Validate before publishing? → dry-run
└─ Share with others for testing? → beta

"Which plugin mode?"
├─ Default → gaia-ops (full orchestration)
├─ GAIA_PLUGIN_MODE=security → gaia-security
└─ Both → test each sequentially
```

## Anti-Patterns

- Testing only in live mode and assuming release works the same
- Publishing without running dry-run first
- Skipping dual-mode testing (ops AND security)
- Forgetting `npm run build:plugins` before pack or publish
- Committing with Verdaccio URLs in lockfiles
- Not telling user to restart Claude Code after mode switch
