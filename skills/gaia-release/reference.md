# Gaia Release -- Reference

Detailed runbooks, diagnostic guide, release checklist, and schema-migration protocol. Read on-demand when actually running a layer or diagnosing a failure. The layers here mirror the three intentions in `SKILL.md`; validation of the resulting install is owned by `gaia-verify`, which this skill calls at each layer's close.

## Layer 1 runbook -- install local (fast iteration)

Install the working tree into a real install folder so a live Claude Code picks it up.

**Primary path -- one command per channel, from the PR's worktree:**
```
python3 <pr-worktree>/bin/gaia dev --channel npm --workspace <dev-install-folder>       # then restart Claude Code
python3 <pr-worktree>/bin/gaia dev --channel plugin --workspace <dev-install-folder>    # then /reload-plugins
python3 <pr-worktree>/bin/gaia dev --channel opencode --workspace <dev-install-folder>  # then restart OpenCode
gaia dev --from-worktree <pr-worktree> --ref <full-sha> --channel npm --workspace <dev-install-folder>
gaia doctor --workspace <dev-install-folder>    # Install provenance: one line per channel, source commit + commits behind
```
`gaia dev` (`bin/cli/dev.py`) is what the manual sequence below collapses into. It runs `npm pack` (via the shared `_pack_helpers.pack_tarball` primitive) against the chosen source tree and serves that tarball through the channel. `npm` installs it into the install folder's `node_modules` (npm or pnpm, auto-detected from lockfile/workspace markers) and runs the freshly-installed copy's own `gaia install` for that channel; `opencode` does the same and wires OpenCode; `plugin` extracts it into a stable per-install-folder directory that is the local marketplace `gaia-dev`, installs `gaia@gaia-dev` at local scope and disables `gaia@gaia-marketplace` in that install folder (two enabled copies would register every `gaia:*` skill and every hook twice). The channel is required and there is no `all`: without `--channel` the command fails listing the three. `npm` and `plugin` refuse each other in one install folder, naming the channel found and the command that removes it; `opencode` joins either. There is no source-linking mode: the install folder, its `.claude/`, and every global alias only ever depend on the packed tarball. The folder that runs the published version (`<released-install-folder>`) is never a `gaia dev` target. Other flags: `--host` (alias of `--channel`), `--pack-dest <dir>`, `--quiet`, `--verbose` -- see `gaia dev --help`.

`gaia dev` is **T3** (it installs into an install folder) and will block for approval before it runs; the `gaia` launcher and `python3 <path>/bin/gaia dev` classify identically. It runs **no tests** (the fast loop stays cheap; tests are Layer 2/3 + CI) and prints a **restart notice** on success -- restart Claude Code before testing, since the harness pins hook commands at session start (see `SKILL.md` -> "Reloading a change").

**What `gaia dev` wraps** (for diagnosing which step failed, or working entirely by hand):

*tarball (fidelity to what ships):*
```
cd <gaia-source-checkout>
pnpm pack                                   # -> jaguilar87-gaia-<ver>.tgz
cd <TARGET>
pnpm add file:<gaia-source-checkout>/jaguilar87-gaia-<ver>.tgz
pnpm exec gaia install --channel npm --workspace <TARGET>
```

Making `gaia` itself available globally from source is a separate concern from wiring a consumer install folder: `pnpm link --global` was removed in modern pnpm, so use `pnpm link <path-to-this-repo>` from the target project, or `pnpm add -g .` from this repo, for a global CLI. `gaia dev` itself only ever wires a consumer install folder's tarball dependency -- there is no source-linking mode, so the tarball above is what any pre-release work should exercise.

**npm equivalent (when the target is an npm project):**
```
cd <gaia-source-checkout>
npm run gaia:install-local -- --workspace <TARGET>
```
`gaia:install-local` runs `npm pack` (whose `prepack` regenerates the root `plugin.json` (metadata only) + `hooks/hooks.json`) + `validate-sandbox.sh --target local`. `gaia dev` auto-detects npm vs pnpm from the target install folder, so it covers this case too; use the raw npm script only when diagnosing.

**fresh (wipe install metadata first):**
Append `--fresh` to the `validate-sandbox.sh` form, or manually clear `node_modules/ package.json package-lock.json` (or the pnpm equivalents) in `<TARGET>` before reinstalling. Use when a prior install left state you want gone.

