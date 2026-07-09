#!/usr/bin/env bash
# bootstrap_database.sh -- Inicializador idempotente de la DB Gaia.
#
# Reemplaza:
#   - scripts/seed_agent_permissions.py
#   - tools/memory/backfill_fts5.py (retirado; sólo cubría la parte de FTS5 mirrors del schema)
#   - cualquier inicialización dispersa via Python de la DB
#
# Principios:
#   - Bash + sqlite3 puros. Nada de python3.
#   - SQL literal y legible.
#   - Idempotente: ejecutarlo dos veces no cambia el estado.
#   - Cada bloque comentado en español, explicando QUÉ hace y POR QUÉ.

set -euo pipefail

# === Section 1: Variables y validación de entorno ===

# Path de la DB. Configurable vía env GAIA_DB; default ~/.gaia/gaia.db.
# Se mantiene una sola fuente de verdad: el resto del script siempre usa $GAIA_DB.
GAIA_DB="${GAIA_DB:-$HOME/.gaia/gaia.db}"

# Path al schema.sql. Se asume que el script vive en gaia/scripts/ y el schema
# en gaia/gaia/store/schema.sql. Resolvemos relativo al script para no depender
# del cwd desde donde se invoca.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
SCHEMA_FILE="${SCHEMA_FILE:-$SCRIPT_DIR/../gaia/store/schema.sql}"

# Workspace cuya identidad se va a registrar en projects. Default: directorio
# raíz del repo (dos niveles arriba de scripts/). Configurable vía env.
WORKSPACE="${WORKSPACE:-$SCRIPT_DIR/..}"

# Verificar que sqlite3 está instalado. Sin esto, todo lo demás falla con
# errores oscuros; preferimos un mensaje claro al inicio.
if ! command -v sqlite3 > /dev/null 2>&1; then
    echo "[bootstrap] ERROR: sqlite3 no encontrado en PATH. Instálalo (apt install sqlite3) y reintenta." >&2
    exit 1
fi

# Verificar que el schema existe. Sin él, no podemos aplicar DDL.
if [ ! -f "$SCHEMA_FILE" ]; then
    echo "[bootstrap] ERROR: schema.sql no encontrado en $SCHEMA_FILE" >&2
    exit 1
fi

# Crear el directorio padre de la DB si no existe. mkdir -p es idempotente.
mkdir -p "$(dirname "$GAIA_DB")"

# Banner inicial: deja claro contra qué DB estamos operando antes de tocar nada.
echo "[bootstrap] Initializing Gaia DB at $GAIA_DB"
echo "[bootstrap] Using schema:  $SCHEMA_FILE"
echo "[bootstrap] Using workspace: $WORKSPACE"

# === Section 1.5: Pre-schema ADD COLUMN reconciliation (existing DBs) ===
#
# schema.sql (Section 2 below) is applied UNCONDITIONALLY and in full. It
# carries the EXPECTED (current) schema shape, which INCLUDES indexes on
# columns that were introduced by forward migrations -- e.g. v27_to_v28 adds
# `agent_contract_handoffs.contract_id` plus a UNIQUE index on it, and
# schema.sql (~line 985) carries
# `CREATE UNIQUE INDEX ... ON agent_contract_handoffs(contract_id)`.
#
# On a FRESH DB this is fine: the `CREATE TABLE agent_contract_handoffs
# (... contract_id ...)` in schema.sql creates the column first, so the index
# build in the same pass succeeds. On an EXISTING DB whose table predates the
# column, `CREATE TABLE IF NOT EXISTS` is a no-op (the table already exists
# WITHOUT the column) and the following `CREATE INDEX ... (contract_id)` aborts
# with "no such column: contract_id". SQLite offers no way to make a
# `CREATE INDEX` conditional on a column, and schema.sql MUST run before the
# migration ledger (Section 3c) can read/advance versions -- so on an existing
# DB the column has to exist BEFORE schema.sql runs.
#
# This section closes that gap generically and idempotently: for every
# `ALTER TABLE <t> ADD COLUMN <c> ...` statement declared in the forward
# migration files, if table <t> ALREADY exists in the live DB and column <c>
# is absent, add it NOW -- before schema.sql. The migration file stays the
# single source of the ADD COLUMN (one-file-per-bump); we only change WHEN a
# pre-existing table receives the column so schema.sql's index build cannot
# trip.
#
#   - Fresh DB: no tables exist yet, so every candidate table fails the
#     "table exists" guard and nothing is added -- schema.sql builds the column
#     itself (Section 2) and the migration replay (Section 3c) is a guarded
#     no-op. FRESH-DB materialisation is unchanged.
#   - Existing DB missing the column: the column is added here, schema.sql's
#     index build then succeeds, and Section 3c advances the ledger.
#   - Re-run / already-migrated DB (incl. one left partial by a prior FAILED
#     install): the column is already present, the guard skips it, and the
#     section is a no-op. Recovery on retry is automatic.
#
# Pure bash + sqlite3, no python3 -- consistent with this script's principles.
# NOTE: the matcher assumes one `ALTER TABLE ... ADD COLUMN ...` per line, the
# same assumption _filter_add_column_idempotent (Section 3c) already relies on.

