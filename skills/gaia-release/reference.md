# Gaia Release -- Reference

Detailed runbooks, diagnostic guide, release checklist, and schema migration protocol. Read on-demand when actually running a mode or diagnosing a failure.

## Mode Runbooks

### live (self or external)

Fresh tarball install over a real workspace. Packs the working tree and installs it like a real consumer would, so a Claude Code restart picks it up.

**self** (install into the workspace where you are running):
```
cd /home/jorge/ws/me/gaia
npm run gaia:install-local
```
The harness detects the workspace by walking up from `cwd` for a `.claude/` with a Gaia instance marker, falling back to `$HOME/ws/me/` if present.

**Important -- always pass `--workspace` when invoking from inside the gaia repo.**
The harness auto-detect skips the gaia repo root itself (guarded by `is_gaia_repo_root()` in `validate-sandbox.sh`), but the safest and most explicit invocation is:
```
cd /home/jorge/ws/me/gaia
npm pack
bash bin/validate-sandbox.sh \
  --tarball ./jaguilar87-gaia-*.tgz \
  --target local \
  --workspace /home/jorge/ws/me
```
This bypasses auto-detect entirely and guarantees the install lands in the correct consumer workspace regardless of cwd ancestry.

**external** (install into a different workspace):
```
cd /home/jorge/ws/me/gaia
npm pack
bash bin/validate-sandbox.sh \
  --tarball ./jaguilar87-gaia-*.tgz \
  --target local \
  --workspace /path/to/target
```
Use `--workspace` to bypass auto-detect when you want a specific project.

**fresh** (wipe install metadata first):
Append `--fresh` to either form. The harness will delete `node_modules/`, `package.json`, and `package-lock.json` in the workspace, then `npm init -y` + `npm install` the tarball. Use this when a previous install left state that you want gone.

**What postinstall does:**
1. Ships `scripts/` (bootstrap_database.sh) -- failed silently in pre-rc.4 builds; verified in `npm pack --dry-run`.
2. Creates `.claude/` if missing.
3. Runs `bootstrap_database.sh` -- seeds the schema, agent rows, and `schema_version`. Fails loud on any error (writes `~/.gaia/last-install-error.json` and exits non-zero).
4. Merges hooks into `settings.local.json` via the consolidated `merge_hooks` step.
5. Creates 7 symlinks under `.claude/` to `node_modules/@jaguilar87/gaia/<dir>/`.
6. Writes `plugin-registry.json` with `installed[].name == "gaia-ops"`.
7. Writes `project-context.json`.

**Revert:** `npm install @jaguilar87/gaia@rc` (or `@latest`) over the same workspace -- the next install wins. The `--fresh` flag is the more aggressive lever.

**Restart:** required after every install. Skills, hooks, and agents cache at startup.

Settings-preservation check is **skipped** under `--target local`: no pre-install snapshot of the real workspace is possible. The other 7 harness checks run.

### dry-run

Validates the full install flow without publishing. Tests exactly what `npm publish` would ship.

The fastest path is `npm run gaia:verify-install:local` -- it packs, installs into `/tmp/gaia-sandbox-<ts>-<pid>/`, runs the 8-check harness, and cleans up.

Manual sequence (use only when you need to poke at the sandbox interactively):
```
npm run build:plugins
npm run pre-publish:validate
npm pack
bash bin/validate-sandbox.sh \
  --tarball ./jaguilar87-gaia-*.tgz \
  --target sandbox \
  --stay
```
Sandbox path prints on exit; inspect `.claude/`, rerun checks, then `rm -rf` manually.

**Test both plugin modes** (requires restarting `claude` in the sandbox dir):
- Default (ops): start `claude`, verify orchestrator, delegation, T3 nonce approval.
- Security: `GAIA_PLUGIN_MODE=security claude`, verify no agents, native T3 dialog.

A change that works in one mode can break the other because they load different skill sets and hook configurations.

**Run the test pyramid before publishing:**
- L1: `npm test` (from gaia-ops-dev, not the sandbox).
- Routing: `python3 tools/gaia_simulator/cli.py "<test prompt>"`.

### RC and stable (pipeline)

Both modes share the same pipeline. The pipeline auto-detects the npm tag from the version string.

1. Dry-run must pass locally first.
2. Version bump:
   - RC: edit `package.json` to `X.Y.Z-rc.N` (the tooling does not provide a single-shot `npm version` for RC; bump manually + rebuild dist/).
   - Stable: `npm version minor` (or `major` / `patch` as appropriate).
3. Rebuild `dist/`: `npm run build:plugins`.
4. Update `dist/*/plugin.json` and `marketplace.json` to match the new version.
5. Commit + push (PR or direct to main).
6. Create a GitHub Release:
   - Tag: the version from `package.json` (e.g., `v5.0.0-rc.4` or `v5.3.0`).
   - Title: the version.
   - Mark RC releases as pre-release.
7. `publish.yml` triggers automatically and publishes with `--tag <auto-detected>`.

**Verify from npm** (registry round-trip):
- RC: `npm run gaia:verify-install:rc`
- stable: `npm run gaia:verify-install:latest`

**Promote RC to stable:** `npm dist-tag add @jaguilar87/gaia@X.Y.Z latest`.

## Diagnostic Guide

Symptoms encountered in real install sessions, with the root cause and the fix. Each row maps to a bug actually fixed in the rc.4 hardening chain (commits `d93451a` through `fd47a74`).