**Always pass `--workspace` when invoking from inside the gaia repo.** The self-referencing `node_modules/@jaguilar87/gaia/` entry tricks the install-folder auto-detector; `bin/validate-sandbox.sh::is_gaia_repo_root` guards against it, but explicit is safest:
```
cd <gaia-source-checkout>
npm pack
bash bin/validate-sandbox.sh \
  --tarball ./jaguilar87-gaia-*.tgz \
  --target local \
  --workspace <dev-install-folder>
```

**What `gaia install` does** (the wiring step -- there is no npm postinstall):
1. Runs `gaia migrate apply` on `~/.gaia/gaia.db` (engine `scripts/bootstrap_database.py`; `bootstrap_database.sh` is a shell/test reference only) -- a fresh DB is built from `schema.sql`; an existing one is backed up to `<db dir>/backups/` and moved along its pending chain in one transaction; then `agent_permissions` seed, project registration, FTS5 backfill, invariant checks. A chain that reaches existing rows stops install and names `gaia migrate apply --consent-chain vA..vB`.
2. Seeds `agent_contract_permissions` from agent frontmatters.
3. Configures `.claude/settings.json` and merges gaia permissions + hook events into `.claude/settings.local.json` (npm-surface hooks path).
4. Creates/repairs the `.claude/{agents,tools,hooks,config,skills,opencode}` symlinks (+ a `CHANGELOG.md` link) pointing at the installed package.
5. Writes `.claude/plugin-registry.json` with the installed version.

Scanning is NOT part of install. `gaia scan` is a separate, on-demand flow that populates project context in the DB; it never installs or symlinks.

**DB bootstrap without `gaia install`:** the very first `gaia` CLI call in any folder lazily creates `~/.gaia/gaia.db` (`_ensure_db_bootstrapped` in `bin/gaia`) -- so a plain `pnpm add` followed by any `gaia` command has a DB even before the explicit wiring. The explicit `gaia install` is what wires the *install folder* (`.claude/` symlinks, settings, registry); it declares no workspace.

**Revert:** reinstall a published version over the same install folder (`pnpm add @jaguilar87/gaia@rc` or `@latest`); the next install wins. `--fresh` is the more aggressive lever.

**Picking up the change:** see `SKILL.md` -> "Reloading a change". After `gaia dev` re-installs, **restart Claude Code** -- the harness pins hook commands at session start and does not hot-reload, so the open session keeps running the OLD hooks until restarted (`gaia dev` prints this notice). Plugin-surface skill/agent changes need `/reload-plugins`; a slash-command change needs a full restart.

Under `--target local` the settings-preservation check is **skipped** (no pre-install snapshot of the real install folder is possible); the other checks run.

## Layer 2 runbook -- pre-release (confidence gate, every channel, local only)

No registry; the only network use is gate 4's read-only GitHub API lookup of the CI verdict. Proves the package, plugin and OpenCode surfaces and reproduces CI before any tag exists.

`gaia release check` (and `gaia release publish`) validate what will be **PUBLISHED**, which lives only in the SOURCE checkout (the pre-publish validator needs devDependencies, the pack/dry-run gates need `build/gaia.manifest.json`, `npm test` needs `tests/` -- all excluded from the slim installed copy). They resolve the canonical source via `resolve_source_root` and **fail loud** if no source checkout is reachable rather than silently validating the slim installed copy. Run them **from the source checkout** (`python3 <checkout>/bin/gaia release check`) -- there is no env-var escape hatch; do not expect the bare launcher invoked from a consumer install folder to locate the source for you.

**Primary path -- one command, all six gates, always run:**
```
gaia release check                # add --functional for the opt-in live plugin probe
gaia release check --quiet        # suppress per-gate progress, only print the summary
```
`gaia release check` (`bin/cli/release.py`) first runs publish's node-deps step (see "Node deps" under the Layer 3 runbook); a failure there is the only result. Then it runs, in order, every gate below and reports a complete PASS/FAIL/SKIP picture -- it never stops at the first red light, so a single run tells you exactly which one broke. Gates 1-4 are each a subprocess call to the existing script (never reimplemented); gate 5 is an in-process read-only inspection via the shared `cli/_converge` inspector, and gate 6 (`opencode:surface`) an in-process inspection of gate 2's tarball. The raw forms below are what gates 1-4 wrap, useful when diagnosing which one failed.

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
`bin/plugin-dryrun.sh` packs the exact npm tarball (its `prepack` regenerates the root `plugin.json` (metadata only) + `hooks/hooks.json`), extracts it to a throwaway temp dir (the package root IS the plugin), and runs a headless, offline gate: filesystem asserts (root `plugin.json` present with NO inline `hooks` block, `hooks/hooks.json` with every entry point resolving, `bin/gaia`, `agents/`, `skills/`, and NO `dist/`) + `claude plugin validate`. It touches no real install folder and spawns no session; both temps are removed by an EXIT trap. `gaia release check` SKIPs (not fails) this gate when `claude` is not on PATH.

