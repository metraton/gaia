# tools/migration

One-shot scripts that moved the local data of the `me` workspace from files on
disk into `~/.gaia/gaia.db`, and reshaped that database before the versioned
migration ledger existed. They are history: each one already ran, the files
they read are gone (`.claude/project-context/episodic-memory/episodes.jsonl`,
`.claude/project-context/project-context.json`, `.claude/events/events.jsonl`),
and the tables they wrote have since been renamed and rebuilt. Nothing in
Gaia's runtime, install or CI runs them.

They stay because two tests import them as fixtures of past behaviour
(`tests/unit/test_migrate_08_rename_workspace.py`,
`tests/integration/test_state_machine_check.py`), and because the gaia.db
write guard names this folder as the sanctioned home of hand-run data moves
(`hooks/modules/security/gaia_db_write_guard.py`). A person runs them by hand,
from a terminal, never an agent.

## How it is wired in

Invoked, by hand, once. The converters `migrate_01` to `migrate_03` and
`migrate_05` read a source on disk and write a `.sql` file of
`INSERT OR IGNORE` statements under `/tmp`; they never open the database. The
`.sh` wrappers of `01` to `03` run their converter and then feed that file to
`sqlite3 ~/.gaia/gaia.db`. `migrate_06` to `migrate_08` open the database
themselves, back it up to a timestamped sibling first, and refuse or no-op on
a database already in the shape they produce.

What breaks if you get it wrong: running any of them against a current
database. Schema changes now ship only as `scripts/migrations/v{N-1}_to_v{N}.sql`,
applied in order and recorded in `schema_version` by `gaia migrate apply`; a
script here bypasses that ledger, its backup and its consent gate, and targets
table and column names that no longer exist (`migrate_08` renamed `projects`
to `workspaces` and `repos` to `projects`).

## What's here

```
migrate_01_episodes.py / .sh            # episodes.jsonl -> episodes (+ episodes_fts)
migrate_02_memory.py / .sh              # memory/*.md (MEMORY.md excluded) -> memory (+ memory_fts)
migrate_03_context_contracts.py / .sh   # project-context.json -> context_contracts
migrate_05_briefs.py                    # brief directories (brief.md, plan.md, ...) -> briefs; source via --src
migrate_06_state_machines.py            # cleans legacy statuses, rebuilds four tables with CHECK constraints
migrate_07_plans_content.py             # adds plans.content and plans.updated_at
migrate_08_rename_workspace.py          # renames projects/repos to workspaces/projects
validate.sh                             # read-only row-count checks after 01-03
```

Domain 04 (harness events) was absorbed and its scripts deleted; the
`event_writer` hook writes `harness_events` directly.

## Conventions

Nothing new is added here. A schema change is a file in `scripts/migrations/`
(see its README); a data change goes through the `gaia` CLI and the typed
store API in `gaia/store/writer.py`. Each source path has no default: a
wrapper takes it as its first argument and stops with a usage line without
it. The shell scripts run under `set -euo pipefail`, and the project is fixed
to `me` throughout. The scripts' own comments are still in Spanish.

## See also

- [`scripts/migrations/README.md`](../../scripts/migrations/README.md) -- the versioned ledger that replaced these scripts.
- [`hooks/modules/security/gaia_db_write_guard.py`](../../hooks/modules/security/gaia_db_write_guard.py) -- why a shell write to gaia.db is refused, and the paths it sanctions.
