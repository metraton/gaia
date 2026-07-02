---
name: gaia-release
description: Use when testing, validating, or publishing Gaia releases -- "install local", "pre-release", "dry-run", "release", RC, stable, plugin dry-run
metadata:
  user-invocable: false
  type: technique
---

# Gaia Release

The norm for getting Gaia onto a machine and into the registry, organized as three layers of increasing confidence. The user expresses exactly one of three intentions -- **install local** (Layer 1, fast iteration), **pre-release** (Layer 2, the confidence gate), or **release** (Layer 3, the official publish) -- and each maps to a complete, automated sequence the orchestrator runs end-to-end. The user never recalls a sub-step and never runs a release script by hand: the script is a tool the flow invokes, not a command the human must remember. This is the lesson of the sagas that shipped broken -- a release failed because a version source was bumped one file at a time and a forgotten `pyproject.toml` drifted; another needed a force-push to reconcile a tag. Every one of those was a manual step a human was trusted to remember and didn't. The fix is to norm the sequence so the steps cannot be forgotten: they are the flow, not a checklist beside it.

**This skill orchestrates the sequence; it does not define what a healthy install looks like.** Every layer closes by installing into a target workspace and then validating it -- and "how you validate" lives in `gaia-verify`, which owns the wire-up checklist and the per-surface checks. When a layer says "verify," it means "run `gaia-verify` for the matching mode." Keep the two apart: release is the *when and in what order*; verify is the *did it come out right*.

## The delivery model: one plugin, two install surfaces

Gaia ships as a **single** plugin named `gaia` (`scripts/build-plugin.py` has `VALID_PLUGINS = ("gaia",)`). There is **one artifact**: the npm package `@jaguilar87/gaia`. `.claude-plugin/marketplace.json` advertises the `gaia` plugin with a `source: npm` object (`{"source": "npm", "package": "@jaguilar87/gaia"}`, no `source.version` -- resolves the npm `latest` dist-tag). **The package root IS the plugin** -- there is no `dist/` bundle. That one artifact reaches a workspace through two surfaces, and a change can pass on one while breaking the other:

- **npm / pnpm surface** -- `npm|pnpm install @jaguilar87/gaia`, then `gaia install` wires the workspace: it symlinks `.claude/{agents,tools,hooks,config,skills}` (plus a `CHANGELOG.md` link) to the installed package and merges hook events into `settings.local.json`. The DB is bootstrapped **lazily on first `gaia` CLI use** (`_ensure_db_bootstrapped` in `bin/gaia`) -- there is **no npm `postinstall`** (removed so the install is non-invasive and works identically under npm and pnpm, which ignores lifecycle scripts by default). This surface reads hooks from the package root's generated `hooks/hooks.json` (via `merge_local_hooks` in `_install_helpers.py`), which is regenerated at pack time from the manifest.
- **plugin surface** -- Claude Code consumes the **npm package directly** (`source: npm` -> CC runs `npm install @jaguilar87/gaia` into its plugin cache), via `/plugin marketplace add` + `/plugin install`. CC reads the package root's `.claude-plugin/plugin.json` with **hooks embedded inline** (the `${CLAUDE_PLUGIN_ROOT}` workaround) -- it does **not** use `settings.local.json` for hooks. The root `.claude-plugin/plugin.json` (inline hooks) and `hooks/hooks.json` are **generated from the manifest at pack time** (`prepack` -> `generate:plugin-root`) and tracked in git.

Both surfaces are served by the **same npm tarball**. That is why Layer 2 exists: the plugin surface can only be proven by packing the tarball, extracting it, and mounting the extracted root in a live Claude Code -- nothing else exercises the inline `plugin.json` / CC plugin loader.

## The three intentions

When the user says one of these, run the *whole* sequence. Do not stop after the first command and wait to be told the next one -- the sequence below IS the intention.

### Layer 1 -- "install local": put the working tree into a real workspace, fast

The fast iteration loop. Install the working tree into a target workspace with pnpm (the modern, non-invasive path), make changes, see them.