For an optional live functional probe (needs Claude auth/tokens -- opt-in, never implicit):
```
gaia release check --functional              # or: npm run gaia:plugin-dryrun -- --functional
```
To exercise the packed plugin in a live Claude Code before any tag exists, serve it through the dev channel instead of a marketplace:
```
python3 <pr-worktree>/bin/gaia dev --channel plugin --workspace <dev-install-folder>
# inside CC, in that install folder:
/reload-plugins
# then, from a terminal:
gaia doctor --workspace <dev-install-folder>
```
The `gaia` entry of `.claude-plugin/marketplace.json` has `source: "."`, so a marketplace added from a ref serves that ref's tree: after a tag exists, `/plugin marketplace add metraton/gaia#v<version>` + `/plugin install gaia@gaia-marketplace` exercises the published path. This is `gaia-verify` mode `plugin`; run `gaia-verify plugin` to score it against the checklist.

**4 -- test pyramid:**
```
npm test                                    # L1 suite
python3 tools/gaia_simulator/cli.py "<test prompt>"   # optional routing check
```
`gaia release check` runs `npm test` only when no green `CI verdict` covers HEAD's tree: it first runs `.github/scripts/ci_verdict.py <HEAD> --repo metraton/gaia --gh <program>` (the `--gh` program the release was given) and, on exit 0, PASSes citing that CI run. HEAD not pushed, a working tree that differs from HEAD, CI red or pending, or no network (the lookup times out at 60s) all fall back to `npm test`; `--local-suite` forces it.

**5 -- drift-free convergence (shared with `gaia dev`):** No raw form to wrap -- this gate is an in-process, read-only call to `bin/cli/_converge.py` (`gate_convergence` in `release.py`), the SAME convergence `gaia dev` runs after its reconcile, but with the **origin = the release artifact** (this repo's `package.json` version). It inspects the destination's 5 install surfaces and applies the **schema-DIRECTION guard** (`scripts/bootstrap_database.py`): a live `~/.gaia/gaia.db` NEWER than the artifact's expected schema (reverse-direction drift) is a hard **FAIL** -- installing that artifact would be REFUSED by bootstrap (never ship code older than the DB). Forward/stale surfaces are informational (a release does not reconcile the developer's machine), so only the reverse-direction guard fails the gate; an inspection error is a **SKIP**. To see the same report by hand outside a release: `gaia doctor` (which reports the 5-surface + schema-direction skew) or `gaia dev` (which prints the convergence report after wiring).

**6 -- OpenCode surface (`opencode:surface`):** No raw form either -- `gate_opencode_surface` in `release.py` extracts gate 2's tarball, wires it into a temp install folder the way `gaia install --channel opencode` does, and FAILs naming each missing piece: `opencode/plugin.ts`, a file it resolves by relative path (`./bridge.py`, `../bin/gaia`), an agent `{file:...}` prompt in the generated `opencode.json`, or a skill link. It never starts OpenCode; the live check stays manual.

A pass here means the package, plugin and OpenCode surfaces of the exact artifact are green, CI's drift gate is satisfied, and the destination is not carrying a DB newer than the artifact would ship.

## Layer 3 runbook -- release (pipeline publish)

The expansion of the "release" intention in `SKILL.md`. The orchestrator runs the steps; the user supplies/confirms the version and approves T3.

