# Gaia Release Reference

Detailed commands for dry-run, beta, and release modes. Read on-demand during release validation.

## Dry-Run Steps (LOCAL)

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
   npm install /path/to/jaguilar87-gaia-X.Y.Z.tgz
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
   `claude --plugin-dir /path/to/gaia-dev/dist/gaia-ops`
8. Run test pyramid:
   - L1: `npm test` (from gaia-dev, not test project)
   - Routing: `python3 tools/gaia_simulator/cli.py "<test prompt>"`

**Default path:** `/tmp/gaia-dry-run-YYYYMMDD/`.
**Cleanup:** Delete `/tmp/gaia-dry-run-*/` when done.

## Beta Steps (PIPELINE)

1. All dry-run steps must pass locally first
2. Version bump with pre-release tag:
   `npm version preminor --preid=beta` (or `premajor` for breaking changes)
3. Commit and push the version bump (PR or direct to main)
4. Create a GitHub Release:
   - Tag: the version from package.json (e.g., `v5.3.0-beta.0`)
   - Title: version number
   - Mark as pre-release
5. `publish.yml` triggers automatically and publishes with `--tag beta`
6. Verify from npm:
   ```
   mkdir /tmp/gaia-beta-verify && cd $_
   npm init -y
   npm install @jaguilar87/gaia@beta
   npx gaia-doctor
   npx gaia-status
   ```

**To promote beta to latest:** `npm dist-tag add @jaguilar87/gaia@X.Y.Z latest`

## Release Steps (PIPELINE)

1. All dry-run steps must pass locally first
2. Version bump:
   `npm version minor` (or `major` / `patch` as appropriate)
3. Commit and push the version bump to main
4. Create a GitHub Release:
   - Tag: the version from package.json (e.g., `v5.3.0`)
   - Title: version number
   - Generate release notes from commits
5. `publish.yml` triggers automatically and publishes with `--tag latest`
6. Verify from npm:
   ```
   mkdir /tmp/gaia-release-verify && cd $_
   npm init -y
   npm install @jaguilar87/gaia@latest
   npx gaia-doctor
   npx gaia-status
   ```

## Pipeline Details

The `publish.yml` workflow (`.github/workflows/publish.yml`) runs on every GitHub Release event. It:
- Checks out the exact tagged commit
- Installs deps with `npm ci`
- Builds plugins with `npm run build:plugins`
- Verifies all expected artifacts in `dist/`
- Commits built artifacts back if changed
- Runs `npm run pre-publish:validate`
- Auto-detects npm tag from version string:
  - `*-beta.*` -> `--tag beta`
  - `*-rc.*` -> `--tag rc`
  - `*-alpha.*` -> `--tag alpha`
  - everything else -> `--tag latest`
- Publishes with `npm publish --access public --tag <detected>`
- `NPM_TOKEN` is stored in GitHub Secrets (never local)

## Path Defaults

| User says | Path used |
|-----------|-----------|
| "here" / "this session" / "this project" | Current working directory |
| "in project X" / specific path | That path |
| Nothing specified (dry-run/beta) | `/tmp/gaia-{mode}-YYYYMMDD/` |
