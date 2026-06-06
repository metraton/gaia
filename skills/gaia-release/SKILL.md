---
name: gaia-release
description: Use when testing, validating, or publishing Gaia releases -- "install local", "dry-run", "release", live install, sandbox verify, RC, or stable
metadata:
  user-invocable: false
  type: technique
---

# Gaia Release

The norm for getting Gaia onto a machine and into the registry. The user expresses exactly one of three intentions -- **install local**, **dry-run**, or **release** -- and each maps to a complete, automated sequence the orchestrator runs end-to-end. The user never recalls a sub-step and never runs a release script by hand: the script is a tool the flow invokes, not a command the human must remember. This is the lesson of the sagas that shipped broken: a release failed because a version source was bumped one file at a time and a forgotten `pyproject.toml` drifted; another failed because the local pre-flight skipped the CI gate that would have caught a Python 3.9 syntax break; a third needed a force-push to reconcile a tag. Every one of those was a manual step a human was trusted to remember and didn't. The fix is to norm the sequence so the steps cannot be forgotten -- they are the flow, not a checklist beside it.

## The three intentions

When the user says one of these, run the *whole* sequence. Do not stop after the first command and wait to be told the next one -- the sequence below IS the intention.

### "install local" -- put the working tree into a real workspace

```
npm run gaia:install-local
```
Then, without being asked:
1. Run the **Wire-up verification checklist** (below). If any check fails, jump to `reference.md` -> "Diagnostic guide".
2. **Remind the user to restart `claude`** -- skills, hooks, and agents cache at startup, so a fresh install is invisible until restart.

Installing into a *different* workspace (e.g. `qxo/`) or wiping install metadata first is the same intention with a different target -- see `reference.md` -> "Mode runbooks" for the `--workspace` and `--fresh` forms. Always pass `--workspace` explicitly when invoking from inside the gaia repo (the self-referencing `node_modules/@jaguilar87/gaia/` tricks auto-detect; guarded by `is_gaia_repo_root()` in `validate-sandbox.sh`).

### "dry-run" -- prove a clean install works, reproducing CI locally

This is not just the sandbox harness -- it is the **local stand-in for CI**, so it must run the same gates CI runs (see the pre-flight principle below). Run, in order:
1. `npm run check:py39` -- the static Python 3.9 union check (`scripts/check-py39-compat.py`). Catches the `X | None` annotation bug that 3.10+ accepts and the 3.9 matrix leg rejects, without needing 3.9 installed.
2. `npm run pre-publish:validate` -- the version-drift gate (`validate-manifests` in `ci.yml`).
3. `npm run gaia:verify-install:local` -- packs, installs into `/tmp/gaia-sandbox-<ts>/`, runs the 8-check harness. Validates exactly what `npm publish` would ship.
4. `npm test` -- the L1 suite (the harness/tests CI runs that reasonably reproduce locally).

A green dry-run that skips step 1 or 2 is a *subset* of CI, not a stand-in for it -- the gap surfaces only after publish, when the fix costs another release.

### "release [version]" -- end-to-end publish, fully automated

The orchestrator runs every step below in order. The user supplies (or confirms) the version and approves the T3 operations; the orchestrator does the rest. **The user does not run `release:prepare` -- step (b) invokes it.**

