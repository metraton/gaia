# Bin

The `bin/` directory holds the command-line surface of Gaia. There is one user-facing binary -- `gaia` -- and every operation is reached through a subcommand of it. The subcommands are not separate scripts you maintain individually; they are Python modules in `bin/cli/` that the dispatcher discovers at runtime.

The diagnostic model to learn first is `gaia doctor`. Every subcommand follows the same pattern -- parse args, resolve paths, run checks, exit with a status code -- so reading `bin/cli/doctor.py` once tells you how every other subcommand here works.

## When it runs

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

There is **no npm `postinstall` hook** — install is non-invasive and bootstrap is lazy. The DB is created on the first `gaia` CLI use (`_ensure_db_bootstrapped` in `bin/gaia`, skipped for `install`, `uninstall`, `migrate` and `now`, `_LAZY_BOOTSTRAP_SKIP`), and the install folder's `.claude/` config is written by `gaia install`. Every SessionStart also writes there (`run_first_time_setup` in `hooks/modules/core/plugin_setup.py`): it creates `plugin-registry.json` if missing (plugin data dir, else `.claude/`), merges Gaia's permissions and attribution into `.claude/settings.local.json`, and syncs the npm channel's hook entries into it; `.claude/hooks` is the only link it creates or repairs:

```
npm|pnpm install @jaguilar87/gaia
        |
(no postinstall — nothing runs automatically)
        |
First `gaia <cmd>` -> _ensure_db_bootstrapped() seeds ~/.gaia/gaia.db (lazy)
        |
gaia install --channel npm|opencode -> gaia migrate apply, merges permissions/hooks,
                                       recreates symlinks, writes registry + .claude/gaia-manifest.json
```

The one lifecycle script that remains is `preuninstall`:

```
npm uninstall @jaguilar87/gaia
        |
preuninstall script -> python3 bin/gaia uninstall --preuninstall
        |
Reverts .claude/gaia-manifest.json, removes Gaia-owned links, cleans caches /
logs / __pycache__, and surgically removes only Gaia's contributions from
settings.local.json and plugin-registry.json
```

npm 7 and later do not run a package's `preuninstall`, so the documented order is `gaia uninstall` first, then `npm uninstall`.

No Claude Code session is involved in either case. The subcommands run in a normal Python process and interact with the filesystem directly.

## What's here

