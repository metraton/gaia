---
name: gaia-release
description: Use when installing, updating, testing, validating, or publishing Gaia -- "install Gaia version X on this machine", "update Gaia", "install local", "pre-release", "dry-run", "release", RC, stable, plugin dry-run, "instala Gaia", "actualiza Gaia a la X", "instala local", "hagamos un release", "probemos el pre-release", "publiquemos la versión estable"
---

# Gaia Release

The norm for getting Gaia onto a machine and into the registry. It covers two jobs:

- **Installing a version on a machine**: any version, on whichever channels the machine has.
- **Publishing**: three layers of increasing confidence, which the user names as one of three intentions -- **install local** (Layer 1), **pre-release** (Layer 2), **release** (Layer 3).

Each publishing intention is a complete sequence the orchestrator runs end to end. The user never recalls a sub-step, because the release sagas that shipped broken were all a step a person was trusted to remember: a version source bumped one file at a time, a forced tag.

This skill is the *when and in what order*. What a healthy install is, and how to check one, is `gaia-verify`'s: when a step here says "verify", run `gaia-verify` in the matching mode.

## The delivery model: one tree, three channels

Gaia ships as a single plugin named `gaia` (`scripts/build-plugin.py` has `VALID_PLUGINS = ("gaia",)`). The npm package root is the plugin root, and both are the git repository root; there is no `dist/` bundle. That one tree reaches a machine through three channels, and a change can pass on one while breaking another:

```
                         one source tree (git repo root = package root)
                                         |
            +----------------------------+----------------------------+
            |                            |                            |
    git ref (branch/tag)        npm registry tarball          npm registry tarball
            |                            |                            |
   Claude Code plugin           package + gaia install         package + gaia install
   gaia@gaia-marketplace        --channel npm                  --channel opencode
```

- **Package (npm/pnpm + `gaia install`, for Claude Code).** The artifact is the `@jaguilar87/gaia` tarball that `publish.yml` publishes. Installing it provides the `gaia` CLI (`node_modules/.bin/gaia`). `gaia install` then wires the folder it runs in:
  - it links `.claude/{agents,tools,hooks,config,skills,opencode}` and `CHANGELOG.md` to the package;
  - it merges permissions and hook registrations into `.claude/settings.local.json` (`bin/cli/_install_helpers.py::merge_local_hooks`).

  The store is created on the first `gaia` call (`bin/gaia::_ensure_db_bootstrapped`). There is no npm `postinstall`, so npm and pnpm behave the same.
- **Claude Code plugin (`gaia@gaia-marketplace`).** The artifact is the git tree at a ref. `.claude-plugin/marketplace.json` names the marketplace `gaia-marketplace` and gives the `gaia` entry `"source": "."` with no version, so a marketplace added at a ref installs that ref's code, at the version its `.claude-plugin/plugin.json` declares. Claude Code loads hooks from the root `hooks/hooks.json`; the root `plugin.json` is metadata only, since hooks declared in both places fired every event twice. Both files are generated from the manifest and tracked in git. The plugin's sessions merge the permission set into `.claude/settings.local.json` (`hooks/modules/core/plugin_setup.py::setup_project_permissions`) and record those writes in `.claude/gaia-manifest.json`. The plugin does not put `gaia` on the terminal's `PATH`.
- **OpenCode (on the package).** The same npm tarball; `gaia install --channel opencode` points `opencode.json` at the package's `opencode/plugin.ts` and leaves `.claude/` alone.

| Channel | Artifact | Where an RC comes from | Where a stable comes from |
|---------|----------|------------------------|---------------------------|
| Package (Claude Code) | `@jaguilar87/gaia` on npm | `gaia release publish x.y.z-rc.N --branch release/x.y.z` -> the `rc` dist-tag | `gaia release publish x.y.z --branch main` -> the `latest` dist-tag |
| Claude Code plugin | the git tree at a ref | tag `vx.y.z-rc.N` on the release branch | tag `vx.y.z` on `main` |
| OpenCode | the same npm tarball | as the package | as the package |

**Why a pre-release never reaches `main`.** The marketplace entry carries no ref, so `main` is what every plugin user who pins nothing receives; 5.7.2-rc.1 reached all of them that way. Three things keep it off `main`:

- `gaia release publish` refuses a pre-release whose push target is `main`, and a stable whose target is anything else (`bin/cli/release.py::_check_release_branch`).
- CI's `No pre-release on main` job fails a pull request into `main`, or a push to it, that declares a hyphenated version (`.github/scripts/main_version_guard.py::main`).
- An RC is published from `release/x.y.z`, whose pushes and pull requests run CI, so the RC's commit carries the verdict `publish.yml` requires.

## Installing a version on a machine

**Gaia describes and checks; the agent executes and resolves.** Gaia never runs the host's install commands itself:

- `claude`, `npm` and OpenCode change their behaviour between releases and differ per OS. A verb that wrapped them would fail inside Gaia, out of sight.
- An agent that runs each step itself reads each answer and can adapt to it.

So Gaia's part is the instruments: the state of every channel, and the gaps between that state and a version. The agent's part is the host commands they name, run one at a time.

**A release candidate and a stable install by the same route; the version is only a ref.** The plugin is pinned to the tag `vX` and npm installs the exact `X`. Because the route is the same, an RC installed on a machine proves the route its stable will take there.

The healthy result is `gaia-verify`'s principle of an installation: one version, from one declared source, in every channel present, all pointing at one database.

### The flow

1. **Read the state.** Run `gaia doctor --channels --workspace <folder>` (`gaia-verify`, mode live). It shows which channels the machine has, the scope and project folder of each plugin install, and the source each marketplace is declared with. Note the verdict before changing anything, so a split you started from is not reported as one you caused.
2. **Read the target view.** Run `gaia doctor --target <version> --workspace <folder>`. It lists each gap and the host command or settings edit that closes it, in order, spelled as the machine declares it and with the folder to run it from. It runs nothing. Read the data part too (below) before the first step.
3. **Run the steps it names, adapting to what the machine answers.** The view is the state as it was read, so the machine can answer differently. When it does -- `enable` exits 1 with `already_in_goal_state`, npm answers `E404`, Windows reports a plugin that "could not be replaced" -- stop and reason from that answer before the next step.
   - Every step changes the user's machine. Request the T3 commands as one set through `subagent-request-approval`; an edit to `~/.claude/settings.json` asks for its own file-write signature.
   - **A signed step the hooks still refuse is not a puzzle to solve.** The denial names the check that refused it: a changed file, the directory, a reused call, a reservation another call holds, or the order of the set. An install step's own files in an installed Gaia are sealed again when the step before it finishes (`subagent-request-approval`), so a refusal there is a real cause or a Gaia defect. Stop there. Do not respell the command or move it into a script (`security-tiers`, no-elusion). Report that cause in `open_gaps`, not the ids, and hand the user the exact command, with the folder to run it from, under `Human steps:`. The steps after it wait until the user has run it.
   - `gaia update` re-wires the channels the folder recorded. For OpenCode that regenerates `opencode.json`'s agent block as well as the plugin entry, so expect a diff wider than one line. `gaia update --dry-run` previews only the Claude Code wiring, so it is no proof of what a real run will do.
4. **Read the state again.** Run `gaia doctor --channels`. It must exit 0, with every channel at the target and provenance `match`. Exit 1 means leftovers: report them as such. Exit 2 means the install did not come out right. A plain `gaia doctor` is no proof: it exits 1 on unrelated workspace warnings even when every install check passes.
5. **Report the restart step and how to go back.**
   - **Restart.** A plugin change reaches a new session in each install's project folder, or `/reload-plugins`. npm and OpenCode need the host restarted. Until then, a running session keeps the version it loaded; Claude Code keeps that old plugin-cache folder for 14 days so the session does not break.
   - **Going back.** It is the same flow with `--target <previous version>`. When the new version migrated the store past what the previous one writes, put the backup back as well (below).

### The data

The database is always upgradable:

- Every migration runs in one transaction, after a verified backup (`scripts/bootstrap_database.py::_apply_chain`, `scripts/bootstrap_database.py::_backup`).
- An install never runs code older than the store.

`gaia migrate plan` shows the chain the data goes through, whether `apply` needs `--consent-chain vA..vB`, and the backups on hand. Run it with the new copy's `gaia` once installed.