_reconcile_pre_schema_add_columns() {
    local mig_file line lower table col tbl_exists col_exists
    for mig_file in "${SCRIPT_DIR}/migrations"/v*_to_v*.sql; do
        [ -f "$mig_file" ] || continue
        while IFS= read -r line || [ -n "$line" ]; do
            lower="$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]')"
            if [[ "$lower" =~ alter[[:space:]]+table[[:space:]]+([a-z0-9_]+)[[:space:]]+add[[:space:]]+column[[:space:]]+([a-z0-9_]+) ]]; then
                table="${BASH_REMATCH[1]}"
                col="${BASH_REMATCH[2]}"
                # Table must already exist (existing DB). On a fresh DB this is
                # 0 and we skip -- schema.sql will create the table + column.
                tbl_exists="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='${table}';")"
                if [ "$tbl_exists" -eq 0 ]; then
                    continue
                fi
                # Column must be absent. If present (re-run / already migrated),
                # skip -- idempotent no-op.
                col_exists="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM pragma_table_info('${table}') WHERE name='${col}';")"
                if [ "$col_exists" -eq 0 ]; then
                    echo "[bootstrap] pre-schema reconcile: adding ${table}.${col} (existing DB predates it)"
                    sqlite3 "$GAIA_DB" "$line"
                fi
            fi
        done < "$mig_file"
    done
}

_reconcile_pre_schema_add_columns

# === Section 2: Aplicar schema (DDL) ===

# Aplicamos schema.sql siempre. Todas las CREATE TABLE / CREATE INDEX /
# CREATE TRIGGER / CREATE VIRTUAL TABLE en schema.sql usan IF NOT EXISTS, así
# que ejecutarlo sobre una DB ya inicializada es seguro (no falla, no recrea).
# Si la DB no existe, sqlite3 la crea al primer comando.
sqlite3 "$GAIA_DB" < "$SCHEMA_FILE"

# Reportar conteo de tablas, triggers y FTS5 mirrors aplicados. Estos números
# nos sirven como evidencia rápida de que el schema está completo. Los pedimos
# por separado para evitar pipes y mantener una salida diagnosticable.
TABLE_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")"
TRIGGER_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger';")"
FTS5_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE '%_fts';")"

echo "[bootstrap] Schema applied (${TABLE_COUNT} tables, ${TRIGGER_COUNT} triggers, ${FTS5_COUNT} FTS5 mirrors)"

# === Section 3: Seed agent_permissions ===

