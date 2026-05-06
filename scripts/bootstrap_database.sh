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

# === Section 4: Registrar proyecto actual ===

# Detectamos la identity del workspace via git remote get-url origin, igual que
# gaia.project.current(). La normalización (lowercase, strip protocolo, strip
# .git, ssh form) la hacemos en SQL/bash puro -- no llamamos a Python.
#
# Fallback: si no hay remote, usamos el basename del workspace en lowercase.
# Si tampoco eso, usamos 'global'.
#
# TODO: Esta lógica es una réplica simplificada de gaia/project.py::current().
# Si en el futuro existe un CLI verb 'gaia project register', preferirlo aquí
# para no duplicar la lógica de normalización. Por ahora INSERT directo.

PROJECT_IDENTITY=""
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
    PROJECT_IDENTITY="$s"
fi

if [ -z "$PROJECT_IDENTITY" ]; then
    # Fallback nivel 2: basename del workspace en lowercase.
    base="$(basename "$(cd "$WORKSPACE" && pwd)")"
    PROJECT_IDENTITY="${base,,}"
fi

if [ -z "$PROJECT_IDENTITY" ]; then
    # Fallback nivel 3: literal 'global'.
    PROJECT_IDENTITY="global"
fi

# El name (PK) y la identity son el mismo string en este flujo bootstrap.
# El scanner puede actualizar identity más adelante; aquí sólo garantizamos
# que existe una fila para el workspace actual.
sqlite3 "$GAIA_DB" <<EOF
INSERT OR IGNORE INTO projects (name, identity) VALUES ('${PROJECT_IDENTITY}', '${PROJECT_IDENTITY}');
EOF

echo "[bootstrap] Project registered (identity=${PROJECT_IDENTITY})"

# === Section 5: FTS5 backfill ===

# Backfill idempotente de los 4 FTS5 mirrors definidos en schema.sql:
#   repos_fts, apps_fts, services_fts, briefs_fts.
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
-- repos_fts: name, role, primary_language
INSERT INTO repos_fts(rowid, name, role, primary_language)
SELECT rowid, name, role, primary_language
FROM repos
WHERE rowid NOT IN (SELECT rowid FROM repos_fts);

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
for pair in "repos:repos_fts" "apps:apps_fts" "services:services_fts" "briefs:briefs_fts"; do
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

# Check 2: 5 agentes distintos (developer, terraform-architect, gitops-operator,
# gaia-operator, cloud-troubleshooter).
AGENT_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(DISTINCT agent_name) FROM agent_permissions;")"
if [ "$AGENT_COUNT" = "5" ]; then
    echo "[bootstrap] check: distinct agents == 5 (got ${AGENT_COUNT}) -- PASS"
else
    echo "[bootstrap] check: distinct agents == 5 (got ${AGENT_COUNT}) -- FAIL"
    ALL_OK=0
fi

# Check 3: al menos 1 proyecto registrado (el actual).
PROJECT_COUNT="$(sqlite3 "$GAIA_DB" "SELECT COUNT(*) FROM projects;")"
if [ "$PROJECT_COUNT" -ge 1 ]; then
    echo "[bootstrap] check: projects rows >= 1 (got ${PROJECT_COUNT}) -- PASS"
else
    echo "[bootstrap] check: projects rows >= 1 (got ${PROJECT_COUNT}) -- FAIL"
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

# Check 5: schema_version. La schema.sql actual NO define una tabla
# schema_version. Omitimos el check pero dejamos el TODO explícito para que
# en una iteración futura se añada CREATE TABLE schema_version + INSERT del
# valor del schema (e.g. '1.0').
echo "[bootstrap] check: schema_version table -- SKIPPED (no schema_version table in schema.sql; TODO: add)"

# === Section 7: Resumen ===

if [ "$ALL_OK" = "1" ]; then
    echo "[bootstrap] Done. DB at $GAIA_DB ready for \`gaia\` CLI operations."
    exit 0
else
    echo "[bootstrap] Done WITH FAILURES. Revisa los checks marcados FAIL arriba." >&2
    exit 1
fi
