# Gaia Release -- Reference

Detailed runbooks, diagnostic guide, release checklist, and schema-migration protocol. Read on-demand when actually running a layer or diagnosing a failure. The layers here mirror the three intentions in `SKILL.md`; validation of the resulting install is owned by `gaia-verify`, which this skill calls at each layer's close.

## Layer 1 runbook -- install local (fast iteration)

Install the working tree into a real workspace so a live Claude Code picks it up. pnpm is the default (non-invasive; ignores lifecycle scripts, which is fine because the DB bootstrap is lazy).

**tarball (fidelity to what ships) -- recommended default:**
```
cd /home/jorge/ws/me/gaia
pnpm pack                                   # -> jaguilar87-gaia-<ver>.tgz
cd <TARGET>
pnpm add file:/home/jorge/ws/me/gaia/jaguilar87-gaia-<ver>.tgz
gaia install --workspace <TARGET>
```

**path link (tightest loop) -- reflects the working tree without a repack:**
```
cd <TARGET>
pnpm link /home/jorge/ws/me/gaia            # links the source tree in place
gaia install --workspace <TARGET>
```
`pnpm link --global` was removed in modern pnpm; use a path link (above) or `pnpm add -g .` from the source repo for a global CLI. Trade-off: the tarball proves what a consumer actually receives (respecting `package.json` `files[]`); the path link is faster to iterate but can mask a missing-from-tarball file. Use the tarball before any pre-release work.

**npm equivalent (when the target is an npm project):**
```
cd /home/jorge/ws/me/gaia
npm run gaia:install-local -- --workspace <TARGET>
```
`gaia:install-local` runs `npm pack` (whose `prepack` regenerates the root inline `plugin.json` + `hooks/hooks.json`) + `validate-sandbox.sh --target local`. Prefer pnpm for the fast loop; use this when the target's package manager is npm.

**fresh (wipe install metadata first):**
Append `--fresh` to the `validate-sandbox.sh` form, or manually clear `node_modules/ package.json package-lock.json` (or the pnpm equivalents) in `<TARGET>` before reinstalling. Use when a prior install left state you want gone.

**Always pass `--workspace` when invoking from inside the gaia repo.** The self-referencing `node_modules/@jaguilar87/gaia/` entry tricks the workspace auto-detector; `is_gaia_repo_root()` in `validate-sandbox.sh` guards against it, but explicit is safest:
```
cd /home/jorge/ws/me/gaia
npm pack
bash bin/validate-sandbox.sh \
  --tarball ./jaguilar87-gaia-*.tgz \
  --target local \
  --workspace /home/jorge/ws/me
```

**What `gaia install` does** (the wiring step -- there is no npm postinstall):
1. Bootstraps `~/.gaia/gaia.db` via `scripts/bootstrap_database.sh` (schema, `agent_permissions` seed, project registration, FTS5 backfill, invariant checks) -- idempotent (`IF NOT EXISTS` / `INSERT OR IGNORE`).
2. Seeds `agent_contract_permissions` from agent frontmatters.
3. Configures `.claude/settings.json` and merges gaia permissions + hook events into `.claude/settings.local.json` (npm-surface hooks path).
4. Creates/repairs the `.claude/{agents,tools,hooks,config,skills}` symlinks (+ a `CHANGELOG.md` link) pointing at the installed package.
5. Writes `.claude/plugin-registry.json` with the installed version.

Scanning is NOT part of install. `gaia scan` is a separate, on-demand flow that populates project context in the DB; it never installs or symlinks.

**DB bootstrap without `gaia install`:** the very first `gaia` CLI call in any workspace lazily creates `~/.gaia/gaia.db` (`_ensure_db_bootstrapped` in `bin/gaia`) -- so a plain `pnpm add` followed by any `gaia` command has a DB even before the explicit wiring. The explicit `gaia install` is what wires the *workspace* (`.claude/` symlinks, settings, registry).

**Revert:** reinstall a published version over the same workspace (`pnpm add @jaguilar87/gaia@rc` or `@latest`); the next install wins. `--fresh` is the more aggressive lever.

**Picking up the change:** see `SKILL.md` -> "Reloading a change". npm/pnpm hook edits reload automatically; plugin-surface changes need `/reload-plugins`; a slash-command change needs a full restart.

Under `--target local` the settings-preservation check is **skipped** (no pre-install snapshot of the real workspace is possible); the other checks run.

## Layer 2 runbook -- pre-release (confidence gate, both surfaces, local only)

