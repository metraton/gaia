# Gaia Verify Reference

Exact commands for each mode. Copy and run. The source repo is `/home/jorge/ws/me/gaia`; `<repo>` below is that path and `<TARGET>` is the workspace being validated.

## Mode: live

Already wired workspace (npm/pnpm surface). No temp dir, no cleanup.

```bash
cd <TARGET>
gaia doctor
gaia status
```
Then run the wire-up checklist (see `SKILL.md` -> "Wire-up checklist").

## Mode: npm-sandbox

Fastest path -- packs, installs into `/tmp/gaia-sandbox-<ts>-<pid>/`, runs the harness, cleans up:
```bash
cd <repo>
npm run gaia:verify-install:local
```

Manual sequence (when you need to poke at the sandbox):
```bash
cd <repo>
npm run pre-publish:validate
npm pack                                    # prepack regenerates root manifests; note the .tgz filename
bash bin/validate-sandbox.sh --tarball ./jaguilar87-gaia-*.tgz --target sandbox --stay
# sandbox path prints on exit; inspect .claude/, then:
rm -rf /tmp/gaia-sandbox-*
```

## Mode: plugin

Validates the exact npm tarball as a plugin -- pack, extract, and validate the extracted root headless. Touches no real workspace, spawns no session. Do NOT publish or install to the real registry.

```bash
cd <repo>
npm run gaia:plugin-dryrun                  # pack -> temp extract -> asserts + `claude plugin validate` -> trap cleanup
```
`bin/plugin-dryrun.sh` packs the tarball (its `prepack` regenerates the root `plugin.json` (metadata only) + `hooks/hooks.json`), extracts to a throwaway temp dir (the package root IS the plugin), and asserts the root `plugin.json` carries NO inline `hooks` block (the `assert "hooks" not in plugin` gate -- hooks live only in `hooks/hooks.json`), plus `hooks/hooks.json`, `bin/gaia`, `agents/`, `skills/`, and NO `dist/`, then runs `claude plugin validate`. Both temp dirs are removed by an EXIT trap.

Optional live functional probe (needs Claude auth/tokens, opt-in):
```bash
npm run gaia:plugin-dryrun -- --functional  # `claude --plugin-dir <temp> -p '...'` from a temp cwd
```
If hooks do not fire, inspect the root `hooks/hooks.json` (the canonical hook source; `.claude-plugin/plugin.json` is metadata only and must NOT carry an inline `hooks` block -- regenerate both with `npm run generate:plugin-root`). After publish, the marketplace path can also be exercised inside CC: `/plugin marketplace add <repo>` (`source: github`, pinned `ref`) + `/plugin install gaia@gaia-marketplace` + `/reload-plugins`.

## Mode: registry

Fresh temp dir, install from the published tag, verify, clean up.

```bash
cd <repo>
npm run gaia:verify-install:rc              # @rc tag
# or:
npm run gaia:verify-install:latest          # @latest / stable tag
```

Manual sequence:
```bash
mkdir /tmp/gaia-registry-verify-$(date +%Y%m%d%H%M%S)
cd /tmp/gaia-registry-verify-*
npm init -y
npm install @jaguilar87/gaia@latest         # or @rc
gaia doctor
gaia status
rm -rf /tmp/gaia-registry-verify-*
```

## Notes

- Run each command separately and verify exit code before proceeding (command-execution discipline).
- `npm pack` lands the `.tgz` in the current working directory; run it from `<repo>`.
- The `registry` mode needs network access. `E404` means the version has not published yet -- wait and retry.
- `gaia doctor` exits non-zero on failure. If it fails, stop and report the error; do not run `gaia status`.
- Picking up changes mid-session: npm/pnpm hook edits do NOT hot-reload -- the harness pins each hook's command at session start, so an open session keeps running the OLD hooks until Claude Code is restarted (`bin/cli/dev.py::_restart_warning`); the plugin surface needs `/reload-plugins`; a slash-command change needs a full restart. See `gaia-release/SKILL.md` -> "Reloading a change".