# Matriz canonical: brief B3 M2 (open_agents-read-write-new-workspace/brief.md).
# 5 agentes × N tablas, allow_write=1 (sólo se enumeran las combinaciones con
# permiso de escritura).
#
# Lista de agentes (5):
#   developer, platform-architect, gitops-operator, gaia-system, cloud-troubleshooter.
# Source of truth para el nombre del 4º agente: tools/scan/migrate_workspace.py
# constante _SCANNER_AGENTS -> "gaia-system".
#
# Drift detectado y resuelto:
#   1) El brief B3 nombra al cuarto agente "gaia-operator". El código vivo
#      (migrate_workspace.py::_SCANNER_AGENTS) lo nombra "gaia-system".
#      Aquí adoptamos "gaia-system" -- el código es la source of truth viva,
#      el brief quedó parcialmente desincronizado y "gaia-operator" en el
#      .py legacy scripts/seed_agent_permissions.py es stale (a borrar
#      después de este bootstrap).
#   2) El brief asigna un dominio NARROW por agente (13 filas total). El
#      código `_SCANNER_AGENTS × _SCANNER_TABLES` da 5×14=70 INSERT OR
#      REPLACE (todos allow_write=1) -- esa matriz es la usada por el
#      scanner durante populate, NO la spec de enforcement de agentes.
#      La spec aquí es la del brief (narrow per-domain). Si el flujo de
#      scanner necesita más tablas, debe vivir aparte (e.g. agente sintético
#      "scanner") sin contaminar la matriz de dominio.
#
# Mapping del brief (B3 M2, sección Approach + AC-2):
#   developer            -> apps, libraries, services, features        (4)
#   platform-architect   -> tf_modules, tf_live, clusters              (3)
#   gitops-operator      -> releases, workloads, clusters_defined      (3)
#   gaia-system          -> integrations, gaia_installations           (2)
#   cloud-troubleshooter -> clusters                                   (1)
#
# Total: 13 filas. Filas ordenadas por agent_name (alfabético) para auditoría
# trivial. Idempotente: INSERT OR IGNORE; reejecutar no falla ni duplica.
# Todas las tablas referenciadas existen en schema.sql -- cero zombie refs.
sqlite3 "$GAIA_DB" <<'EOF'
-- cloud-troubleshooter: estado observado de clusters (read-heavy, write declarativo)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('clusters', 'cloud-troubleshooter', 1);