```
bin/
├── gaia                       # Python entry point — dispatches to bin/cli/<name>; its --help epilog is the lane map the orchestrator guard enforces
├── pre-publish-validate.js    # Pre-publish gate for the release pipeline
├── python-detect.js           # Python runtime detection helper for npm lifecycles
├── validate-sandbox.sh        # End-to-end consumer-install verification harness
├── README.md
└── cli/                       # Subcommand modules (one file per subcommand)
    ├── __init__.py            # Discovery: imports every sibling that defines register()
    ├── _install_helpers.py    # Shared helpers for install/update (private, leading _)
    ├── _manifest.py           # The install manifest (.claude/gaia-manifest.json): record on install, revert on uninstall, per-channel ownership (private)
    ├── _leftovers.py          # What uninstall removes or lists as left beyond the manifest: package lines, gaia dev caches (private)
    ├── _dev_plugin.py         # gaia dev --channel plugin: the local gaia-dev marketplace and its undo (private)
    ├── _brief_scope.py        # Shared brief lookup by name across workspaces (private)
    ├── _converge.py           # Shared drift-free convergence primitives for dev/release (private, no register())
    ├── _pack_helpers.py       # shared `npm pack` primitive for dev/release (private, no register())
    ├── ac.py                  # gaia ac         — acceptance criteria for briefs (DB-canonical)
    ├── approvals.py           # gaia approvals  — list/pending/show/question/request-set/approve/revoke/reject/reject-all/history/replay/clean/stats T3 grants
    ├── brief.py               # gaia brief      — feature briefs / specs lifecycle, set-project, history (brief_events)
    ├── cleanup.py             # gaia cleanup    — preuninstall: caches, logs, __pycache__
    ├── context.py             # gaia context    — show / scan / get / query / wipe / prune-workspaces project context from gaia.db
    ├── contract.py            # gaia contract   — build/validate an agent_contract_handoff draft by-value: init/set/add/view (--field for one subtree)/validate/finalize + fill --json, plus reconcile for hook-written residue rows
    ├── defects.py             # gaia defects    — row-level triage of subagent anomalies and graded hook-log failures
    ├── dev.py                 # gaia dev        — fast local dev loop: pack/link + install + wire, one command, per --channel
    ├── doctor.py              # gaia doctor     — system health check (the model to learn)
    ├── evidence.py            # gaia evidence   — per-AC evidence (three-tier storage)
    ├── history.py             # gaia history    — recent agent sessions
    ├── install.py             # gaia install    — migrate the DB, wire settings and symlinks, record the manifest (run manually; no postinstall; declares no workspace)
    ├── memory.py              # gaia memory     — curated memory, append-only (add/append/reclassify/link/checkpoint; add --replace and delete signed) + reads (show [--links|--history], story) + episodic log (stats, search, episode-show)
    ├── memory_story.py        # backs `gaia memory story` (lineage narration); imported by memory.py, no register() of its own
    ├── metrics.py             # gaia metrics    — usage analytics (DB-canonical episodes/anomalies + audit-log tier/commands)
    ├── migrate.py             # gaia migrate    — plan | apply the schema migration chain: backup, one transaction, --consent-chain for data-reaching chains
    ├── milestone.py           # gaia milestone  — milestone management for briefs (DB-canonical)
    ├── notifications.py       # gaia notifications — reports, reminders and routines (add/list/show/ack/snooze/cancel)
    ├── now.py                 # gaia now        — local time, UTC offset and zone; opens no database
    ├── paths.py               # gaia paths      — report the resolved storage paths and create the ~/.gaia layout
    ├── plan.py                # gaia plan       — manage plans (one per brief, DB-canonical)
    ├── project.py             # gaia project    — move a project, its briefs and its profile into another declared workspace
    ├── query.py               # gaia query      — cross-surface read-only query (memory, episodes, harness_events)
    ├── release.py             # gaia release    — check (Layer 2 local gate) | publish (Layer 3 trigger sequence)
    ├── scan.py                # gaia scan       — project scanner over a declared workspace; writes scan results to gaia.db (DB-canonical)
    ├── session.py             # gaia session    — preview the SessionStart birth block in-process
    ├── status.py              # gaia status     — quick installation snapshot
    ├── task.py                # gaia task       — manage tasks within plans (DB-canonical)
    ├── uninstall.py           # gaia uninstall  — full, one channel (--channel), or preuninstall removal
    ├── update.py              # gaia update     — re-wire the channels the manifest records after a package upgrade
    ├── usage.py               # gaia usage      — ingest Claude Code transcripts into token_usage | show tokens per plan or session
    ├── workspace.py           # gaia workspace  — declare/list/current/info, curate, retire (with undo) and merge of workspace files
    └── worktree.py            # gaia worktree   — create/release/list/show a specialist's isolated git worktree
```

