---
name: gaia-release
description: Use when testing, validating, or publishing Gaia releases -- "install local", "pre-release", "dry-run", "release", RC, stable, plugin dry-run
---

# Gaia Release

The norm for getting Gaia onto a machine and into the registry, organized as three layers of increasing confidence. The user expresses exactly one of three intentions -- **install local** (Layer 1, fast iteration), **pre-release** (Layer 2, the confidence gate), or **release** (Layer 3, the official publish) -- and each maps to a complete, automated sequence the orchestrator runs end-to-end. The user never recalls a sub-step and never runs a release script by hand: the script is a tool the flow invokes, not a command the human must remember. This is the lesson of the sagas that shipped broken -- a release failed because a version source was bumped one file at a time and a forgotten `pyproject.toml` drifted; another needed a force-push to reconcile a tag. Every one of those was a manual step a human was trusted to remember and didn't. The fix is to norm the sequence so the steps cannot be forgotten: they are the flow, not a checklist beside it.

**This skill orchestrates the sequence; it does not define what a healthy install looks like.** Every layer closes by installing into a target workspace and then validating it -- and "how you validate" lives in `gaia-verify`, which owns the wire-up checklist and the per-surface checks. When a layer says "verify," it means "run `gaia-verify` for the matching mode." Keep the two apart: release is the *when and in what order*; verify is the *did it come out right*.

## The delivery model: one plugin, two distribution channels

Gaia ships as a **single** plugin named `gaia` (`scripts/build-plugin.py` has `VALID_PLUGINS = ("gaia",)`). **The package root IS the plugin, and it is the same tree as the git repo root** -- there is no `dist/` bundle. That single tree reaches a workspace through **two distribution channels**, and a change can pass on one while breaking the other:

- **plugin surface (git marketplace source)** -- Claude Code consumes the **git repository directly**: `.claude-plugin/marketplace.json` advertises the `gaia` plugin with `{"source": "github", "repo": "metraton/gaia"}`, so `/plugin marketplace add` + `/plugin install` make CC **clone the repo** into its plugin cache. **This is the channel that loads Gaia's agents, skills, and hooks in Claude Code.** The git source was adopted (commit `a43ef22`) precisely because the prior `source: npm` entry loaded **0 skills** on `/plugin install` -- a confirmed CC gap -- while the identical tree loads all 32 skills via a git/local source. CC loads hooks from the repo root's `hooks/hooks.json` (the standard plugin convention) -- it does **not** use `settings.local.json` for hooks, and the root `.claude-plugin/plugin.json` is **metadata only** (no inline `hooks` block). Hooks are declared in exactly ONE place because CC reads both a plugin.json inline block AND `hooks/hooks.json`; declaring them in both double-registered every hook and fired every event twice (fixed in `a1b1245`). Both `.claude-plugin/plugin.json` and `hooks/hooks.json` are **generated from the manifest** (`prepack` / `generate:plugin-root`) and tracked in git, so the cloned tree already carries them. Git sources support version pinning by ref/sha, and `release:prepare`'s `bumpMarketplace()` does exactly that: for github/git sources it sets `source.ref = v<version>` atomically alongside `plugin.version`, so `/plugin install` clones the fixed, reproducible release tag instead of tracking moving default-branch HEAD (npm/local sources carry no `ref` and are left untouched).
- **CLI / npm surface (npm package `@jaguilar87/gaia`)** -- `npm|pnpm install @jaguilar87/gaia` (and `gaia dev`) install the npm package; this is what provides the `gaia` **CLI** and the workspace wiring. `gaia install` then wires the workspace: it symlinks `.claude/{agents,tools,hooks,config,skills}` (plus a `CHANGELOG.md` link) to the installed package and merges hook events into `settings.local.json`. The DB is bootstrapped **lazily on first `gaia` CLI use** (`_ensure_db_bootstrapped` in `bin/gaia`) -- there is **no npm `postinstall`** (removed so the install is non-invasive and works identically under npm and pnpm, which ignores lifecycle scripts by default). This surface reads hooks from the package root's generated `hooks/hooks.json` (via `merge_local_hooks` in `_install_helpers.py`), which is regenerated at pack time from the manifest.