-- developer: capa de aplicación (apps, libraries, services, features)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('apps',      'developer', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('features',  'developer', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('libraries', 'developer', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('services',  'developer', 1);

-- gaia-system: integraciones e instalaciones de Gaia (renombrado desde "gaia-operator" del brief)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('gaia_installations', 'gaia-system', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('integrations',       'gaia-system', 1);

-- gitops-operator: estado deseado (releases, workloads, clusters_defined)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('clusters_defined', 'gitops-operator', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('releases',         'gitops-operator', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('workloads',        'gitops-operator', 1);

-- platform-architect: capa IaC (tf_modules, tf_live, clusters declarativos)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('clusters',   'platform-architect', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('tf_live',    'platform-architect', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('tf_modules', 'platform-architect', 1);
EOF

echo "[bootstrap] agent_permissions seeded (13 rows, 5 agents, brief B3 M2 mapping)"

# === Section 3a: Cleanup legacy agent_permissions rows ===
#
# Section 3 (above) inserts the canonical "gaia-system" name. A previous
# version of this bootstrap (or the legacy scripts/seed_agent_permissions.py)
# inserted rows under the old name "gaia-operator" -- see the rename note in
# Section 3 above (line 83-86). Those legacy rows persist across upgrades
# because INSERT OR IGNORE never removes anything. Without cleanup, the
# distinct-agents check below sees 6 agents on upgraded DBs instead of 5,
# and the strict equality variant of the check (pre-fix) used to fail.
#
# DELETE is safe here: the legacy "gaia-operator" rows have no live consumer
# in the current model -- the gaia-system agent owns its own table_name set
# (gaia_installations, integrations) which never collided with the legacy
# row's table_name. We are pruning orphan data, not migrating it.
sqlite3 "$GAIA_DB" <<'EOF'
DELETE FROM agent_permissions WHERE agent_name = 'gaia-operator';
EOF

# === Section 3b: Seed schema_version baseline (floor) ===
#
# Modelo de FLOOR (piso de schema), reemplaza al viejo "seed v1 + camina
# v1..v17". Gaia es una herramienta personal de un solo usuario: nadie
# actualiza una DB más vieja que la versión actual, y las instalaciones
# nuevas construyen el schema directamente desde schema.sql (que ya produce
# la forma del FLOOR). Por eso colapsamos la historia v1->v17 a un baseline.
#
# SCHEMA_FLOOR es la versión mínima soportada in-place. schema.sql produce
# exactamente esta forma. Reglas:
#
#   - DB nueva (sin filas en schema_version): schema.sql ya creó el estado
#     FLOOR, así que sellamos (version=SCHEMA_FLOOR) directamente. No se
#     siembra v1 ni se camina la cadena.
#   - DB en o por encima del FLOOR: no se hace nada aquí (Section 3c decide
#     si hay migraciones forward pendientes hacia EXPECTED).
#   - DB por debajo del FLOOR (1 <= version < FLOOR): NO soportada para
#     upgrade in-place. Abortamos con un mensaje claro pidiendo recrear la DB.
#
# `gaia doctor` lee MAX(version) y lo compara contra EXPECTED_SCHEMA_VERSION
# baked in al CLI. Adicionalmente, check_schema_ddl_consistency compara el CHECK
# constraint vivo contra el de schema.sql para cazar drift de ledger.
SCHEMA_FLOOR=18

NOW_UTC="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
EXISTING_VERSION="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"

if [ "$EXISTING_VERSION" -eq 0 ]; then
    # Fresh install: schema.sql ya construyó el estado FLOOR. Sellamos el
    # ledger directamente en el FLOOR. INSERT OR IGNORE mantiene idempotencia.
    sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${SCHEMA_FLOOR}, '${NOW_UTC}', 'baseline floor: schema.sql at v${SCHEMA_FLOOR}');
EOF
    echo "[bootstrap] schema_version baseline seeded at floor (v${SCHEMA_FLOOR})"
elif [ "$EXISTING_VERSION" -lt "$SCHEMA_FLOOR" ]; then
    # DB por debajo del piso: ya no soportamos upgrade in-place desde la
    # cadena histórica v1..v17. Fallamos claro (no en silencio).
    echo "[bootstrap] ERROR: DB at schema_version=${EXISTING_VERSION} is below the supported floor v${SCHEMA_FLOOR}." >&2
    echo "[bootstrap] In-place upgrade from pre-v${SCHEMA_FLOOR} databases is no longer supported." >&2
    echo "[bootstrap] Recreate the DB: back up any data you need, delete ${GAIA_DB}, then re-run \`gaia install\`." >&2
    exit 1
else
    # DB en o por encima del piso: nada que sembrar aquí. Section 3c decide
    # si hay migraciones forward pendientes hacia EXPECTED_SCHEMA_VERSION.
    echo "[bootstrap] schema_version at v${EXISTING_VERSION} (>= floor v${SCHEMA_FLOOR}); no baseline seed needed"
fi

# === Section 3c: Apply pending forward migrations (floor+1 .. EXPECTED) ===
#
# Modelo FLOOR forward-only. La cadena histórica v1..v17 fue eliminada; el
# baseline es el FLOOR (Section 3b). Esta sección aplica SÓLO migraciones
# forward que se agreguen en el futuro, una por bump:
#
#   scripts/migrations/v{N-1}_to_v{N}.sql   (N > SCHEMA_FLOOR)
#
# Convención forward-only (ver scripts/migrations/README.md):
#   - El baseline es la versión actual (FLOOR). schema.sql produce esa forma.
#   - Cada bump futuro agrega EXACTAMENTE un v{N-1}_to_v{N}.sql y sube
#     EXPECTED_SCHEMA_VERSION en doctor.py en el mismo commit.
#   - Para una DB en el FLOOR, esa migración corre directo (la DB está en el
#     estado source de la migración). No se necesitan variantes _fresh: un
#     fresh install sella el ledger en el FLOOR (Section 3b) y, cuando hay
#     migraciones forward (EXPECTED > FLOOR), ESTE loop SÍ se replaya en cada
#     fresh install desde FLOOR+1 hasta EXPECTED contra una DB cuyos objetos
#     schema.sql ya creó -- por eso las migraciones DEBEN ser idempotentes
#     (CREATE ... IF NOT EXISTS; ADD COLUMN neutralizado por el guard runner).
#     El guard-probe por-versión del modelo viejo desaparece junto con la
#     cadena histórica.
#
# Cada migración corre en su propia transacción BEGIN/COMMIT. Si falla, abort
# -- el ledger NO avanza y el próximo bootstrap retry ve la misma pendiente.
#
# EXPECTED_SCHEMA_VERSION se lee dinámicamente de doctor.py para mantener una
# sola fuente de verdad. test_schema_version_lockstep garantiza que el número
# en doctor.py concuerda con las migraciones disponibles (== FLOOR cuando no
# hay migraciones forward todavía).

DOCTOR_PY="${SCRIPT_DIR}/../bin/cli/doctor.py"
if [ ! -f "$DOCTOR_PY" ]; then
    echo "[bootstrap] ERROR: doctor.py no encontrado en $DOCTOR_PY (no puedo leer EXPECTED_SCHEMA_VERSION)" >&2
    exit 1
fi

# Extract EXPECTED_SCHEMA_VERSION = N literal. grep + awk; no pipes per
# command-execution skill -- two commands, one variable.
EXPECTED_LINE="$(grep -E '^EXPECTED_SCHEMA_VERSION\s*=\s*[0-9]+' "$DOCTOR_PY")"
EXPECTED_VERSION="${EXPECTED_LINE##*= }"
EXPECTED_VERSION="${EXPECTED_VERSION// /}"

if ! [[ "$EXPECTED_VERSION" =~ ^[0-9]+$ ]]; then
    echo "[bootstrap] ERROR: no pude parsear EXPECTED_SCHEMA_VERSION desde $DOCTOR_PY (got: '${EXPECTED_VERSION}')" >&2
    exit 1
fi

CURRENT_VERSION="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"
echo "[bootstrap] schema_version: current=${CURRENT_VERSION}, expected=${EXPECTED_VERSION}"

MIG_DIR="${SCRIPT_DIR}/migrations"

# --- Idempotent ADD COLUMN guard (runner-level) ------------------------------
#
# Forward migrations are applied on EVERY fresh install: schema.sql produces the
# EXPECTED shape, the ledger is stamped at the FLOOR, and Section 3c walks
# FLOOR+1..EXPECTED (this is what test_fresh_install_stamps_floor and
# test_bootstrap_idempotent_at_floor require). Because schema.sql already
# carries each migration's target DDL (see migrations/README.md section 1:
# "add the DDL to schema.sql AND create the migration"), a migration is always
# replayed against a DB that already has its objects.
#
# CREATE ... IF NOT EXISTS makes CREATE statements idempotent under that replay
# (v18_to_v19 relies on it). But SQLite has NO `ADD COLUMN IF NOT EXISTS`, so a
# bare `ALTER TABLE t ADD COLUMN c` aborts with "duplicate column name" when the
# column already exists from schema.sql. This guard restores idempotency for
# ADD COLUMN at the RUNNER level (not by putting invalid SQL in the .sql file):
# for each `ALTER TABLE <t> ADD COLUMN <c> ...` line, if column <c> already
# exists on table <t> (PRAGMA table_info), the line is neutralised (commented
# out) before the migration runs. Every other statement passes through verbatim.
#
# Pure bash + sqlite3, no python3 -- consistent with this script's principles.
_filter_add_column_idempotent() {
    # $1 = path to the migration .sql file. Emits the (possibly filtered) SQL on
    # stdout. Lines that are `ALTER TABLE t ADD COLUMN c` for an existing column
    # are replaced by a comment; all other lines are passed through unchanged.
    local mig_file="$1"
    local line lower table col exists
    while IFS= read -r line || [ -n "$line" ]; do
        # Normalise whitespace for matching only (emit the ORIGINAL line).
        lower="$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]')"
        if [[ "$lower" =~ alter[[:space:]]+table[[:space:]]+([a-z0-9_]+)[[:space:]]+add[[:space:]]+column[[:space:]]+([a-z0-9_]+) ]]; then
            table="${BASH_REMATCH[1]}"
            col="${BASH_REMATCH[2]}"
            exists="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM pragma_table_info('${table}') WHERE name='${col}';")"
            if [ "$exists" -gt 0 ]; then
                printf -- '-- [bootstrap] skipped (column %s.%s already present): %s\n' "$table" "$col" "$line"
                continue
            fi
        fi
        printf '%s\n' "$line"
    done < "$mig_file"
}

if [ "$CURRENT_VERSION" -lt "$EXPECTED_VERSION" ]; then
    # Forward-only loop. Runs whenever the live DB is behind EXPECTED, which
    # INCLUDES a fresh install: Section 3b stamps the ledger at the FLOOR, and
    # when forward migrations exist (EXPECTED > FLOOR) a fresh DB sits at the
    # FLOOR while EXPECTED is higher, so it enters here and replays FLOOR+1..
    # EXPECTED. That replay runs against a DB whose objects schema.sql already
    # created, which is exactly why these migrations MUST be idempotent (CREATE
    # ... IF NOT EXISTS; ADD COLUMN neutralised by the runner guard above).
    # Any DB below the FLOOR was already rejected in Section 3b, so
    # CURRENT_VERSION here is always >= FLOOR.
    for N in $(seq $((CURRENT_VERSION + 1)) "$EXPECTED_VERSION"); do
        PREV=$((N - 1))
        MIG_FILE="${MIG_DIR}/v${PREV}_to_v${N}.sql"

        if [ ! -f "$MIG_FILE" ]; then
            echo "[bootstrap] ERROR: missing migration file ${MIG_FILE}" >&2
            echo "[bootstrap] Cannot advance from v${PREV} to v${N}. The ledger will remain at v${CURRENT_VERSION}." >&2
            echo "[bootstrap] When bumping EXPECTED_SCHEMA_VERSION to v${N}, add scripts/migrations/v${PREV}_to_v${N}.sql in the same commit." >&2
            exit 1
        fi

        # Apply the migration inside an explicit transaction. The SQL is passed
        # through _filter_add_column_idempotent first so that `ADD COLUMN`
        # statements for columns schema.sql already created are skipped (SQLite
        # lacks `ADD COLUMN IF NOT EXISTS`). CREATE ... IF NOT EXISTS statements
        # are already idempotent and pass through unchanged. This is what lets a
        # fresh install (where schema.sql produced the EXPECTED shape) replay the
        # FLOOR+1..EXPECTED migrations without aborting on duplicate columns.
        echo "[bootstrap] migration v${PREV}->v${N}: applying ${MIG_FILE}"
        MIG_SQL="$(_filter_add_column_idempotent "$MIG_FILE")"
        if ! sqlite3 "$GAIA_DB" <<EOF
BEGIN;
${MIG_SQL}
COMMIT;
EOF
        then
            echo "[bootstrap] ERROR: migration v${PREV}->v${N} failed. Transaction rolled back." >&2
            echo "[bootstrap] schema_version ledger remains at v${CURRENT_VERSION} -- not stamping v${N}." >&2
            exit 1
        fi
        echo "[bootstrap] migration v${PREV}->v${N}: applied successfully"
        MIG_DESC="applied migration $(basename "$MIG_FILE")"
        sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${N}, '${NOW_UTC}', '${MIG_DESC}');
EOF
    done
else
    echo "[bootstrap] schema_version up-to-date (no migrations pending)"
fi

# === Section 4: Registrar workspace actual ===
#
# El schema v2.0 (commit be9698f) renombró:
#   - projects (organizational container) -> workspaces
#   - repos (git-bearing) -> projects
# El seed aquí inserta una fila inicial en `workspaces` (el contenedor
# organizacional, no la tabla de repos git). El scanner luego puebla
# `projects` cuando descubre repos git dentro del workspace.

# Detectamos la identity del workspace via git remote get-url origin, igual que
# gaia.store.writer._resolve_identity(). La normalización (lowercase, strip
# protocolo, strip .git, ssh form) la hacemos en SQL/bash puro -- no llamamos
# a Python.
#
# Fallback: si no hay remote, usamos el basename del workspace en lowercase.
# Si tampoco eso, usamos 'global'.

WORKSPACE_IDENTITY=""
RAW_REMOTE=""

# Capturamos el remote sin pipes; si git falla, RAW_REMOTE queda vacío.
if command -v git > /dev/null 2>&1; then
    RAW_REMOTE="$(git -C "$WORKSPACE" remote get-url origin 2> /dev/null || true)"
fi

if [ -n "$RAW_REMOTE" ]; then
    # Normalización mínima: lowercase + strip de prefijos comunes + strip .git.
    # Equivalente a gaia.project._normalize_remote() en bash puro.
    s="${RAW_REMOTE,,}"             # lowercase (bash 4+)
    s="${s#https://}"
    s="${s#http://}"
    s="${s#ssh://}"
    s="${s#git+ssh://}"
    s="${s#git+https://}"
    # SSH form: git@host:owner/repo -> host/owner/repo
    if [[ "$s" == git@* ]]; then
        s="${s#git@}"
        s="${s/:/\/}"               # primer ':' -> '/'
    fi
    s="${s%.git}"
    s="${s%/}"
    WORKSPACE_IDENTITY="$s"
fi

if [ -z "$WORKSPACE_IDENTITY" ]; then
    # Fallback nivel 2: basename del workspace en lowercase.
    base="$(basename "$(cd "$WORKSPACE" && pwd)")"
    WORKSPACE_IDENTITY="${base,,}"
fi

if [ -z "$WORKSPACE_IDENTITY" ]; then
    # Fallback nivel 3: literal 'global'.
    WORKSPACE_IDENTITY="global"
fi

# El name (PK) y la identity son el mismo string en este flujo bootstrap.
# El scanner puede actualizar identity más adelante; aquí sólo garantizamos
# que existe una fila en `workspaces` para el workspace actual.
sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO workspaces (name, identity) VALUES ('${WORKSPACE_IDENTITY}', '${WORKSPACE_IDENTITY}');
EOF

echo "[bootstrap] Workspace registered (identity=${WORKSPACE_IDENTITY})"

# === Section 5: FTS5 backfill ===

# Backfill idempotente de los 4 FTS5 mirrors definidos en schema.sql:
#   projects_fts, apps_fts, services_fts, briefs_fts.
# (Antes del rename schema v2.0 era repos_fts; ahora projects_fts mirrorea
#  la tabla `projects` -- el repo git-bearing.)
#
# Estrategia: para cada mirror, insertamos sólo los rowids que no están ya
# presentes en el mirror. Los triggers AFTER INSERT mantienen los nuevos rows
# en sync automáticamente; el backfill cubre el caso de filas que existían
# antes de que los triggers / mirrors fueran creados.
#
# Nota: briefs usa `id` como rowid (PRIMARY KEY AUTOINCREMENT), las otras
# tablas usan rowid implícito de SQLite. Ambos casos funcionan con la
# expresión `rowid` en el SELECT.

sqlite3 "$GAIA_DB" <<'EOF'
-- projects_fts: name, role, primary_language (mirror of `projects`)
INSERT INTO projects_fts(rowid, name, role, primary_language)
SELECT rowid, name, role, primary_language
FROM projects
WHERE rowid NOT IN (SELECT rowid FROM projects_fts);

-- apps_fts: name, description, topic_key
INSERT INTO apps_fts(rowid, name, description, topic_key)
SELECT rowid, name, description, topic_key
FROM apps
WHERE rowid NOT IN (SELECT rowid FROM apps_fts);

-- services_fts: name, description, topic_key
INSERT INTO services_fts(rowid, name, description, topic_key)
SELECT rowid, name, description, topic_key
FROM services
WHERE rowid NOT IN (SELECT rowid FROM services_fts);

-- briefs_fts: objective, context, approach (rowid = briefs.id)
INSERT INTO briefs_fts(rowid, objective, context, approach)
SELECT id, objective, context, approach
FROM briefs
WHERE id NOT IN (SELECT rowid FROM briefs_fts);
EOF

# Verificación de consistencia: COUNT(base) debe ser igual a COUNT(fts) para
# cada uno de los 4 pares. Si difieren, el backfill no completó (probablemente
# porque algún trigger emitió 'delete' tombstones y los conteos directos
# divergen; en ese caso la consulta de delta sigue siendo más fiable que la
# raw count, pero documentamos la discrepancia).
FTS_OK=1
for pair in "projects:projects_fts" "apps:apps_fts" "services:services_fts" "briefs:briefs_fts"; do
    base="${pair%%:*}"
    mirror="${pair##*:}"
    base_count="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM ${base};")"
    mirror_count="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM ${mirror};")"
    if [ "$base_count" != "$mirror_count" ]; then
        echo "[bootstrap]   WARN: ${base} (${base_count}) != ${mirror} (${mirror_count})" >&2
        FTS_OK=0
    fi
done

if [ "$FTS_OK" = "1" ]; then
    echo "[bootstrap] FTS5 backfilled (4/4 consistency check passed)"
else
    echo "[bootstrap] FTS5 backfilled (consistency check WARNING -- ver líneas anteriores)"
fi

# === Section 6: Invariantes finales ===

# Cinco checks SQL que reportan estado final. Cada check imprime PASS o FAIL
# con su valor concreto. Si alguno FAIL, el script termina con exit 1 al final.
ALL_OK=1

# Check 1: agent_permissions tiene al menos 13 filas (matriz canonical) +
# la fila ejemplo del schema (apps, developer) = 13 únicas (la fila ejemplo
# coincide con una de la matriz, así que esperamos exactamente 13).
PERMS_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM agent_permissions WHERE allow_write IS NOT NULL;")"
if [ "$PERMS_COUNT" -ge 13 ]; then
    echo "[bootstrap] check: agent_permissions rows >= 13 (got ${PERMS_COUNT}) -- PASS"
else
    echo "[bootstrap] check: agent_permissions rows >= 13 (got ${PERMS_COUNT}) -- FAIL"
    ALL_OK=0
fi

# Check 2: at least 5 distinct agents (developer, platform-architect,
# gitops-operator, gaia-system, cloud-troubleshooter). Uses -ge for the same
# reason Checks 1, 3, 5 do: the seed is INSERT OR IGNORE (idempotent), so a
# DB carrying rows from prior Gaia versions may legitimately have additional
# distinct agent_name values (e.g. the legacy "gaia-operator" before the
# rename to "gaia-system" documented in Section 3 above). Strict equality
# breaks every install on machines where ~/.gaia/gaia.db survived a Gaia
# upgrade -- contradicts the "idempotent over many runs" principle declared
# at line 12 of this script.
AGENT_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(DISTINCT agent_name) FROM agent_permissions;")"
if [ "$AGENT_COUNT" -ge "5" ]; then
    echo "[bootstrap] check: distinct agents >= 5 (got ${AGENT_COUNT}) -- PASS"
else
    echo "[bootstrap] check: distinct agents >= 5 (got ${AGENT_COUNT}) -- FAIL"
    ALL_OK=0
fi

# Check 3: al menos 1 workspace registrado (el actual). El bootstrap seedea
# `workspaces`, no `projects`; el scanner es quien crea filas en `projects`
# cuando descubre repos git dentro del workspace.
WORKSPACE_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM workspaces;")"
if [ "$WORKSPACE_COUNT" -ge 1 ]; then
    echo "[bootstrap] check: workspaces rows >= 1 (got ${WORKSPACE_COUNT}) -- PASS"
else
    echo "[bootstrap] check: workspaces rows >= 1 (got ${WORKSPACE_COUNT}) -- FAIL"
    ALL_OK=0
fi

# Check 4: los 12 FTS5 triggers existen.
# 3 por mirror (insert/delete/update) × 3 mirrors antiguos +
# 3 para briefs (briefs_ai/briefs_ad/briefs_au) = 12.
TRIGGER_FTS_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND (name LIKE '%_fts_%' OR name LIKE 'briefs_a%');")"
if [ "$TRIGGER_FTS_COUNT" = "12" ]; then
    echo "[bootstrap] check: FTS5 triggers == 12 (got ${TRIGGER_FTS_COUNT}) -- PASS"
else
    echo "[bootstrap] check: FTS5 triggers == 12 (got ${TRIGGER_FTS_COUNT}) -- FAIL"
    ALL_OK=0
fi

# Check 5: schema_version. La tabla se crea en schema.sql y la Section 3b
# sella la fila baseline en el FLOOR (v${SCHEMA_FLOOR}). Verificamos que
# MAX(version) >= FLOOR -- por debajo del piso ya habríamos abortado en 3b.
SCHEMA_VER="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"
if [ "$SCHEMA_VER" -ge "$SCHEMA_FLOOR" ]; then
    echo "[bootstrap] check: schema_version >= floor v${SCHEMA_FLOOR} (got ${SCHEMA_VER}) -- PASS"
else
    echo "[bootstrap] check: schema_version >= floor v${SCHEMA_FLOOR} (got ${SCHEMA_VER}) -- FAIL"
    ALL_OK=0
fi

# === Section 7: Resumen ===

if [ "$ALL_OK" = "1" ]; then
    echo "[bootstrap] Done. DB at $GAIA_DB ready for \`gaia\` CLI operations."
    exit 0
else
    echo "[bootstrap] Done WITH FAILURES. Revisa los checks marcados FAIL arriba." >&2
    exit 1
fi
