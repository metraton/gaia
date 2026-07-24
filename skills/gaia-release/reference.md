# Gaia Release -- Reference

Detailed runbooks, diagnostic guide, release checklist, and schema-migration protocol. Read on-demand when actually running a layer or diagnosing a failure. The layers here mirror the three intentions in `SKILL.md`; validation of the resulting install is owned by `gaia-verify`, which this skill calls at each layer's close.

## Layer 1 runbook -- install local (fast iteration)

Install the working tree into a real workspace so a live Claude Code picks it up.

**Primary path -- one command, either mode:**
```
gaia dev --workspace <TARGET>                      # --mode pack (default): pack + install + wire
gaia dev --workspace <TARGET> --mode link           # --mode link: symlink source, no pack, instant iteration
```
`gaia dev` (`bin/cli/dev.py`) is what the manual sequences below collapse into. `--mode pack` runs `npm pack` (via the shared `_pack_helpers.pack_tarball` primitive) against the CURRENT source tree, installs the freshly packed tarball into `<TARGET>`'s `node_modules` (npm or pnpm, auto-detected from lockfile/workspace markers), then wires `.claude/` and bootstraps the DB by invoking the freshly-installed copy's own `gaia install --workspace <TARGET>` -- reflecting a real shippable version and exercising the exact machinery a real consumer would. `--mode link` symlinks `<TARGET>/node_modules/@jaguilar87/gaia` straight at this source tree (no pack, no install) and wires in-process, for the tightest possible loop when fidelity to the shipped tarball does not matter yet. Extra flags: `--keep-tarball`, `--pack-dest <dir>`, `--quiet`, `--verbose` -- see `gaia dev --help`.

`gaia dev` is **T3** (it installs into a workspace) and will block for approval before it runs; the `gaia` launcher and `python3 <path>/bin/gaia dev` classify identically. It runs **no tests** (the fast loop stays cheap; tests are Layer 2/3 + CI) and prints a **restart notice** on success -- restart Claude Code before testing, since the harness pins hook commands at session start (see `SKILL.md` -> "Reloading a change").

**What `gaia dev --mode pack` wraps** (for diagnosing which step failed, or working entirely by hand):

*tarball (fidelity to what ships):*
```
cd /home/jorge/ws/me/gaia
pnpm pack                                   # -> jaguilar87-gaia-<ver>.tgz
cd <TARGET>
pnpm add file:/home/jorge/ws/me/gaia/jaguilar87-gaia-<ver>.tgz
gaia install --workspace <TARGET>
```

