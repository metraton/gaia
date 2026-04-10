# Gaia Dev Mode Reference

Detailed commands for dry-run and beta modes. Read on-demand during release validation.

## Dry-Run Steps

1. Build both plugins:
   `npm run build:plugins`
2. Validate build:
   `npm run pre-publish:validate`
3. Pack the package:
   `npm pack` -> creates `.tgz` (uses `files` array from package.json)
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
**Cleanup:** Delete `/tmp/gaia-dry-run-*/` when done.

## Beta Steps

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
