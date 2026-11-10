---
name: gaia-verify
description: Use when the user wants to verify a Gaia installation -- "probemos la instalación", "verifiquemos que Gaia funcione", "verify", "test installation", "gaia-verify"
---

# Gaia Verify

Confirm that a Gaia installation works: given a machine, an install folder and a delivery surface, run the checks that match the surface and report PASS or FAIL. This skill owns what a healthy install is; `gaia-release` owns how a version gets onto a machine and in what order, and calls this skill at the close of every layer.

## What a healthy install is

**The principle of an installation: on one machine Gaia is one version, from one declared source, in every channel present, all pointing at one database. What is declared, registered, installed and running agree.**

- **Declared** is what the user's files ask for: the marketplace source and ref in Claude Code's settings (`extraKnownMarketplaces`), the spec in a workspace's `package.json`, the entries in `opencode.json`.
- **Registered** is what the host recorded from that: `known_marketplaces.json`, npm's lockfile.
- **Installed** is the copy on disk: the plugin cache entry with its commit, `node_modules/@jaguilar87/gaia`, the npm global copy.
- **Running** is what a session actually loaded, and the `gaia` on `PATH`.

Why each word matters:

- *One version*, because every channel writes the same `~/.gaia/gaia.db`. Two builds on one store means the older one is either refused every write (`gaia/store/writer.py::writes_refused`) or reads a schema it was not written for, whichever channel the user happens to open.
- *One declared source*, because Claude Code ignores a marketplace whose registered source differs from its declaration. Nothing errors in the terminal; the plugin just stops updating.
- *Every channel present*, because the plugin, npm and OpenCode load hooks by different paths. A channel left behind keeps running the old hooks beside the new ones.
- *Agree*, because a disagreement is the defect, whichever side looks newer. A session started before an update still runs the version it loaded, and an install with no declaration behind it is undone by the next update.

The instrument that reads it is `gaia doctor --channels [--json] [--workspace <folder>]` (`bin/cli/_install_state.py::read`). Per channel it prints the four stages, each with its version and origin. It also prints the provenance it can decide locally (the plugin's commit against its tag in the marketplace clone, npm's two integrity records) and the store row (the schema `gaia.db` is at, and for each copy whether it writes it, would migrate it or is refused). Its exit code is the install verdict and nothing else:

| Exit | Verdict | Meaning |
|------|---------|---------|
| 0 | aligned | every stage of every channel agrees |
| 1 | leftovers | a disabled or unused copy remains; nothing running disagrees |
| 2 | split | two versions run, a declaration and its registration differ, or a channel points at a copy that is gone or orphaned |

Read the verdict from the exit code and the `[FAIL]`/`[WARN]` lines, never from a plain `gaia doctor` summary. Plain doctor mixes the install verdict with unrelated workspace warnings, so it can exit 1 on a healthy install and once passed a split one.

## Decision tree

```
"probemos" / "verify" / "test installation"
├─ Is the install on this machine (any channel) right?              -> live
├─ Proving the npm tarball before a release?                        -> npm-sandbox
├─ Proving the npm tarball as a plugin before a release?           -> plugin
├─ Proving every install channel runs the session smoke set,
│  on a tarball or on an RC installed on this machine?             -> smoke
└─ Confirming a version npm already serves?                         -> registry
```

If the user does not name a mode, ask: "Which mode -- live, npm-sandbox, plugin, smoke, or registry?"

## Mode: live

Validates what is already installed on this machine, whichever route installed it. It builds nothing, uses no temp dir and needs no cleanup.

1. `gaia doctor --channels --workspace <install-folder>`: the verdict, read as above. On exit 2, stop and report the `[FAIL]` lines; the rest of this mode would only test one of the split copies.
2. `gaia doctor` in the install folder, then `gaia status`: health of the wiring the verdict does not cover (hooks, links, routing, schema).
3. The wire-up checklist below.