Zero network. Proves both install surfaces and reproduces CI before any tag exists.

**1 -- version-drift gate (reproduces `validate-manifests`):**
```
npm run pre-publish:validate
```

**2 -- npm surface (what `npm publish` would ship):**
```
npm run gaia:verify-install:local          # pack + install into /tmp/gaia-sandbox-<ts>/ + harness
```
To poke at the sandbox interactively:
```
npm run pre-publish:validate
npm pack                                    # prepack regenerates root manifests
bash bin/validate-sandbox.sh --tarball ./jaguilar87-gaia-*.tgz --target sandbox --stay
```
Sandbox path prints on exit; inspect `.claude/`, rerun checks, then `rm -rf` manually.

**3 -- plugin surface (the dry-run nothing else covers):**
```
npm run gaia:plugin-dryrun                   # pack -> temp extract -> headless validate -> trap cleanup
```
`bin/plugin-dryrun.sh` packs the exact npm tarball (its `prepack` regenerates the root inline `plugin.json` + `hooks/hooks.json`), extracts it to a throwaway temp dir (the package root IS the plugin), and runs a headless, offline gate: filesystem asserts (root inline `plugin.json`, `hooks/hooks.json`, `bin/gaia`, `agents/`, `skills/`, and NO `dist/`) + `claude plugin validate`. It touches no real workspace and spawns no session; both temps are removed by an EXIT trap.

For an optional live functional probe (needs Claude auth/tokens -- opt-in, never implicit):
```
npm run gaia:plugin-dryrun -- --functional   # runs `claude --plugin-dir <temp> -p '...'` from a temp cwd
```
Alternatively, exercise the published marketplace path (after publish) by adding the marketplace and installing from npm:
```
# inside CC:
/plugin marketplace add /home/jorge/ws/me/gaia     # reads .claude-plugin/marketplace.json (source: npm @jaguilar87/gaia)
/plugin install gaia@gaia-marketplace              # CC runs `npm install @jaguilar87/gaia`
/reload-plugins
gaia doctor
```
This is `gaia-verify` mode `plugin`; run `gaia-verify plugin` to score it against the checklist.

**4 -- test pyramid:**
```
npm test                                    # L1 suite
python3 tools/gaia_simulator/cli.py "<test prompt>"   # optional routing check
```

A pass here means both surfaces of the exact artifact are green and CI's drift gate is satisfied.

## Layer 3 runbook -- release (pipeline publish)

The expansion of the "release" intention in `SKILL.md`. The orchestrator runs the steps; the user supplies/confirms the version and approves T3.

1. Layer 2 (pre-release) must be green first.
2. **`npm run release:prepare <version>`** -- the atomic bump. This is `scripts/release-prepare.mjs`, invoked by the flow, **never run by the user by hand**. In one command it:
   - writes `<version>` to the hand-owned sources at once -- `package.json`, `pyproject.toml`, `.claude-plugin/marketplace.json` (top-level `version`), and the `CHANGELOG.md` top header (inserts a dated stub above the current top if absent -- edit its body before release);
   - runs `npm run generate:plugin-root` to regenerate the ROOT `.claude-plugin/plugin.json` (inline hooks) + `hooks/hooks.json` from the manifest -- `plugin.json`'s version is inherited from `package.json` (`from:package.json`), so it is NOT hand-bumped. No `dist/` bundle;
   - runs `npm run pre-publish:validate` and fails loud on any drift.

   This replaces hand-bumping one file at a time. The two real escapes a hand-bump leaves are a `pyproject.toml` left behind on a prior version (caught only by `pre-publish:validate`) and a `marketplace.json` that still advertises the old top-level version. `release:prepare` makes the desync impossible because all hand-owned sources are written from one target version and `plugin.json` is generated from it. For a bare semver: `5.0.5` for stable, `5.1.0-rc.1` for RC (no leading `v` -- the tag adds it). Idempotent: re-running with the same version is a no-op bump that re-validates.
3. Pre-flight that reproduces CI (partly done inside `release:prepare`): `npm test`. `pre-publish:validate` ran in step 2.
4. Commit (`git add` + `git commit` -- local-only, not T3). **If the remote diverged, reconcile with MERGE, never rebase** (see "Reconciling a diverged remote").
5. Tag (force-free -- a *new* `v<version>`, never moved) + push (`git push`, T3). The merge in step 4 keeps the push force-free.
6. Create a GitHub Release:
   - Tag: the version from `package.json` (e.g., `v5.0.0-rc.4` or `v5.3.0`).
   - Title: the version.
   - Mark RC releases as pre-release.
