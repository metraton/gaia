#!/usr/bin/env bash
# bootstrap_database.sh -- Inicializador idempotente de la DB Gaia.
#
# Reemplaza:
#   - scripts/seed_agent_permissions.py
#   - tools/memory/backfill_fts5.py (sólo la parte de FTS5 mirrors del schema)
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
#   developer, terraform-architect, gitops-operator, gaia-system, cloud-troubleshooter.
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
#   terraform-architect  -> tf_modules, tf_live, clusters              (3)
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

-- terraform-architect: capa IaC (tf_modules, tf_live, clusters declarativos)
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('clusters',   'terraform-architect', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('tf_live',    'terraform-architect', 1);
INSERT OR IGNORE INTO agent_permissions (table_name, agent_name, allow_write) VALUES ('tf_modules', 'terraform-architect', 1);
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

# === Section 3b: Seed schema_version baseline (v1) ===
#
# La tabla schema_version se crea en schema.sql. Aquí insertamos SOLO la fila
# v1 ("initial schema") como baseline del ledger. Idempotente vía INSERT OR
# IGNORE -- reejecutar el bootstrap no duplica ni reescribe la fila.
#
# Las versiones >= 2 NO se insertan aquí. Section 3c (abajo) aplica migraciones
# en orden y emite la fila schema_version correspondiente sólo si la migración
# concreta tuvo éxito. Diseño elegido para evitar el bug histórico de "ledger
# miente": v2 era stampada incondicionalmente aunque CREATE TABLE IF NOT EXISTS
# short-circuiteaba la DDL nueva sobre DBs preexistentes.
#
# `gaia doctor` lee MAX(version) y lo compara contra EXPECTED_SCHEMA_VERSION
# baked in al CLI. Adicionalmente, check_schema_ddl_consistency compara el CHECK
# constraint vivo contra el de schema.sql para cazar drift de ledger.
NOW_UTC="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (1, '${NOW_UTC}', 'initial schema');
EOF
echo "[bootstrap] schema_version baseline seeded (v1)"

# === Section 3c: Apply pending schema migrations ===
#
# Itera desde MAX(version)+1 hasta EXPECTED_SCHEMA_VERSION (extraído de
# bin/cli/doctor.py vía grep) y aplica scripts/migrations/v{N-1}_to_v{N}.sql
# cuando son necesarios. Cada migración se aplica en su propia transacción
# BEGIN/COMMIT con guard de pre-condición para soportar fresh installs donde
# schema.sql ya creó la tabla en estado target.
#
# Lógica por versión N:
#   1. ¿Existe el archivo de migración? Si no, abort.
#   2. Guard probe: ¿la live DB ya está en estado target? Si sí, sólo stampa
#      el row del ledger y continúa (caso fresh install).
#   3. Si no, ejecuta la migración dentro de BEGIN/COMMIT. Si la transacción
#      falla, abort -- el ledger NO se actualiza, el próximo bootstrap retry
#      ve la misma migración pendiente.
#   4. Tras éxito, INSERT OR IGNORE en schema_version (version=N, ...).
#
# EXPECTED_SCHEMA_VERSION se lee dinámicamente de doctor.py para mantener una
# sola fuente de verdad. test_schema_version_lockstep garantiza que el número
# en doctor.py concuerda con las migraciones disponibles.

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

if [ "$CURRENT_VERSION" -lt "$EXPECTED_VERSION" ]; then
    for N in $(seq $((CURRENT_VERSION + 1)) "$EXPECTED_VERSION"); do
        PREV=$((N - 1))
        MIG_FILE="${MIG_DIR}/v${PREV}_to_v${N}.sql"

        if [ ! -f "$MIG_FILE" ]; then
            echo "[bootstrap] ERROR: missing migration file ${MIG_FILE}" >&2
            echo "[bootstrap] Cannot advance from v${PREV} to v${N}. The ledger will remain at v${CURRENT_VERSION}." >&2
            exit 1
        fi

        # Per-version guard probe. Each migration has a fingerprint that
        # tells us whether the live DDL is already at the target state
        # (fresh install where schema.sql ran with the new DDL) or still
        # at the source state (existing DB where CREATE TABLE IF NOT EXISTS
        # short-circuited).
        ALREADY_AT_TARGET=0
        case "$N" in
            2)
                # v1 -> v2: widen memory.type CHECK. Target state contains 'atom'.
                MEMORY_DDL="$(sqlite3 "$GAIA_DB" "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory';")"
                if [[ "$MEMORY_DDL" == *"'atom'"* ]]; then
                    ALREADY_AT_TARGET=1
                fi
                ;;
            *)
                # Future migrations: each new N must add a case here with a
                # fingerprint of the post-migration state.
                echo "[bootstrap] ERROR: no guard probe registered for v${PREV}->v${N}." >&2
                echo "[bootstrap] Add a case to Section 3c when introducing migration v${N}." >&2
                exit 1
                ;;
        esac

        if [ "$ALREADY_AT_TARGET" = "1" ]; then
            echo "[bootstrap] migration v${PREV}->v${N}: live DDL already at target (fresh install), stamping ledger only"
            sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${N}, '${NOW_UTC}', 'auto-stamped: schema.sql created table at v${N} state');
EOF
        else
            echo "[bootstrap] migration v${PREV}->v${N}: applying ${MIG_FILE}"
            # Wrap the migration in an explicit transaction. The migration SQL
            # itself does NOT contain BEGIN/COMMIT so we control atomicity here.
            # Errors abort the script via set -e + sqlite3 exit code.
            MIG_SQL="$(cat "$MIG_FILE")"
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
            sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${N}, '${NOW_UTC}', 'applied migration v${PREV}_to_v${N}.sql');
EOF
        fi
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

# Check 2: at least 5 distinct agents (developer, terraform-architect,
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
# inserta la fila (version=1). Verificamos que MAX(version) >= 1.
SCHEMA_VER="$(sqlite3 "$GAIA_DB" "SELECT COALESCE(MAX(version), 0) FROM schema_version;")"
if [ "$SCHEMA_VER" -ge 1 ]; then
    echo "[bootstrap] check: schema_version >= 1 (got ${SCHEMA_VER}) -- PASS"
else
    echo "[bootstrap] check: schema_version >= 1 (got ${SCHEMA_VER}) -- FAIL"
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
