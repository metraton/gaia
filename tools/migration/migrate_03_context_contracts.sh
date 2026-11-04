#!/usr/bin/env bash
# migrate_03_context_contracts.sh
# Regenerates the .sql from project-context.json, then loads it into ~/.gaia/gaia.db.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HERE}/migrate_03_context_contracts.py"
SQL_FILE="/tmp/migrate_03_context_contracts.sql"
DB_PATH="${HOME}/.gaia/gaia.db"
SRC="${1:?usage: migrate_03_context_contracts.sh <path to project-context.json>}"

echo "[migrate_03] regenerando ${SQL_FILE} ..."
python3 "${PY_SCRIPT}" --src "${SRC}"

echo "[migrate_03] aplicando ${SQL_FILE} en ${DB_PATH} ..."
sqlite3 "${DB_PATH}" < "${SQL_FILE}"

echo "[migrate_03] OK"
