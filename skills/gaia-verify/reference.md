# Gaia Verify Reference

The commands for each mode. `<repo>` is the Gaia source checkout and `<TARGET>` the install folder being validated. Run each command on its own and read its result before the next (`command-execution`).

## Mode: live

What is installed on this machine, whichever route installed it. No temp dir, no cleanup.

```bash
gaia doctor --channels --workspace <TARGET>          # exit 0 aligned, 1 leftovers, 2 split
gaia doctor --channels --json --workspace <TARGET>   # the same report as data
gaia doctor --workspace <TARGET>
gaia status
gaia migrate plan                                    # the store: pending chain, consent, backups on hand
```
Then the wire-up checklist (`SKILL.md` -> "Wire-up checklist").

## Mode: npm-sandbox

Fastest path: packs, installs into `gaia-sandbox-<ts>-<pid>/` under `$TMPDIR`, else `/tmp`, else `$HOME/.cache/gaia-sandbox` (the first not mounted noexec, since npm's bin shims must execute; `bin/validate-sandbox.sh::select_sandbox_prefix`), runs the harness, cleans up.
```bash
cd <repo>
npm run gaia:verify-install:local
```

Manual sequence, to inspect the sandbox:
```bash
cd <repo>
npm run pre-publish:validate
npm pack                                    # prepack regenerates root manifests; note the .tgz filename
bash bin/validate-sandbox.sh --tarball ./jaguilar87-gaia-*.tgz --target sandbox --stay
# the sandbox path prints on exit; inspect .claude/, then remove that sandbox folder
```

## Mode: plugin

The exact npm tarball as a plugin: pack, extract, validate the extracted root headless. It touches no install folder and starts no session.

```bash
cd <repo>
npm run gaia:plugin-dryrun                  # pack -> temp extract -> asserts + `claude plugin validate` -> trap cleanup
npm run gaia:plugin-dryrun -- --functional  # opt-in live probe; needs Claude auth
```
`bin/plugin-dryrun.sh` asserts the root `plugin.json` carries no inline `hooks` block (hooks live only in `hooks/hooks.json`; two declarations fire every event twice), plus `hooks/hooks.json`, `bin/gaia`, `agents/`, `skills/` and no `dist/`, then runs `claude plugin validate`. If hooks do not fire, regenerate both files with `npm run generate:plugin-root`. A published version is exercised in a real session through `gaia-release`'s install flow, not here.

## Mode: registry

A version npm already serves, installed from the registry into a temp sandbox. Always the exact version:

```bash
cd <repo>
npm view @jaguilar87/gaia@x.y.z-rc.N version          # prints the version once the registry serves it
bash bin/validate-sandbox.sh --version @x.y.z-rc.N --target sandbox
```
`E404` from `npm view` means the registry does not serve it yet; wait and retry. `npm run gaia:verify-install:rc` and `:latest` install the dist-tag, which can still name the previous version for several minutes after a publish, so they may validate the wrong build.
