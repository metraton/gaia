# gaia.paths

Path resolution, directory layout and database backups for Gaia's data root.

Every file Gaia keeps outside a repository lives under one data root, `~/.gaia` by default. This package is the only place that root and the paths under it are computed, so a test, a hook or a CLI command that wants the database, scratch or a log directory asks here instead of joining strings. It also owns how a copy of the database is written and how many copies are kept, because a backup that cannot be trusted is worse than none.

Its readers are the `gaia` CLI (`bin/gaia`, `bin/cli/`), the hooks, the store in `gaia/store/`, and tests, which relocate everything by setting `GAIA_DATA_DIR` or `GAIA_DB`.

## How it is wired in

Consumed: everything imports from `gaia.paths`, never from its modules, so `__init__.py` is the public surface.

- `resolver.py` reads `GAIA_DATA_DIR` on every call, with no cache, so `monkeypatch.setenv` takes effect at once. `db_path()` also reads `GAIA_DB`, which wins for the database file alone; when both point at different files it warns on stderr naming the winner. `tests/paths/test_db_path_precedence.py` holds that order.
- `layout.py::ensure_layout` creates the root, `workspaces/`, `logs/`, `events/`, `cache/` and `scratch/` with mode 0700. `gaia paths` calls it before printing.
- Backups. `backup.py::write_verified_gzip` copies one committed state with SQLite's Online Backup API, proves it with `integrity_check`, compresses it and publishes it with an atomic rename; a copy that fails is deleted and never replaces or prunes an existing backup. It writes the session-start backup (`hooks/modules/session/db_backup.py`, at most once a day) and the backup before `gaia workspace retire` (`gaia/store/workspace_retire.py`). Pre-migration backups are written the same way by `scripts/bootstrap_database.py::_backup`, in the same file format. `snapshot.py::create_snapshot`, used only by `gaia uninstall`, copies the database file's bytes into a gzip without that verification.

What breaks if you get it wrong here: a path computed once and cached ignores a test's override and writes into the user's real `~/.gaia`; a backup published before it verifies can replace the last good one.

## What's here

```
__init__.py   the public API: every resolver, ensure_layout, workspace_id (alias of gaia.project.current) and the snapshot helpers
resolver.py   pure path functions; GAIA_DATA_DIR and GAIA_DB precedence
layout.py     ensure_layout(): the directories created on first use, mode 0700
backup.py     verified gzip writer, GFS and newest-N pruning, backup status and its health verdict
snapshot.py   create_snapshot / enforce_retention / latest_snapshot_age_seconds, used by gaia uninstall
```

## Conventions

Which backups are kept is decided by the timestamp in each file name. Session-start backups keep the newest of each of 7 days, 4 ISO weeks and 3 months (`backup.py::prune_gfs`; `uninstall` snapshots are exempt). Pre-migration and pre-retire backups keep the newest 3 of their kind (`backup.py::prune_newest`, and the bootstrap's own pruning). `enforce_retention(snapshot_dir, prefix=..., retain=...)` touches only snapshots of the prefix it is given. The last session-start outcome is written to `state/db-backup-status.json`, which `backup.py::backup_health` turns into the verdict `gaia doctor` shows.

A new directory under the root gets its resolver in `resolver.py`, its export in `__init__.py`, and an entry in `ensure_layout` only if something must find it existing before first use.

## See also

- [`../README.md`](../README.md) -- the rest of the `gaia` package.
- [`../../scripts/bootstrap_database.py`](../../scripts/bootstrap_database.py) -- creates and migrates the database whose path this package resolves.

Path-resolver patterns are adapted from [engram](https://github.com/koaning/engram) (MIT); no engram code runs.
