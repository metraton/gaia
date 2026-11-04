#!/usr/bin/env bash
# migrate_02_memory.sh
# Regenerates the .sql from the .md files, then loads it into ~/.gaia/gaia.db.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HERE}/migrate_02_memory.py"
SQL_FILE="/tmp/migrate_02_memory.sql"
DB_PATH="${HOME}/.gaia/gaia.db"
SRC="${1:?usage: migrate_02_memory.sh <memory directory with the .md files>}"

echo "[migrate_02] regenerando ${SQL_FILE} ..."
python3 "${PY_SCRIPT}" --src "${SRC}"

echo "[migrate_02] aplicando ${SQL_FILE} en ${DB_PATH} ..."
sqlite3 "${DB_PATH}" < "${SQL_FILE}"

echo "[migrate_02] OK"