The two channels ship from the **same source tree** (git repo root and npm package root are one and the same). That is why Layer 2 exists: the plugin surface can only be proven by materializing the plugin root -- packing the tarball, extracting it, and mounting the extracted root in a live Claude Code -- nothing else exercises the root `plugin.json` / `hooks.json` / CC plugin loader.

## The three intentions

When the user says one of these, run the *whole* sequence. Do not stop after the first command and wait to be told the next one -- the sequence below IS the intention.

### Layer 1 -- "install local": put the working tree into a real workspace, fast

The fast iteration loop. One command replaces the manual `npm pack` -> `npm`/`pnpm add <tarball>` -> `gaia install` sequence:

```
gaia dev --workspace <TARGET>
```

`gaia dev` (`bin/cli/dev.py`) packs the CURRENT source tree (via the shared `_pack_helpers.pack_tarball` primitive), installs the freshly packed tarball into `<TARGET>`'s `node_modules` (npm or pnpm, auto-detected), then wires `.claude/` and bootstraps the DB by invoking the freshly-installed copy's own `gaia install --workspace <TARGET>`. This is the default `--mode pack`: it reflects a real shippable version and exercises the exact install machinery a real consumer would. For the tightest possible loop -- no repack at all, edits visible on the next Claude Code restart -- use `--mode link`, which symlinks `<TARGET>/node_modules/@jaguilar87/gaia` straight at the source tree instead of packing.

Both modes always wire through `gaia install`'s own logic (no duplicated wiring code); `gaia dev --help` documents the full flag set (`--workspace`, `--mode pack|link`, `--keep-tarball`, `--pack-dest`, `--no-global-link`, `--quiet`, `--verbose`).

**Drift-free reconcile.** Pack mode also reconciles the **global npm surface** (surface 4) to the local source via `npm link` (`install.reconcile_global_via_npm_link`), so a bare `gaia` on PATH matches the just-built workspace instead of a stale `npm install -g` copy -- the shadow that, against a forward-migrated DB, broke `gaia contract finalize`. It then prints a read-only **convergence report** of the 5 surfaces vs the origin (aligned / stale / absent; `bin/cli/_converge.py`) and warns on a PATH-shadowing global (POSIX + Windows). Pass `--no-global-link` to leave the global install untouched. The DB half is guarded at bootstrap: an install NEVER runs code older than the DB (the reverse, finalize-breaking direction is refused, no clobber); it migrates forward when the code is newer. `gaia doctor` REPORTS this 5-surface + schema-direction skew but never fixes it -- the reconcile lives here in `gaia dev` (local source) and, for an artifact, `gaia install`/`gaia release`. No `--from` flag: the command IS the origin.

Two properties of `gaia dev` shape the flow:
- **It is T3.** `gaia dev` installs into a workspace, so it is classified state-mutating (anchored in `COMMAND_SUBCOMMAND_MUTATIVE_UPGRADES`, `mutative_verbs.py`) and **blocks for approval** before it runs -- expected, not a failure. The `gaia` launcher and `python3 <path>/bin/gaia dev` classify identically (the `bin/gaia` dispatcher re-dispatches through the classifier), so approval behaves the same from either entry point.
- **It runs NO tests.** Install local is deliberately cheap -- edit, install, restart, poke. The L1 test subset runs in Layer 2 (`gaia release check`) and Layer 3 (`gaia release publish`) and in CI, never in this fast loop.

**Then, without being asked:**
1. Run `gaia-verify` in `live` mode against `<TARGET>`. If any check fails, jump to `reference.md` -> "Diagnostic guide".
2. **Restart Claude Code to pick up the change.** `gaia dev` prints an explicit restart notice on success (both modes) because the harness pins each hook's command at session start and does NOT hot-reload -- the open session keeps running the OLD hooks until it is restarted, so a freshly installed fix is inert until then. Heed the notice; do not tell the user the change is live without it. (Plugin-surface skill/agent changes differ -- see "Reloading a change".)

