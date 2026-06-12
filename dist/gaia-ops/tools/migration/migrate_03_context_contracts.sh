#!/usr/bin/env bash
# migrate_03_context_contracts.sh
# Wrapper: regenera el .sql desde project-context.json y lo carga en ~/.gaia/gaia.db.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HERE}/migrate_03_context_contracts.py"
SQL_FILE="/tmp/migrate_03_context_contracts.sql"
DB_PATH="${HOME}/.gaia/gaia.db"

# Paso 1: regenerar el .sql.
echo "[migrate_03] regenerando ${SQL_FILE} ..."
python3 "${PY_SCRIPT}"

# Paso 2: aplicar el SQL (interceptado por el hook).
echo "[migrate_03] aplicando ${SQL_FILE} en ${DB_PATH} ..."
sqlite3 "${DB_PATH}" < "${SQL_FILE}"

echo "[migrate_03] OK"