**Install (pnpm, the default). Always name the target workspace explicitly:**
```
cd /home/jorge/ws/me/gaia
pnpm pack                                   # -> jaguilar87-gaia-<ver>.tgz (fidelity to what ships)
cd <TARGET>
pnpm add file:/home/jorge/ws/me/gaia/jaguilar87-gaia-<ver>.tgz
gaia install --workspace <TARGET>           # bootstrap DB + settings.local.json + symlinks + registry
```
For an even tighter loop that reflects the working tree live, `pnpm link /home/jorge/ws/me/gaia` instead of the tarball (see `reference.md` -> "Layer 1 runbook" for the link-vs-tarball trade-off; note `pnpm link --global` was removed in modern pnpm -- use `pnpm add -g .` or a path link).

**Then, without being asked:**
1. Run `gaia-verify` in `live` mode against `<TARGET>`. If any check fails, jump to `reference.md` -> "Diagnostic guide".
2. **Pick up the change.** How depends on the surface (see "Reloading a change" below) -- do not blanket-tell the user to restart.

Installing into a *different* workspace or wiping install metadata first is the same intention with a different target -- see `reference.md` -> "Layer 1 runbook" for the `--workspace` and `--fresh` forms. Always pass `--workspace` explicitly when invoking from inside the gaia repo: the self-referencing `node_modules/@jaguilar87/gaia/` entry tricks auto-detect (guarded by `is_gaia_repo_root()` in `validate-sandbox.sh`, but explicit is safer).

### Layer 2 -- "pre-release": prove a clean install works on BOTH surfaces, reproducing CI

This is the confidence gate before a version is cut. It is *entirely local* -- zero network, zero registry -- and it must run the same gates CI runs (see the pre-flight principle) **and** exercise the plugin surface, which nothing else validates. Run, in order:

1. `npm run pre-publish:validate` -- the version-drift gate (`validate-manifests` in `ci.yml`). This is what catches a `package.json` / `pyproject.toml` / `plugin.json` / `marketplace.json` desync before it ships.
2. `npm run gaia:verify-install:local` -- packs, installs into `/tmp/gaia-sandbox-<ts>/`, runs the harness. This proves the **npm surface** of exactly what `npm publish` would ship. (This is `gaia-verify` mode `npm-sandbox`.)
3. **Plugin-surface dry-run** -- pack the tarball, extract it, and validate the extracted root headless:
   ```
   npm run gaia:plugin-dryrun                   # pack -> temp extract -> structural asserts + `claude plugin validate` -> trap cleanup
   ```
   `gaia:plugin-dryrun` (`bin/plugin-dryrun.sh`) packs the exact npm tarball, extracts it to a throwaway temp dir (the package root IS the plugin), and runs a **headless, offline** gate: filesystem asserts (root inline `plugin.json`, `hooks/hooks.json`, `bin/gaia`, `agents/`, `skills/`, and NO `dist/`) plus `claude plugin validate`. It touches no real workspace and spawns no session. For an optional live functional probe, `npm run gaia:plugin-dryrun -- --functional` runs `claude --plugin-dir <temp> -p '...'` from a temp cwd (needs Claude auth/tokens -- opt-in, never implicit). This is the only place the plugin surface is proven before a tag exists. (This is `gaia-verify` mode `plugin`.) See the plugin-surface principle below for why a green npm sandbox does not cover it.
4. `npm test` -- the L1 suite (the tests CI runs that reproduce locally).

A green Layer 2 that skips step 1 is a *subset* of CI, not a stand-in for it; a green Layer 2 that skips step 3 has never run the plugin surface -- both gaps surface only after publish, when the fix costs another release.

### Layer 3 -- "release [version]": end-to-end publish, fully automated

The orchestrator runs every step below in order. The user supplies (or confirms) the version and approves the T3 operations; the orchestrator does the rest. **The user does not run `release:prepare` -- step (b) invokes it.**