When a change is risky, the route is: back up, install, and bring the data over. A change is risky when the plan shows a chain that rewrites existing rows, when the target's `CHANGELOG.md` names a breaking migration, or when the user is going back to an older version.

1. Note the backup the plan lists. A `pre-migrate` backup is written on its own, verified, and the newest 3 are kept.
2. Install.
3. Bring the data over: `gaia migrate apply` (with the consent the plan names) moves it forward, and `gaia migrate restore --from <backup>` puts an older store back for the version it came from.

Never copy `gaia.db` by hand: live sessions hold it open, and `restore` copies under the store's lock so none of them sees a half-swapped file.

### What goes wrong

Each row is a failure seen on a real machine, and what `gaia doctor --channels` shows for it. The fix is the step `--target` names.

| What goes wrong | Why | What closes it |
|-----------------|-----|----------------|
| **The plugin is pinned to a ref** (`#v5.7.2-rc.2`) | `claude plugin marketplace update` refreshes the marketplace at the ref it is declared with, so a new version never arrives. Editing only the declaration leaves it differing from the registration, and Claude Code then ignores the marketplace without an error. | Change the declared ref, then re-add the marketplace at the new ref, then update each install. |
| **The source form differs** | `claude plugin marketplace add` refuses a source that differs from the declaration in any field, and `owner/repo` and its `https://...git` URL are different sources (Windows installs often carry the URL). | Type the source exactly as declared; `--target` spells it that way. |
| **The scope or project folder is wrong** | A `local` or `project` install belongs to one folder. `claude plugin update` run elsewhere updates another scope, or nothing. | Run the update with that install's `--scope`, from the project folder `--channels` prints for it. |
| **npm serves the new version late** | For several minutes after a publish the registry answers `E404` for the exact version while `@rc` and `@latest` still name the previous one. A caret range such as `^5.7.2-rc.2` never reaches another version's pre-release. | Wait until `npm view @jaguilar87/gaia@<version> version` prints it, then install that exact version with `--save-exact`; never a dist-tag. |
| **A dev plugin is left beside the real one** | `gaia dev --channel plugin` installs `gaia@gaia-dev` and disables `gaia@gaia-marketplace` in that folder. Left that way, the published version never loads there; with both enabled, two `gaia:*` sets load and every hook runs twice. | Disable the dev plugin and enable the published one, at the scope `--channels` shows. |
| **OpenCode is wired to a plugin-cache path** | Inside a Claude Code session the `gaia` on `PATH` is the plugin cache, and an install run with it used to wire `opencode.json` to `cache/<marketplace>/gaia/<version>`. Claude Code deletes that folder 14 days after the next plugin update, and OpenCode then fails at startup. `gaia install` and `gaia update` now refuse that copy and drop such entries (`bin/cli/_install_helpers.py::configure_opencode_plugin`). | Remove the entry and let the workspace's npm copy re-wire OpenCode. |

**`claude plugin marketplace remove` is never an update route.** Removing a marketplace from the last scope that declares it deletes its cache and uninstalls every plugin installed from it -- including the cache that running sessions load their hooks and skills from, so they break mid-session. A new ref arrives through a re-add with the declared source.

### When it does not come out right

Reason from three sources, read this turn:

- what the instruments report: `gaia doctor --channels`, `--target`, `gaia migrate plan`;
- the target version's `CHANGELOG.md`;
- the host's own documentation: Claude Code's plugin reference and troubleshooting pages, npm's, OpenCode's.

Do not improvise from memory. The host CLIs change between releases, and the remembered flag is the one most likely to have changed. When an instrument contradicts what the machine shows, the instrument is the defect: report it in `open_gaps` instead of working around it.

## The three publishing intentions

When the user names one, run the whole sequence; the sequence is the intention.

### Layer 1 -- "install local": serve the working tree through one channel, fast

The fast loop needs neither a merge nor a publish. The PR's code lives in its worktree and is tried in a dev install folder (`<dev-install-folder>`); the folder that runs the published version is never a `gaia dev` target, because `gaia dev` replaces what it serves and that folder must keep proving the published route.

```
python3 <pr-worktree>/bin/gaia dev --channel <npm|plugin|opencode> --workspace <dev-install-folder>
# or, from the installed CLI:
gaia dev --from-worktree <pr-worktree> --channel <channel> --workspace <dev-install-folder>
```