7. `publish.yml` triggers automatically and publishes with `--tag <auto-detected>`.
8. Verify from the registry and install into the target: `gaia-verify registry` (`gaia:verify-install:rc` / `:latest`), then `pnpm add @jaguilar87/gaia@<tag>` + `gaia install` into `<TARGET>` and `gaia-verify live`. The release is done when the published version installs and validates -- not at the tag.

### Reconciling a diverged remote -- merge, never rebase; never move a tag

`publish.yml` no longer commits artifacts back to `main` (it is a read-only checkout that packs + publishes), so a release does **not** auto-advance the remote. But if the remote ever diverges for any other reason, the reconciliation choice is forced by local policy:

- **Reconcile with merge, not rebase.** Rebase rewrites your local commit hashes. If a tag already pointed at one of those commits, you would have to re-point it -- which means `git tag -f` or a force-push of the tag. Both match the `git_destructive` pattern in `hooks/modules/security/blocked_commands.py` and are **hard-denied locally** (exit 2, not approvable) -- there is no `approval_id` that unblocks them. Merge preserves the existing hashes, so existing tags stay valid and no force is ever needed.
- **Tags are create-only -- never move one.** A published tag is immutable history; a new release gets a *new* tag (`-rc.N+1`, next patch/minor), it does not re-point an old one. Moving a tag requires the same force path that local hooks deny.
- **The force-deny is a local hooks policy.** With the `dist/` commit-back removed, `publish.yml` no longer runs `git tag -f` or `git push --force` at all -- it neither commits nor pushes. The local force-deny still stands for any manual reconciliation: never force a tag or push locally; merge instead.

**Verify from npm** (registry round-trip):
- RC: `npm run gaia:verify-install:rc`
- stable: `npm run gaia:verify-install:latest`

**Promote RC to stable:** `npm dist-tag add @jaguilar87/gaia@X.Y.Z latest`.

## Diagnostic Guide

Symptoms encountered in real install sessions, with the root cause and the fix.

| Symptom | Cause | Fix |
|---|---|---|
| Install reports PASS, `~/.gaia/gaia.db` migrated, but `.claude/hooks` symlink still points to an old version after reload | The workspace detector matched the gaia repo itself (self-referencing `node_modules/@jaguilar87/gaia/`) instead of the consumer workspace. Symlinks got wired to the repo's `node_modules`, not the consumer workspace's. | Always pass `--workspace /home/jorge/ws/me` explicitly. `is_gaia_repo_root()` guards this, but explicit `--workspace` is safest. Verify with `readlink /home/jorge/ws/me/.claude/hooks` -- it must resolve under the workspace, not the repo. |
| `.claude/` not wired after install | `gaia install` not run, or it exited non-zero | `cat ~/.gaia/last-install-error.json` (written by `gaia install` on failure). Re-run `gaia install --workspace <path>`. If it persists, file a bug. |
| `gaia doctor` walks up to the user `.claude/` instead of the workspace | Workspace not initialized (`.claude/` missing or no `plugin-registry.json`) | Re-run `gaia install --workspace <path>`. |
| DB missing / `no such table` on first use | Lazy bootstrap did not run (e.g. `gaia` never invoked yet) | Run any `gaia` command (it triggers `_ensure_db_bootstrapped`), or `gaia install` for the full seed. There is no postinstall to "re-run". |
| Plugin mounts but hooks never fire | Generated inline `plugin.json` / `hooks.json` broken at the package root, or CC did not reload | Regenerate (`npm run generate:plugin-root`), re-run `npm run gaia:plugin-dryrun`, and `/reload-plugins`. Inspect the root `.claude-plugin/plugin.json` for the inline `hooks` block. |
| `bootstrap exited 1: table projects has no column named identity` | Schema/bootstrap drift (old seed SQL) | Update to a build whose seed matches the current schema. |
| `Permission denied` invoking a hook `.py` | Exec bit lost on cross-platform checkout | Hooks are invoked via `python3 <script>` (see `build-plugin.py` `generate_hooks_json`), so the exec bit should not matter; if it does, update to a build that uses the `python3 <script>` invoker. |
| Agent says "no conozco Gaia" or "developer agent does not exist" | `settings.local.json` missing or mis-wired (npm surface) | Re-run `gaia install`. In plugin surface, `/reload-plugins`. |
| Same approved command emits a fresh `approval_id` on retry | Not a bug -- single-use per sub-agent invocation is intentional | Re-approve. Each blocked command produces its own approval; there is no batch grant. See `orchestrator-present-approval/SKILL.md` -> Rule 3. |