The route that installed it is `gaia-release`'s concern, and it decides which copy you are judging:

- **Layer 1** installs the working tree with `gaia dev` into a dev install folder, never into the folder that runs a published version.
- **Layer 3** installs a published RC or stable through the channels users get. It never uses `gaia dev`, because a stable is only proven on the route users take. Live mode checks both the same way; only the provenance it reports differs (a source commit for `gaia dev`, a tag commit or npm integrity for a published version).

## Mode: npm-sandbox

Validates the npm surface of exactly what a registry publish would ship: pack, install into a clean sandbox, run the harness, clean up. No accumulated install-folder state takes part.

`gaia release check` runs it as gate 2 (`gaia:verify-install:local`). On its own, `npm run gaia:verify-install:local` packs, installs into `gaia-sandbox-<ts>-<pid>/` under the first exec-capable of `$TMPDIR`, `/tmp` and `$HOME/.cache/gaia-sandbox` (`bin/validate-sandbox.sh::select_sandbox_prefix`), runs the harness and cleans up; the manual sequence is in `reference.md`.

## Mode: plugin

Validates the Claude Code plugin surface: the exact npm tarball, extracted, with its root mounted as a plugin. Only this mode exercises the root `plugin.json` (metadata only), `hooks/hooks.json`, the packaged agents and skills and `bin/gaia`; the npm surface never touches them.

`gaia release check` runs it as gate 3 (`gaia:plugin-dryrun`) and SKIPs it when `claude` is not on `PATH`. On its own, `npm run gaia:plugin-dryrun` (`bin/plugin-dryrun.sh`) packs, extracts to a throwaway dir and runs a headless, offline gate: filesystem asserts plus `claude plugin validate`. It touches no install folder and starts no session. `--functional` adds an opt-in live probe that needs Claude auth. Never publish or install to the real registry to run this mode.

## Mode: smoke

Validates that the hooks each install channel loads let the session smoke set run free: `--version`, `now`, `doctor`, `memory show`, `approvals question`, and a subagent's `contract finalize`. Each runs by path and as a bare `gaia`, as orchestrator and as subagent (`bin/cli/_install_smoke.py::smoke`).

- **On a tarball (`install:smoke`).** `gaia release smoke --tarball <tgz>` (or no flag, to pack this tree) lays the tarball out in a throwaway home as the marketplace cache, a workspace's `node_modules`, the npm global prefix and a dev-plugin entry. It is gate 7 of `gaia release check`, and `gaia release publish` runs it on the bumped tree.
- **Live on an RC (`--live`).** Install the RC on this machine with `gaia-release`'s install flow, and confirm the install with mode live. Then `gaia release smoke --live --version x.y.z-rc.N --workspace <install-folder>` drives the copies those channels installed: the marketplace cache and npm (global, or the folder's `node_modules`), both required (`bin/cli/release.py::live_channels`). It records the pass in `release-smoke/<rc>.json` under the state directory. A stable publish is refused without that record (`bin/cli/release.py::_check_promoted_from_rc`). The record is local to the machine that ran it, so the smoke runs where the stable will be published from.

## Mode: registry

Validates a version npm already serves: a fresh temp dir, an install from the registry, the harness, cleanup.

Name the exact version: `bash bin/validate-sandbox.sh --version @x.y.z-rc.N --target sandbox` from the source checkout. A dist-tag (`npm run gaia:verify-install:rc` / `:latest`) resolves to whatever the registry serves at that moment. For several minutes after a publish that is still the previous version, so the check passes on the wrong build. `E404` on the exact version means npm does not serve it yet; wait and retry, and never fall back to the tag. Step-by-step in `reference.md`.

## Wire-up checklist (live / after any install)

These checks catch what `gaia doctor` cannot reach when the wiring is so broken that doctor cannot resolve the install folder. If one fails, go to `gaia-release/reference.md` -> "Diagnostic guide".

