# gaia (Python package)

The library behind the `gaia` CLI and the hooks: Gaia's database, its approvals, agent contracts, workspaces and the paths of everything it keeps outside a repository.

This folder holds the logic that both entry layers share. A hook entry point and a CLI command are thin: they parse their input and call into here, so a rule such as "what an approval authorizes" or "which contract a turn owns" is decided once and reads the same from a Claude Code hook, an OpenCode plugin call and a terminal. Nothing in it writes into the user's repositories; its state lives in `~/.gaia` (see [`paths/`](paths/README.md)).

Think of it as the system's model layer: the CLI and the hooks are two views over it. Developers change it; the hooks and `bin/gaia` run it on every tool call and command; tests import it directly. It depends on the Python standard library only.

## How it is wired in

Consumed. `bin/gaia` and `bin/cli/*.py` import it after putting the repository root on `sys.path`; the hooks do the same from `hooks/`. The database schema is `store/schema.sql`, applied by `scripts/bootstrap_database.py`, and every later change to it is a migration under `scripts/migrations/` paired with `EXPECTED_SCHEMA_VERSION` in `bin/cli/doctor.py`.

What breaks if you get it wrong here: a module that imports from `hooks/` or a third-party package breaks every CLI command that loads it outside the hook process; a column added to `schema.sql` without its migration leaves every existing database behind the code that reads it.

## What's here

```
paths/                 data-root paths, directory layout and database backups
store/                 the SQLite store: schema.sql, reader and writer, memory placement, project context, workspace retire and curation
approvals/             signature requests, grants and their rendering for each host (surface.py)
contract/              agent contract drafts, validation, cross-checks and the harness record
state/                 the state machines for tasks, gates and permissions
briefs/                brief and plan storage
evidence/              evidence files deposited by a contract
retention/             reclaiming scratch, worktrees, branches and workspace leftovers
project.py             which declared workspace a folder belongs to; merge of ~/.gaia/workspaces files
worktree.py            creation, identity and managed roots of agent worktrees
refs.py                KIND:SCOPE/ID#FIELD references used by gaia open
signals.py             what other sessions changed in curated memory, read once per session
session_ledger.py      the ledger PreCompact writes and SessionStart renders as the resume point
session_snapshot.py    the orchestrator's hand note and the session's open contracts
conclusions.py         each closed specialist's conclusion delivered to its session once
context_drift.py       guarded auto-apply of project-context corrections a turn proposes
context_keys.py        per context section, the keys that name what a request is about
dispatch.py            host-independent execution of writing dispatches
secret_reads.py        reads whose output can carry secrets, run with that output redacted
redaction.py           redaction of secret-bearing values
comment_lint.py        the comment budget a push is measured against (gaia lint comments)
usage.py               token usage ingested from transcripts and reported per plan or session
install_root.py        the folder Gaia was installed in and the declared roots above it
install_provenance.py  local dev-install observations and drift diagnosis
source_parity.py       whether the installed package still matches the source tree
hooks_build.py         content identity of a wired or running hooks tree
dev_builds.py          per-machine count of local dev builds over a base version
hook_shell.py          whether the host can run the PreToolUse gate so a failure blocks
agent_identity.py      the one normalization of an agent name from outside Gaia
identity_shape.py      classifier for a project_identity payload
notifications_time.py  when a reminder or routine is due, in local wall-clock time
cli_parsing.py         argument-parsing policy shared by the CLI entry points
atomic_replace.py      os.replace that survives a transient hold on the target
subprocess_text.py     text subprocess runs whose codec does not depend on the locale
text_digest.py         content digest independent of line endings
```

## Conventions

A workspace exists only when declared (`gaia workspace declare NAME PATH`). `project.py::current` returns the declared workspace whose root is nearest above a folder, or `global` outside every declared root; it never derives a name from a git remote or a folder name. `project.py::resolve_workspace` is what a writer attributes a row to: `GAIA_DISPATCH_WORKSPACE`, then `GAIA_WORKSPACE`, then `current`, then `global`.

A path under the data root, the database file included, comes from `gaia.paths`, so `GAIA_DATA_DIR` and `GAIA_DB` relocate it. A change that adds a module here adds its line to the tree above.

## See also

- [`paths/README.md`](paths/README.md) -- the data root, `GAIA_DATA_DIR` and `GAIA_DB` precedence, and backup retention.
- [`../bin/README.md`](../bin/README.md) -- the CLI commands that call this package.
- [`../hooks/README.md`](../hooks/README.md) -- the hook layer that calls it on every tool use.
- [`../scripts/migrations/README.md`](../scripts/migrations/README.md) -- how the schema in `store/schema.sql` changes.