**Primary path -- one command runs items 2-8 below (item 9 fires automatically as a consequence of item 8), stopping at the first failure:**
```
gaia release publish [version] --gh ghx               # version: bare semver, or patch/minor/major (default: patch)
gaia release publish --dry-run [version] --gh ghx     # preview the eight-step sequence, mutate nothing
gaia release publish --local-suite [version] --gh ghx # run npm test in step 4 even when CI already passed HEAD
gaia release publish [version] --gh ghx --branch NAME # push to origin's NAME instead of the derived target
```
`gaia release publish` (`bin/cli/release.py`) runs node deps -> `release:prepare` -> sandbox install -> tests (CI verdict or local suite) -> `git commit` -> `git tag` -> `git push --atomic origin HEAD:refs/heads/<branch> refs/tags/v<version>` -> `<gh> release create`, in that order, STOPPING at the first failure (unlike `release check`'s always-run-all-gates design -- these steps are causally dependent). The first six steps are local and un-gated; the last two are Tier 3 and the hook layer will require your approval before they run -- that is expected, not retried around. It never runs npm's own registry-publish command directly: that command runs only inside `.github/workflows/publish.yml`, which publishes through npm trusted publishing (OIDC) with no npm token. `--dry-run` prints the resolved version, the push target, the account the `--gh` program runs as, and all eight planned commands (with the two Tier-3 ones flagged, and step 4 shown as "CI verdict or local suite"); the only subprocesses it runs are the read-only lookups that name the target (`git rev-parse`, `git ls-remote`) and the account (`<gh> api user`). Every gh call runs through `--gh` with the repository as cwd -- see SKILL.md, "The GitHub account is resolved per process".

**Preconditions gate (T0, before step 1).** `run_release_publish` first runs `preflight_publish` -- a read-only gate that fails EARLY, loud, and actionably rather than mid-sequence. If any check fails, **no step runs** and the failure names its own fix:

| Precondition | Checked via | On failure |
|--------------|-------------|-----------|
| The `--gh` program's account has push/admin on `metraton/gaia` | `<gh> api repos/metraton/gaia -q .permissions.push`, cwd = the repository | Missing program, an account the program cannot resolve or has no token for (e.g. `ghx: owner ... maps to no account`), unauthenticated gh, or `push:false` -> actionable FAIL quoting the program's own error and naming `--gh <program>` (e.g. `--gh ghx`), with `gh auth login` for a missing account. Never `gh auth switch` -- see SKILL.md, "The GitHub account is resolved per process". **A network failure is "could not verify" -> does NOT block.** This is a real Layer 3 precondition because step 8 (`<gh> release create`) is a Tier-3 `gh` mutation that would otherwise fail after the tag is pushed. |
| Tag `v<version>` does NOT already exist (local **or** remote) | `git rev-parse --verify refs/tags/v<version>` + `git ls-remote --tags origin v<version>` | Actionable FAIL: finish a half-completed release with `gh release create v<version>`, or delete the tag (`git tag -d v<version>` [+ `git push origin :refs/tags/v<version>`]) and re-run. Attacks the "tag already exists" atasco. |
| pytest-xdist is importable | `importlib.util.find_spec("xdist")` | Actionable FAIL in ~1s (not after the full npm-test wait): `pip install pytest-xdist`. `npm test` runs pytest with `-n auto`, which cannot start without it. |
| The push target resolves and HEAD fast-forwards it | `--branch`, else `git rev-parse --abbrev-ref --symbolic-full-name @{upstream}` (an `origin/` upstream), else `git ls-remote origin refs/heads/*` (the one branch whose tip is HEAD); then `git ls-remote origin refs/heads/<branch>` + `git merge-base --is-ancestor <tip> HEAD` | FAIL asking for `--branch` when no single target resolves; FAIL when origin's tip is not in HEAD's history: merge it (never rebase) and re-run. The local branch name never decides the target. |
| A stable version pushes to `main` (a pre-release to any branch) | the resolved push target, only when the version has no `-rc.`/`-beta.`/`-alpha.` | FAIL naming the target and `main`: merge to `main` and publish there, or publish `<version>-rc.N`. An unresolved target is not `main`. No bypass flag. |

**`npm test` timeout is configurable.** When the tests step (step 4, shared with `gaia release check`) falls back to the local suite, `npm test` defaults to a **1800s** timeout -- it applies only to that fallback, never to a reused CI verdict -- (raised from 1200s as the L1 suite grew), overridable per-run via the **`GAIA_RELEASE_NPM_TEST_TIMEOUT`** env var (a positive integer of seconds; a malformed or non-positive value is ignored and falls back to the default). On expiry it reports an explicit `TIMEOUT after Ns -- ... This is a TIMEOUT, not a test failure.` message naming the env-var lever and pytest-xdist, instead of the raw `TimeoutExpired` (which reads like a failing test).

1. Layer 2 (`gaia release check`) is where the plugin and OpenCode surfaces are proven; publish does not run those gates. It no longer needs Layer 2 for the npm sandbox install or for Node dependencies -- publish runs both itself, below.
2. **Node deps** (`gaia release publish` step 1, `step_node_deps`). A fresh worktree has no `node_modules`, and `release:prepare` imports `chalk`. When any dependency `package.json` declares is missing from `node_modules`, the step runs `npm ci --ignore-scripts --no-audit --no-fund`: a frozen install of `package-lock.json` with no lifecycle scripts (the root declares no install script and `chalk`/`eslint` ship none). When all of them are present it installs nothing. A missing lockfile, or one npm refuses as out of sync, FAILs here before any bump, quoting npm and naming the command to rerun. No manual `npm ci` before publishing.
3. **`release:prepare <version>`** (`gaia release publish` step 2) -- the atomic bump. This wraps `scripts/release-prepare.mjs`, invoked by the flow, **never run by the user by hand**. In one command it:
   - writes `<version>` to the hand-owned sources at once -- `package.json`, `pyproject.toml`, and the `CHANGELOG.md` top header (`scripts/changelog-bump.mjs`: a stable `X.Y.Z` folds the `[Unreleased]` body and every `X.Y.Z-<pre>` section into one dated `[X.Y.Z]` section, merging same-named `###` subsections, and leaves an empty `[Unreleased]` on top; a pre-release inserts an empty dated header below `[Unreleased]`, which keeps its body for the stable; a top header already at the version changes nothing). No body is edited at release time: changes are written under `[Unreleased]` as they land. `.claude-plugin/marketplace.json` is never written: its `gaia` entry has `source: "."` and no version;
   - runs `npm run generate:plugin-root` to regenerate the ROOT `.claude-plugin/plugin.json` (metadata only -- no inline hooks) + `hooks/hooks.json` from the manifest -- `plugin.json`'s version is inherited from `package.json` (`from:package.json`), so it is NOT hand-bumped. No `dist/` bundle;
   - runs `npm run pre-publish:validate` and fails loud on any drift.

   This replaces hand-bumping one file at a time. The real escape a hand-bump leaves is a `pyproject.toml` left behind on a prior version (caught only by `pre-publish:validate`). `release:prepare` makes the desync impossible because all hand-owned sources are written from one target version and `plugin.json` is generated from it. For a bare semver: `5.0.5` for stable, `5.1.0-rc.1` for RC (no leading `v` -- the tag adds it). Idempotent: re-running with the same version is a no-op bump that re-validates.
4. **Sandbox install** (`gaia release publish` step 3, `step_sandbox_install`) -- the same gate `publish.yml` runs after the GitHub release exists (`bin/validate-sandbox.sh --tarball <packed> --target sandbox`), run here on the bumped tree before anything irreversible. It packs the tree (`prepack` regenerates the root manifests, as in CI) and installs the tarball into a throwaway sandbox with its own `HOME`, `GAIA_DATA_DIR` and `GAIA_DB` (the gate strips the operator's `GAIA_DB`/`GAIA_DATA_DIR` from the harness environment, and the harness pins both to the sandbox), which bootstraps a fresh DB through `scripts/bootstrap_database.sh` -- the path that failed after `v5.5.0-rc.4` was already tagged. It refuses a tree that differs from HEAD outside the version sources, because the tag would not hold those changes. A reused CI verdict does not run this install path, so a green `CI verdict` alone never stands in for it. What this local run does not reproduce: the CI runner's OS image, its Node 22 / npm 11 toolchain, and its `sqlite3`.
5. Pre-flight that reproduces CI (partly done inside `release:prepare`) -- `gaia release publish` step 4, the same `gate_tests` `gaia release check` uses. HEAD is the parent of the version-only bump commit step 5 makes, so a green `CI verdict` on HEAD PASSes the step citing the CI run, as long as only the version sources `release:prepare` rewrote differ from HEAD. Otherwise `npm test` runs; `--local-suite` forces it. CI later reuses that same verdict for the bump commit. `pre-publish:validate` ran inside `release:prepare`.
6. Commit -- `gaia release publish` step 5: `git add` (the version-source paths only) + `git commit` -- local-safe, not T3. Idempotent: nothing-to-commit on a tree already at the target version is a PASS. **If the remote diverged, reconcile with MERGE, never rebase** (see "Reconciling a diverged remote").
7. Tag -- `gaia release publish` step 6: `git tag -a` (force-free -- a *new* `v<version>`, never `--force`). **Idempotent (P1a):** if the tag already exists AND points at the current release HEAD, this is a PASS/skip -- so a re-run after a LATE failure (push or `gh release create`) advances *through* the tag to the gh-release step instead of dying on it. If the tag exists but points at a DIFFERENT commit, it is a clear FAIL (the tag is never moved silently -- delete and re-run, or publish the existing tag). Then push -- step 7: `git push --atomic origin HEAD:refs/heads/<branch> refs/tags/v<version>` (T3; the commit and the tag in one push to the resolved push target, both or neither). The merge in item 6 above keeps the push a fast-forward; nothing in it forces.
8. Create a GitHub Release -- `gaia release publish` step 8: `gh release create v<version> --title v<version> --generate-notes` (T3):
   - Tag: the version from `package.json` (e.g., `v5.0.0-rc.4` or `v5.3.0`).
   - Title: the version.
   - RC/beta/alpha versions get `--prerelease` automatically.
9. `publish.yml` triggers automatically (as a consequence of step 8) and publishes with `--tag <auto-detected>`.
10. Verify from the registry and update every install folder on every channel: `gaia-verify registry` (`gaia:verify-install:rc` / `:latest`), then update EACH install folder. **A publish updates no install folder on its own** -- a `file:` install keeps the dev-pack tarball and a plugin install keeps its version. Per channel: package, `pnpm add @jaguilar87/gaia@<dist-tag>` (or `npm install`) + `npx gaia update`, which re-wires every channel the install folder's manifest recorded, OpenCode included, then restart Claude Code or OpenCode; plugin, `claude plugin marketplace update gaia-marketplace` + `claude plugin update gaia@gaia-marketplace`, then restart -- it updates only when the fetched `plugin.json` version differs from the installed one. Then `gaia-verify live`. To find WHICH local install folders are still on stale code, run `gaia doctor` in each: its **Install provenance** check (order 57) detects a `file:` install and reports whether it is fresh vs source, hinting `gaia dev --workspace <install-folder> --channel <channel>` to fix. (There is no bulk `sync-local` command -- that action-at-a-distance was removed in favor of per-install-folder `gaia dev` + the `doctor` diagnostic.) The release is done when the published version installs and validates in every target -- not at the tag.

**Why the reinstall is mandatory, not optional:** `gaia dev`'s content-addressed tarball naming (`jaguilar87-gaia-<version>+<sha8>.tgz`) guarantees a *changed* build gets a fresh pnpm store key, but only when `gaia dev` is actually re-run. A publish alone leaves the local `file:` install frozen at whatever it last packed. Skipping item 10's reinstall is exactly how a fixed release keeps exhibiting the old behaviour in a local install folder.

### Reconciling a diverged remote -- merge, never rebase; never move a tag

`publish.yml` no longer commits artifacts back to `main` (it is a read-only checkout that packs + publishes), so a release does **not** auto-advance the remote. But if the remote ever diverges for any other reason, the reconciliation choice is forced by local policy:

- **Reconcile with merge, not rebase.** Rebase rewrites your local commit hashes. If a tag already pointed at one of those commits, you would have to re-point it -- which means `git tag -f` or a force-push of the tag. Both match the `git_destructive` pattern in `hooks/modules/security/blocked_commands.py` and are **hard-denied locally** (exit 2, not approvable) -- there is no `approval_id` that unblocks them. Merge preserves the existing hashes, so existing tags stay valid and no force is ever needed.
- **Tags are create-only -- never move one.** A published tag is immutable history; a new release gets a *new* tag (`-rc.N+1`, next patch/minor), it does not re-point an old one. Moving a tag requires the same force path that local hooks deny.
- **The force-deny is a local hooks policy.** With the `dist/` commit-back removed, `publish.yml` no longer runs `git tag -f` or `git push --force` at all -- it neither commits nor pushes. The local force-deny still stands for any manual reconciliation: never force a tag or push locally; merge instead.

**Verify from npm** (registry round-trip):
- RC: `npm run gaia:verify-install:rc`
- stable: `npm run gaia:verify-install:latest`

**From rc to stable:** merge the accumulating branch to `main`, then `gaia release publish X.Y.Z` from `main`. Do not move an rc onto `latest` with `npm dist-tag`: its version still carries `-rc.N` on every channel, and the plugin's tag would not be on `main`.

**Uninstall order:** `npx gaia uninstall` runs **before** `npm uninstall @jaguilar87/gaia` / `pnpm remove @jaguilar87/gaia`. npm >= 7 does not run the package's `preuninstall` script (and pnpm skips lifecycle scripts by default), so `gaia uninstall --preuninstall` never fires on its own; removing the package first leaves the install folder's wiring behind with no CLI to revert it. See `SKILL.md` -> "Uninstalling, per channel".

## Diagnostic Guide

Symptoms encountered in real install sessions, with the root cause and the fix.

| Symptom | Cause | Fix |
|---|---|---|
| Install reports PASS, `~/.gaia/gaia.db` migrated, but `.claude/hooks` symlink still points to an old version after reload | The install-folder detector matched the gaia repo itself (self-referencing `node_modules/@jaguilar87/gaia/`) instead of the consumer install folder. Symlinks got wired to the repo's `node_modules`, not the consumer install folder's. | Always pass `--workspace <dev-install-folder>` explicitly. `bin/validate-sandbox.sh::is_gaia_repo_root` guards this, but explicit `--workspace` is safest. Verify with `readlink <dev-install-folder>/.claude/hooks` -- it must resolve under the install folder, not the repo. |
| `.claude/` not wired after install | `gaia install` not run, or it exited non-zero | `cat ~/.gaia/last-install-error.json` (written by `gaia install` on failure). Re-run `gaia install --channel npm --workspace <path>`. If it persists, file a bug. |
| `gaia doctor` walks up to the user `.claude/` instead of the install folder | Install folder not initialized (`.claude/` missing or no `plugin-registry.json`) | Re-run `gaia install --channel npm --workspace <path>`. |
| DB missing / `no such table` on first use | Lazy bootstrap did not run (e.g. `gaia` never invoked yet) | Run any `gaia` command (it triggers `_ensure_db_bootstrapped`), or `gaia update` for the full seed. There is no postinstall to "re-run". |
| Plugin mounts but hooks never fire | Generated `hooks/hooks.json` broken at the package root, or CC did not reload | Regenerate (`npm run generate:plugin-root`), re-run `npm run gaia:plugin-dryrun`, and `/reload-plugins`. Inspect the root `hooks/hooks.json` (the canonical hook source); `.claude-plugin/plugin.json` is metadata only and must NOT carry an inline `hooks` block. |
| `bootstrap exited 1: table projects has no column named identity` | Schema/bootstrap drift (old seed SQL) | Update to a build whose seed matches the current schema. |
| `gaia contract finalize` (or another CLI) errors `no column named ...`; `gaia doctor` reports `schema_version=N > code expected M` | **Reverse-direction drift**: the live `~/.gaia/gaia.db` was migrated by a NEWER Gaia than the code now running -- stale code mis-reading a newer schema. | Install code at least as new as the DB by running the drift-safe convergence -- `gaia dev` (from a source checkout whose `EXPECTED_SCHEMA_VERSION` >= the DB) or `gaia install --channel <channel>`/`gaia release` (artifact). The bootstrap direction guard now REFUSES this case (no clobber) and migrates forward when the code is newer; do **not** `npm install @latest` a build older than the DB, and **never** downgrade the DB. |
| `Permission denied` invoking a hook `.py` | Exec bit lost on cross-platform checkout | Hooks are invoked via `python3 <script>` (see `build-plugin.py` `generate_hooks_json`), so the exec bit should not matter; if it does, update to a build that uses the `python3 <script>` invoker. |
| Agent says "no conozco Gaia" or "developer agent does not exist" | `settings.local.json` missing or mis-wired (npm surface) | Re-run `gaia install --channel npm`. In plugin surface, `/reload-plugins`. |
| Same approved command emits a fresh `approval_id` on retry | Not a bug -- single-use per sub-agent invocation is intentional | Re-approve. Each blocked command produces its own approval; there is no batch grant. See `orchestrator-present-approval/SKILL.md` -> Rule 3. |
| A T3 release command (`git push`, `gh release`, `gaia dev`) is re-blocked right after you approved it | The grant signature binds to the **cwd** the command was approved in; an execution environment that resets cwd between calls (e.g. an agent harness) signs the retry from a different cwd, so it no longer matches the grant | Invoke T3 commands with **absolute paths** (`git -C <abs> push`, `gaia dev --channel <channel> --workspace <abs>`, run `gh` from an explicit dir) so the signed command is cwd-independent and the grant matches on retry |

## Schema Migration Protocol

When you bump `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py`, the four steps below must move in lockstep. `tests/cli/test_schema_version_lockstep.py` verifies the relationship; skipping a step fails in CI.

1. Update `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py` -- the single source of truth for the target version (`scripts/bootstrap_database.py::_read_expected_schema_version` in the bootstrapper reads it).
2. Add the forward-migration file `scripts/migrations/v{N-1}_to_v{N}.sql` -- the `ALTER TABLE` / `UPDATE` / `CREATE` that advances a DB from `N-1` to `N`. This is **required**, not optional: the bootstrapper ABORTS with "missing migration file" if `EXPECTED_SCHEMA_VERSION` is bumped past the migrations on disk.
3. Do NOT hand-write any `schema_version` insert into a bootstrap script. The canonical bootstrapper is `scripts/bootstrap_database.py` (the cross-platform install/lazy path; `bootstrap_database.sh` is retained for shell/test parity -- see the same distinction in "What `gaia install` does" above). It uses the **floor + forward-migration** model: a fresh DB is built from `schema.sql` and stamped at `SCHEMA_FLOOR`, and every migration from `floor+1` replays on top; an existing DB never gets `schema.sql` and runs only the migrations from its ledger up to `scripts/bootstrap_database.py::_read_expected_schema_version`. Each migration's ledger seal is written inside the single transaction that holds the whole chain -- so shipping the migration file (step 2) is what makes fresh installs and in-place upgrades both land at the new version. A migration that rewrites or removes existing rows makes its chain ask one consent (`gaia migrate apply --consent-chain vA..vB`); preview with `gaia migrate plan`.
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

The workflow at `.github/workflows/publish.yml` runs on every GitHub Release event (read-only checkout -- it neither commits nor pushes). Its permissions are `contents: read`, `actions: read` (to read `ci.yml`'s runs) and `id-token: write` (for npm trusted publishing). It:
- Checks out the exact tagged commit.
- Requires a green `CI verdict` for that commit before doing anything else: `.github/scripts/ci_verdict.py` (the same helper `ci.yml` uses) accepts a green verdict on the tag's commit, on another commit with its tree, or on its parent when the tag only bumped version sources. Because the tag lands seconds after the push that starts CI, the step polls for up to `VERDICT_WAIT_SECONDS` (1200 s). It fails closed -- nothing is packed or published -- when the tag's `ci.yml` run is red, when no verdict appears, or when the limit elapses, and the error names the commit, the tag and the `ci.yml` run it consulted.
- Sets up Node 22 (trusted publishing needs Node >= 22.14.0) and installs `npm@^11.5.1` (it needs npm >= 11.5.1; Node 22 bundles npm 10), printing `node --version` and `npm --version`.
- Installs deps with `npm ci`.
- Packs the tarball with `npm pack` -- `prepack` (clean + `generate:plugin-root`) regenerates the root `plugin.json` (metadata only) + `hooks/hooks.json`, so the tarball root is a valid plugin root (the same tree the git plugin source serves). No `dist/` bundle is built or committed back.
- Runs `npm run pre-publish:validate` (after pack, so it sees the fresh root manifests).
- Auto-detects npm tag from the version string: `*-rc.*` -> `rc`, `*-beta.*` -> `beta`, `*-alpha.*` -> `alpha`, else -> `latest`.
- Runs the sandbox validation harness against the packed tarball (the gate).
- Publishes the same tarball with `npm publish <tarball> --access public --tag <detected>`.

Publishing authenticates by npm trusted publishing (OIDC): npm exchanges the job's OIDC token for a credential bound to the trusted publisher registered on npmjs.com (GitHub Actions, `metraton/gaia`, `publish.yml`, no environment) and attaches a provenance attestation on its own. The publish step reads no npm token, so `NODE_AUTH_TOKEN` and the `NPM_TOKEN` secret are not used. The match requires `package.json` `repository.url` to name `github.com/metraton/gaia`; renaming the repository or the workflow file breaks publishing until the trusted publisher is updated. `NPM_TOKEN` stays in GitHub Secrets only as the fallback: if trusted publishing fails, the fallback is reverting the commit that introduced it, never a token branch in the workflow.

## Path Defaults

| User says | Path used |
|-----------|-----------|
| "here" / "this session" / "this project" / live | Nearest `.claude/` ancestor of cwd with a Gaia marker, falling back to `$GAIA_WORKSPACE_PATH` when set; with neither, `bin/validate-sandbox.sh` fails and asks for `--workspace <install-folder>` |
| "in project X" / specific path | Pass `--workspace /absolute/path/to/project` to `bin/validate-sandbox.sh` (bypasses auto-detect) |
| Nothing specified (pre-release sandbox) | `/tmp/gaia-sandbox-<unix-ts>-<pid>/` (auto-cleanup unless `--stay`) |
