# Bin

The `bin/` directory holds the command-line surface of Gaia. There is one user-facing binary -- `gaia` -- and every operation is reached through a subcommand of it. The subcommands are not separate scripts you maintain individually; they are Python modules in `bin/cli/` that the dispatcher discovers at runtime.

The diagnostic model to learn first is `gaia doctor`. Every subcommand follows the same pattern -- parse args, resolve paths, run checks, exit with a status code -- so reading `bin/cli/doctor.py` once tells you how every other subcommand here works.

## Cuándo se activa

```
User runs: gaia <subcommand> [args]
        |
bin/gaia (Python entry point) loads the dispatcher
        |
bin/cli/__init__.py imports every module in bin/cli/ that defines register()
        |
Each module's register(subparsers) attaches its argparse + cmd_<name>() handler
        |
Dispatcher routes to the matched handler, which exits with a status code
```

There is **no npm `postinstall` hook** — install is non-invasive and bootstrap is lazy. The DB is created on the first `gaia` CLI use (`_ensure_db_bootstrapped` in `bin/gaia`, skipped only for `install`/`uninstall` themselves), and workspace `.claude/` config is written by running `gaia install` explicitly or by the SessionStart hook:

```
npm|pnpm install @jaguilar87/gaia
        |
(no postinstall — nothing runs automatically)
        |
First `gaia <cmd>` -> _ensure_db_bootstrapped() seeds ~/.gaia/gaia.db (lazy)
        |
gaia install -> merges permissions/hooks, recreates symlinks, writes registry
```

The one lifecycle script that remains is `preuninstall`:

```
npm uninstall @jaguilar87/gaia
        |
preuninstall script -> python3 bin/gaia uninstall --preuninstall
        |
Removes Gaia-owned symlinks (agents, hooks, skills, …), cleans caches /
logs / __pycache__, and surgically removes only Gaia's contributions from
settings.local.json and plugin-registry.json
```

No Claude Code session is involved in either case. The subcommands run in a normal Python process and interact with the filesystem directly.

## Qué hay aquí

```
bin/
├── gaia                       # Python entry point — dispatches to bin/cli/<name>
├── pre-publish-validate.js    # Pre-publish gate for the release pipeline
├── python-detect.js           # Python runtime detection helper for npm lifecycles
├── validate-sandbox.sh        # End-to-end consumer-install verification harness
├── README.md
└── cli/                       # Subcommand modules (one file per subcommand)
    ├── __init__.py            # Discovery: imports every sibling that defines register()
    ├── _install_helpers.py    # Shared helpers for install/update (private, leading _)
    ├── ac.py                  # gaia ac         — acceptance criteria for briefs (DB-canonical)
    ├── approvals.py           # gaia approvals  — list/show/reject/clean/stats T3 grants
    ├── brief.py               # gaia brief      — feature briefs / specs lifecycle
    ├── cleanup.py             # gaia cleanup    — preuninstall: caches, logs, __pycache__
    ├── context.py             # gaia context    — show / scan / get / query / wipe / prune-workspaces project context from gaia.db
    ├── contract.py            # gaia contract   — build/validate an agent_contract_handoff draft by-value: init/set/add/view (--field for one subtree)/validate/finalize + fill --json
    ├── dev.py                 # gaia dev        — fast local dev loop: pack/link + install + wire, one command
    ├── doctor.py              # gaia doctor     — system health check (the model to learn)
    ├── evidence.py            # gaia evidence   — per-AC evidence (three-tier storage)
    ├── history.py             # gaia history    — recent agent sessions
    ├── install.py             # gaia install    — bootstrap DB, settings, symlinks (run manually; no postinstall)
    ├── memory.py              # gaia memory     — curated memory (append/add/edit/reclassify/delete/link) + reads (show [--links|--history], story) + episodic log (stats, search, episode-show)
    ├── memory_story.py        # backs `gaia memory story` (lineage narration); imported by memory.py, no register() of its own
    ├── metrics.py             # gaia metrics    — usage analytics (DB-canonical episodes/anomalies + audit-log tier/commands)
    ├── milestone.py           # gaia milestone  — milestone management for briefs (DB-canonical)
    ├── _pack_helpers.py       # shared `npm pack` primitive for dev/release (private, no register())
    ├── paths.py               # Shared path resolution helpers
    ├── plan.py                # gaia plan       — manage plans (one per brief, DB-canonical)
    ├── query.py               # gaia query      — cross-surface read-only query (memory, episodes, harness_events)
    ├── release.py             # gaia release    — check (Layer 2 local gate) | publish (Layer 3 trigger sequence)
    ├── task.py                # gaia task       — manage tasks within plans (DB-canonical)
    ├── workspace.py           # gaia workspace  — workspace identity / consolidate operations
    ├── scan.py                # gaia scan       — project scanner; writes scan results to gaia.db (DB-canonical)
    ├── status.py              # gaia status     — quick installation snapshot
    ├── uninstall.py           # gaia uninstall  — full or preuninstall removal
    └── update.py              # gaia update     — re-sync after npm install bumped the version
```