Checks 1-3 belong to the npm channel. A plugin-only install folder has none of them: its `.claude/` holds only the `hooks` link and `settings.local.json`, and doctor judges `Symlinks`, `Identity`, `Agent routing`, `Workspace initialized` and `Plugin registered` against the plugin install itself (`bin/cli/doctor.py::_plugin_tree`).

1. `ls -la <install-folder>/.claude/` -- 6 directory links (`agents`, `tools`, `hooks`, `config`, `skills`, `opencode`) and a `CHANGELOG.md` link, plus `logs/`, `approvals/`, `plugin-registry.json`, `settings.local.json` (`_SYMLINK_NAMES` and `_SYMLINK_FILES` in `bin/cli/_install_helpers.py`).
2. `cat <install-folder>/.claude/plugin-registry.json` -- `installed[].name` is `gaia` at the expected version. `gaia` is the only canonical identity (`bin/cli/_install_helpers.py::_read_plugin_name` strips the npm scope), so any other name is a real bug.
3. `cat <install-folder>/.claude/settings.local.json | jq '.hooks | keys'` -- the hook events are registered (npm channel only; the plugin reads hooks from the package root's `hooks/hooks.json`).
4. `ls ~/.gaia/gaia.db` -- the store exists. It is created on the first `gaia` CLI call or by `gaia install`; there is no postinstall.
5. `cat ~/.gaia/last-install-error.json` -- the file does not exist. `gaia install` writes it on any bootstrap or wiring failure; its presence is a failure whatever doctor says.
6. `gaia doctor` in the install folder -- `Status: HEALTHY`, 0 errors. Outside a `node_modules` install, doctor checks the root recorded in `gaia.db` that contains the cwd (or `CLAUDE_PROJECT_DIR`); a folder no recorded root contains exits 2 with the `--workspace` hint.

## The store

The store row of `gaia doctor --channels` names the schema `gaia.db` is at and, for each copy, whether that copy writes it, would migrate it, or is refused. `gaia migrate plan [--json]` shows the chain the data would go through, whether `apply` needs `--consent-chain`, and the backups on hand. Code ahead of the store migrates forward, after a verified backup (`scripts/bootstrap_database.py::_backup`). Code behind the store keeps writing only while the store's `min_code_version` allows it; past that it is refused every write and keeps its reads. The cure is newer code, or `gaia migrate restore --from <backup>` when the user deliberately goes back to the version that backup came from. Never edit the schema by hand.

## All modes: reporting

Every mode ends with a structured result for the contract's evidence; the person hears the verdict first, in `human-register`'s shape:

```
Mode:      <live | npm-sandbox | plugin | smoke | registry>
Channels:  <plugin | npm-local | npm-global | opencode>, each at its version
Verdict:   aligned | leftovers | split (gaia doctor --channels exit 0 | 1 | 2), or n/a
Doctor:    PASS | FAIL
Checklist: <n/n wire-up checks passed, or n/a>
Cleanup:   done | n/a (live)
```

If the verdict is split or `gaia doctor` fails, report the exact lines and stop; later checks would describe one copy of a split install.

## Anti-Patterns

- **Picking a mode by habit.** Each mode tests one surface; a green npm sandbox says nothing about the plugin mount or the OpenCode wiring. Match the mode to what changed or what was installed.
- **Reading the install from a plain `gaia doctor`.** Its exit code mixes unrelated warnings, and a split install once passed it. The verdict is `gaia doctor --channels`.
- **Verifying through a dist-tag.** `@rc` and `@latest` follow the registry, which serves a new version minutes late. The check then validates the previous build and passes.
- **Continuing after a failed verdict.** A split install or a failing doctor makes every later check a description of one copy. Report and stop.
- **Assuming a postinstall bootstrapped the store.** There is none. The store is created on the first `gaia` call or by `gaia install`.
- **Skipping cleanup.** `/tmp/gaia-sandbox-*` and registry temp dirs accumulate; delete them after reporting (n/a for live).
