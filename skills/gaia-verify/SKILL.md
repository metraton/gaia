---
name: gaia-verify
description: Use when the user wants to verify a Gaia installation -- "probemos", "verify", "test installation", "gaia-verify"
---

# Gaia Verify

Confirm that a Gaia installation actually works. Given a workspace and a delivery surface, run the checks that match that surface and report PASS/FAIL. This skill owns the definition of "a healthy install" -- the wire-up checklist and the per-surface checks. It is the check that `gaia-release` calls at the close of every layer; here it stands alone so the user can invoke it directly against whatever they just installed.

Gaia ships as one tree reaching a workspace through two surfaces -- npm/pnpm (the npm package `@jaguilar87/gaia`: symlinks + `settings.local.json`) and the Claude Code plugin (`source: github` with a pinned `ref` -- `.claude-plugin/marketplace.json` advertises the plugin, so `/plugin install` makes CC clone the git repo into its plugin cache and load hooks from the repo root's `hooks/hooks.json`; the root `.claude-plugin/plugin.json` is metadata-only, and there is no `dist/` bundle). A change can pass on one surface and break on the other, so the mode you pick must match the surface you are validating.

## Decision tree

```
"probemos" / "verify" / "test installation"
├─ Already installed in a workspace (npm/pnpm), just edited source? -> live
├─ Proving the npm tarball before a release?                        -> npm-sandbox
├─ Proving the npm tarball as a plugin before a release?           -> plugin
└─ Confirming a version already published to npm?                   -> registry
```

If the user does not name a mode, ask: "Which mode -- live, npm-sandbox, plugin, or registry?"

## Mode: live

Validates a workspace that is already wired (npm/pnpm surface). No build, no temp dir, no cleanup.

Run against the workspace: `gaia doctor` then `gaia status`, then the **wire-up checklist** below. This is the mode `gaia-release` Layer 1 and Layer 3 call after installing into the target (via `gaia dev --workspace <TARGET>`, the one-command install).

## Mode: npm-sandbox

Validates the npm surface of exactly what a registry publish would ship -- pack, install into a clean sandbox, run the harness, clean up. No accumulated workspace state.

Primary path: `gaia release check` runs this gate (as gate 2, `gaia:verify-install:local`) inside the full Layer 2 sequence. To run just this surface in isolation, `npm run gaia:verify-install:local` (packs, installs into `/tmp/gaia-sandbox-<ts>-<pid>/`, runs the harness, cleans up) is what `gaia release check` wraps -- manual step-by-step in `reference.md`. This is the mode `gaia-release` Layer 2 gate 2 calls.

## Mode: plugin

Validates the Claude Code plugin surface -- the exact npm tarball, extracted, with its root mounted as a plugin. This is the only mode that exercises the root `plugin.json` (metadata-only) / `hooks/hooks.json`, the packaged agents/skills, and `bin/gaia` on PATH; the npm surface never touches any of it.

Primary path: `gaia release check` runs this gate (as gate 3, `gaia:plugin-dryrun`) inside the full Layer 2 sequence, and SKIPs it gracefully when the `claude` binary is absent. To run just this surface, `npm run gaia:plugin-dryrun` (`bin/plugin-dryrun.sh`, what `gaia release check` wraps) packs the tarball, extracts it to a throwaway temp dir, and runs a headless, offline gate -- filesystem asserts (root `plugin.json` present with NO inline `hooks` block, `hooks/hooks.json`, `bin/gaia`, `agents/`, `skills/`, and NO `dist/`) + `claude plugin validate`. It touches no real workspace and spawns no session; the temps are trap-cleaned. Add `gaia release check --functional` (or `npm run gaia:plugin-dryrun -- --functional`) for an optional live `claude --plugin-dir <temp> -p '...'` probe (needs Claude auth/tokens). This is the mode `gaia-release` Layer 2 gate 3 calls. Do NOT publish or install to the real registry to run this.

## Mode: registry

Validates a version already published to npm -- fresh temp dir, install from the registry tag, verify, clean up.

Core flow: `npm run gaia:verify-install:rc` (the `@rc` tag) or `npm run gaia:verify-install:latest` (the `@latest` / stable tag). Step-by-step in `reference.md`. This is the mode `gaia-release` Layer 3 step (h) calls after the pipeline publishes.

## Wire-up checklist (live / after any install)

After wiring a workspace, these checks catch what `gaia doctor` cannot reach when the wire-up is so broken that doctor itself walks up to the user `.claude/` instead of the workspace. If any check fails, jump to `gaia-release/reference.md` -> "Diagnostic guide".

1. `ls -la <workspace>/.claude/` -- **5 symlinks** (`agents`, `tools`, `hooks`, `config`, `skills`) + a `CHANGELOG.md` link, plus `logs/`, `approvals/`, `plugin-registry.json`, `settings.local.json`. (`_SYMLINK_NAMES` + `_SYMLINK_FILES` in `bin/cli/_install_helpers.py`.)
2. `cat <workspace>/.claude/plugin-registry.json` -- `installed[].name` at the expected version. **Decided:** the canonical registry identity is `gaia` (`_read_plugin_name` in `_install_helpers.py` strips the npm scope from `@jaguilar87/gaia` and falls back to `"gaia"`). A fresh install always writes `gaia`; fail the check if the name is anything other than `gaia`.
3. `cat <workspace>/.claude/settings.local.json | jq '.hooks | keys'` -- hook events registered (npm surface only; the plugin surface reads hooks from the repo root's `hooks/hooks.json`, not from `settings.local.json` or the metadata-only `plugin.json`).
4. `ls ~/.gaia/gaia.db` -- DB file exists. It is bootstrapped **lazily on first `gaia` CLI use** (`_ensure_db_bootstrapped` in `bin/gaia`) or by `gaia install` -- there is no postinstall.
5. `cat ~/.gaia/last-install-error.json` -- file does **not** exist. `gaia install` writes this marker on any bootstrap or wire-up failure; treat its presence as a hard failure regardless of what `gaia doctor` reports.
6. `cd <workspace> && gaia doctor` -- `Status: HEALTHY`, checks pass, 0 errors.

## Drift-free surfaces (what doctor validates == what dev/release reconcile)

An install is drift-free when **5 surfaces** agree on one build, plus the DB
**schema direction** is not reversed. `gaia doctor` REPORTS this skew (it never
fixes -- the reconcile lives in the install actors); the checklist above and the
per-surface report `gaia dev` prints after wiring both mirror it. The surfaces
(inspected read-only by `bin/cli/_converge.py`, classified aligned / stale /
absent):

1. **PATH `gaia`** -- a bare `gaia` resolves to the expected build (`gaia doctor`
   check 58, `Global CLI alignment`). A stale `npm install -g` copy earlier on
   PATH shadowing the workspace shim is the classic drift.
2. **hooks in `.claude/settings.local.json`** (checklist 3 / doctor `Settings`).
3. **workspace `node_modules/@jaguilar87/gaia`** (doctor `Install provenance`).
4. **global npm** (`~/.npm-global`) -- reconciled to the origin by `gaia dev`
   via `npm link` (`install.reconcile_global_via_npm_link`); doctor warns on a
   PATH-shadowing global (POSIX + Windows).
5. **DB schema** (`~/.gaia/gaia.db`) -- `gaia doctor` check `Schema version`
   reports BOTH directions: code AHEAD of DB (forward migration pending -> run
   `gaia dev`/`install`/`release`) and code BEHIND DB (the reverse,
   finalize-breaking drift the bootstrap direction guard REFUSES -- install
   newer code, never downgrade the DB).

The reconcile is idempotent in all 3 cases (not installed / stale / aligned):
`gaia dev` (origin = local source) and `gaia release` (origin = artifact) share
this convergence; there is no `--from` flag -- the command IS the origin.

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
- **Accepting a non-canonical registry name** -- `gaia` is the sole canonical registry identity (check 2). A fresh install that writes anything other than `gaia` is a real bug.
- **Assuming a postinstall bootstrapped the DB** -- the DB is lazy (first CLI use) or explicit (`gaia install`); if it is missing, run a `gaia` command, not "re-run postinstall".
- **Skipping cleanup** -- `/tmp/gaia-sandbox-*` and registry temp dirs accumulate; always delete after reporting (n/a for live).
- **Continuing after doctor failure** -- a failing doctor means the installation is broken; status output is meaningless.