| Step | Action | Notes |
|------|--------|-------|
| **(a)** | Determine the version | Default to the next **patch**. If the change is major/minor, **confirm with the user** (`NEEDS_INPUT`) before proceeding -- never silently pick major/minor. |
| **(b)** | `npm run release:prepare <version>` | The atomic core: bumps ALL version sources at once (`package.json`, `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `CHANGELOG.md`), runs `build:plugins`, then `pre-publish:validate`. Fails loud on any drift. This is `scripts/release-prepare.mjs` -- invoked by the flow, never by the user. |
| **(c)** | Pre-flight that reproduces CI | `pre-publish:validate` already ran inside (b). Now run `npm run check:py39` and `npm test` (plus any harness that applies) so the local gate matches CI before the tag exists. |
| **(d)** | Commit | `git add` + `git commit` -- local-only, not T3. |
| **(e)** | Tag, **force-free** | A *new* tag (`v<version>`); never move an existing one. If the remote diverged, reconcile with **merge, not rebase** (rebase forces a tag move, hard-denied locally). See `reference.md` -> "Reconciling a diverged remote". |
| **(f)** | Push | `git push` (T3). If diverged, the merge from (e) makes this force-free. |
| **(g)** | `gh release create v<version>` | Triggers `publish.yml`, which builds, validates, and publishes to npm with the auto-detected tag (`-rc.` -> rc, else latest). Mark RC as pre-release. |
| **(h)** | Monitor to the outcome | Watch the workflow run to its desenlace, then verify the package landed on npm (`npm run gaia:verify-install:rc` / `:latest`). The release is not done when the tag is pushed -- it is done when npm serves the new version. |

For the full command forms, the schema-migration lockstep, and the diverged-remote reconciliation, see `reference.md`.

## Wire-up verification checklist

After any install (install local, dry-run sandbox, RC, stable), the same checklist applies. If any check fails, jump to `reference.md` -> "Diagnostic guide".

1. `ls -la <workspace>/.claude/` -- 7 symlinks (agents, hooks, skills, commands, config, templates, tools) + `logs/`, `approvals/`, `plugin-registry.json`, `settings.local.json`.
2. `cat <workspace>/.claude/plugin-registry.json` -- `installed[].name` includes `gaia-ops` (or `gaia-security`) at the expected version.
3. `cat <workspace>/.claude/settings.local.json | jq '.hooks | keys'` -- 12 hook events registered.
4. `ls ~/.gaia/gaia.db` -- DB file exists (bootstrapped by postinstall).
5. `cat ~/.gaia/last-install-error.json` -- file does **not** exist (postinstall completed cleanly; the marker is written on any bootstrap or wire-up failure).
6. `cd <workspace> && gaia doctor` -- `Status: HEALTHY`, 17+ checks pass, 0 errors.

These six checks are not redundant with `gaia doctor`. Steps 1-5 catch what doctor cannot reach when the wire-up is so broken that doctor itself walks up to the user `.claude/` instead of the workspace.

## CI/CD

| Workflow | File | Triggers |
|----------|------|----------|
| CI | `.github/workflows/ci.yml` | Push / PR -- runs pytest (Python 3.9/3.11/3.12), Node tests, plugin build verification, and `validate-manifests` |
| Publish | `.github/workflows/publish.yml` | GitHub Release event -- builds plugins, validates artifacts, auto-detects npm tag from version (`-rc.` -> rc, `-beta.` -> beta, else -> latest), and publishes |

`NPM_TOKEN` lives in GitHub Secrets; local `npm publish` bypasses build verification and is not the supported path.

## Principles -- why the sequence is normed, not optional

- **The pre-flight reproduces what CI validates, not a subset of it.** When the local check skips a gate CI runs (`pre-publish:validate`, the Python 3.9 leg), that gate's failures surface only *after* publishing, on the published tarball, where the only remedy is another release. That is exactly how a `pyproject.toml` drift and a Python 3.9 `type | None` break both shipped green-local and red-CI. The "dry-run" intention and step (c) of "release" close both gaps -- `check:py39` for the 3.9 leg, `pre-publish:validate` for drift. See `reference.md` -> "The pre-flight reproduces what CI validates".
- **Bump every version source in one step, never one at a time.** `pre-publish:validate` requires `package.json`, `pyproject.toml`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, and the `CHANGELOG.md` top header to agree. A partial bump leaves the tree in a state the validator rejects and lets a stale source ship. `release:prepare` writes all of them from one target version, so a hand-desync is impossible. See `scripts/release-prepare.mjs`.
- **Tag force-free; reconcile with merge, never rebase.** `publish.yml` commits built artifacts back to `main`, so the remote leads after every release. Rebasing rewrites hashes and forces a tag move (`git tag -f` / `--force`), hard-denied by local hooks (`git_destructive` in `blocked_commands.py`, exit 2, not approvable). Merge preserves hashes and tags; a new release gets a *new* tag, never a moved one. See `reference.md` -> "Reconciling a diverged remote".
- **A release ends at npm, not at the tag.** Pushing the tag only starts `publish.yml`. The intention is not satisfied until the workflow reaches its outcome and npm serves the new version -- step (h) is part of the sequence, not a follow-up.

## Anti-Patterns

- **Stopping after the first command of an intention** -- "install local" is not just `gaia:install-local`; "release" is not just `release:prepare`. Each intention is the *whole* sequence. Running one command and waiting to be told the next reintroduces the forgettable manual step the norm exists to remove.
- **Asking the user to run `release:prepare`** -- it is a tool the "release" flow invokes at step (b), not a command the human runs. Surfacing it as a manual step is the same failure mode (a step someone must remember) wearing a new script.
- **Pre-flight that is a subset of CI** -- skipping `check:py39` or `pre-publish:validate` locally means the 3.9 break or the version drift surfaces after publish. Reproduce CI; do not approximate it.
- **Bumping version sources one at a time** -- desyncs a source by hand; `pre-publish:validate` rejects the tree and a forgotten file ships if the check is skipped. Always go through `release:prepare`.
- **Rebase to reconcile a diverged remote** -- forces a tag move, hard-denied locally. Merge instead.
- **Live-only testing** -- live install runs against accumulated workspace state; only dry-run proves a clean install works.
- **Local npm publish** -- bypasses the pipeline's build verification step.
- **Single-mode testing** -- `ops` and `security` load different skill sets and hook configurations; one can break while the other passes.
- **Stale dist/** -- forgetting `npm run build:plugins` before pack means validating old code. `release:prepare` and `build:plugins` regenerate it; dry-run packs fresh.
- **Missing restart** -- the process caches skills, hooks, and agents at startup; installs and mode switches require restarting `claude`.
- **Ignoring `~/.gaia/last-install-error.json`** -- when postinstall fails silently, this is the marker. Treat its presence as a hard failure regardless of what `gaia doctor` reports.
- **Relying on auto-detect when cwd is inside the gaia repo** -- the self-referencing `node_modules/@jaguilar87/gaia/` entry tricks the workspace detector. Pass `--workspace /home/jorge/ws/me` explicitly; verify with `readlink /home/jorge/ws/me/.claude/hooks`.
