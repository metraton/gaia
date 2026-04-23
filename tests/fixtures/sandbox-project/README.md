# sandbox-project fixture

Reproducible consumer-project shape used by `bin/validate-sandbox.sh` to
exercise a freshly installed Gaia package end-to-end. The fixture is the
bare minimum a real user project would have after running `gaia-scan`
once and accumulating a handful of episodes -- enough to trigger every
install-time code path (postinstall hook merge, FTS5 backfill, doctor,
context show) without depending on any external state.

## What lives here

```
sandbox-project/
|- README.md                               -- this file
|- package.json.template                   -- minimal consumer package.json
|- .claude/
   |- settings.local.json.template         -- custom settings to verify preservation
   |- project-context/
      |- project-context.json.template     -- schema v2 / contract v4.0 minimum
      |- episodic-memory/
         |- index.json                     -- 10 seeded episodes (NO FTS5 db yet)
         |- episodes/                      -- per-episode JSON files (created by script)
```

The `.template` suffix is stripped by `validate-sandbox.sh` when it
copies the fixture into a scratch directory -- templates stay inert in
git so lint/test tools do not accidentally pick them up as live config.

## Running locally

From the Gaia repo root:

```
npm run gaia:verify-install:local   # npm pack + install tarball in /tmp/ sandbox, full checks
npm run gaia:verify-install:rc      # install @rc from registry in /tmp/ sandbox
npm run gaia:verify-install:latest  # install @latest from registry in /tmp/ sandbox
npm run gaia:install-local          # OVERWRITE local workspace with current working tree (dev fresh-install)
```

Arbitrary version:

```
bash bin/validate-sandbox.sh --version @jaguilar87/gaia@X.Y.Z --target sandbox
```

## How to use

| Script | Target | When |
|--------|--------|------|
| `npm run gaia:verify-install:local` | `/tmp/gaia-sandbox-<ts>/` (ephemeral) | Validating a local build before publishing -- packs working tree, full 8-check harness, auto-cleanup |
| `npm run gaia:verify-install:rc` | `/tmp/gaia-sandbox-<ts>/` (ephemeral) | Smoke-test the `@rc` dist-tag as a real consumer would |
| `npm run gaia:verify-install:latest` | `/tmp/gaia-sandbox-<ts>/` (ephemeral) | Smoke-test the `@latest` dist-tag |
| `npm run gaia:install-local` | `$HOME/ws/me/` or nearest `.claude/` ancestor | **OVERWRITE** local workspace install with current working tree (development fresh-install; replaces symlink-based live mode) |

`gaia:install-local` is the dev iteration loop: edit code in `gaia-ops-dev/`, run it, restart Claude Code. Same harness runs in both modes -- the only difference is workspace path and that the checksum-preservation check is skipped under `--target local` (no pre-snapshot possible for a real workspace).

Keep the sandbox for debugging:

```
bash bin/validate-sandbox.sh --tarball ./jaguilar87-gaia-*.tgz --target sandbox --stay
```

The sandbox dir is printed on exit; inspect `.claude/`, rerun `gaia
doctor`, etc., then `rm -rf` manually when done. `--stay` is ignored
with `--target local`.

## Invariants the harness validates

| # | Check | Why it matters |
|---|-------|----------------|
| 1 | `gaia --version` matches installed version | Rules out stale shim on PATH |
| 2 | `gaia doctor` passes 13/13 | Catches missing hooks, bad symlinks, context drift |
| 3 | `gaia status` exits 0 | Ensures runtime state loader is healthy |
| 4 | `gaia context show` reports >=5 sections | Project-context reader works |
| 5 | FTS5 indexed count >= 9/10 after install | Postinstall backfill safety-net fired |
| 6 | `gaia memory search deploy` returns >=1 hit | FTS5 prefix + hybrid scoring live |
| 7 | `gaia memory search bildwiz-deploy` returns >=1 hit | Hyphen tokenisation fix is in |
| 8 | `gaia scan` exits 0 | Scanner runs cleanly on a minimal project |
| 9 | `settings.local.json` sha256 preserved (user keys) | Postinstall merge does not clobber user config |

Any new invariant goes in `bin/validate-sandbox.sh` as a numbered step
-- each step prints `PASS`/`FAIL` with elapsed ms so a CI log scan is
one-screen readable.

## Adding a new check

1. Add a `_step "<name>" "<command>" "<assertion>"` block to
   `bin/validate-sandbox.sh` after the existing checks.
2. If the check needs a new fixture file, drop it under this tree with
   a `.template` suffix and extend the `copy_fixture` function in the
   script to strip the suffix.
3. If the check needs a precondition (e.g. pre-created empty database),
   extend `prepare_sandbox` -- keep declarative state in templates, not
   runtime shell-generated artifacts that drift between machines.

## Episodes seed

10 episodes with timestamps spread across the last 20 days so scoring
has signal. Keyword distribution:

- 3 mention `deploy`  (smoke search)
- 2 mention `bildwiz-deploy` (hyphen tokenisation regression)
- 2 mention `memory` (dogfood)
- 3 mixed (planner, terraform, gitops) for scoring variety

Shape matches `tools/memory/episodic.py` `Episode` dataclass exactly --
the index entries carry `id`, `timestamp`, `keywords`, `tags`, `type`,
`title`, `relevance_score`, `retrieval_count`, `last_retrieved`.
