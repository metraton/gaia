#!/usr/bin/env python3
"""bootstrap_database.py -- Inicializador idempotente de la DB Gaia (Python port).

Port cross-platform de ``scripts/bootstrap_database.sh``. Produce EXACTAMENTE el
mismo esquema y los mismos seeds que el .sh, pero usando el modulo ``sqlite3``
built-in de Python en lugar del binario ``sqlite3`` CLI. Esto permite que
``gaia install``, el lazy bootstrap (bin/gaia) y ``gaia update`` funcionen en
Windows -- donde ``sqlite3`` no suele estar en PATH y ``bash`` puede no existir.

Fuente declarativa unica: ``gaia/store/schema.sql`` (ejecutado via
``con.executescript``), igual que el .sh. La logica de seeds/migraciones se
porta seccion-por-seccion desde el .sh para garantizar PARIDAD.

Principios (heredados del .sh):
  - Idempotente: ejecutarlo dos veces no cambia el estado.
  - foreign_keys queda en OFF (default de sqlite3, igual que el CLI): las
    migraciones con rebuild de tabla (DROP/RENAME) lo requieren.
  - Autocommit (isolation_level=None): cada statement commitea al instante,
    igual que cada invocacion separada de ``sqlite3 "$GAIA_DB" "..."`` en el .sh.

Configuracion (identica al .sh):
  - GAIA_DB    -- path de la DB. Default ~/.gaia/gaia.db.
  - SCHEMA_FILE-- override del schema.sql. Default <repo>/gaia/store/schema.sql.
  - WORKSPACE  -- workspace cuya identidad se registra. Default = raiz del repo.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# === Section 1: Variables y validacion de entorno ===

_SCRIPT_DIR = Path(__file__).resolve().parent

# Path de la DB. Configurable via env GAIA_DB; default ~/.gaia/gaia.db.
# Misma resolucion que el .sh (linea 21): GAIA_DB o ~/.gaia/gaia.db.
_DEFAULT_DB = Path.home() / ".gaia" / "gaia.db"
GAIA_DB = Path(os.environ.get("GAIA_DB") or _DEFAULT_DB).expanduser()

# Path al schema.sql. El script vive en gaia/scripts/, el schema en
# gaia/gaia/store/schema.sql. Resolvemos relativo al script (no al cwd).
SCHEMA_FILE = Path(
    os.environ.get("SCHEMA_FILE")
    or (_SCRIPT_DIR.parent / "gaia" / "store" / "schema.sql")
).expanduser()

# Workspace cuya identidad se registra. Default: raiz del repo (un nivel arriba
# de scripts/). Configurable via env.
WORKSPACE = Path(os.environ.get("WORKSPACE") or _SCRIPT_DIR.parent).expanduser()

MIG_DIR = _SCRIPT_DIR / "migrations"
DOCTOR_PY = _SCRIPT_DIR.parent / "bin" / "cli" / "doctor.py"

# floor de schema: schema.sql produce exactamente esta forma (ver Section 3b).
SCHEMA_FLOOR = 18


def _log(msg: str) -> None:
    print(f"[bootstrap] {msg}")


def _err(msg: str) -> None:
    print(f"[bootstrap] {msg}", file=sys.stderr)


def _gaia_sha256(value: str | None) -> str:
    """Scalar SHA-256 usado por el trigger ai_approval_events_hash.

    schema.sql crea ese trigger; el bootstrap no inserta en approval_events, asi
    que la funcion no llega a invocarse aqui -- pero la registramos igual que
    gaia.store.writer._connect para que cualquier connection sea consistente."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    """Abre la connection en autocommit, foreign_keys OFF (default), y registra
    gaia_sha256 -- paridad con el comportamiento del sqlite3 CLI."""
    GAIA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(GAIA_DB))
    con.isolation_level = None  # autocommit: cada execute commitea (como el CLI)
    con.create_function("gaia_sha256", 1, _gaia_sha256, deterministic=True)
    return con


def _scalar(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


_ADD_COLUMN_RE = re.compile(
    r"alter\s+table\s+([a-z0-9_]+)\s+add\s+column\s+([a-z0-9_]+)",
    re.IGNORECASE,
)


# === Section 1.5: Pre-schema ADD COLUMN reconciliation (existing DBs) ===
#
# schema.sql se aplica INCONDICIONALMENTE y completo, y carga indices sobre
# columnas introducidas por migraciones forward (p.ej. contract_id + su UNIQUE
# index). En una DB EXISTENTE cuya tabla precede a la columna, el CREATE INDEX
# abortaria con "no such column". Esta seccion agrega esas columnas ANTES de
# schema.sql, de forma generica e idempotente: para cada ALTER TABLE ADD COLUMN
# declarado en las migraciones forward, si la tabla ya existe y la columna
# falta, la agrega ahora. En una DB fresca no hay tablas todavia -> no-op.
def _reconcile_pre_schema_add_columns(con: sqlite3.Connection) -> None:
    for mig_file in sorted(MIG_DIR.glob("v*_to_v*.sql")):
        for line in mig_file.read_text(encoding="utf-8").splitlines():
            m = _ADD_COLUMN_RE.search(line)
            if not m:
                continue
            table, col = m.group(1), m.group(2)
            tbl_exists = _scalar(
                con,
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if tbl_exists == 0:
                continue
            col_exists = _scalar(
                con,
                f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name=?",
                (col,),
            )
            if col_exists == 0:
                _log(
                    f"pre-schema reconcile: adding {table}.{col} (existing DB predates it)"
                )
                con.execute(line)


# === Section 3c helper: idempotent ADD COLUMN guard (runner-level) ===
#
# schema.sql ya carga el DDL objetivo de cada migracion, asi que las migraciones
# se replayean contra una DB que ya tiene sus objetos. CREATE ... IF NOT EXISTS
# es idempotente; pero SQLite no tiene ADD COLUMN IF NOT EXISTS, asi que un
# ALTER TABLE t ADD COLUMN c aborta con "duplicate column name" si la columna ya
# existe. Este filtro neutraliza (comenta) esas lineas antes de correr la
# migracion; todo lo demas pasa verbatim.
def _filter_add_column_idempotent(con: sqlite3.Connection, mig_file: Path) -> str:
    out_lines: list[str] = []
    for line in mig_file.read_text(encoding="utf-8").splitlines():
        m = _ADD_COLUMN_RE.search(line)
        if m:
            table, col = m.group(1), m.group(2)
            exists = _scalar(
                con,
                f"SELECT COUNT(*) FROM pragma_table_info('{table}') WHERE name=?",
                (col,),
            )
            if exists > 0:
                out_lines.append(
                    f"-- [bootstrap] skipped (column {table}.{col} already present): {line}"
                )
                continue
        out_lines.append(line)
    return "\n".join(out_lines) + "\n"


def _read_expected_schema_version() -> int:
    """Lee EXPECTED_SCHEMA_VERSION = N de doctor.py (una sola fuente de verdad),
    igual que el .sh (grep de ^EXPECTED_SCHEMA_VERSION\\s*=\\s*[0-9]+)."""
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
    """Normalizacion de remote a identity, port exacto del bash (Section 4):
    lowercase + strip de prefijos + forma ssh git@host:owner/repo -> host/owner/repo
    + strip .git + strip trailing slash."""
    s = raw_remote.lower()
    for prefix in ("https://", "http://", "ssh://", "git+ssh://", "git+https://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.startswith("git@"):
        s = s[len("git@"):]
        s = s.replace(":", "/", 1)  # primer ':' -> '/'
    if s.endswith(".git"):
        s = s[: -len(".git")]
    if s.endswith("/"):
        s = s[:-1]
    return s


def _resolve_workspace_identity() -> str:
    """Section 4: identity via git remote get-url origin, con fallbacks a
    basename(lowercase) y luego 'global'. git es cross-platform y guarded."""
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

    identity = ""
    if raw_remote:
        identity = _normalize_remote(raw_remote)
    if not identity:
        try:
            identity = WORKSPACE.resolve().name.lower()
        except OSError:
            identity = ""
    if not identity:
        identity = "global"
    return identity


def main() -> int:
    # --- Section 1: validaciones ---
    # NOTA: a diferencia del .sh, NO validamos la presencia de un binario
    # sqlite3 en PATH -- ese es justamente el objetivo de este port. Usamos el
    # modulo sqlite3 built-in de Python.
    if not SCHEMA_FILE.is_file():
        _err(f"ERROR: schema.sql no encontrado en {SCHEMA_FILE}")
        return 1

    _log(f"Initializing Gaia DB at {GAIA_DB}")
    _log(f"Using schema:  {SCHEMA_FILE}")
    _log(f"Using workspace: {WORKSPACE}")

    con = _connect()
    try:
        # --- Section 1.5: pre-schema ADD COLUMN reconciliation ---
        _reconcile_pre_schema_add_columns(con)

        # --- Section 2: aplicar schema (DDL) ---
        # Todas las CREATE ... usan IF NOT EXISTS -> re-ejecutar es seguro.
        con.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
        table_count = _scalar(
            con, "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )
        trigger_count = _scalar(
            con, "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
        )
        fts5_count = _scalar(
            con,
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'",
        )
        _log(
            f"Schema applied ({table_count} tables, {trigger_count} triggers, "
            f"{fts5_count} FTS5 mirrors)"
        )

        # --- Section 3: seed agent_permissions (brief B3 M2 mapping, 13 filas) ---
        # INSERT OR IGNORE, ordenadas por agent_name. Paridad exacta con el .sh.
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

        # --- Section 3a: cleanup legacy agent_permissions rows ---
        con.execute("DELETE FROM agent_permissions WHERE agent_name = 'gaia-operator'")

        # --- Section 3b: seed schema_version baseline (floor) ---
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        existing_version = _scalar(
            con, "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        )
        if existing_version == 0:
            con.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at, description) "
                "VALUES (?, ?, ?)",
                (
                    SCHEMA_FLOOR,
                    now_utc,
                    f"baseline floor: schema.sql at v{SCHEMA_FLOOR}",
                ),
            )
            _log(f"schema_version baseline seeded at floor (v{SCHEMA_FLOOR})")
        elif existing_version < SCHEMA_FLOOR:
            _err(
                f"ERROR: DB at schema_version={existing_version} is below the "
                f"supported floor v{SCHEMA_FLOOR}."
            )
            _err(
                f"In-place upgrade from pre-v{SCHEMA_FLOOR} databases is no longer supported."
            )
            _err(
                f"Recreate the DB: back up any data you need, delete {GAIA_DB}, "
                "then re-run `gaia install`."
            )
            return 1
        else:
            _log(
                f"schema_version at v{existing_version} (>= floor v{SCHEMA_FLOOR}); "
                "no baseline seed needed"
            )

        # --- Section 3c: apply pending forward migrations (floor+1 .. EXPECTED) ---
        expected_version = _read_expected_schema_version()
        current_version = _scalar(
            con, "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        )
        _log(
            f"schema_version: current={current_version}, expected={expected_version}"
        )

        # --- Section 3c.0: schema-DIRECTION guard (drift-free install) ---
        # bootstrap only ever migrates FORWARD (code newer than DB). The reverse
        # direction -- a live DB migrated by a NEWER Gaia than the code now being
        # installed -- was previously unguarded: the `else` branch below logged
        # "up-to-date" and the install "succeeded", leaving stale code to run
        # against a newer schema. That is the exact drift that broke
        # `gaia contract finalize` ("no column named ...") when a stale global CLI
        # (code v36) ran against a DB migrated to v37. Refuse it: never install
        # code OLDER than the DB (no clobber). The DB is left untouched (schema.sql
        # is all CREATE ... IF NOT EXISTS, and no migration or stamp runs past this
        # point); the remedy is to install a Gaia at least as new as the DB, not to
        # downgrade the DB.
        if current_version > expected_version:
            _err(
                f"ERROR: live DB schema_version={current_version} is NEWER than the "
                f"schema this code expects (v{expected_version})."
            )
            _err(
                "This Gaia code is OLDER than the database it would run against; "
                "installing it would leave stale code reading a newer schema "
                "(the finalize-breaking drift). Refusing to install -- the DB is "
                "left untouched (no clobber)."
            )
            _err(
                f"Install a Gaia whose EXPECTED_SCHEMA_VERSION >= {current_version} "
                "(the source checkout or release artifact that produced this DB), "
                "then re-run. To validate without changing anything, run `gaia doctor`."
            )
            return 1

        if current_version < expected_version:
            for n in range(current_version + 1, expected_version + 1):
                prev = n - 1
                mig_file = MIG_DIR / f"v{prev}_to_v{n}.sql"
                if not mig_file.is_file():
                    _err(f"ERROR: missing migration file {mig_file}")
                    _err(
                        f"Cannot advance from v{prev} to v{n}. The ledger will "
                        f"remain at v{current_version}."
                    )
                    _err(
                        f"When bumping EXPECTED_SCHEMA_VERSION to v{n}, add "
                        f"scripts/migrations/v{prev}_to_v{n}.sql in the same commit."
                    )
                    return 1

                _log(f"migration v{prev}->v{n}: applying {mig_file}")
                mig_sql = _filter_add_column_idempotent(con, mig_file)
                # Cada migracion en su propia transaccion (BEGIN/COMMIT), igual
                # que el .sh. En autocommit, executescript con BEGIN/COMMIT da
                # atomicidad; si falla, la transaccion se revierte y el ledger
                # NO avanza.
                try:
                    con.executescript(f"BEGIN;\n{mig_sql}\nCOMMIT;")
                except sqlite3.Error as exc:
                    try:
                        con.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    _err(
                        f"ERROR: migration v{prev}->v{n} failed. Transaction "
                        f"rolled back. ({exc})"
                    )
                    _err(
                        f"schema_version ledger remains at v{current_version} -- "
                        f"not stamping v{n}."
                    )
                    return 1
                _log(f"migration v{prev}->v{n}: applied successfully")
                con.execute(
                    "INSERT OR IGNORE INTO schema_version (version, applied_at, description) "
                    "VALUES (?, ?, ?)",
                    (n, now_utc, f"applied migration {mig_file.name}"),
                )
        else:
            _log("schema_version up-to-date (no migrations pending)")

        # --- Section 4: registrar workspace actual ---
        workspace_identity = _resolve_workspace_identity()
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name, identity) VALUES (?, ?)",
            (workspace_identity, workspace_identity),
        )
        _log(f"Workspace registered (identity={workspace_identity})")

        # --- Section 5: FTS5 backfill (4 mirrors) ---
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
        if fts_ok:
            _log("FTS5 backfilled (4/4 consistency check passed)")
        else:
            _log("FTS5 backfilled (consistency check WARNING -- ver lineas anteriores)")

        # --- Section 6: invariantes finales ---
        all_ok = True

        perms_count = _scalar(
            con, "SELECT COUNT(*) FROM agent_permissions WHERE allow_write IS NOT NULL"
        )
        if perms_count >= 13:
            _log(f"check: agent_permissions rows >= 13 (got {perms_count}) -- PASS")
        else:
            _log(f"check: agent_permissions rows >= 13 (got {perms_count}) -- FAIL")
            all_ok = False

        agent_count = _scalar(
            con, "SELECT COUNT(DISTINCT agent_name) FROM agent_permissions"
        )
        if agent_count >= 5:
            _log(f"check: distinct agents >= 5 (got {agent_count}) -- PASS")
        else:
            _log(f"check: distinct agents >= 5 (got {agent_count}) -- FAIL")
            all_ok = False

        workspace_count = _scalar(con, "SELECT COUNT(*) FROM workspaces")
        if workspace_count >= 1:
            _log(f"check: workspaces rows >= 1 (got {workspace_count}) -- PASS")
        else:
            _log(f"check: workspaces rows >= 1 (got {workspace_count}) -- FAIL")
            all_ok = False

        trigger_fts_count = _scalar(
            con,
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
            "AND (name LIKE '%_fts_%' OR name LIKE 'briefs_a%')",
        )
        if trigger_fts_count == 12:
            _log(f"check: FTS5 triggers == 12 (got {trigger_fts_count}) -- PASS")
        else:
            _log(f"check: FTS5 triggers == 12 (got {trigger_fts_count}) -- FAIL")
            all_ok = False

        schema_ver = _scalar(
            con, "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        )
        if schema_ver >= SCHEMA_FLOOR:
            _log(
                f"check: schema_version >= floor v{SCHEMA_FLOOR} (got {schema_ver}) -- PASS"
            )
        else:
            _log(
                f"check: schema_version >= floor v{SCHEMA_FLOOR} (got {schema_ver}) -- FAIL"
            )
            all_ok = False

        # --- Section 7: resumen ---
        if all_ok:
            _log(f"Done. DB at {GAIA_DB} ready for `gaia` CLI operations.")
            return 0
        _err("Done WITH FAILURES. Revisa los checks marcados FAIL arriba.")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