| Symptom | Cause | Fix |
|---|---|---|
| Install reports PASS, `~/.gaia/gaia.db` migrated, but `.claude/hooks` symlink still points to old rc version after restart | `detect_local_workspace()` matched the gaia repo itself (which has `node_modules/@jaguilar87/gaia/` as a self-referencing dep) instead of the consumer workspace. Symlinks got wired to the repo's `node_modules`, not the consumer workspace's. | Always pass `--workspace /home/jorge/ws/me` explicitly. The harness now guards against this with `is_gaia_repo_root()` but explicit `--workspace` is the safest path. Verify with `readlink /home/jorge/ws/me/.claude/hooks` -- it must resolve to `../node_modules/@jaguilar87/gaia/hooks` relative to the workspace, not the repo. |
| `.claude/` not created after `npm install` | Bootstrap script not shipped, or bootstrap exited non-zero | `cat ~/.gaia/last-install-error.json`. Re-install with `npm install @jaguilar87/gaia@latest`. If persists, file a bug. |
| `gaia doctor` walks up to user `.claude/` instead of the workspace | Workspace not initialized (`.claude/` missing or no `plugin-registry.json`) | Re-run `gaia install --workspace <path>` or `gaia scan --fresh`. |
| `mode: security` when it should be `ops` | `plugin-registry.json` not created with `name: gaia-ops` -- bootstrap failed silently in an older build | Check `~/.gaia/last-install-error.json`. Re-install with current rc. |
| `bootstrap exited 1: table projects has no column named identity` | Schema/bootstrap drift (pre-rc.4 seed SQL) | Update to `@jaguilar87/gaia >= 5.0.0-rc.4` (`33b68b4` synced the seed). |
| `[bootstrap] check: distinct agents == 5 (got 6) -- FAIL` | Legacy `gaia-operator` row in DB from an older schema | Update to `>= rc.4` (`174cf62` includes the cleanup migration + lenient count check). |
| `Permission denied` invoking `session_end_hook.py`, `pre_compact.py`, etc. | Exec bit lost on cross-platform checkout | Update to `>= rc.4` (`b45304a` switched the invoker to `python3 <script>`, so exec bit no longer matters). |
| Agent says "no conozco Gaia" or "developer agent does not exist" | `settings.local.json` missing or mis-wired | Re-install. If persists, file a bug. |
| Bash command unexpectedly blocked despite looking innocuous | Bash tokenization bug in `mutative_verbs.py` (pre-Round-2) | Update to `>= rc.4` post-Round-2 (`fd47a74` fundamental tokenization fix). |
| Same approved command emits a fresh `approval_id` on retry | Not a bug -- single-use per sub-agent invocation is intentional | Re-approve. Each blocked command produces its own approval; there is no batch grant. See `orchestrator-present-approval/SKILL.md` -> Rule 3. |

## Schema Migration Protocol

When you bump `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py`, the four steps below must move in lockstep. A `tests/cli/test_schema_version_lockstep.py` test verifies the relationship; if you skip a step it will fail in CI.

1. Update `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py`.
2. Add `INSERT INTO schema_version VALUES (N+1, datetime('now'), '<description>');` to `scripts/bootstrap_database.sh` so fresh installs land at the new version.
3. Add migration SQL (`UPDATE` / `ALTER TABLE`) when existing DBs need to upgrade in place; otherwise old workspaces stay below the expected version and `gaia doctor` fails.
4. Run `pytest tests/cli/test_schema_version_lockstep.py` -- it cross-references the constant, the bootstrap insert, and the migration SQL to confirm they all agree.

## Release Checklist

**Pre-publish:**
- `pytest tests/` green (or `npm test` for the L1 subset).
- `npm pack --dry-run | grep scripts/` confirms `scripts/bootstrap_database.sh` is included in the tarball.
- `bash bin/validate-sandbox.sh --tarball ./jaguilar87-gaia-*.tgz --target sandbox --fresh` green (or `npm run gaia:verify-install:local`).
- Optional smoke: `npm run gaia:install-local -- --workspace /tmp/test-install --fresh`.

**Publish:**
- Bump version in `package.json`, `dist/*/plugin.json`, and `marketplace.json`.
- `npm run build:plugins` regenerates `dist/`.
- Commit + push.
- Create GitHub Release with the version tag.
- Pipeline publishes (`publish.yml` triggers on Release event):
  - `*-rc.*` -> `--tag rc`.
  - `*-beta.*` -> `--tag beta`.
  - `*-alpha.*` -> `--tag alpha`.
  - everything else -> `--tag latest`.

**Post-publish:**
- Smoke test from a fresh workspace: `claude -p '<test prompt>' --output-format json`.
- Run the wire-up verification checklist (see `SKILL.md`).
- If rollback is needed: document the problematic version + steps in the next session.

## Pipeline (`publish.yml`)

The workflow at `.github/workflows/publish.yml` runs on every GitHub Release event. It:
- Checks out the exact tagged commit.
- Installs deps with `npm ci`.
- Builds plugins with `npm run build:plugins`.
- Verifies all expected artifacts in `dist/`.
- Commits built artifacts back if changed.
- Runs `npm run pre-publish:validate`.
- Auto-detects npm tag from version string (see "Publish" above).
- Publishes with `npm publish --access public --tag <detected>`.

`NPM_TOKEN` lives in GitHub Secrets, never local.

## Path Defaults

| User says | Path used |
|-----------|-----------|
| "here" / "this session" / "this project" / live mode | Nearest `.claude/` ancestor of cwd with a Gaia marker, falling back to `$HOME/ws/me/` if present |
| "in project X" / specific path | Pass `--workspace /absolute/path/to/project` to `bin/validate-sandbox.sh` (bypasses auto-detect) |
| Nothing specified (dry-run / verify) | `/tmp/gaia-sandbox-<unix-ts>-<pid>/` (auto-cleanup unless `--stay`) |
