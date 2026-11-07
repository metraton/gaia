#!/usr/bin/env bash
# migrate_01_episodes.sh
# Regenerates the .sql from the JSONL, then loads it into ~/.gaia/gaia.db.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${HERE}/migrate_01_episodes.py"
SQL_FILE="/tmp/migrate_01_episodes.sql"
DB_PATH="${HOME}/.gaia/gaia.db"
SRC="${1:?usage: migrate_01_episodes.sh <path to episodes.jsonl>}"

echo "[migrate_01] regenerando ${SQL_FILE} ..."
python3 "${PY_SCRIPT}" --src "${SRC}"

echo "[migrate_01] aplicando ${SQL_FILE} en ${DB_PATH} ..."
sqlite3 "${DB_PATH}" < "${SQL_FILE}"

echo "[migrate_01] OK"
