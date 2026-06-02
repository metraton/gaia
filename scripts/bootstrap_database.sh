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
        #
        # A guard probe can also OVERRIDE which .sql file to run when the
        # entry state is mutative but distinct from the canonical source.
        # See v2->v3 below for the "both tables present" case, which needs
        # the merge variant rather than the rename variant.
        ALREADY_AT_TARGET=0
        OVERRIDE_MIG_FILE=""
        case "$N" in
            2)
                # v1 -> v2: widen memory.type CHECK. Target state contains 'atom'.
                MEMORY_DDL="$(sqlite3 "$GAIA_DB" "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory';")"
                if [[ "$MEMORY_DDL" == *"'atom'"* ]]; then
                    ALREADY_AT_TARGET=1
                fi
                ;;
            3)
                # v2 -> v3: rename context_contracts -> project_context_contracts
                # and add agent_contract_permissions. Three entry states:
                #   state 1 (only old): rename via v2_to_v3.sql (the default file).
                #   state 2 (only new + perms exist): "at target", stamp ledger.
                #   state 3 (both tables): copy rows + drop old via v2_to_v3_merge.sql.
                #
                # The detection order is deliberate: we check for the legacy
                # table first because its presence is the disqualifying signal
                # for "already at target", regardless of what else exists.
                HAS_OLD="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='context_contracts';")"
                HAS_NEW="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='project_context_contracts';")"
                HAS_PERMS="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_contract_permissions';")"

                if [ -z "$HAS_OLD" ] && [ "$HAS_NEW" = "project_context_contracts" ] && [ "$HAS_PERMS" = "agent_contract_permissions" ]; then
                    # State 2: only new tables exist, fully migrated.
                    ALREADY_AT_TARGET=1
                elif [ "$HAS_OLD" = "context_contracts" ] && [ "$HAS_NEW" = "project_context_contracts" ]; then
                    # State 3: both tables exist -- run the merge variant.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v2_to_v3_merge.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: state-3 merge script missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: state 1 (only old) -- fall through to default rename script.
                ;;
            4)
                # v3 -> v4: add memory.class + memory.status columns plus the
                # memory_links table. Target state contains the `class` column
                # on the memory table. We probe pragma_table_info; presence of
                # 'class' is the fingerprint of v4 target state.
                #
                # Note: idx_memory_class_status is intentionally NOT declared
                # in schema.sql -- it references columns that ALTER TABLE adds
                # later, and CREATE INDEX in schema.sql would parse-fail on
                # v3 DBs. On fresh-install (ALREADY_AT_TARGET=1) we run the
                # migration script anyway because its statements are all
                # idempotent (`IF NOT EXISTS`) and the only operation that is
                # NOT a no-op on fresh install is the index creation.
                MEMORY_HAS_CLASS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('memory') WHERE name='class';")"
                MEMORY_LINKS_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_links';")"
                if [ "$MEMORY_HAS_CLASS" = "class" ] && [ "$MEMORY_LINKS_EXISTS" = "memory_links" ]; then
                    # Fresh install: schema.sql created the v4 columns and
                    # memory_links table. Run the migration anyway -- the
                    # ALTER TABLE statements need to be skipped because the
                    # columns already exist. We branch to a fresh-install
                    # variant that ONLY creates the index.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v3_to_v4_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v3->v4 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v3 DB -- fall through to default v3_to_v4.sql.
                ;;
            5)
                # v4 -> v5: add acceptance_criteria.status + milestones.status.
                # Target state: acceptance_criteria has 'status' column.
                AC_HAS_STATUS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('acceptance_criteria') WHERE name='status';")"
                MS_HAS_STATUS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('milestones') WHERE name='status';")"
                if [ "$AC_HAS_STATUS" = "status" ] && [ "$MS_HAS_STATUS" = "status" ]; then
                    # Fresh install: schema.sql already created v5 columns.
                    # Run the fresh-install variant that ONLY creates the indexes.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v4_to_v5_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v4->v5 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v4 DB -- fall through to default v4_to_v5.sql.
                ;;
            6)
                # v5 -> v6: add evidence table (three-tier storage model).
                # Target state: evidence table exists.
                EVIDENCE_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='evidence';")"
                if [ "$EVIDENCE_EXISTS" = "evidence" ]; then
                    # Fresh install: schema.sql already created the evidence table.
                    # Run the fresh-install variant that ONLY creates the indexes.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v5_to_v6_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v5->v6 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v5 DB -- fall through to default v5_to_v6.sql.
                ;;
            7)
                # v6 -> v7: add workspaces.last_scan_at column (agent-contract-handoff M1).
                # Target state: workspaces table has a 'last_scan_at' column.
                WS_HAS_LAST_SCAN="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('workspaces') WHERE name='last_scan_at';")"
                if [ "$WS_HAS_LAST_SCAN" = "last_scan_at" ]; then
                    # Fresh install: schema.sql already created the column.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v6_to_v7_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v6->v7 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v6 DB -- fall through to default v6_to_v7.sql.
                ;;
            8)
                # v7 -> v8: add approval_grants table (agent-contract-handoff M3).
                # Target state: approval_grants table exists.
                GRANTS_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='approval_grants';")"
                if [ "$GRANTS_EXISTS" = "approval_grants" ]; then
                    # Fresh install: schema.sql already created the approval_grants table.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v7_to_v8_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v7->v8 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v7 DB -- fall through to default v7_to_v8.sql.
                ;;
            9)
                # v8 -> v9: add agent_contract_handoffs, agent_contract_handoff_approvals,
                # project_context_contracts_history tables + trg_pcc_history trigger
                # (agent-contract-handoff M4: handoff persistence).
                # Target state: agent_contract_handoffs table exists.
                HANDOFFS_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_contract_handoffs';")"
                if [ "$HANDOFFS_EXISTS" = "agent_contract_handoffs" ]; then
                    # Fresh install: schema.sql already created all v9 tables and trigger.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v8_to_v9_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v8->v9 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v8 DB -- fall through to default v8_to_v9.sql.
                ;;
            10)
                # v9 -> v10: add episodes.tier column + idx_episodes_tier + idx_episodes_tier_outcome
                # + episode_anomalies table + its 3 indexes
                # (episodic-workflow-to-db AC-3: migration apply).
                #
                # Target state fingerprint: episodes.tier column exists.
                # We use PRAGMA table_info to check for the tier column.
                # This is the correct fingerprint because:
                #   - Fresh install: schema.sql creates episodes WITH tier -> tier exists
                #   - Existing v9 DB: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op
                #     -> tier does NOT exist -> falls through to the full migration
                # Note: episode_anomalies table is NOT a valid fingerprint because
                # schema.sql creates it via CREATE TABLE IF NOT EXISTS even on existing DBs.
                TIER_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('episodes') WHERE name='tier';")"
                if [ "$TIER_EXISTS" = "tier" ]; then
                    # Fresh install: schema.sql already created episodes with tier column.
                    # Run the fresh-install variant (creates tier indexes) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v9_to_v10_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v9->v10 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v9 DB -- fall through to default v9_to_v10.sql.
                ;;
            11)
                # v10 -> v11: memory.class NOT NULL + CHECK(anchor|thread|log)
                # + trg_pcc_history trigger column fix (contract_key->contract_name,
                # payload_json->payload). Closes ledger task #6.
                #
                # Target state fingerprint: memory.class column is NOT NULL.
                # We query pragma_table_info and check the notnull flag (column 3 in
                # the pragma output: 0=nullable, 1=NOT NULL). A fresh install creates
                # memory with NOT NULL class -> notnull=1. An existing v10 DB has
                # class as nullable -> notnull=0 -> falls through to the full migration.
                # Correct fingerprint because:
                #   - Fresh install (schema.sql creates memory with NOT NULL class): notnull=1
                #   - Existing v10 DB (CREATE TABLE IF NOT EXISTS is a no-op): notnull=0
                MEMORY_CLASS_NOTNULL="$(sqlite3 "$GAIA_DB" "SELECT \"notnull\" FROM pragma_table_info('memory') WHERE name='class';")"
                if [ "$MEMORY_CLASS_NOTNULL" = "1" ]; then
                    # Fresh install: schema.sql already created memory with NOT NULL class.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v10_to_v11_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v10->v11 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v10 DB -- fall through to default v10_to_v11.sql.
                ;;
            12)
                # v11 -> v12: add approvals + approval_events tables + three hash-chain triggers
                # (approval-model-redesign M1: user-in-loop, fingerprint-bound, hash-chained).
                #
                # Target state fingerprint: approvals table exists.
                # We probe sqlite_master for the table name.
                # This is the correct fingerprint because:
                #   - Fresh install: schema.sql creates approvals table -> it exists
                #   - Existing v11 DB: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op
                #     -> approvals does NOT exist -> falls through to the full migration
                # Note: approval_events triggers require the gaia_sha256 scalar function
                # to be registered on the connection before any INSERT fires them.
                # bootstrap_database.sh uses sqlite3 CLI which does NOT register Python
                # functions; the trigger DDL is stored but can only fire via gaia.store.
                # The migration SQL itself only defines the DDL (no INSERTs into
                # approval_events), so the migration applies cleanly via sqlite3 CLI.
                APPROVALS_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='approvals';")"
                if [ "$APPROVALS_EXISTS" = "approvals" ]; then
                    # Fresh install: schema.sql already created the approvals table.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v11_to_v12_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v11->v12 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v11 DB -- fall through to default v11_to_v12.sql.
                ;;
            13)
                # v12 -> v13: add group_name column to projects table
                # (gaia-scan-overhaul: workspace->group->repo model, AC-2).
                #
                # Target state fingerprint: projects.group_name column exists.
                # We probe pragma_table_info for the column name.
                # This is the correct fingerprint because:
                #   - Fresh install: schema.sql creates projects WITH group_name -> it exists
                #   - Existing v12 DB: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op
                #     -> group_name does NOT exist -> falls through to the full migration
                GROUP_NAME_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('projects') WHERE name='group_name';")"
                if [ "$GROUP_NAME_EXISTS" = "group_name" ]; then
                    # Fresh install: schema.sql already created projects with group_name column.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v12_to_v13_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v12->v13 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v12 DB -- fall through to default v12_to_v13.sql.
                ;;
            14)
                # v13 -> v14: add path column to projects table
                # (gaia-scan-overhaul: findability, project -> path + workspace).
                #
                # Target state fingerprint: projects.path column exists.
                # We probe pragma_table_info for the column name.
                # This is the correct fingerprint because:
                #   - Fresh install: schema.sql creates projects WITH path -> it exists
                #   - Existing v13 DB: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op
                #     -> path does NOT exist -> falls through to the full migration
                PATH_EXISTS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('projects') WHERE name='path';")"
                if [ "$PATH_EXISTS" = "path" ]; then
                    # Fresh install: schema.sql already created projects with path column.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v13_to_v14_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v13->v14 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v13 DB -- fall through to default v13_to_v14.sql.
                ;;
            15)
                # v14 -> v15: rename the per-project child-table FK column
                # repo -> project on apps, libraries, services, features,
                # tf_modules, tf_live, releases, workloads, clusters_defined
                # (substrate rename catch-up; closes "no such column: project").
                #
                # Target state fingerprint: apps.project column exists.
                # We probe pragma_table_info for the column name on `apps`
                # (representative of all nine child tables, which are renamed
                # together in the same migration).
                # This is the correct fingerprint because:
                #   - Fresh install: schema.sql creates apps WITH `project` -> it exists
                #   - Existing v14 DB: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op
                #     so apps still has the legacy `repo` column -> `project` does
                #     NOT exist -> falls through to the full rename migration.
                APPS_HAS_PROJECT="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('apps') WHERE name='project';")"
                if [ "$APPS_HAS_PROJECT" = "project" ]; then
                    # Fresh install: schema.sql already created child tables with
                    # the `project` column. Run the fresh-install variant (no-op
                    # SELECT) to stamp the ledger without attempting the rename.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v14_to_v15_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v14->v15 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v14 DB -- fall through to default v14_to_v15.sql.
                ;;
            16)
                # v15 -> v16: add status + missing_since columns to projects table
                # (gaia-scan-overhaul: soft-delete support for missing projects).
                #
                # Target state fingerprint: projects.status column exists.
                # We probe pragma_table_info for the column name.
                # This is the correct fingerprint because:
                #   - Fresh install: schema.sql creates projects WITH status -> it exists
                #   - Existing v15 DB: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op
                #     -> status does NOT exist -> falls through to the full migration
                PROJECTS_HAS_STATUS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('projects') WHERE name='status';")"
                if [ "$PROJECTS_HAS_STATUS" = "status" ]; then
                    # Fresh install: schema.sql already created projects with status column.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v15_to_v16_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v15->v16 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v15 DB -- fall through to default v15_to_v16.sql.
                ;;
            17)
                # v16 -> v17: add status + missing_since columns to workspaces
                # table (DEMOTE case: soft-delete support for demoted workspaces
                # whose Gaia install footprint disappeared).
                #
                # Target state fingerprint: workspaces.status column exists.
                # We probe pragma_table_info for the column name.
                # This is the correct fingerprint because:
                #   - Fresh install: schema.sql creates workspaces WITH status -> it exists
                #   - Existing v16 DB: schema.sql's CREATE TABLE IF NOT EXISTS is a no-op
                #     -> status does NOT exist -> falls through to the full migration
                WORKSPACES_HAS_STATUS="$(sqlite3 "$GAIA_DB" "SELECT name FROM pragma_table_info('workspaces') WHERE name='status';")"
                if [ "$WORKSPACES_HAS_STATUS" = "status" ]; then
                    # Fresh install: schema.sql already created workspaces with status column.
                    # Run the fresh-install variant (no-op SELECT) to stamp the ledger.
                    OVERRIDE_MIG_FILE="${MIG_DIR}/v16_to_v17_fresh.sql"
                    if [ ! -f "$OVERRIDE_MIG_FILE" ]; then
                        echo "[bootstrap] ERROR: v16->v17 fresh-install variant missing at ${OVERRIDE_MIG_FILE}" >&2
                        exit 1
                    fi
                fi
                # Otherwise: existing v16 DB -- fall through to default v16_to_v17.sql.
                ;;
            *)
                # Future migrations: each new N must add a case here with a
                # fingerprint of the post-migration state.
                echo "[bootstrap] ERROR: no guard probe registered for v${PREV}->v${N}." >&2
                echo "[bootstrap] Add a case to Section 3c when introducing migration v${N}." >&2
                exit 1
                ;;
        esac

        # Resolve which file actually runs: per-state override or default.
        EFFECTIVE_MIG_FILE="${OVERRIDE_MIG_FILE:-$MIG_FILE}"

        if [ "$ALREADY_AT_TARGET" = "1" ]; then
            echo "[bootstrap] migration v${PREV}->v${N}: live DDL already at target (fresh install), stamping ledger only"
            sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${N}, '${NOW_UTC}', 'auto-stamped: schema.sql created table at v${N} state');
EOF
        else
            echo "[bootstrap] migration v${PREV}->v${N}: applying ${EFFECTIVE_MIG_FILE}"
            # Wrap the migration in an explicit transaction. The migration SQL
            # itself does NOT contain BEGIN/COMMIT so we control atomicity here.
            # Errors abort the script via set -e + sqlite3 exit code.
            MIG_SQL="$(cat "$EFFECTIVE_MIG_FILE")"
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
            MIG_DESC="applied migration $(basename "$EFFECTIVE_MIG_FILE")"
            sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (${N}, '${NOW_UTC}', '${MIG_DESC}');
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