**`gaia contract` (by-value contract construction):** a turn born at dispatch already has an `agent_contract_handoffs` row before it runs anything, so `init` is no longer the required first step -- `set`/`add`/`fill --json` adopt that identity implicitly on their own first call against the same `--draft-id` (`_maybe_adopt_draft`: only converges an id whose row already exists, never mints one, and recovers the row's real mirrored evidence instead of a blank envelope when the row already carries any), each call validating the full resulting envelope on write (no false-pass) before persisting; `view` is pure read and never adopts or materializes a draft file -- it prints the current draft (or, with `--field <dotted-path>`, ONLY that subtree of the envelope, addressed with the same dotted-path scheme `set` uses -- an absent path is a clean non-zero-exit error), falling back to the row's own `raw_handoff_json` (`_freshest_envelope`) when no draft file exists, without ever writing one; `validate` reports the verdict without mutating (and, like `view`, never auto-adopts); `finalize` confirms the verdict and writes the SOLE, idempotent `agent_contract_handoffs` row. `reconcile` is a different door for a different kind of row and is deliberately NOT a relaxation of `finalize`: the SubagentStop backstop keys a residue row `hook-backstop.{agent_id}.{session_id}`, whose first dot-segment is the literal `hook-backstop` — a value `AGENT_ID_PATTERN_TEXT` forbids, so `_maybe_adopt_draft` cannot materialize a draft for it and `finalize`'s identity-coherence check could never be satisfied by any value. Such a row was unclosable by every route, and it accumulates in `gaia contract list --cut` as a false positive for a turn whose verdict already closed clean on another row. `reconcile --contract-id <id> [--harness-id <id>] [--superseded-by <contract_id>]` clears only the cut mark (via `gaia.store.writer.reconcile_cut_row`, which writes `cut_reason` and `raw_handoff_json` and nothing else), NEVER touches `agent_state`, and refuses any row lacking a hook-capture marker — so a genuinely cut turn stays visible where the signal is meant to find it. `gaia contract init --agent-id <id>` still mints a draft (its own contract id, never `CLAUDE_SESSION_ID`) and remains the explicit path for a turn with no identity injected at all. Every verb delegates to the single combined validator entry point (`gaia.contract.crosscheck.validate`, which layers `gaia.contract.validator`'s pure-stdlib shape check under a gaia.db cross-check) — this CLI never re-implements shape rules. See `skills/agent-contract-handoff/SKILL.md` for the field schema and `gaia/contract/validator.py` for the SSOT repair message.

**Fast local dev loop and release flow (`gaia dev`, `gaia release`):** `gaia dev --channel npm|plugin|opencode [--workspace <path>]` collapses the manual `npm pack` + `npm/pnpm add <tarball>` + `gaia install --channel npm` sequence into one command. `gaia release check [--functional] [--local-suite] [--gh PROGRAM]` runs the full Layer 2 pre-release gate (drift check, npm-sandbox install, plugin dry-run, tests, convergence) as one command; the tests gate reuses a green `CI verdict` for HEAD's tree and runs `npm test` only without one, or with `--local-suite`. `gaia release publish [version] [--dry-run] [--local-suite] [--gh PROGRAM] [--branch NAME]` runs the Layer 3 trigger sequence -- version bump, tests (same CI-verdict reuse), commit, tag, then the Tier-3 `git push --atomic origin HEAD:refs/heads/<branch> refs/tags/v<version>` and `gh release create` that hand off to `.github/workflows/publish.yml`. Every `gh` call goes through `--gh` (default `gh`), so the documented form with several accounts is `gaia release publish <version> --gh ghx`, a wrapper that sets the account for its own process; it never runs npm's own registry-publish step itself, that stays in CI, which publishes through npm trusted publishing (OIDC). See `skills/gaia-release/SKILL.md` for the full three-layer model these commands implement.

## Conventions

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

Modules whose name starts with `_` (e.g. `_install_helpers.py`) are private helpers, never registered as subcommands. Files that expose only utilities and no `register()` are also skipped by the dispatcher.

**Lanes:** a new subcommand that the orchestrator may run belongs in the READ or orchestrator-WRITE lane of the `_EPILOG` in `bin/gaia` AND in `ALLOWED_READ_PHRASES`/`ALLOWED_WRITE_PHRASES` of `hooks/modules/security/gaia_cli_only_guard.py`; `tests/cli/test_help_lanes_match_guard.py` fails when the two differ. Anything else is denied to the orchestrator and is named under the lane of the specialist that owns it.

**Lifecycle binding:** Only `gaia uninstall` (preuninstall) is wired to an npm event via `package.json` `scripts`. There is no `postinstall` — install bootstraps lazily on first `gaia` use (`_ensure_db_bootstrapped` in `bin/gaia`) and via explicit `gaia install`. The `--postinstall` flag on `gaia install` still exists for fail-soft non-interactive callers, but nothing in the npm/pnpm lifecycle invokes it automatically.

