#!/usr/bin/env bash
# migrate_01_episodes.sh
# Wrapper: regenera el .sql desde JSONL y luego lo carga en ~/.gaia/gaia.db.
# El INSERT vía sqlite3 es interceptado por el hook pre_tool_use (flujo correcto).
set -euo pipefail

# Rutas absolutas (cwd reset entre invocaciones).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HERE}/migrate_01_episodes.py"
SQL_FILE="/tmp/migrate_01_episodes.sql"
DB_PATH="${HOME}/.gaia/gaia.db"

# Paso 1: regenerar el .sql (solo lectura de filesystem; no toca DB).
echo "[migrate_01] regenerando ${SQL_FILE} ..."
python3 "${PY_SCRIPT}"

# Paso 2: aplicar el SQL en la DB (interceptado por el hook).
echo "[migrate_01] aplicando ${SQL_FILE} en ${DB_PATH} ..."
sqlite3 "${DB_PATH}" < "${SQL_FILE}"

echo "[migrate_01] OK"