**`gaia contract` (by-value contract construction):** `gaia contract init --agent-id <id>` mints a draft (its own contract id, never `CLAUDE_SESSION_ID`); `set`/`add`/`fill --json` mutate it field-by-field or in a batch, each call validating the full resulting envelope on write (no false-pass) before persisting; `view` prints the current draft without mutating it (or, with `--field <dotted-path>`, ONLY that subtree of the envelope, addressed with the same dotted-path scheme `set` uses -- an absent path is a clean non-zero-exit error); `validate` reports the verdict without mutating; `finalize` confirms the verdict and writes the SOLE, idempotent `agent_contract_handoffs` row. Every verb delegates to the single combined validator entry point (`gaia.contract.crosscheck.validate`, which layers `gaia.contract.validator`'s pure-stdlib shape check under a gaia.db cross-check) — this CLI never re-implements shape rules. See `skills/agent-contract-handoff/SKILL.md` for the field schema and `gaia/contract/validator.py` for the SSOT repair message.

**Fast local dev loop and release flow (`gaia dev`, `gaia release`):** `gaia dev [--workspace <path>] [--mode pack|link]` collapses the manual `npm pack` + `npm/pnpm add <tarball>` + `gaia install` sequence into one command. `gaia release check [--functional]` runs the full offline Layer 2 pre-release gate (drift check, npm-sandbox install, plugin dry-run, `npm test`) as one command. `gaia release publish [version] [--dry-run]` runs the Layer 3 trigger sequence -- version bump, test, commit, tag, then the Tier-3 `git push` / `gh release create` that hands off to `.github/workflows/publish.yml`; it never runs npm's own registry-publish step itself, that stays in CI behind `NODE_AUTH_TOKEN`. See `skills/gaia-release/SKILL.md` for the full three-layer model these commands implement.

## Convenciones

**Subcommand contract:** Every file in `bin/cli/` that exposes a subcommand defines two functions:

```python
def register(subparsers) -> None:
    """Attach this subcommand's argparse parser. Called once at startup
    by bin/cli/__init__.py."""
    p = subparsers.add_parser("<name>", help="...")
    p.add_argument(...)
    p.set_defaults(func=cmd_<name>)

def cmd_<name>(args) -> int:
    """Handler. Receives parsed argparse Namespace, returns exit code."""
```

Modules whose name starts with `_` (e.g. `_install_helpers.py`) are private helpers, never registered as subcommands. Files like `paths.py` that expose only utilities and no `register()` are also skipped by the dispatcher.

**Lifecycle binding:** Only `gaia uninstall` (preuninstall) is wired to an npm event via `package.json` `scripts`. There is no `postinstall` — install bootstraps lazily on first `gaia` use (`_ensure_db_bootstrapped` in `bin/gaia`) and via explicit `gaia install`. The `--postinstall` flag on `gaia install` still exists for fail-soft non-interactive callers, but nothing in the npm/pnpm lifecycle invokes it automatically.

**Path resolution:** Subcommands resolve paths through symlinks to the source package using `Path.resolve()`. The pattern is visible in `cli/doctor.py`.

**Exit codes:** `0` on success, `1` on warnings, `2` on errors. The release pipeline's sandbox harness relies on these -- do not print a success line and exit non-zero, or vice versa.

**Cleanup footprint:** Full cleanup (the default, used by `gaia uninstall`) removes everything `gaia install` wrote: `CLAUDE.md`, `.claude/settings.json`, all Gaia-owned symlinks (`.claude/agents`, `.claude/hooks`, `.claude/skills`, and siblings), and the `.claude/.plugin-initialized` marker. Two files are handled surgically because they are shared with Claude Code: `settings.local.json` has only Gaia-injected keys removed (agent identity, two env vars, Gaia's permission entries; user content is preserved); `plugin-registry.json` has only Gaia's `installed[]` entry removed and is deleted only if it contained nothing else. The user DB at `~/.gaia/gaia.db` is NEVER deleted by `gaia uninstall` -- there is no purge path. By default, uninstall takes a gzip snapshot of the DB before it runs (pass `--no-backup` to skip that snapshot); `SessionStart` independently auto-backs up the DB at most once per 24h, keeping the last 5 snapshots. The canonical source for what gets removed is `cli/cleanup.py` (`SYMLINKS_TO_REMOVE`, `_clean_settings_local_json`, `_remove_plugin_registry_entry`); the shared snapshot logic lives in `gaia/paths/snapshot.py`.

**`package.json` `bin` field:**

```json
{
  "bin": {
    "gaia": "bin/gaia"
  }
}
```

A single binary; subcommands are discovered, not registered.

## Ver también

- [`package.json`](../package.json) -- exposes `bin/gaia`; `scripts.preuninstall` wires the one lifecycle subcommand (no `postinstall`; see `_install_note`)
- [`INSTALL.md`](../INSTALL.md) -- installation workflow that calls `gaia scan` and `gaia install`
- [`hooks/README.md`](../hooks/README.md) -- `gaia doctor` verifies the hook registrations are valid
- [`bin/validate-sandbox.sh`](./validate-sandbox.sh) -- end-to-end harness that drives `gaia` subcommands against a fresh tarball install
- [`skills/gaia-release/SKILL.md`](../skills/gaia-release/SKILL.md) -- the three-layer install/release model `gaia dev` and `gaia release check|publish` implement
- [`skills/gaia-verify/SKILL.md`](../skills/gaia-verify/SKILL.md) -- how to validate what `gaia dev` / `gaia release check|publish` just installed or triggered
- [`skills/agent-contract-handoff/SKILL.md`](../skills/agent-contract-handoff/SKILL.md) -- the field schema `gaia contract` builds and validates by-value
- [`gaia/contract/validator.py`](../gaia/contract/validator.py) -- the portable form-layer validator (SSOT for `CANONICAL_REPAIR_MESSAGE`) every `gaia contract` write delegates to