`gaia dev` (`bin/cli/dev.py`) packs the source tree (`_pack_helpers.pack_tarball`) and serves the tarball, never the checkout:

| `--channel` | What it changes in the install folder | How the change is picked up |
|-------------|----------------------------------|-----------------------------|
| `npm` | installs the tarball into `node_modules` (npm or pnpm, auto-detected) and runs the fresh copy's own `gaia install` | restart Claude Code |
| `plugin` | extracts the tarball into a per-folder directory that is the local marketplace `gaia-dev`, installs `gaia@gaia-dev` at local scope, and disables `gaia@gaia-marketplace` in that folder | `/reload-plugins` |
| `opencode` | installs the tarball and wires OpenCode | restart OpenCode |

The channel is required and there is no `all`. `npm` and `plugin` refuse each other in one folder; `opencode` joins either. `--ref <full-sha>` refuses to build unless the worktree's HEAD is that commit. `gaia dev --help` lists the rest. It prints a read-only convergence report (`bin/cli/_converge.py`). The store is guarded the same way as any install: it migrates forward, and is never run by code older than it.

`gaia dev` is T3 (it installs into a folder) and blocks for approval, from the launcher and from `python3 <path>/bin/gaia dev` alike. It runs no tests; the suite belongs to Layer 2, Layer 3 and CI.

**Then, without being asked:** pick the change up the channel's way (see "Reloading a change") and do not call it live before that; run `gaia-verify` mode live on the dev folder, whose `Install provenance` must name the worktree's commit. On a failure, go to `reference.md` -> "Diagnostic guide".

### Layer 2 -- "pre-release": prove a clean install on every channel, reproducing CI