## Schema Migration Protocol

When you bump `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py`, the four steps below must move in lockstep. `tests/cli/test_schema_version_lockstep.py` verifies the relationship; skipping a step fails in CI.

1. Update `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py`.
2. Add `INSERT INTO schema_version VALUES (N+1, datetime('now'), '<description>');` to `scripts/bootstrap_database.sh` so fresh installs land at the new version.
3. Add migration SQL (`UPDATE` / `ALTER TABLE`) when existing DBs need to upgrade in place; otherwise old workspaces stay below the expected version and `gaia doctor` fails.
4. Run `pytest tests/cli/test_schema_version_lockstep.py` -- it cross-references the constant, the bootstrap insert, and the migration SQL to confirm they all agree.

### Build/pre-publish Schema-Drift Guard

`bin/pre-publish-validate.js` Step 5c runs `scripts/check_schema_drift.py`, which sha256-fingerprints `gaia/store/schema.sql` and compares it against `scripts/migrations/schema.checksum` (pinned to `EXPECTED_SCHEMA_VERSION`). If the schema changed but the version was not bumped and no migration file added, the guard fails the build.

**Consequence:** if you edit `schema.sql` you MUST either (a) bump `EXPECTED_SCHEMA_VERSION` + add the migration file (lockstep above), OR (b) re-pin the checksum with `python3 scripts/check_schema_drift.py --record` (the escape hatch for pure-comment or non-semantic edits). Without one of these, `npm run pre-publish:validate` -- and therefore `release:prepare` -- will FAIL.

## The pre-flight reproduces what CI validates, not a subset of it

A green pre-flight only protects the release if it runs the same gates CI runs. When the local check is a *subset* of CI, the gaps CI covers are discovered after publishing -- on the published tarball, where the only remedy is another release. `npm run gaia:verify-install:local` packs and installs into a sandbox, but it does **not** run `pre-publish:validate`; CI (`.github/workflows/ci.yml`) runs it separately. A real failure escaped exactly through that gap: a `pyproject.toml` version drift that only `pre-publish:validate` catches, green locally and red in CI *after* the tag was pushed. Layer 2 step 1 closes it.

**Pre-publish gate (Layer 2):**
- `pytest tests/` green (or `npm test` for the L1 subset).
- `npm run pre-publish:validate` green locally -- the version-drift gate (`validate-manifests` in `ci.yml`).
- `npm pack --dry-run` confirms `scripts/bootstrap_database.sh`, `.claude-plugin/plugin.json` (with inline hooks), and `hooks/hooks.json` are included in the tarball, and that `dist/` is NOT.
- `npm run gaia:verify-install:local` green (npm surface).
- `npm run gaia:plugin-dryrun` green (plugin surface -- pack + extract + headless validate).

## Pipeline (`publish.yml`)

The workflow at `.github/workflows/publish.yml` runs on every GitHub Release event (read-only checkout -- it neither commits nor pushes). It:
- Checks out the exact tagged commit.
- Installs deps with `npm ci`.
- Packs the tarball with `npm pack` -- `prepack` (clean + `generate:plugin-root`) regenerates the root inline `plugin.json` + `hooks/hooks.json`, so the tarball root is a valid `source: npm` plugin. No `dist/` bundle is built or committed back.
- Runs `npm run pre-publish:validate` (after pack, so it sees the fresh root manifests).
- Auto-detects npm tag from the version string: `*-rc.*` -> `rc`, `*-beta.*` -> `beta`, `*-alpha.*` -> `alpha`, else -> `latest`.
- Runs the sandbox validation harness against the packed tarball (the gate).
- Publishes the same tarball with `npm publish <tarball> --access public --tag <detected>`.

`NPM_TOKEN` lives in GitHub Secrets, never local.

## Path Defaults

| User says | Path used |
|-----------|-----------|
| "here" / "this session" / "this project" / live | Nearest `.claude/` ancestor of cwd with a Gaia marker, falling back to `$HOME/ws/me/` if present |
| "in project X" / specific path | Pass `--workspace /absolute/path/to/project` to `bin/validate-sandbox.sh` (bypasses auto-detect) |
| Nothing specified (pre-release sandbox) | `/tmp/gaia-sandbox-<unix-ts>-<pid>/` (auto-cleanup unless `--stay`) |