Installing into a *different* workspace or wiping install metadata first is the same intention with a different target -- see `reference.md` -> "Layer 1 runbook" for the `--workspace` and `--fresh` forms, and for the raw `pnpm pack` / `npm run gaia:install-local` sequence `gaia dev` wraps (useful when diagnosing a failure inside the wrapped steps). Always pass `--workspace` explicitly when invoking from inside the gaia repo: the self-referencing `node_modules/@jaguilar87/gaia/` entry tricks auto-detect (guarded by `is_gaia_repo_root()` in `validate-sandbox.sh`, but explicit is safer).

### Layer 2 -- "pre-release": prove a clean install works on BOTH surfaces, reproducing CI

This is the confidence gate before a version is cut. It is *entirely local* -- zero network, zero registry -- and it must run the same gates CI runs (see the pre-flight principle) **and** exercise the plugin surface, which nothing else validates. One command runs all four gates, always, and reports a complete PASS/FAIL/SKIP picture (never stopping at the first red light):

```
gaia release check              # add --functional for the opt-in live plugin probe
```

`gaia release check` (`bin/cli/release.py`) runs, in order, exactly these four gates -- each a subprocess call to the existing script, never reimplemented:

1. `pre-publish:validate` -- the version-drift gate (`validate-manifests` in `ci.yml`, via `bin/pre-publish-validate.js --validate-only`). This is what catches a `package.json` / `pyproject.toml` / `plugin.json` / `marketplace.json` desync before it ships.
2. `gaia:verify-install:local` -- packs (via the shared `_pack_helpers.pack_tarball`, the same primitive `gaia dev` uses) and installs into a throwaway sandbox (`bin/validate-sandbox.sh --target sandbox`). This proves the **npm surface** of exactly what a registry publish would ship. (This is `gaia-verify` mode `npm-sandbox`.)
3. **Plugin-surface dry-run** (`bin/plugin-dryrun.sh`) -- packs the tarball itself, extracts it to a throwaway temp dir (the package root IS the plugin), and runs a **headless, offline** gate: filesystem asserts (root `plugin.json` with NO inline `hooks` block, `hooks/hooks.json`, `bin/gaia`, `agents/`, `skills/`, and NO `dist/`) plus `claude plugin validate`. It touches no real workspace and spawns no session. `--functional` forwards to the script's own opt-in live `claude --plugin-dir <temp> -p '...'` probe (needs Claude auth/tokens -- never implicit). **SKIPs** (not fails) when the `claude` binary is not on PATH. This is the only place the plugin surface is proven before a tag exists. (This is `gaia-verify` mode `plugin`.) See the plugin-surface principle below for why a green npm sandbox does not cover it.
4. `npm test` -- the L1 suite (the tests CI runs that reproduce locally).

A `check` run that reports `FAIL` on gate 1 has failed a *subset* of CI, not passed a stand-in for it; a `FAIL`/`SKIP` on gate 3 means the plugin surface was never run (SKIP only when `claude` is genuinely absent) -- both gaps surface only after publish, when the fix costs another release. For the raw npm-script forms each gate wraps (useful when diagnosing which gate failed), see `reference.md` -> "Layer 2 runbook".

### Layer 3 -- "release [version]": end-to-end publish, fully automated

The orchestrator runs every step below in order. The user supplies (or confirms) the version and approves the T3 operations; the orchestrator does the rest. **The user does not run `release:prepare` by hand -- `gaia release publish` invokes it as step 1 of one command.**

```
gaia release publish [version]              # add --dry-run to preview the sequence first
```

`gaia release publish` (`bin/cli/release.py`) collapses steps (b)-(g) below into ONE command that runs steps 1-4 (local, no approval needed) then steps 5-6 (Tier 3, will block for approval), **stopping at the first failure** -- unlike `release check`'s always-run-all-4-gates design, these steps are causally dependent: tagging an untested tree or pushing before the tag exists is actively harmful. `[version]` accepts a bare semver (`5.1.0-rc.1`) or the bump keywords `patch`/`minor`/`major` (computed from the current `package.json` version -- `patch` is the default). It **never** runs npm's own registry-publish command itself: that stays inside `publish.yml`, behind `NODE_AUTH_TOKEN` in GitHub Secrets.