The confidence gate before a version is cut. It runs locally with no registry (its only network use is gate 4's read-only lookup of the CI verdict), runs the gates CI runs, and exercises the plugin and OpenCode surfaces nothing else validates. One command runs all seven gates, always, and reports a complete PASS/FAIL/SKIP picture:

```
gaia release check --gh <wrapper>     # --functional: opt-in live plugin probe; --local-suite: force npm test in gate 4
```

**It is T3; request consent before running it.** It is local but not free: gates 2 and 3 pack the tarball, whose `prepack` rewrites the plugin root manifests including `hooks/hooks.json`, so it is anchored mutative in `COMMAND_PATH_MUTATIVE_UPGRADES` (`hooks/modules/security/mutative_verbs.py`). Request it with the other release commands rather than meeting the block mid-runbook.

It first installs the locked Node dependencies a fresh worktree lacks (`bin/cli/release.py::step_node_deps`), then runs the seven gates (`bin/cli/release.py::run_release_check`):

1. `pre-publish:validate` -- the version-drift gate CI runs as `validate-manifests`.
2. `gaia:verify-install:local` -- packs and installs into a throwaway sandbox: the npm surface of what a publish would ship (`gaia-verify` mode npm-sandbox).
3. **Plugin dry-run** (`bin/plugin-dryrun.sh`) -- the tarball extracted and validated as a plugin, headless and offline (`gaia-verify` mode plugin). SKIPs, not fails, when `claude` is not on `PATH`; it is the only place the plugin surface is proven before a tag exists.
4. **Tests** -- PASS citing a green `CI verdict` that covers HEAD's tree (`.github/scripts/ci_verdict.py`), else `npm test`; `--local-suite` forces the suite.
5. `convergence` -- the install surfaces against the artifact's version (`bin/cli/_converge.py`); a live store newer than the artifact expects is a FAIL, because installing it would be refused.
6. `opencode:surface` -- the tarball wired into a temp folder as `gaia install --channel opencode` does; FAILs naming what OpenCode would not find.
7. `install:smoke` -- the tarball laid out as every install channel in a throwaway home, running the session smoke set (`gaia-verify` mode smoke).

A FAIL on gate 1 is a subset of CI failing, and a FAIL or SKIP on gate 3 means the plugin surface never ran; both surface after publish otherwise, when the fix costs a release. The raw forms each gate wraps are in `reference.md` -> "Layer 2 runbook".

### Layer 3 -- "release [version]": end-to-end publish

**A stable is promoted only from an RC published to the rc channel, installed on this machine the way users install it, and passed the smoke set live.** 5.7.0 went from a dev install to stable; every check ran on the dev-plugin path, and the published build locked the orchestrator out of its own CLI. The sequence for every version:

1. **Publish the RC from a release branch.** Fixes reach `release/x.y.z` through pull requests, and CI runs on each pull request and on each merge: a release-branch push carries its own commit in the concurrency group, so no merge's run is cancelled by the next. From a worktree on the branch's tip, `gaia release publish rc --branch release/x.y.z --gh <wrapper>` (the next `-rc.N`; an explicit `x.y.z-rc.N` works too) runs gate 7 on the bumped tarball, pushes the bump commit and the tag, and `gh release create --prerelease` starts `publish.yml`, which publishes under `rc`.
2. **Install it on this machine** with "Installing a version on a machine" above (`gaia doctor --target x.y.z-rc.N`), on every channel present, never with `gaia dev`.
3. **Pass the smoke set live.** `gaia release smoke --live --version x.y.z-rc.N --workspace <install-folder>` (`gaia-verify` mode smoke) records the pass in `release-smoke/<rc>.json` under this machine's state directory. Then open a session in the install folder and run the set as orchestrator and through one dispatch that closes its contract; the per-prompt preferences line must show.
4. **Promote.** Merge the release branch into a worktree on `main` locally (`git merge --no-ff release/x.y.z`), then `gaia release publish x.y.z --branch main --gh <wrapper>` from it, so the merge and the stable bump reach `main` in one push and `main` never serves the RC's version.

**What a stable still needs that CI cannot give it: the live smoke record.** CI never installs Claude Code, so only a machine that installed the RC through the published channels can produce `release-smoke/<rc>.json`. The preconditions gate (`bin/cli/release.py::_check_promoted_from_rc`) refuses a stable unless:

- origin holds a `vx.y.z-rc.N` tag;
- the newest RC has a recorded live pass **on the machine that publishes**;
- HEAD holds that RC's tree with only the version sources changed.

Anything merged after the RC means a new RC. When the RC commit is absent locally (a squash merge, a deleted branch), the gate fetches it by its tag without writing a ref.

```
gaia release publish <version> --gh <wrapper>
python3 <worktree>/bin/gaia release publish <version> --gh <wrapper>
```

Add `--dry-run` to preview: it names the push target and the gh account. The second form runs the release from a Gaia-managed worktree's own code; `release check`/`publish` resolve their repository from the `bin/gaia` they run from.

`gaia release publish` (`bin/cli/release.py::run_release_publish`) runs steps 1-6 (local) then 7-8 (T3, blocking for approval), stopping at the first failure, because tagging an untested tree or pushing before the tag exists is harmful. `[version]` is a bare semver or `patch`/`minor`/`major` (default `patch`). It never runs npm's publish itself: `publish.yml` publishes through npm trusted publishing (OIDC) with no npm token.

**Before step 1, a read-only preconditions gate (`bin/cli/release.py::preflight_publish`) checks six blockers and runs no step if any fails:**

1. The `--gh` program's account has push on `metraton/gaia`, because step 8 is a T3 `gh release create`. A network failure is "could not verify" and does not block.
2. Tag `v<version>` does not exist, locally or on origin.
3. pytest-xdist is importable, since `npm test` runs with `-n auto`.
4. The push target resolves and HEAD fast-forwards it -- see "The push target".
5. A stable publishes only to `main`, an RC only elsewhere.
6. A stable is promoted from a published, live-smoked RC, as above.

**The push target is an origin branch, not the local branch.** Step 7 runs `git push --atomic origin HEAD:refs/heads/<branch> refs/tags/v<version>`: commit and tag together or not at all, never forced, so git accepts only a fast-forward. `<branch>` is `--branch`, else the current branch's `origin` upstream, else the one origin branch whose tip is HEAD. When none or several resolve, the gate asks for `--branch`. When origin's tip is not in HEAD's history, merge it (never rebase) and re-run. `--dry-run` shows a refused target as `REFUSED: <reason>`, and no flag bypasses it.

**The GitHub account is resolved per process, by the program the release runs gh through.** `gh` keeps one active account per host, shared with every session on the machine, and `gh auth switch` changes it for all of them; that is how accounts drifted mid-release. Every gh call of `release check` and `publish` runs through `--gh <program>` (default `gh`) with the repository as cwd. Name the wrapper that maps the origin owner to an account and sets `GH_TOKEN` for its own process only, so nothing else the release runs sees the token. `git push` authenticates with git's own credentials. `gh auth switch` and `gh auth logout` classify T3 (`hooks/modules/security/mutative_verbs.py::_check_generic_cli_shape`).

**Nothing irreversible happens before the sandbox install has passed on the bumped tree.** Step 1 installs locked Node dependencies; step 3 packs the bumped tree and installs it with `bin/validate-sandbox.sh --target sandbox`, the gate `publish.yml` runs only after the tag exists (`v5.5.0-rc.4` was tagged and then failed there). Step 4 is gate 4's tests: HEAD is the parent of the version-only bump commit, so a green CI verdict on HEAD covers the release. When it falls back to `npm test`, the timeout defaults to 1800s and `GAIA_RELEASE_NPM_TEST_TIMEOUT` overrides it.

| Step | Action | Notes |
|------|--------|-------|
| **(a)** | Determine the version | Default to the next patch. A major or minor change is confirmed with the user (`NEEDS_INPUT`), never picked silently. |
| **(b)** | Node deps, then `release:prepare <version>` -- steps 1-2 | Bumps every hand-owned version source at once (`package.json`, `pyproject.toml`, `CHANGELOG.md`; a stable folds `[Unreleased]` and its pre-release sections into one), regenerates `.claude-plugin/plugin.json` and `hooks/hooks.json`, then runs `pre-publish:validate`. `marketplace.json` is never written. Wraps `scripts/release-prepare.mjs`, never run by hand. |
| **(c)** | Sandbox install, then tests -- steps 3-4 | The sandbox install of the bumped tree; the CI verdict for HEAD, else `npm test`. |
| **(d)** | Commit -- step 5 | The version-source paths only; local, not T3. Nothing to commit is a PASS. |
| **(e)** | Tag, force-free -- step 6 | A new annotated `v<version>`; an existing tag at HEAD is a PASS, at another commit a FAIL. Never moved. See `reference.md` -> "Reconciling a diverged remote". |
| **(f)** | Push -- step 7, T3 | The atomic push to the push target. |
| **(g)** | `gh release create v<version>` -- step 8, T3 | Starts `publish.yml`: CI verdict for the tag's commit, pack, validate, sandbox gate, `npm publish --tag <rc or latest>`. Pre-releases get `--prerelease`. |
| **(h)** | Install it everywhere it runs | Watch `publish.yml` to its outcome, run `gaia-verify` mode registry on the exact version, then install it on every machine and channel with "Installing a version on a machine". A publish changes no machine on its own: every install keeps its version until that flow moves it. The release is done when the published version is installed and verified, not when the tag is pushed. |

The raw commands each step wraps, the schema-migration lockstep and the diverged-remote reconciliation are in `reference.md`.

## Reloading a change

A fresh install or an edit is invisible until the host picks it up, and how depends on the channel:

- **npm and OpenCode -- restart the host.** The harness pins each hook's command at session start and does not hot-reload, so an open session keeps running the old hooks; `gaia dev` prints this notice.
- **Plugin -- a new session, or `/reload-plugins`.** `gaia@gaia-dev` is read from its per-folder directory, so a rebuild reaches the session through `/reload-plugins`. `gaia@gaia-marketplace` is a copy in the plugin cache at the installed version; `claude plugin update` installs a new copy only when the fetched `plugin.json` declares a different version, so re-fetching a ref at the same version changes nothing.
- **A slash-command change on any channel** needs a full restart: `/reload-plugins` does not rebuild the slash-command index.

## Uninstalling, per channel

Each channel takes back only what it wrote; `~/.gaia/gaia.db` is never touched.

- **Package and OpenCode:** `npx gaia uninstall` (`--workspace <folder>`, `--dry-run` to preview) **before** `npm uninstall @jaguilar87/gaia` / `pnpm remove @jaguilar87/gaia`. npm >= 7 and pnpm run no `preuninstall`, so removing the package first leaves links, settings keys and `opencode.json` pointing at nothing, with no `gaia` left to revert them. `gaia uninstall` reverts `.claude/gaia-manifest.json` entry by entry, removes or lists what the package manager and `gaia dev` left, and snapshots the store to `~/.gaia/snapshots/` unless `--no-backup`.
- **Claude Code plugin:** `gaia uninstall` in the folder first, then `claude plugin uninstall gaia@gaia-marketplace`. Removing the marketplace too (`claude plugin marketplace remove gaia-marketplace`) is optional and only for leaving Gaia: it deletes the cache, so close the sessions that run from it first.
- **One channel beside another:** without `--channel`, `gaia uninstall` takes back every channel the folder records; `--channel opencode` removes OpenCode alone, and `--channel plugin` or `--channel npm` the Claude Code side (npm and the plugin share `.claude/`).

## CI/CD

| Workflow | File | Triggers |
|----------|------|----------|
| CI | `.github/workflows/ci.yml` | Pushes to `main` and `release/**`, and pull requests into either. A push to a release branch gets its own run, never cancelled by the next merge. A `Reuse decision` job first asks `.github/scripts/ci_verdict.py` whether this tree already passed. If it has not, pytest runs in 4 shards over the `npm test` selection, beside: the `upgrade` matrix (ubuntu and windows, every dump in `tests/fixtures/published_bases`), the OpenCode TypeScript tests, ESLint, plugin build verification, `validate-manifests`, the Windows job, and `No pre-release on main` (pull requests into `main` and pushes to it). The `CI verdict` job aggregates them and is the one check `release check`/`publish` read. CI never installs Claude Code, so it cannot run the live smoke. |
| Nightly | `.github/workflows/nightly.yml` | Daily schedule and manual dispatch: the exhaustive OpenCode alias matrix CI deselects. |
| Publish | `.github/workflows/publish.yml` | A GitHub Release: requires a green CI verdict for the tag's commit, packs, validates, runs the sandbox gate, picks the dist-tag from the version (`-rc.` -> rc, `-beta.` -> beta, else latest) and publishes through npm trusted publishing. Read-only checkout. |

## Principles -- why the sequence is normed

- **The pre-flight reproduces what CI validates, not a subset.** A gate skipped locally fails only after publish, on the published tarball, where the remedy is another release; a `pyproject.toml` drift shipped green-local and red-CI that way.
- **The plugin surface is proven only by packing the tarball and mounting the extracted root.** The npm sandbox never touches the root `plugin.json`, `hooks.json` or the plugin loader, so gate 3 is the only pre-tag check of what plugin users get.
- **Bump every version source in one step.** `release:prepare` writes them from one target version, so a hand desync cannot happen.
- **Tags are create-only; reconcile with merge, never rebase.** A rebase moves hashes a tag already points at, and moving a published tag rewrites history others fetched.
- **A release ends at an installed, verified version on every machine and channel, not at the tag.**

## Anti-Patterns

- **Stopping after the first command of an intention** -- each intention is its whole sequence, and `gaia dev`, `gaia release check` and `gaia release publish` each already run theirs in one invocation.
- **Asking the user to run a step by hand** -- `release:prepare` and every Layer 3 step are invoked by `gaia release publish`; a step someone must remember is the failure mode the norm exists to remove.
- **Installing from memory instead of from the instruments** -- a remembered host command was written for another machine's declaration, scope and version. The target view spells the command this machine accepts.
- **Updating the plugin with `claude plugin marketplace remove`** -- it deletes the cache running sessions load from and uninstalls the plugin.
- **Installing a dist-tag** -- `@rc` and `@latest` name whatever the registry serves at that moment, minutes late after a publish; the exact version is the only one that proves what was installed.
- **Promoting a stable that only a dev install ever ran** -- tests, CI and `gaia dev` all run the dev path; only the published channels prove what users get, and the preflight refuses a stable without that record.
- **Rebasing to reconcile a diverged remote** -- it forces a tag move. Merge.
- **Removing the package before `gaia uninstall`** -- nothing is left to revert the folder.
- **Treating a published version as a running one** -- a publish changes the registry and no machine; every install keeps its version until step (h) moves it.
