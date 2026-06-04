#!/usr/bin/env bash
# migrate_04_harness_events.sh
# Wrapper: regenera el .sql desde events.jsonl y lo carga en ~/.gaia/gaia.db.
#
# OJO: harness_events no tiene PK natural. Re-ejecutar este wrapper duplica
# filas. Si necesitas re-ejecutar limpio, primero elimina las filas con:
#   sqlite3 ~/.gaia/gaia.db "DELETE FROM harness_events WHERE project='me';"
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HERE}/migrate_04_harness_events.py"
SQL_FILE="/tmp/migrate_04_harness_events.sql"
DB_PATH="${HOME}/.gaia/gaia.db"

# Paso 1: regenerar el .sql.
echo "[migrate_04] regenerando ${SQL_FILE} ..."
python3 "${PY_SCRIPT}"

# Paso 2: aplicar el SQL (interceptado por el hook).
echo "[migrate_04] aplicando ${SQL_FILE} en ${DB_PATH} ..."
sqlite3 "${DB_PATH}" < "${SQL_FILE}"

echo "[migrate_04] OK"