**Path resolution:** Subcommands resolve paths through symlinks to the source package using `Path.resolve()`. The pattern is visible in `cli/doctor.py`.

**Exit codes:** `0` on success, `1` on warnings, `2` on errors. The release pipeline's sandbox harness relies on these -- do not print a success line and exit non-zero, or vice versa.

**Cleanup footprint:** Full cleanup (the default, used by `gaia uninstall`) reverts `.claude/gaia-manifest.json` -- every file, link and settings key `gaia install` wrote returns to its prior state, and files Gaia did not create are kept -- and removes a `.claude/.plugin-initialized` marker an earlier version left there (the marker now lives in the Gaia data home, `~/.gaia` or `GAIA_DATA_DIR`, beside the logs and session state every channel shares, and uninstall does not touch it). Two files are handled surgically because they are shared with Claude Code: `settings.local.json` has only Gaia-injected keys removed (agent identity, two env vars, Gaia's permission entries; user content is preserved); `plugin-registry.json` has only Gaia's `installed[]` entry removed and is deleted only if it contained nothing else. The user DB at `~/.gaia/gaia.db` is NEVER deleted by `gaia uninstall` -- there is no purge path. By default, uninstall takes a gzip snapshot of the DB before it runs (pass `--no-backup` to skip that snapshot); `SessionStart` independently snapshots the DB at most once per 24 h (`hooks/modules/session/db_backup.py::maybe_backup_db`, throttled on the newest `sessionstart` snapshot). Retention keeps the newest 5 snapshots of each prefix (`uninstall`, `sessionstart`, ...), so one caller's snapshots never prune another's (`enforce_retention` in `gaia/paths/snapshot.py`). The canonical sources for what gets removed are `cli/_manifest.py`, `cli/_leftovers.py` and `cli/cleanup.py` (`_clean_settings_local_json`, `_remove_plugin_registry_entry`). `gaia uninstall --channel npm|plugin|opencode` is the per-channel cleanup (`_manifest.uninstall` -> `_uninstall_channel`): npm and the plugin share `.claude/`, so uninstalling either takes back every Claude Code entry in the manifest, while OpenCode owns `opencode.json` and `.opencode/` and is removed on its own with `--channel opencode`. The remaining channel stays recorded, the package copy stays while it runs from it, and hook state and `gaia dev` caches stay; once the named channel is the last one recorded, the full cleanup runs.

**`package.json` `bin` field:**

```json
{
  "bin": {
    "gaia": "bin/gaia"
  }
}
```

A single binary; subcommands are discovered, not registered.

## See also

- [`package.json`](../package.json) -- exposes `bin/gaia`; `scripts.preuninstall` wires the one lifecycle subcommand (no `postinstall`; see `_install_note`)
- [`INSTALL.md`](../INSTALL.md) -- installation workflow that calls `gaia install`, `gaia workspace declare` and `gaia scan`
- [`hooks/README.md`](../hooks/README.md) -- `gaia doctor` verifies the hook registrations are valid
- [`hooks/modules/security/gaia_cli_only_guard.py`](../hooks/modules/security/gaia_cli_only_guard.py) -- the allowlist the `gaia --help` lane map must equal
- [`bin/validate-sandbox.sh`](./validate-sandbox.sh) -- end-to-end harness that drives `gaia` subcommands against a fresh tarball install
- [`skills/gaia-release/SKILL.md`](../skills/gaia-release/SKILL.md) -- the three-layer install/release model `gaia dev` and `gaia release check|publish` implement
- [`skills/gaia-verify/SKILL.md`](../skills/gaia-verify/SKILL.md) -- how to validate what `gaia dev` / `gaia release check|publish` just installed or triggered
- [`skills/agent-contract-handoff/SKILL.md`](../skills/agent-contract-handoff/SKILL.md) -- the field schema `gaia contract` builds and validates by-value
- [`gaia/contract/validator.py`](../gaia/contract/validator.py) -- the portable form-layer validator (SSOT for `CANONICAL_REPAIR_MESSAGE`) every `gaia contract` write delegates to