**Before step 1, a read-only (T0) preconditions gate (`preflight_publish`) runs and fails EARLY, loud, and actionably** rather than blowing up mid-sequence -- the failure mode behind the release saga (a tag created, then a permission error at `gh`; or a 30-minute `npm test` wait that only failed because pytest-xdist was missing). It checks three definite blockers and, if any fails, **runs no step**: (1) the **active `gh` account has push/admin on `metraton/gaia`** -- this is a real Layer 3 precondition, because step (g) is a Tier-3 `gh release create`; a `push:false` or an unauthenticated account fails with a `gh auth switch -u <account>` / `gh auth login` fix, while a transient/no-network/`gh`-missing case is treated as "could not verify" and does **not** block; (2) tag `v<version>` **does not already exist** (local or remote) -- if it does, the gate names the fix (`gh release create v<version>` to finish a half-completed release, or delete the tag to redo it); (3) **pytest-xdist is importable** (`npm test` runs pytest with `-n auto`) -- caught in ~1s instead of after the full npm-test wait.

The `npm test` gate (step 2, shared with `gaia release check`) has a **configurable timeout**: the DEFAULT is **1800s** (raised from 1200s as the suite grew), overridable per-run via the **`GAIA_RELEASE_NPM_TEST_TIMEOUT`** env var (a positive integer of seconds). On expiry the gate reports an explicit `TIMEOUT after Ns` message that names the env-var lever and pytest-xdist, so a slow run is never mistaken for a test failure.

| Step | Action | Notes |
|------|--------|-------|
| **(a)** | Determine the version | Default to the next **patch**. If the change is major/minor, **confirm with the user** (`NEEDS_INPUT`) before proceeding -- never silently pick major/minor. Pass the confirmed version as `gaia release publish`'s argument, or let it default to `patch`. |
| **(b)** | `release:prepare <version>` -- `gaia release publish` step 1 | The atomic core: bumps the hand-owned version sources at once (`package.json`, `pyproject.toml`, `.claude-plugin/marketplace.json`, `CHANGELOG.md`), runs `generate:plugin-root` (regenerating the ROOT `.claude-plugin/plugin.json` (metadata only) + `hooks/hooks.json` from the manifest -- `plugin.json` version is inherited from `package.json`, not hand-bumped), then `pre-publish:validate`. Fails loud on any drift. No `dist/` bundle. This wraps `scripts/release-prepare.mjs` -- invoked by the flow, never run by hand. |
| **(c)** | Pre-flight that reproduces CI -- step 2 | `pre-publish:validate` already ran inside (b). Now `npm test` runs so the local gate matches CI before the tag exists. Layer 2 should already be green. |
| **(d)** | Commit -- step 3 | `git add` (the version-source paths only) + `git commit` -- local-safe, not T3. Idempotent: nothing-to-commit on a tree already at the target version is a PASS, not a failure. |
| **(e)** | Tag, **force-free** -- step 4 | A *new* annotated tag (`v<version>`); never moves an existing one. If the remote diverged, reconcile with **merge, not rebase** (rebase forces a tag move, hard-denied locally). See `reference.md` -> "Reconciling a diverged remote". |
| **(f)** | Push -- step 5, **Tier 3** | `git push --follow-tags` (pushes the commit and the new tag in one push). If diverged, the merge from (e) makes this force-free. The hook layer blocks this for approval -- expected. |
| **(g)** | `gh release create v<version>` -- step 6, **Tier 3** | Triggers `publish.yml`, which packs (prepack regenerates root manifests), validates, sandbox-gates, and publishes to npm with the auto-detected tag (`-rc.` -> rc, else latest). It no longer builds/commits a `dist/` bundle or force-moves the tag. RC/beta/alpha versions are marked `--prerelease` automatically. |
| **(h)** | Verify from the registry, then **reinstall every local workspace** | Watch the workflow to its outcome, then `gaia-verify` mode `registry` (`gaia:verify-install:rc` / `:latest`) confirms npm serves the new version. **A publish does NOT touch any workspace installed from a local `file:` tarball** -- their `package.json`/lockfile still point at the pre-release dev-pack tarball, so they keep running the OLD code until they are explicitly reinstalled. For EACH active local workspace, reinstall the new version (`gaia dev --workspace <TARGET>` re-packs and re-wires; or `pnpm add @jaguilar87/gaia@<tag>` + `gaia install` against the published tarball) and run `gaia-verify` live. To find WHICH local workspaces are running stale code, run `gaia doctor` in each -- its **Install provenance** check reports whether a `file:` install is fresh vs source and hints `gaia dev --workspace <ws>` to fix. The release is not done when the tag is pushed -- it is done when the published version is installed and validated in every target. |

