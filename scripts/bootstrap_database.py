#!/usr/bin/env python3
"""bootstrap_database.py -- the migration engine behind ``gaia migrate``.

Brings a Gaia database from whatever version its ledger records to the version
this code expects, and is what ``gaia install``, ``gaia update`` and the lazy
first-run bootstrap reach through ``gaia migrate``. Cross-platform: it uses
Python's built-in ``sqlite3`` module, never the ``sqlite3`` CLI or ``bash``.

Two actions:
  (default) apply -- run the pending chain.
  --plan          -- print the chain, what each step reaches, and what apply
                     would require; writes nothing, creates nothing.

How a run treats the database:
  * Fresh (no file, or no tables): ``schema.sql`` builds it, the ledger is
    stamped at the floor, and every forward migration replays on top. Nothing
    pre-dates the run, so there is nothing to back up and nothing to consent.
  * Sealed (ledger rows present): only the migrations from the ledger to the
    expected version run. ``schema.sql`` is never applied to it.
  * Unsealed (tables but an empty ledger -- what older writer builds left
    behind): treated as a fresh build over existing data, so it is backed up
    and gated like a sealed one.

Every write of the chain -- schema, each migration, and each ledger seal --
runs inside ONE transaction, so an interruption at any point, including
between a migration and its seal, leaves the objects and the version exactly
as they were. Before that transaction opens on an existing database, a copy is
written with SQLite's backup API to ``<database dir>/backups/``. A chain that
only adds structure then applies on its own; a chain in which any migration
reaches rows that already exist is refused until ``--consent-chain vA..vB``
names that exact chain (D68: one consent for the whole chain).

Configuration:
  - GAIA_DB    -- path of the DB. Default ~/.gaia/gaia.db.
  - SCHEMA_FILE-- override of schema.sql. Default <repo>/gaia/store/schema.sql.
  - WORKSPACE  -- workspace whose identity is registered. Default = repo root.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import migration_guard  # noqa: E402

_SCRIPT_DIR = Path(__file__).resolve().parent

_DEFAULT_DB = Path.home() / ".gaia" / "gaia.db"
GAIA_DB = Path(os.environ.get("GAIA_DB") or _DEFAULT_DB).expanduser()

SCHEMA_FILE = Path(
    os.environ.get("SCHEMA_FILE")
    or (_SCRIPT_DIR.parent / "gaia" / "store" / "schema.sql")
).expanduser()

WORKSPACE = Path(os.environ.get("WORKSPACE") or _SCRIPT_DIR.parent).expanduser()

MIG_DIR = _SCRIPT_DIR / "migrations"
DOCTOR_PY = _SCRIPT_DIR.parent / "bin" / "cli" / "doctor.py"

# The lowest ledger version an existing database may carry; schema.sql plus the
# replayed forward chain is what a fresh database is stamped from.
SCHEMA_FLOOR = 18

FRESH, SEALED, UNSEALED = "fresh", "sealed", "unsealed"


def _log(msg: str) -> None:
    print(f"[bootstrap] {msg}")


def _err(msg: str) -> None:
    print(f"[bootstrap] {msg}", file=sys.stderr)


def _gaia_sha256(value: str | None) -> str:
    """Scalar SHA-256 the ai_approval_events_hash trigger calls; registered so
    this connection matches gaia.store.writer._connect."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    """Autocommit connection, foreign_keys OFF (table-rebuild migrations need
    it), with gaia_sha256 registered."""
    GAIA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(GAIA_DB))
    con.isolation_level = None
    con.create_function("gaia_sha256", 1, _gaia_sha256, deterministic=True)
    return con


def _scalar(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _read_expected_schema_version() -> int:
    """EXPECTED_SCHEMA_VERSION = N from doctor.py, the single source of truth."""
    if not DOCTOR_PY.is_file():
        _err(
            f"ERROR: doctor.py no encontrado en {DOCTOR_PY} "
            "(no puedo leer EXPECTED_SCHEMA_VERSION)"
        )
        sys.exit(1)
    m = re.search(
        r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)",
        DOCTOR_PY.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not m:
        _err(f"ERROR: no pude parsear EXPECTED_SCHEMA_VERSION desde {DOCTOR_PY}")
        sys.exit(1)
    return int(m.group(1))


def _normalize_remote(raw_remote: str) -> str:
    """Remote -> identity: lowercase, strip scheme, ssh git@host:owner/repo ->
    host/owner/repo, strip .git and trailing slash."""
    s = raw_remote.lower()
    for prefix in ("https://", "http://", "ssh://", "git+ssh://", "git+https://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.startswith("git@"):
        s = s[len("git@"):]
        s = s.replace(":", "/", 1)
    if s.endswith(".git"):
        s = s[: -len(".git")]
    if s.endswith("/"):
        s = s[:-1]
    return s


def _resolve_workspace_identity() -> str:
    """Identity via git remote get-url origin, falling back to the lowercase
    basename and then 'global'."""
    raw_remote = ""
    try:
        result = subprocess.run(
            ["git", "-C", str(WORKSPACE), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            raw_remote = result.stdout.strip()
    except OSError:
        raw_remote = ""

    identity = _normalize_remote(raw_remote) if raw_remote else ""
    if not identity:
        try:
            identity = WORKSPACE.resolve().name.lower()
        except OSError:
            identity = ""
    return identity or "global"


# === Reading the database's state ===

def _ledger_version(con: sqlite3.Connection) -> int:
    if not _scalar(
        con,
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_version'",
    ):
        return 0
    return _scalar(con, "SELECT COALESCE(MAX(version), 0) FROM schema_version")


def _database_state(census: dict[str, int], ledger: int) -> str:
    if ledger:
        return SEALED
    return UNSEALED if census else FRESH


def _chain(start: int, expected: int) -> list[tuple[int, Path]]:
    """Migration files (target version, path) from ``start`` to ``expected``."""
    return [
        (n, MIG_DIR / f"v{n - 1}_to_v{n}.sql") for n in range(start + 1, expected + 1)
    ]


# === Applying the chain ===

_ADD_COLUMN_RE = re.compile(
    r"^alter\s+table\s+[\"'`\[]?([a-z0-9_]+)[\"'`\]]?\s+add\s+column\s+[\"'`\[]?([a-z0-9_]+)",
    re.IGNORECASE,
)


# SQLite refuses to change the journal mode inside a transaction, so schema.sql's
# PRAGMA journal_mode is honoured once before the chain opens instead.
_JOURNAL_MODE_RE = re.compile(r"^pragma\s+journal_mode\b", re.IGNORECASE)


def _column_present(con: sqlite3.Connection, statement: str) -> bool:
    """Whether an ADD COLUMN statement's column already exists.

    SQLite has no ADD COLUMN IF NOT EXISTS, and the forward chain replays over
    a fresh schema.sql build that already carries every column, so an ADD
    COLUMN whose column is present is skipped rather than aborting the chain.
    """
    m = _ADD_COLUMN_RE.match(migration_guard.normalize(statement))
    if not m:
        return False
    table, column = m.group(1), m.group(2)
    return bool(
        _scalar(
            con,
            f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name=?",
            (column,),
        )
    )


def _run_script(con: sqlite3.Connection, sql: str) -> None:
    """Execute a script statement by statement inside the open transaction.

    ``executescript`` is avoided on purpose: it COMMITs any open transaction
    before running, which would split the chain into separately durable parts.
    """
    for statement in migration_guard.split_statements(sql):
        if _JOURNAL_MODE_RE.match(migration_guard.normalize(statement)):
            continue
        if _column_present(con, statement):
            continue
        con.execute(statement)


def _ledger_minimum(con: sqlite3.Connection) -> int | None:
    """min_code_version of the newest seal; None without the column or a value."""
    try:
        row = con.execute(
            "SELECT min_code_version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _stamp(
    con: sqlite3.Connection,
    version: int,
    description: str,
    now_utc: str,
    breaking: bool = False,
) -> None:
    """Seal ``version``; once the ledger has min_code_version, record it too.

    The minimum is carried from the previous seal and raised to ``version``
    only by a breaking migration. A ledger that never recorded one (sealed
    before v59) falls back to the last breaking migration on disk.
    """
    has_minimum = _scalar(
        con,
        "SELECT COUNT(*) FROM pragma_table_info('schema_version') "
        "WHERE name='min_code_version'",
    )
    if not has_minimum:
        con.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at, description) "
            "VALUES (?, ?, ?)",
            (version, now_utc, description),
        )
        return
    if breaking:
        minimum = version
    else:
        minimum = _ledger_minimum(con)
        if minimum is None:
            minimum = migration_guard.last_breaking_version(MIG_DIR, version)
    con.execute(
        "INSERT OR IGNORE INTO schema_version "
        "(version, applied_at, description, min_code_version) VALUES (?, ?, ?, ?)",
        (version, now_utc, description, minimum),
    )


def _backup(con: sqlite3.Connection, ledger: int, now: datetime) -> Path:
    """Copy the whole database, beside it, before anything writes to it."""
    backup_dir = GAIA_DB.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / (
        f"{GAIA_DB.stem}-v{ledger}-pre-migrate-{now.strftime('%Y%m%dT%H%M%S%fZ')}.db"
    )
    dest = sqlite3.connect(str(target))
    try:
        con.backup(dest)
    finally:
        dest.close()
    return target


def _apply_chain(
    con: sqlite3.Connection,
    state: str,
    start: int,
    chain: list[tuple[int, Path]],
    now_utc: str,
) -> None:
    """Schema (fresh/unsealed only), every migration and every seal, in one
    transaction. Any exception rolls the whole chain back."""
    if state != SEALED:
        con.execute("PRAGMA journal_mode = WAL")
    con.execute("BEGIN IMMEDIATE")
    try:
        if state != SEALED:
            _run_script(con, SCHEMA_FILE.read_text(encoding="utf-8"))
            _stamp(con, start, f"baseline floor: schema.sql at v{start}", now_utc)
        for n, mig_file in chain:
            _log(f"migration v{n - 1}->v{n}: applying {mig_file}")
            sql = mig_file.read_text(encoding="utf-8")
            _run_script(con, sql)
            _stamp(
                con,
                n,
                f"applied migration {mig_file.name}",
                now_utc,
                breaking=migration_guard.is_breaking(sql),
            )
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


# === Plan / apply ===

def _open_for_plan() -> sqlite3.Connection | None:
    """Read-only connection, or None when the database does not exist yet."""
    if not GAIA_DB.is_file():
        return None
    return sqlite3.connect(f"file:{GAIA_DB}?mode=ro", uri=True)


def _assess_chain(
    chain: list[tuple[int, Path]], census: dict[str, int]
) -> list[migration_guard.Verdict]:
    return [
        migration_guard.assess(
            path.stem, path.read_text(encoding="utf-8"), census
        )
        for _, path in chain
    ]


def _plan(expected: int) -> int:
    con = _open_for_plan()
    census = migration_guard.take_census(con) if con else {}
    ledger = _ledger_version(con) if con else 0
    minimum = _ledger_minimum(con) if con else None
    if con:
        con.close()
    state = _database_state(census, ledger)
    print(f"database: {GAIA_DB}")
    if state == FRESH:
        print(
            f"fresh database: schema.sql builds it and the ledger is sealed at "
            f"v{expected}. Nothing to back up, nothing to consent."
        )
        return 0
    start = ledger if state == SEALED else SCHEMA_FLOOR
    if ledger > expected:
        if minimum is not None and minimum <= expected:
            print(
                f"ledger v{ledger} is newer than this code (v{expected}) and accepts "
                f"code from v{minimum}: nothing to apply, this code keeps writing."
            )
            return 0
        print(
            f"ledger v{ledger} is NEWER than this code (v{expected}) and requires "
            f"code at v{minimum if minimum is not None else ledger}; apply refuses."
        )
        return 1
    if start == expected and state == SEALED:
        print(f"ledger at v{ledger}: up to date, nothing to apply.")
        return 0
    chain = _chain(start, expected)
    missing = [p.name for _, p in chain if not p.is_file()]
    if missing:
        print(f"missing migration files: {', '.join(missing)}; apply refuses.")
        return 1
    label = migration_guard.chain_label(start, expected)
    print(f"chain {label} ({len(chain)} migrations, {state} database):")
    if state == UNSEALED:
        print("  schema.sql      rebuilds the unsealed database before the chain")
    verdicts = _assess_chain(chain, census)
    for verdict in verdicts:
        if not verdict.reaches:
            print(f"  {verdict.migration:<15} structure only")
            continue
        for reach in verdict.reaches:
            print(
                f"  {verdict.migration:<15} reaches data: {reach.verb} on "
                f"`{reach.table}` ({reach.rows} rows)"
            )
    print(f"backup: written to {GAIA_DB.parent / 'backups'} before anything changes")
    if any(v.reaches for v in verdicts):
        print(
            "consent: required once for the whole chain -> "
            f"{migration_guard.consent_command(label)}"
        )
    else:
        print("consent: none -- the chain only adds structure and applies on its own")
    return 0


def _apply(expected: int, consent_chain: str | None) -> int:
    if not SCHEMA_FILE.is_file():
        _err(f"ERROR: schema.sql no encontrado en {SCHEMA_FILE}")
        return 1

    _log(f"Initializing Gaia DB at {GAIA_DB}")
    _log(f"Using schema:  {SCHEMA_FILE}")
    _log(f"Using workspace: {WORKSPACE}")

    now = datetime.now(timezone.utc)
    now_utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    con = _connect()
    try:
        # Taken before anything in this run can add a row, so it names exactly
        # the data a migration could destroy rather than data this run produced.
        census = migration_guard.take_census(con)
        ledger = _ledger_version(con)
        state = _database_state(census, ledger)

        if state == SEALED and ledger < SCHEMA_FLOOR:
            _err(
                f"ERROR: DB at schema_version={ledger} is below the supported "
                f"floor v{SCHEMA_FLOOR}."
            )
            _err(
                f"In-place upgrade from pre-v{SCHEMA_FLOOR} databases is no longer supported."
            )
            _err(
                f"Recreate the DB: back up any data you need, delete {GAIA_DB}, "
                "then re-run `gaia install`."
            )
            return 1

        # A database newer than this code is never moved down. Within its
        # compatibility window this code may still use it, so there is nothing
        # to do; past a breaking migration the install must stop and name the fix.
        if ledger > expected:
            minimum = _ledger_minimum(con)
            if minimum is not None and minimum <= expected:
                _log(
                    f"schema_version: database v{ledger} is newer than this code "
                    f"(v{expected}) and accepts code from v{minimum}; nothing to apply."
                )
                return 0
            _err(
                f"ERROR: live DB schema_version={ledger} is NEWER than the "
                f"schema this code expects (v{expected}), and a breaking "
                f"migration requires code at v{minimum if minimum is not None else ledger} or newer."
            )
            _err("Refusing -- the DB is left untouched (no clobber).")
            _err(
                f"Install a Gaia whose EXPECTED_SCHEMA_VERSION >= "
                f"{minimum if minimum is not None else ledger}, then re-run. "
                "To validate without changing anything, run `gaia doctor`."
            )
            return 1

        start = ledger if state == SEALED else SCHEMA_FLOOR
        _log(f"schema_version: current={ledger}, expected={expected} ({state} database)")
        chain = _chain(start, expected)
        for n, mig_file in chain:
            if not mig_file.is_file():
                _err(f"ERROR: missing migration file {mig_file}")
                _err(f"Cannot advance to v{n}. The ledger remains at v{ledger}.")
                _err(
                    f"When bumping EXPECTED_SCHEMA_VERSION to v{n}, add "
                    f"scripts/migrations/v{n - 1}_to_v{n}.sql in the same commit."
                )
                return 1

        if state == SEALED and not chain:
            _log("schema_version up-to-date (no migrations pending)")
        else:
            label = migration_guard.chain_label(start, expected)
            reaching = [v for v in _assess_chain(chain, census) if v.reaches]
            if reaching and consent_chain != label:
                if consent_chain:
                    _err(
                        f"consent was given for {consent_chain}, but the pending "
                        f"chain is {label}."
                    )
                _err(migration_guard.format_chain_block(reaching, label, GAIA_DB, ledger))
                return 1
            if state != FRESH:
                _log(f"backup written: {_backup(con, ledger, now)}")
            if reaching:
                _log(f"chain {label}: reaches existing data; applying under consent")
            try:
                _apply_chain(con, state, start, chain, now_utc)
            except sqlite3.Error as exc:
                _err(f"ERROR: migration chain {label} failed and was rolled back. ({exc})")
                _err(f"schema_version ledger remains at v{ledger}; no object changed.")
                return 1
            _log(f"chain {label}: applied and sealed at v{expected}")

        return _seed_and_check(con)
    finally:
        con.close()


def _seed_and_check(con: sqlite3.Connection) -> int:
    """Idempotent seeds and invariants that follow a current schema."""
    _perms = [
        ("clusters", "cloud-troubleshooter"),
        ("apps", "developer"),
        ("features", "developer"),
        ("libraries", "developer"),
        ("services", "developer"),
        ("gaia_installations", "gaia-system"),
        ("integrations", "gaia-system"),
        ("clusters_defined", "gitops-operator"),
        ("releases", "gitops-operator"),
        ("workloads", "gitops-operator"),
        ("clusters", "platform-architect"),
        ("tf_live", "platform-architect"),
        ("tf_modules", "platform-architect"),
    ]
    con.executemany(
        "INSERT OR IGNORE INTO agent_permissions "
        "(table_name, agent_name, allow_write) VALUES (?, ?, 1)",
        _perms,
    )
    _log("agent_permissions seeded (13 rows, 5 agents, brief B3 M2 mapping)")
    con.execute("DELETE FROM agent_permissions WHERE agent_name = 'gaia-operator'")

    workspace_identity = _resolve_workspace_identity()
    con.execute(
        "INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
        (workspace_identity, workspace_identity),
    )
    _log(f"Workspace registered (identity={workspace_identity})")

    con.executescript(
        """
        INSERT INTO projects_fts(rowid, name, role, primary_language)
        SELECT rowid, name, role, primary_language
        FROM projects
        WHERE rowid NOT IN (SELECT rowid FROM projects_fts);

        INSERT INTO apps_fts(rowid, name, description, topic_key)
        SELECT rowid, name, description, topic_key
        FROM apps
        WHERE rowid NOT IN (SELECT rowid FROM apps_fts);

        INSERT INTO services_fts(rowid, name, description, topic_key)
        SELECT rowid, name, description, topic_key
        FROM services
        WHERE rowid NOT IN (SELECT rowid FROM services_fts);

        INSERT INTO briefs_fts(rowid, objective, context, approach)
        SELECT id, objective, context, approach
        FROM briefs
        WHERE id NOT IN (SELECT rowid FROM briefs_fts);
        """
    )
    fts_ok = True
    for base, mirror in (
        ("projects", "projects_fts"),
        ("apps", "apps_fts"),
        ("services", "services_fts"),
        ("briefs", "briefs_fts"),
    ):
        base_count = _scalar(con, f"SELECT COUNT(*) FROM {base}")
        mirror_count = _scalar(con, f"SELECT COUNT(*) FROM {mirror}")
        if base_count != mirror_count:
            _err(f"  WARN: {base} ({base_count}) != {mirror} ({mirror_count})")
            fts_ok = False
    _log(
        "FTS5 backfilled (4/4 consistency check passed)"
        if fts_ok
        else "FTS5 backfilled (consistency check WARNING -- ver lineas anteriores)"
    )

    checks = (
        ("agent_permissions rows >= 13",
         _scalar(con, "SELECT COUNT(*) FROM agent_permissions WHERE allow_write IS NOT NULL"),
         lambda v: v >= 13),
        ("distinct agents >= 5",
         _scalar(con, "SELECT COUNT(DISTINCT agent_name) FROM agent_permissions"),
         lambda v: v >= 5),
        ("workspaces rows >= 1",
         _scalar(con, "SELECT COUNT(*) FROM workspaces"),
         lambda v: v >= 1),
        ("FTS5 triggers == 12",
         _scalar(
             con,
             "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
             "AND (name LIKE '%_fts_%' OR name LIKE 'briefs_a%')",
         ),
         lambda v: v == 12),
        (f"schema_version >= floor v{SCHEMA_FLOOR}",
         _ledger_version(con),
         lambda v: v >= SCHEMA_FLOOR),
    )
    all_ok = True
    for label, value, ok in checks:
        passed = ok(value)
        all_ok = all_ok and passed
        _log(f"check: {label} (got {value}) -- {'PASS' if passed else 'FAIL'}")

    if all_ok:
        _log(f"Done. DB at {GAIA_DB} ready for `gaia` CLI operations.")
        return 0
    _err("Done WITH FAILURES. Revisa los checks marcados FAIL arriba.")
    return 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bootstrap_database.py",
        description="Migration engine behind `gaia migrate` (plan / apply).",
    )
    parser.add_argument(
        "--plan", action="store_true", help="print the pending chain; write nothing"
    )
    parser.add_argument(
        migration_guard.CONSENT_FLAG,
        dest="consent_chain",
        metavar="vA..vB",
        help="consent, once, to a chain that reaches existing data",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the engine. ``argv`` defaults to no arguments (apply, no consent),
    so an in-process caller never inherits its host's command line."""
    args = _parse_args(argv or [])
    expected = _read_expected_schema_version()
    if args.plan:
        return _plan(expected)
    return _apply(expected, args.consent_chain)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
