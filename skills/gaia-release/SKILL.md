---
name: gaia-release
description: Use when testing, validating, or publishing Gaia releases -- live install, dry-run, RC, or stable
metadata:
  user-invocable: false
  type: technique
---

# Gaia Release

Single source of truth for install modes. Each mode exercises a different surface: live tests the working tree against a real workspace, dry-run tests the full install pipeline in an ephemeral sandbox, and the published channels (rc / latest) test npm registry delivery. Skipping a layer means discovering its bugs in production -- a clean dry-run does not prove that a freshly published tarball is what consumers actually receive, and a live install over an existing workspace does not predict a missing file on a clean project.

## Install Modes

| Mode | Command | When to use |
|------|---------|-------------|
| **live self** | `cd /path/to/gaia && npm run gaia:install-local` | Re-install Gaia in the same workspace where Claude Code is running (e.g. `me/`). Validates working-tree changes against your dev environment. |
| **live external** | `cd /path/to/gaia && bash bin/validate-sandbox.sh --tarball ./jaguilar87-gaia-*.tgz --target local --workspace /path/to/target` | Install the working tree into a different workspace (e.g. `qxo/`). Validates consumer-real conditions without touching your dev environment. |
| **live fresh** | Add `--fresh` to either of the above | Wipes `node_modules/`, `package.json`, and `package-lock.json` from the target before install. Forces a clean postinstall run. |
| **dry-run** | `npm run gaia:verify-install:local` | Pack + install into `/tmp/gaia-sandbox-<ts>/` + run the 8-check harness. Validates exactly what `npm publish` would ship. |
| **RC** | Version bump to `X.Y.Z-rc.N` + GitHub Release | Pipeline publishes to npm with `--tag rc`. Consumers opt-in: `npm install @jaguilar87/gaia@rc`. |
| **stable** | Version bump to `X.Y.Z` + GitHub Release | Pipeline publishes to npm with `--tag latest`. Default install: `npm install @jaguilar87/gaia`. |

For step-by-step commands per mode (including version-bump syntax, `--stay` for interactive inspection, and how to test both `ops` and `security` plugin modes), see `reference.md` -> "Mode runbooks".

## Wire-up Verification Checklist

After any install (live, dry-run, RC, stable), the same checklist applies. If any check fails, jump to `reference.md` -> "Diagnostic guide".

1. `ls -la <workspace>/.claude/` -- 7 symlinks (agents, hooks, skills, commands, config, templates, tools) + `logs/`, `approvals/`, `plugin-registry.json`, `settings.local.json`.
2. `cat <workspace>/.claude/plugin-registry.json` -- `installed[].name` includes `gaia-ops` (or `gaia-security`) at the expected version.
3. `cat <workspace>/.claude/settings.local.json | jq '.hooks | keys'` -- 12 hook events registered.
4. `ls ~/.gaia/gaia.db` -- DB file exists (bootstrapped by postinstall).
5. `cat ~/.gaia/last-install-error.json` -- file does **not** exist (postinstall completed cleanly; the marker is written on any bootstrap or wire-up failure).
6. `cd <workspace> && gaia doctor` -- `Status: HEALTHY`, 17+ checks pass, 0 errors.

These six checks are not redundant with `gaia doctor`. Steps 1-5 catch what doctor cannot reach when the wire-up is so broken that doctor itself walks up to the user `.claude/` instead of the workspace.

## Release Checklist

Pre-publish, publish, and post-publish steps -- plus the schema migration protocol when `EXPECTED_SCHEMA_VERSION` changes -- live in `reference.md` -> "Release checklist" and "Schema migration protocol". Both are read on-demand from the SKILL when actually doing a release; they are not in this file because they would dominate the line budget without informing the day-to-day mode decision.

## CI/CD

| Workflow | File | Triggers |
|----------|------|----------|
| CI | `.github/workflows/ci.yml` | Push / PR -- runs pytest (Python 3.9/3.11/3.12), Node tests, and plugin build verification |
| Publish | `.github/workflows/publish.yml` | GitHub Release event -- builds plugins, validates artifacts, auto-detects npm tag from version (`-rc.` -> rc, `-beta.` -> beta, else -> latest), and publishes |

`NPM_TOKEN` lives in GitHub Secrets; local `npm publish` bypasses build verification and is not the supported path.

## Anti-Patterns

- **Live-only testing** -- live install runs against your accumulated workspace state; only dry-run proves a clean-install works.
- **Local npm publish** -- bypasses the pipeline's build verification step.
- **Single-mode testing** -- `ops` and `security` load different skill sets and hook configurations; one can break while the other passes.
- **Stale dist/** -- forgetting `npm run build:plugins` before pack means validating old code.
- **Missing restart** -- the process caches skills, hooks, and agents at startup; mode switches and fresh installs require restarting `claude`.
- **Ignoring `~/.gaia/last-install-error.json`** -- when postinstall fails silently, this is the marker that says so. Treat its presence as a hard failure regardless of what `gaia doctor` reports.
- **Relying on auto-detect when cwd is inside the gaia repo** -- the repo has a self-referencing `node_modules/@jaguilar87/gaia/` entry that can trick the workspace detector. Always pass `--workspace /home/jorge/ws/me` explicitly when running installs from within the gaia repo. Verify with `readlink /home/jorge/ws/me/.claude/hooks` post-install -- it must point to the consumer workspace's `node_modules`, not the repo's.
