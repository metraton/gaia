---
name: gaia-verify
description: Use when the user wants to verify a Gaia installation -- "probemos", "verify", "test installation", "gaia-verify"
metadata:
  user-invocable: true
  type: technique
---

# Gaia Verify

Confirm that a Gaia installation actually works. Given a workspace and a delivery surface, run the checks that match that surface and report PASS/FAIL. This skill owns the definition of "a healthy install" -- the wire-up checklist and the per-surface checks. It is the check that `gaia-release` calls at the close of every layer; here it stands alone so the user can invoke it directly against whatever they just installed.

Gaia ships as one plugin (`gaia`) reaching a workspace through two surfaces -- npm/pnpm (symlinks + `settings.local.json`) and the Claude Code plugin (`dist/gaia` bundle with inline hooks). A change can pass on one surface and break on the other, so the mode you pick must match the surface you are validating.

## Decision tree

```
"probemos" / "verify" / "test installation"
├─ Already installed in a workspace (npm/pnpm), just edited source? -> live
├─ Proving the npm tarball before a release?                        -> npm-sandbox
├─ Proving the plugin bundle (dist/gaia) before a release?          -> plugin
└─ Confirming a version already published to npm?                   -> registry
```

If the user does not name a mode, ask: "Which mode -- live, npm-sandbox, plugin, or registry?"

## Mode: live

Validates a workspace that is already wired (npm/pnpm surface). No build, no temp dir, no cleanup.

Run against the workspace: `gaia doctor` then `gaia status`, then the **wire-up checklist** below. This is the mode `gaia-release` Layer 1 and Layer 3 call after installing into the target.

## Mode: npm-sandbox

Validates the npm surface of exactly what `npm publish` would ship -- pack, install into a clean sandbox, run the harness, clean up. No accumulated workspace state.

Fastest path: `npm run gaia:verify-install:local` (packs, installs into `/tmp/gaia-sandbox-<ts>-<pid>/`, runs the harness, cleans up). Manual step-by-step in `reference.md`. This is the mode `gaia-release` Layer 2 step 2 calls.

## Mode: plugin

Validates the Claude Code plugin surface -- the built `dist/gaia` bundle mounted in a live Claude Code. This is the only mode that exercises the generated inline `hooks.json` / `plugin.json`, the bundled agents/skills, and `bin/gaia` on PATH; the npm surface never touches any of it.

Core flow (commands in `reference.md`): `npm run build:plugins` to regenerate `dist/gaia`, then `claude --plugin-dir <repo>/dist/gaia` (or `/plugin marketplace add` + `/plugin install`), then `gaia doctor` inside that session. This is the mode `gaia-release` Layer 2 step 3 calls. Do NOT publish or install to the real registry to run this.

## Mode: registry

Validates a version already published to npm -- fresh temp dir, install from the registry tag, verify, clean up.

Core flow: `npm run gaia:verify-install:rc` (the `@rc` tag) or `npm run gaia:verify-install:latest` (the `@latest` / stable tag). Step-by-step in `reference.md`. This is the mode `gaia-release` Layer 3 step (h) calls after the pipeline publishes.

## Wire-up checklist (live / after any install)

After wiring a workspace, these checks catch what `gaia doctor` cannot reach when the wire-up is so broken that doctor itself walks up to the user `.claude/` instead of the workspace. If any check fails, jump to `gaia-release/reference.md` -> "Diagnostic guide".

1. `ls -la <workspace>/.claude/` -- **5 symlinks** (`agents`, `tools`, `hooks`, `config`, `skills`) + a `CHANGELOG.md` link, plus `logs/`, `approvals/`, `plugin-registry.json`, `settings.local.json`. (`_SYMLINK_NAMES` + `_SYMLINK_FILES` in `bin/cli/_install_helpers.py`.)
2. `cat <workspace>/.claude/plugin-registry.json` -- `installed[].name` at the expected version. **TBD (open user decision):** the plugin bundle is named `gaia`, but the registry currently records `gaia-ops` as the canonical name (`register_plugin` in `_install_helpers.py` maps `gaia` -> `gaia-ops`, and `gaia doctor` expects it). Whether the registry identity becomes `gaia` or stays `gaia-ops` is not yet decided -- validate against whatever the install wrote; do NOT hardcode one and fail the other.
3. `cat <workspace>/.claude/settings.local.json | jq '.hooks | keys'` -- hook events registered (npm surface only; the plugin surface reads hooks from the bundle's `hooks.json` / inline `plugin.json`, not from `settings.local.json`).
4. `ls ~/.gaia/gaia.db` -- DB file exists. It is bootstrapped **lazily on first `gaia` CLI use** (`_ensure_db_bootstrapped` in `bin/gaia`) or by `gaia install` -- there is no postinstall.
5. `cat ~/.gaia/last-install-error.json` -- file does **not** exist. `gaia install` writes this marker on any bootstrap or wire-up failure; treat its presence as a hard failure regardless of what `gaia doctor` reports.
6. `cd <workspace> && gaia doctor` -- `Status: HEALTHY`, checks pass, 0 errors.

## All modes: reporting

Every mode ends with a structured result:

```
Mode:     <live | npm-sandbox | plugin | registry>
Surface:  <npm/pnpm | plugin>
Version:  <version installed, or "symlinked source" for live>
Doctor:   PASS | FAIL
Status:   <`gaia status` output summary>
Checklist: <n/n wire-up checks passed, or n/a>
Cleanup:  done | n/a (live)
```

If `gaia doctor` fails, report the exact error and stop -- do not continue to `gaia status`.

## Anti-Patterns

- **Skipping the mode question** -- each mode tests a different surface; running the wrong one gives false confidence. A green npm-sandbox says nothing about the plugin bundle.
- **Validating only one surface** -- npm/pnpm and plugin load hooks by different paths; one can pass while the other is broken. Match the mode to what changed.
- **Hardcoding the registry name** -- the `gaia` vs `gaia-ops` identity is an open decision (check 2). Validate against what the install wrote, not against a fixed string.
- **Assuming a postinstall bootstrapped the DB** -- the DB is lazy (first CLI use) or explicit (`gaia install`); if it is missing, run a `gaia` command, not "re-run postinstall".
- **Skipping cleanup** -- `/tmp/gaia-sandbox-*` and registry temp dirs accumulate; always delete after reporting (n/a for live).
- **Continuing after doctor failure** -- a failing doctor means the installation is broken; status output is meaningless.