| Step | Action | Notes |
|------|--------|-------|
| **(a)** | Determine the version | Default to the next **patch**. If the change is major/minor, **confirm with the user** (`NEEDS_INPUT`) before proceeding -- never silently pick major/minor. |
| **(b)** | `npm run release:prepare <version>` | The atomic core: bumps the hand-owned version sources at once (`package.json`, `pyproject.toml`, `.claude-plugin/marketplace.json`, `CHANGELOG.md`), runs `generate:plugin-root` (regenerating the ROOT `.claude-plugin/plugin.json` inline-hooks + `hooks/hooks.json` from the manifest -- `plugin.json` version is inherited from `package.json`, not hand-bumped), then `pre-publish:validate`. Fails loud on any drift. No `dist/` bundle. This is `scripts/release-prepare.mjs` -- invoked by the flow, never by the user. |
| **(c)** | Pre-flight that reproduces CI | `pre-publish:validate` already ran inside (b). Now run `npm test` so the local gate matches CI before the tag exists. Layer 2 should already be green. |
| **(d)** | Commit | `git add` + `git commit` -- local-only, not T3. |
| **(e)** | Tag, **force-free** | A *new* tag (`v<version>`); never move an existing one. If the remote diverged, reconcile with **merge, not rebase** (rebase forces a tag move, hard-denied locally). See `reference.md` -> "Reconciling a diverged remote". |
| **(f)** | Push | `git push` (T3). If diverged, the merge from (e) makes this force-free. |
| **(g)** | `gh release create v<version>` | Triggers `publish.yml`, which packs (prepack regenerates root manifests), validates, sandbox-gates, and publishes to npm with the auto-detected tag (`-rc.` -> rc, else latest). It no longer builds/commits a `dist/` bundle or force-moves the tag. Mark RC as pre-release. |
| **(h)** | Verify from the registry, then install into the target | Watch the workflow to its outcome, then `gaia-verify` mode `registry` (`gaia:verify-install:rc` / `:latest`) confirms npm serves the new version. Then install the published version into `<TARGET>` (`pnpm add @jaguilar87/gaia@<tag>` + `gaia install`) and run `gaia-verify` live. The release is not done when the tag is pushed -- it is done when the published version is installed and validated. |

For the full command forms, the schema-migration lockstep, and the diverged-remote reconciliation, see `reference.md`.

## Reloading a change

A fresh install or an edit is invisible until Claude Code picks it up, and *how* it picks it up depends on the surface -- do not reflexively tell the user to restart:

- **npm / pnpm surface (symlinked source):** hook modules reload **automatically** via the file-watcher -- no `/reload-plugins`, no restart. Editing a hook under the symlinked package takes effect on the next tool call.
- **plugin surface (npm package in CC's plugin cache):** changes require **`/reload-plugins`** in-session to refresh skills, agents, hooks, and MCP servers. A source edit only reaches this surface after re-publishing (or a fresh local dry-run mount); the plugin cache holds the installed npm version, not the working tree.
- **Both surfaces:** adding or renaming a **slash-command** needs a **full restart** -- `/reload-plugins` loads skills into context but does not rebuild the slash-command parser index.

## CI/CD

| Workflow | File | Triggers |
|----------|------|----------|
| CI | `.github/workflows/ci.yml` | Push / PR -- runs pytest (Python 3.11/3.12), Node tests, plugin build verification, and `validate-manifests` |
| Publish | `.github/workflows/publish.yml` | GitHub Release event -- packs the npm tarball (`prepack` regenerates root manifests), validates, runs the sandbox gate, auto-detects npm tag from version (`-rc.` -> rc, `-beta.` -> beta, else -> latest), and publishes. It no longer builds/commits a `dist/` bundle or force-moves the tag (read-only checkout). |

`NPM_TOKEN` lives in GitHub Secrets; local `npm publish` bypasses build verification and is not the supported path.

## Principles -- why the sequence is normed, not optional

- **The pre-flight reproduces what CI validates, not a subset of it.** When the local check skips a gate CI runs (`pre-publish:validate`), that gate's failures surface only *after* publishing, on the published tarball, where the only remedy is another release. That is exactly how a `pyproject.toml` drift shipped green-local and red-CI. Layer 2 step 1 and Layer 3 step (c) close the gap. See `reference.md` -> "The pre-flight reproduces what CI validates".
- **The plugin surface is only proven by packing the tarball and mounting the extracted root.** The npm sandbox exercises the symlink / `settings.local.json` path; it never touches the root inline `plugin.json` / `hooks.json` or CC's plugin loader. The tarball can be missing files, carry broken inline hooks, or fail to expose `bin/gaia` on PATH -- and none of that shows until CC mounts it. Layer 2 step 3 (`gaia:plugin-dryrun` -- pack, extract, headless validate) is the only pre-tag check that runs the plugin surface. Skipping it means the plugin breaks silently in production.
- **Bump every version source in one step, never one at a time.** `pre-publish:validate` requires `package.json`, `pyproject.toml`, `.claude-plugin/plugin.json` (generated), `.claude-plugin/marketplace.json`, and the `CHANGELOG.md` top header to agree. `release:prepare` writes the hand-owned sources from one target version and regenerates `plugin.json` (version inherited from `package.json`), so a hand-desync is impossible. See `scripts/release-prepare.mjs`.
- **Tag force-free; reconcile with merge, never rebase.** `publish.yml` no longer commits artifacts back to `main` (read-only checkout), so the remote does not auto-advance on release. But if the remote ever diverges, rebasing rewrites hashes and forces a tag move (`git tag -f` / `--force`), hard-denied by local hooks (`git_destructive` in `blocked_commands.py`, exit 2, not approvable). Merge preserves hashes and tags; a new release gets a *new* tag, never a moved one. See `reference.md` -> "Reconciling a diverged remote".
- **A release ends at an installed, validated version -- not at the tag.** Pushing the tag only starts `publish.yml`. The intention is not satisfied until the workflow reaches its outcome, npm serves the new version, and it installs cleanly into the target (Layer 3 step (h)).

## Anti-Patterns

- **Stopping after the first command of an intention** -- "install local" is not just `pnpm add`; "release" is not just `release:prepare`. Each intention is the *whole* sequence. Running one command and waiting to be told the next reintroduces the forgettable manual step the norm exists to remove.
- **Asking the user to run `release:prepare`** -- it is a tool the "release" flow invokes at step (b), not a command the human runs. Surfacing it as a manual step is the same failure mode (a step someone must remember) wearing a new script.
- **Publishing without running `gaia:plugin-dryrun`** -- the plugin surface breaks silently. Run the Layer 2 plugin dry-run (pack + extract + headless validate) before any tag.
- **Pre-flight that is a subset of CI** -- skipping `pre-publish:validate` locally means the version drift surfaces after publish. Reproduce CI; do not approximate it.
- **Bumping version sources one at a time** -- desyncs a source by hand; `pre-publish:validate` rejects the tree and a forgotten file ships if the check is skipped. Always go through `release:prepare`.
- **Rebase to reconcile a diverged remote** -- forces a tag move, hard-denied locally. Merge instead.
- **Single-surface testing** -- a change can pass the npm sandbox and break the plugin mount, or vice versa. Layer 2 runs both surfaces for a reason.
- **Stale root manifests** -- editing `build/gaia.manifest.json` or a hook entry without regenerating means the tarball ships old inline hooks. `prepack` regenerates at every `npm pack`, and `release:prepare` regenerates via `generate:plugin-root`; the dry-run packs fresh, so a stale manifest surfaces there.
- **Blanket "restart Claude Code"** -- npm/pnpm hooks reload automatically; only the plugin surface needs `/reload-plugins`, and only a slash-command change needs a full restart. See "Reloading a change".
- **Assuming a `postinstall` ran** -- there is none. The DB is bootstrapped lazily on first `gaia` CLI use; under pnpm a lifecycle hook would never fire anyway. If the DB is missing, run any `gaia` command (or `gaia install`), not "re-run postinstall".
- **Local npm publish** -- bypasses the pipeline's pack + validate + sandbox gate.
