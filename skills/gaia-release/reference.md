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
3. Runs `bootstrap_database.sh` -- seeds the schema (v17), agent rows, and `schema_version`. Fails loud on any error (writes `~/.gaia/last-install-error.json` and exits non-zero).
4. Merges hooks into `settings.local.json` via the consolidated `merge_hooks` step.
5. Creates 7 symlinks under `.claude/` to `node_modules/@jaguilar87/gaia/<dir>/`.
6. Writes `plugin-registry.json` with `installed[].name == "gaia-ops"`.

No `project-context.json` is written. Project context lives in `~/.gaia/gaia.db`. Run `gaia scan` in the workspace to populate it after install.

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

Both modes share the same pipeline. The pipeline auto-detects the npm tag from the version string. These steps are the expansion of the "release" intention in `SKILL.md`; the orchestrator runs them, the user supplies/confirms the version.

1. Dry-run must pass locally first.
2. **`npm run release:prepare <version>`** -- the atomic bump. This is `scripts/release-prepare.mjs`, invoked by the flow, **never run by the user by hand**. In one command it:
   - writes `<version>` to ALL sources at once -- `package.json`, `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (every plugin entry), and the `CHANGELOG.md` top header (inserts a dated stub above the current top if absent -- edit its body before release);
   - runs `npm run build:plugins` to regenerate `dist/` (including the per-plugin manifests that carry the version);
   - runs `npm run pre-publish:validate` and fails loud on any drift.

   This replaces hand-bumping one file at a time. `pre-publish:validate` fails the release unless every version source agrees, and the two real escapes a hand-bump leaves are a `pyproject.toml` left behind on a prior version (caught only by `pre-publish:validate`) and a `marketplace.json` that still advertises the old tag. `release:prepare` makes the desync impossible because all sources are written from one target version. For a bare semver: `5.0.5` for stable, `5.1.0-rc.1` for RC (no leading `v` -- the tag adds it). The script is idempotent: re-running with the same version is a no-op bump that re-validates.
3. Pre-flight that reproduces CI (steps already partly done inside `release:prepare`): `npm run check:py39` (the 3.9 union gate) and `npm test`. `pre-publish:validate` ran in step 2; do not skip `check:py39`.
4. Commit (`git add` + `git commit` -- local-only, not T3). **If the remote diverged, reconcile with MERGE, never rebase** (see "Reconciling a diverged remote" below).
5. Tag (force-free -- a *new* `v<version>`, never moved) + push (`git push`, T3). The merge in step 4 keeps the push force-free.
6. Create a GitHub Release:
   - Tag: the version from `package.json` (e.g., `v5.0.0-rc.4` or `v5.3.0`).
   - Title: the version.
   - Mark RC releases as pre-release.
7. `publish.yml` triggers automatically and publishes with `--tag <auto-detected>`.
8. Monitor the workflow run to its outcome, then verify npm serves the new version (`npm run gaia:verify-install:rc` / `:latest`). The release is done at npm, not at the tag.

### Reconciling a diverged remote -- merge, never rebase; never move a tag

`publish.yml` commits built artifacts back to `main` and pushes (the "Commit built plugins" step), so after a release the remote `main` is *ahead* of your local. When you next go to release and find the remote diverged, the reconciliation choice is forced by local policy:

- **Reconcile with merge, not rebase.** Rebase rewrites your local commit hashes. If a tag already pointed at one of those commits, you would have to re-point it -- which means `git tag -f` or a force-push of the tag. Both match the `git_destructive` pattern in `hooks/modules/security/blocked_commands.py` and are **hard-denied locally** (exit 2, not approvable) -- there is no `approval_id` that unblocks them. Merge preserves the existing hashes, so existing tags stay valid and no force is ever needed.
- **Tags are create-only -- never move one.** A published tag is immutable history; a new release gets a *new* tag (`-rc.N+1`, next patch/minor), it does not re-point an old one. Moving a tag requires the same force path that local hooks deny.
- **The force-deny is a local hooks policy, not a CI one.** `publish.yml` itself runs `git tag -f` and `git push --force` for the tag after committing `dist/` -- that is the pipeline operating under its own permissions, outside the local hook layer. Do not read the pipeline's force-push as license to force locally; the local deny stands regardless of what CI does.

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
| `gaia doctor` walks up to user `.claude/` instead of the workspace | Workspace not initialized (`.claude/` missing or no `plugin-registry.json`) | Re-run `gaia install --workspace <path>`. To start completely fresh: `npm install @jaguilar87/gaia` in the workspace directory. |
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

### The pre-flight reproduces what CI validates, not a subset of it

A green pre-flight only protects the release if it runs the same gates CI runs. When the local check is a *subset* of CI, the gaps CI covers are discovered after publishing -- on the published tarball, where the only remedy is another release. `npm run gaia:verify-install:local` packs and installs into a sandbox, but it does **not** run `pre-publish:validate` and it runs only the **local** Python. CI (`.github/workflows/ci.yml`) runs both, across a Python matrix. Two real failures escaped exactly through that gap: a `pyproject.toml` version drift that only `pre-publish:validate` catches, and a Python 3.9 syntax break (`type | None`, which 3.10+ accepts and 3.9 rejects) that only the 3.9 matrix leg catches. Both were green locally and red in CI *after* the tag was pushed.

So the pre-flight must close both gaps before any tag or push:

**Pre-publish:**
- `pytest tests/` green (or `npm test` for the L1 subset).
- **`npm run pre-publish:validate` green locally** -- this is the version-drift gate (`validate-manifests` job in `ci.yml`). Run it before tag/push, not only in CI. It is what catches a `pyproject.toml` / `package.json` / `plugin.json` / `marketplace.json` desync before it ships.
- **`npm run check:py39` green locally** -- the static Python 3.9 union gate (`scripts/check-py39-compat.py`). It runs on any Python 3.x (no 3.9 install needed) and AST-scans the runtime trees (`bin/cli`, `hooks`, `gaia`, `tools`) for PEP 604 `X | None` annotations in modules that lack `from __future__ import annotations` -- exactly the class of break that shipped in 5.0.4. A green on 3.10+ does **not** prove 3.9: that syntax parses on the newer interpreter and fails at annotation-evaluation time on 3.9, invisible until the 3.9 leg runs. This check makes it visible locally. (For full fidelity you can still build under each interpreter -- `python3.9 scripts/build-plugin.py gaia-ops`, etc. -- and re-run the harness, but `check:py39` catches the union class without that setup.)
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