For the raw command forms `gaia release publish` wraps, the schema-migration lockstep, and the diverged-remote reconciliation, see `reference.md`.

## Reloading a change

A fresh install or an edit is invisible until Claude Code picks it up, and *how* depends on what changed:

- **After `gaia dev` (a re-install) -- restart Claude Code.** The harness snapshots each hook's command (and the `settings.local.json` hook registration) at **session start** and does not hot-reload it, so an open session keeps running the OLD hooks until it is restarted -- a freshly installed fix is inert until then. `gaia dev` prints this restart notice on success (both modes); heed it. This is why the fast loop is *edit source -> `gaia dev` -> restart -> test*, not *edit -> test*.
- **plugin surface (cloned git repo in CC's plugin cache):** skills, agents, hooks, and MCP servers refresh with **`/reload-plugins`** in-session. A source edit only reaches this surface after the marketplace re-pulls the git source (or a fresh local dry-run mount); the plugin cache holds the cloned repo at the installed ref, not your working tree.
- **Any surface, slash-command change:** adding or renaming a **slash-command** needs a **full restart** -- `/reload-plugins` loads skills into context but does not rebuild the slash-command parser index.

## CI/CD

| Workflow | File | Triggers |
|----------|------|----------|
| CI | `.github/workflows/ci.yml` | Push / PR -- runs pytest (Python 3.11/3.12), Node tests, plugin build verification, and `validate-manifests` |
| Publish | `.github/workflows/publish.yml` | GitHub Release event -- packs the npm tarball (`prepack` regenerates root manifests), validates, runs the sandbox gate, auto-detects npm tag from version (`-rc.` -> rc, `-beta.` -> beta, else -> latest), and publishes. It no longer builds/commits a `dist/` bundle or force-moves the tag (read-only checkout). |

`NPM_TOKEN` lives in GitHub Secrets; local `npm publish` bypasses build verification and is not the supported path.

## Principles -- why the sequence is normed, not optional

- **The pre-flight reproduces what CI validates, not a subset of it.** When the local check skips a gate CI runs (`pre-publish:validate`), that gate's failures surface only *after* publishing, on the published tarball, where the only remedy is another release. That is exactly how a `pyproject.toml` drift shipped green-local and red-CI. Layer 2 step 1 and Layer 3 step (c) close the gap. See `reference.md` -> "The pre-flight reproduces what CI validates".
- **The plugin surface is only proven by packing the tarball and mounting the extracted root.** The npm sandbox exercises the symlink / `settings.local.json` path; it never touches the root `plugin.json` / `hooks.json` or CC's plugin loader. The tarball can be missing files, carry a broken `hooks.json`, or fail to expose `bin/gaia` on PATH -- and none of that shows until CC mounts it. Layer 2 step 3 (`gaia:plugin-dryrun` -- pack, extract, headless validate) is the only pre-tag check that runs the plugin surface. Skipping it means the plugin breaks silently in production.
- **Bump every version source in one step, never one at a time.** `pre-publish:validate` requires `package.json`, `pyproject.toml`, `.claude-plugin/plugin.json` (generated), `.claude-plugin/marketplace.json`, and the `CHANGELOG.md` top header to agree. `release:prepare` writes the hand-owned sources from one target version and regenerates `plugin.json` (version inherited from `package.json`), so a hand-desync is impossible. See `scripts/release-prepare.mjs`.
- **Tag force-free; reconcile with merge, never rebase.** `publish.yml` no longer commits artifacts back to `main` (read-only checkout), so the remote does not auto-advance on release. But if the remote ever diverges, rebasing rewrites hashes and forces a tag move (`git tag -f` / `--force`), hard-denied by local hooks (`git_destructive` in `blocked_commands.py`, exit 2, not approvable). Merge preserves hashes and tags; a new release gets a *new* tag, never a moved one. See `reference.md` -> "Reconciling a diverged remote".
- **A release ends at an installed, validated version -- not at the tag.** Pushing the tag only starts `publish.yml`. The intention is not satisfied until the workflow reaches its outcome, npm serves the new version, and it installs cleanly into the target (Layer 3 step (h)).

## Anti-Patterns

- **Stopping after the first command of an intention** -- "install local" is not just `gaia dev`'s pack step; "release" is not just `release:prepare`. Each intention is the *whole* sequence, and `gaia dev` / `gaia release check` / `gaia release publish` each already run their whole sequence in one invocation -- do not run one gate by hand and stop.
- **Asking the user to run `release:prepare` (or any Layer 3 step) by hand** -- it is a step `gaia release publish` invokes internally, not a command the human runs. Surfacing it as a manual step is the same failure mode (a step someone must remember) wearing a new script.
- **Publishing without a green `gaia release publish --dry-run` / `gaia release check`** -- the plugin surface (gate 3) breaks silently if skipped. Preview or run the full Layer 2 gate before any tag.
- **Pre-flight that is a subset of CI** -- skipping `pre-publish:validate` locally means the version drift surfaces after publish. `gaia release check` reproduces CI; do not approximate it by hand-picking gates.
- **Bumping version sources one at a time** -- desyncs a source by hand; `pre-publish:validate` rejects the tree and a forgotten file ships if the check is skipped. Always go through `gaia release publish` (which invokes `release:prepare`), never a hand-edit.
- **Rebase to reconcile a diverged remote** -- forces a tag move, hard-denied locally. Merge instead.
- **Single-surface testing** -- a change can pass the npm sandbox and break the plugin mount, or vice versa. Layer 2 runs both surfaces for a reason.
- **Stale root manifests** -- editing `build/gaia.manifest.json` or a hook entry without regenerating means the tarball ships a stale `hooks/hooks.json`. `prepack` regenerates at every `npm pack`, and `release:prepare` regenerates via `generate:plugin-root`; the dry-run packs fresh, so a stale manifest surfaces there.
- **Skipping the restart after `gaia dev`** -- the harness pins hook commands at session start, so a reinstalled fix is inert in the open session until Claude Code is restarted. `gaia dev` prints the notice for exactly this reason; do not report the change as live without a restart. (Plugin-surface skill/agent changes take `/reload-plugins`; a slash-command change takes a full restart -- see "Reloading a change".)
- **Running tests in the install-local loop** -- Layer 1 (`gaia dev`) is deliberately cheap and runs NO tests; the L1 suite belongs to `gaia release check` / `gaia release publish` and CI. Adding a test gate to the fast loop defeats its purpose.
- **Assuming a `postinstall` ran** -- there is none. The DB is bootstrapped lazily on first `gaia` CLI use; under pnpm a lifecycle hook would never fire anyway. If the DB is missing, run any `gaia` command (or `gaia install`), not "re-run postinstall".
- **Local npm publish** -- bypasses the pipeline's pack + validate + sandbox gate.
- **Treating a green publish as a live local install** -- a publish updates the registry; it does NOT touch a workspace installed from a local `file:` tarball. Those workspaces keep running the pre-release dev-pack code until step (h) reinstalls them (`gaia dev --workspace <TARGET>`); `gaia doctor`'s Install provenance check surfaces which ones are stale. Confusing "published" with "running locally" is exactly how a fixed release keeps exhibiting the old bug in a local workspace.