*path link (what `--mode link` wraps) -- reflects the working tree without a repack:*
```
cd <TARGET>
pnpm link /home/jorge/ws/me/gaia            # links the source tree in place
gaia install --workspace <TARGET>
```
`pnpm link --global` was removed in modern pnpm; use a path link (above) or `pnpm add -g .` from the source repo for a global CLI. Trade-off: the tarball proves what a consumer actually receives (respecting `package.json` `files[]`); the path link is faster to iterate but can mask a missing-from-tarball file. Use the tarball (`gaia dev`'s default `--mode pack`) before any pre-release work.

**npm equivalent (when the target is an npm project):**
```
cd /home/jorge/ws/me/gaia
npm run gaia:install-local -- --workspace <TARGET>
```
`gaia:install-local` runs `npm pack` (whose `prepack` regenerates the root `plugin.json` (metadata only) + `hooks/hooks.json`) + `validate-sandbox.sh --target local`. `gaia dev --mode pack` auto-detects npm vs pnpm from the target workspace, so it covers this case too; use the raw npm script only when diagnosing.

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
1. Bootstraps `~/.gaia/gaia.db` via `scripts/bootstrap_database.py` (the cross-platform Python bootstrapper on the install/lazy path; `bootstrap_database.sh` is retained for shell/test parity) -- schema, `agent_permissions` seed, project registration, FTS5 backfill, invariant checks; idempotent (`IF NOT EXISTS` / `INSERT OR IGNORE`).
2. Seeds `agent_contract_permissions` from agent frontmatters.
3. Configures `.claude/settings.json` and merges gaia permissions + hook events into `.claude/settings.local.json` (npm-surface hooks path).
4. Creates/repairs the `.claude/{agents,tools,hooks,config,skills}` symlinks (+ a `CHANGELOG.md` link) pointing at the installed package.
5. Writes `.claude/plugin-registry.json` with the installed version.

Scanning is NOT part of install. `gaia scan` is a separate, on-demand flow that populates project context in the DB; it never installs or symlinks.

**DB bootstrap without `gaia install`:** the very first `gaia` CLI call in any workspace lazily creates `~/.gaia/gaia.db` (`_ensure_db_bootstrapped` in `bin/gaia`) -- so a plain `pnpm add` followed by any `gaia` command has a DB even before the explicit wiring. The explicit `gaia install` is what wires the *workspace* (`.claude/` symlinks, settings, registry).

**Revert:** reinstall a published version over the same workspace (`pnpm add @jaguilar87/gaia@rc` or `@latest`); the next install wins. `--fresh` is the more aggressive lever.

**Picking up the change:** see `SKILL.md` -> "Reloading a change". After `gaia dev` re-installs, **restart Claude Code** -- the harness pins hook commands at session start and does not hot-reload, so the open session keeps running the OLD hooks until restarted (`gaia dev` prints this notice). Plugin-surface skill/agent changes need `/reload-plugins`; a slash-command change needs a full restart.

Under `--target local` the settings-preservation check is **skipped** (no pre-install snapshot of the real workspace is possible); the other checks run.

## Layer 2 runbook -- pre-release (confidence gate, both surfaces, local only)

Zero network. Proves both install surfaces and reproduces CI before any tag exists.

`gaia release check` (and `gaia release publish`) validate what will be **PUBLISHED**, which lives only in the SOURCE checkout (the pre-publish validator needs devDependencies, the pack/dry-run gates need `build/gaia.manifest.json`, `npm test` needs `tests/` -- all excluded from the slim installed copy). They resolve the canonical source via `resolve_source_root` and **fail loud** if no source checkout is reachable rather than silently validating the slim installed copy. Run them **from the source checkout** (`python3 <checkout>/bin/gaia release check`) -- there is no env-var escape hatch; do not expect the bare launcher invoked from a consumer workspace to locate the source for you.

**Primary path -- one command, all five gates, always run:**
```
gaia release check                # add --functional for the opt-in live plugin probe
gaia release check --quiet        # suppress per-gate progress, only print the summary
```
`gaia release check` (`bin/cli/release.py`) runs, in order, every gate below and reports a complete PASS/FAIL/SKIP picture -- it never stops at the first red light, so a single run tells you exactly which one broke. Gates 1-4 are each a subprocess call to the existing script (never reimplemented); gate 5 is an in-process read-only inspection via the shared `cli/_converge` inspector. The raw forms below are what gates 1-4 wrap, useful when diagnosing which one failed.

**1 -- version-drift gate (reproduces `validate-manifests`):**
```
npm run pre-publish:validate
```

**2 -- npm surface (what a registry publish would ship):**
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
`bin/plugin-dryrun.sh` packs the exact npm tarball (its `prepack` regenerates the root `plugin.json` (metadata only) + `hooks/hooks.json`), extracts it to a throwaway temp dir (the package root IS the plugin), and runs a headless, offline gate: filesystem asserts (root `plugin.json` present with NO inline `hooks` block, `hooks/hooks.json` with every entry point resolving, `bin/gaia`, `agents/`, `skills/`, and NO `dist/`) + `claude plugin validate`. It touches no real workspace and spawns no session; both temps are removed by an EXIT trap. `gaia release check` SKIPs (not fails) this gate when `claude` is not on PATH.

For an optional live functional probe (needs Claude auth/tokens -- opt-in, never implicit):
```
gaia release check --functional              # or: npm run gaia:plugin-dryrun -- --functional
```
Alternatively, exercise the published marketplace path by adding the marketplace and installing the plugin from its git source:
```
# inside CC:
/plugin marketplace add /home/jorge/ws/me/gaia     # reads .claude-plugin/marketplace.json (source: github metraton/gaia)
/plugin install gaia@gaia-marketplace              # CC clones metraton/gaia into its plugin cache
/reload-plugins
gaia doctor
```
This is `gaia-verify` mode `plugin`; run `gaia-verify plugin` to score it against the checklist.

**4 -- test pyramid:**
```
npm test                                    # L1 suite
python3 tools/gaia_simulator/cli.py "<test prompt>"   # optional routing check
```

**5 -- drift-free convergence (shared with `gaia dev`):** No raw form to wrap -- this gate is an in-process, read-only call to `bin/cli/_converge.py` (`gate_convergence` in `release.py`), the SAME convergence `gaia dev` runs after its reconcile, but with the **origin = the release artifact** (this repo's `package.json` version). It inspects the destination's 5 install surfaces and applies the **schema-DIRECTION guard** (`scripts/bootstrap_database.py`): a live `~/.gaia/gaia.db` NEWER than the artifact's expected schema (reverse-direction drift) is a hard **FAIL** -- installing that artifact would be REFUSED by bootstrap (never ship code older than the DB). Forward/stale surfaces are informational (a release does not reconcile the developer's machine), so only the reverse-direction guard fails the gate; an inspection error is a **SKIP**. To see the same report by hand outside a release: `gaia doctor` (which reports the 5-surface + schema-direction skew) or `gaia dev` (which prints the convergence report after wiring).

A pass here means both surfaces of the exact artifact are green, CI's drift gate is satisfied, and the destination is not carrying a DB newer than the artifact would ship.

## Layer 3 runbook -- release (pipeline publish)

The expansion of the "release" intention in `SKILL.md`. The orchestrator runs the steps; the user supplies/confirms the version and approves T3.

**Primary path -- one command runs steps 2-6 below (step 7 fires automatically as a consequence of step 6), stopping at the first failure:**
```
gaia release publish [version]              # version: bare semver, or patch/minor/major (default: patch)
gaia release publish --dry-run [version]    # preview the six-step sequence, execute nothing
```
`gaia release publish` (`bin/cli/release.py`) runs `release:prepare` -> `npm test` -> `git commit` -> `git tag` -> `git push --follow-tags` -> `gh release create`, in that order, STOPPING at the first failure (unlike `release check`'s always-run-all-gates design -- these steps are causally dependent). The first four steps are local and un-gated; the last two are Tier 3 and the hook layer will require your approval before they run -- that is expected, not retried around. It never runs npm's own registry-publish command directly: that command runs only inside `.github/workflows/publish.yml`, gated behind `NODE_AUTH_TOKEN`. `--dry-run` prints the resolved version and all six planned commands (with the two Tier-3 ones flagged) without spawning any subprocess.

**Preconditions gate (T0, before step 1).** `run_release_publish` first runs `preflight_publish` -- a read-only gate that fails EARLY, loud, and actionably rather than mid-sequence. If any check fails, **no step runs** and the failure names its own fix:

| Precondition | Checked via | On failure |
|--------------|-------------|-----------|
| Active `gh` account has push/admin on `metraton/gaia` | `gh api repos/metraton/gaia -q .permissions.push` | `push:false` / unauthenticated -> actionable FAIL naming `gh auth switch -u <account>` / `gh auth login`. **Transient / no-network / `gh` missing is "could not verify" -> does NOT block** (only a definite "no" blocks). This is a real Layer 3 precondition because step 6 (`gh release create`) is a Tier-3 `gh` mutation. |
| Tag `v<version>` does NOT already exist (local **or** remote) | `git rev-parse --verify refs/tags/v<version>` + `git ls-remote --tags origin v<version>` | Actionable FAIL: finish a half-completed release with `gh release create v<version>`, or delete the tag (`git tag -d v<version>` [+ `git push origin :refs/tags/v<version>`]) and re-run. Attacks the "tag already exists" atasco. |
| pytest-xdist is importable | `importlib.util.find_spec("xdist")` | Actionable FAIL in ~1s (not after the full npm-test wait): `pip install pytest-xdist`. `npm test` runs pytest with `-n auto`, which cannot start without it. |

**`npm test` timeout is configurable.** The `npm test` gate (step 2, shared with `gaia release check`) defaults to a **1800s** timeout (raised from 1200s as the L1 suite grew), overridable per-run via the **`GAIA_RELEASE_NPM_TEST_TIMEOUT`** env var (a positive integer of seconds; a malformed or non-positive value is ignored and falls back to the default). On expiry it reports an explicit `TIMEOUT after Ns -- ... This is a TIMEOUT, not a test failure.` message naming the env-var lever and pytest-xdist, instead of the raw `TimeoutExpired` (which reads like a failing test).

1. Layer 2 (pre-release) must be green first -- run `gaia release check`.
2. **`release:prepare <version>`** (`gaia release publish` step 1) -- the atomic bump. This wraps `scripts/release-prepare.mjs`, invoked by the flow, **never run by the user by hand**. In one command it:
   - writes `<version>` to the hand-owned sources at once -- `package.json`, `pyproject.toml`, `.claude-plugin/marketplace.json` (top-level `version`), and the `CHANGELOG.md` top header (inserts a dated stub above the current top if absent -- edit its body before release);
   - runs `npm run generate:plugin-root` to regenerate the ROOT `.claude-plugin/plugin.json` (metadata only -- no inline hooks) + `hooks/hooks.json` from the manifest -- `plugin.json`'s version is inherited from `package.json` (`from:package.json`), so it is NOT hand-bumped. No `dist/` bundle;
   - runs `npm run pre-publish:validate` and fails loud on any drift.

   This replaces hand-bumping one file at a time. The two real escapes a hand-bump leaves are a `pyproject.toml` left behind on a prior version (caught only by `pre-publish:validate`) and a `marketplace.json` that still advertises the old top-level version. `release:prepare` makes the desync impossible because all hand-owned sources are written from one target version and `plugin.json` is generated from it. For a bare semver: `5.0.5` for stable, `5.1.0-rc.1` for RC (no leading `v` -- the tag adds it). Idempotent: re-running with the same version is a no-op bump that re-validates.
3. Pre-flight that reproduces CI (partly done inside `release:prepare`) -- `gaia release publish` step 2: `npm test` (reuses the same `gate_npm_test` helper `gaia release check` uses). `pre-publish:validate` ran in step 2 above.
4. Commit -- `gaia release publish` step 3: `git add` (the version-source paths only) + `git commit` -- local-safe, not T3. Idempotent: nothing-to-commit on a tree already at the target version is a PASS. **If the remote diverged, reconcile with MERGE, never rebase** (see "Reconciling a diverged remote").
5. Tag -- `gaia release publish` step 4: `git tag -a` (force-free -- a *new* `v<version>`, never `--force`). **Idempotent (P1a):** if the tag already exists AND points at the current release HEAD, this is a PASS/skip -- so a re-run after a LATE failure (push or `gh release create`) advances *through* the tag to the gh-release step instead of dying on it. If the tag exists but points at a DIFFERENT commit, it is a clear FAIL (the tag is never moved silently -- delete and re-run, or publish the existing tag). Then push -- step 5: `git push --follow-tags` (T3; pushes the commit and the tag in one push). The merge in step 4 above keeps the push force-free.
6. Create a GitHub Release -- `gaia release publish` step 6: `gh release create v<version> --title v<version> --generate-notes` (T3):
   - Tag: the version from `package.json` (e.g., `v5.0.0-rc.4` or `v5.3.0`).
   - Title: the version.
   - RC/beta/alpha versions get `--prerelease` automatically.
7. `publish.yml` triggers automatically (as a consequence of step 6) and publishes with `--tag <auto-detected>`.
8. Verify from the registry and reinstall every local workspace: `gaia-verify registry` (`gaia:verify-install:rc` / `:latest`), then reinstall EACH active local workspace. **A publish never touches a workspace installed from a local `file:` tarball** -- its `package.json`/lockfile still reference the pre-release dev-pack tarball, so it keeps running the OLD code until reinstalled. Per workspace: `gaia dev --workspace <TARGET>` (re-packs the tagged tree and re-wires) or `pnpm add @jaguilar87/gaia@<tag>` + `gaia install` against the published tarball, then `gaia-verify live`. To find WHICH local workspaces are still on stale code, run `gaia doctor` in each: its **Install provenance** check (order 57) detects a `file:` install and reports whether it is fresh vs source, hinting `gaia dev --workspace <ws>` to fix. (There is no bulk `sync-local` command -- that action-at-a-distance was removed in favor of per-workspace `gaia dev` + the `doctor` diagnostic.) The release is done when the published version installs and validates in every target -- not at the tag.

**Why the reinstall is mandatory, not optional:** `gaia dev`'s content-addressed tarball naming (`jaguilar87-gaia-<version>+<sha8>.tgz`) guarantees a *changed* build gets a fresh pnpm store key, but only when `gaia dev` is actually re-run. A publish alone leaves the local `file:` install frozen at whatever it last packed. Skipping step 8's reinstall is exactly how a fixed release keeps exhibiting the old behaviour in a local workspace.

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
| Plugin mounts but hooks never fire | Generated `hooks/hooks.json` broken at the package root, or CC did not reload | Regenerate (`npm run generate:plugin-root`), re-run `npm run gaia:plugin-dryrun`, and `/reload-plugins`. Inspect the root `hooks/hooks.json` (the canonical hook source); `.claude-plugin/plugin.json` is metadata only and must NOT carry an inline `hooks` block. |
| `bootstrap exited 1: table projects has no column named identity` | Schema/bootstrap drift (old seed SQL) | Update to a build whose seed matches the current schema. |
| `gaia contract finalize` (or another CLI) errors `no column named ...`; `gaia doctor` reports `schema_version=N > code expected M` | **Reverse-direction drift**: the live `~/.gaia/gaia.db` was migrated by a NEWER Gaia than the code now running -- stale code mis-reading a newer schema. | Install code at least as new as the DB by running the drift-safe convergence -- `gaia dev` (from a source checkout whose `EXPECTED_SCHEMA_VERSION` >= the DB) or `gaia install`/`gaia release` (artifact). The bootstrap direction guard now REFUSES this case (no clobber) and migrates forward when the code is newer; do **not** `npm install @latest` a build older than the DB, and **never** downgrade the DB. |
| `Permission denied` invoking a hook `.py` | Exec bit lost on cross-platform checkout | Hooks are invoked via `python3 <script>` (see `build-plugin.py` `generate_hooks_json`), so the exec bit should not matter; if it does, update to a build that uses the `python3 <script>` invoker. |
| Agent says "no conozco Gaia" or "developer agent does not exist" | `settings.local.json` missing or mis-wired (npm surface) | Re-run `gaia install`. In plugin surface, `/reload-plugins`. |
| Same approved command emits a fresh `approval_id` on retry | Not a bug -- single-use per sub-agent invocation is intentional | Re-approve. Each blocked command produces its own approval; there is no batch grant. See `orchestrator-present-approval/SKILL.md` -> Rule 3. |
| A T3 release command (`git push`, `gh release`, `gaia dev`) is re-blocked right after you approved it | The grant signature binds to the **cwd** the command was approved in; an execution environment that resets cwd between calls (e.g. an agent harness) signs the retry from a different cwd, so it no longer matches the grant | Invoke T3 commands with **absolute paths** (`git -C <abs> push`, `gaia dev --workspace <abs>`, run `gh` from an explicit dir) so the signed command is cwd-independent and the grant matches on retry |

## Schema Migration Protocol

When you bump `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py`, the four steps below must move in lockstep. `tests/cli/test_schema_version_lockstep.py` verifies the relationship; skipping a step fails in CI.

1. Update `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py` -- the single source of truth for the target version (`_read_expected_schema_version()` in the bootstrapper reads it).
2. Add the forward-migration file `scripts/migrations/v{N-1}_to_v{N}.sql` -- the `ALTER TABLE` / `UPDATE` / `CREATE` that advances a DB from `N-1` to `N`. This is **required**, not optional: the bootstrapper ABORTS with "missing migration file" if `EXPECTED_SCHEMA_VERSION` is bumped past the migrations on disk.
3. Do NOT hand-write any `schema_version` insert into a bootstrap script. The canonical bootstrapper is `scripts/bootstrap_database.py` (the cross-platform install/lazy path; `bootstrap_database.sh` is retained for shell/test parity -- see the same distinction in "What `gaia install` does" above). It uses the **floor + forward-migration** model: it seals a fresh DB at `SCHEMA_FLOOR` from `schema.sql`, then applies every pending migration from `floor+1` up to `_read_expected_schema_version()`, stamping each in the ledger with `INSERT OR IGNORE INTO schema_version (version, applied_at, description) VALUES (n, ...)` automatically -- so shipping the migration file (step 2) is what makes fresh installs and in-place upgrades both land at the new version.
4. Run `pytest tests/cli/test_schema_version_lockstep.py` -- it verifies `EXPECTED_SCHEMA_VERSION` agrees with the migration files present (equal to the highest `v{N-1}_to_v{N}.sql` target, or the floor when none exist yet).

### Build/pre-publish Schema-Drift Guard

`bin/pre-publish-validate.js` Step 5c runs `scripts/check_schema_drift.py`, which sha256-fingerprints `gaia/store/schema.sql` and compares it against `scripts/migrations/schema.checksum` (pinned to `EXPECTED_SCHEMA_VERSION`). If the schema changed but the version was not bumped and no migration file added, the guard fails the build.

**Consequence:** if you edit `schema.sql` you MUST either (a) bump `EXPECTED_SCHEMA_VERSION` + add the migration file (lockstep above), OR (b) re-pin the checksum with `python3 scripts/check_schema_drift.py --record` (the escape hatch for pure-comment or non-semantic edits). Without one of these, `npm run pre-publish:validate` -- and therefore `release:prepare` -- will FAIL.

## The pre-flight reproduces what CI validates, not a subset of it

A green pre-flight only protects the release if it runs the same gates CI runs. When the local check is a *subset* of CI, the gaps CI covers are discovered after publishing -- on the published tarball, where the only remedy is another release. `npm run gaia:verify-install:local` packs and installs into a sandbox, but it does **not** run `pre-publish:validate`; CI (`.github/workflows/ci.yml`) runs it separately. A real failure escaped exactly through that gap: a `pyproject.toml` version drift that only `pre-publish:validate` catches, green locally and red in CI *after* the tag was pushed. Layer 2 step 1 closes it.

**Pre-publish gate (Layer 2):**
- `pytest tests/` green (or `npm test` for the L1 subset).
- `npm run pre-publish:validate` green locally -- the version-drift gate (`validate-manifests` in `ci.yml`).
- `npm pack --dry-run` confirms `scripts/bootstrap_database.sh`, `.claude-plugin/plugin.json` (metadata only), and `hooks/hooks.json` (the canonical hook source) are included in the tarball, and that `dist/` is NOT.
- `npm run gaia:verify-install:local` green (npm surface).
- `npm run gaia:plugin-dryrun` green (plugin surface -- pack + extract + headless validate).

## Pipeline (`publish.yml`)

The workflow at `.github/workflows/publish.yml` runs on every GitHub Release event (read-only checkout -- it neither commits nor pushes). It:
- Checks out the exact tagged commit.
- Installs deps with `npm ci`.
- Packs the tarball with `npm pack` -- `prepack` (clean + `generate:plugin-root`) regenerates the root `plugin.json` (metadata only) + `hooks/hooks.json`, so the tarball root is a valid plugin root (the same tree the git plugin source serves). No `dist/` bundle is built or committed back.
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
