#!/usr/bin/env bash
# migrate_02_notes.sh
# Wrapper: regenera el .sql desde los .md y luego lo carga en ~/.gaia/gaia.db.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HERE}/migrate_02_notes.py"
SQL_FILE="/tmp/migrate_02_notes.sql"
DB_PATH="${HOME}/.gaia/gaia.db"

# Paso 1: regenerar el .sql (solo lectura de filesystem).
echo "[migrate_02] regenerando ${SQL_FILE} ..."
python3 "${PY_SCRIPT}"

# Paso 2: aplicar el SQL (interceptado por el hook).
echo "[migrate_02] aplicando ${SQL_FILE} en ${DB_PATH} ..."
sqlite3 "${DB_PATH}" < "${SQL_FILE}"

echo "[migrate_02] OK"
